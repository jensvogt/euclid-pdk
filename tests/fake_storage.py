"""A euclid storage module small enough to read, for the ESM tests.

Buckets and objects in dictionaries, and the multipart sequences implemented rather than stubbed:
an upload assembles its parts in part order and a download hands back the byte range that was
asked for, so a client that numbers its parts wrongly, sizes them inconsistently or reassembles
them out of order fails here rather than by writing a corrupt object to a real server.

Registered on a :class:`fake_gateway.FakeGateway`, which is what authenticates the requests before
any of this is reached.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Any

from fake_gateway import FakeGateway, RecordedRequest


def bucket_ern(name: str) -> str:
    return f"ern:euclid:esm:eu-central-1:000000000000:bucket/{name}"


class FakeStorage:
    """The state a storage module keeps, and the actions that read and write it."""

    def __init__(self) -> None:
        #: (bucket ERN, key) -> the object's bytes.
        self.objects: dict[tuple[str, str], bytes] = {}
        #: (bucket ERN, key) -> the ``x-euclid-attributes`` header the write carried, if any.
        self.attribute_headers: dict[tuple[str, str], str | None] = {}
        #: (bucket ERN, key) -> the ``x-euclid-system-attributes`` header the write carried.
        self.system_attribute_headers: dict[tuple[str, str], str | None] = {}
        self.uploads: dict[str, dict[str, Any]] = {}
        self.downloads: dict[str, tuple[str, str]] = {}
        #: The ``x-euclid-expected-concurrency`` each create-upload/create-download declared.
        self.declared_concurrency: list[str | None] = []
        #: action -> (how many more times to fail it, the status to fail it with).
        self.failures: dict[str, tuple[int, int]] = {}
        self._lock = threading.Lock()

    # -- setup -------------------------------------------------------------------------------

    def install(self, gateway: FakeGateway) -> "FakeStorage":
        """Registers every action this stand-in implements."""
        handlers = {
            "put-object": self.put_object,
            "get-object": self.get_object,
            "create-upload": self.create_upload,
            "upload-part": self.upload_part,
            "complete-upload": self.complete_upload,
            "create-download": self.create_download,
            "download-part": self.download_part,
            "complete-download": self.complete_download,
            "list-objects": self.list_objects,
            "delete-object": self.delete_object,
        }
        for action, handler in handlers.items():
            gateway.on("esm", action, self._guarded(action, handler))
        return self

    def fail_next(self, action: str, times: int = 1, status: int = 500) -> "FakeStorage":
        """Fails the next ``times`` requests for ``action``.

        A 500 is the storage node that is briefly unavailable, which a client is right to try
        again; a 4xx is the request that is simply wrong, which it is not.
        """
        self.failures[action] = (times, status)
        return self

    def _guarded(self, action, handler):
        """Serialises the handlers and applies the injected failures.

        The gateway is threaded and a transfer sends its parts at once, so without the lock the
        dictionaries above would be mutated concurrently - and a test that failed because of that
        would say nothing about the client.
        """

        def handle(request: RecordedRequest) -> tuple[int, Any]:
            with self._lock:
                pending, status = self.failures.get(action, (0, 500))
                if pending:
                    self.failures[action] = (pending - 1, status)
                    return status, {"error": "Storage temporarily unavailable"}
                return handler(request)

        return handle

    # -- single-request transfers --------------------------------------------------------------

    def put_object(self, request: RecordedRequest) -> tuple[int, Any]:
        key = (request.headers["x-euclid-bucket-ern"], request.headers["x-euclid-key"])
        self._store(key, request.body, request)
        return 200, self._stored(key)

    def get_object(self, request: RecordedRequest) -> tuple[int, Any]:
        key = (request.headers["x-euclid-bucket-ern"], request.headers["x-euclid-key"])
        limit = int(request.headers["x-euclid-part-size"])
        data = self.objects.get(key)
        if data is None:
            return 404, {"error": "Object not found"}
        if len(data) >= limit:
            return 413, {"error": "Object too large for a single response"}
        return 200, data

    # -- multipart upload ------------------------------------------------------------------------

    def create_upload(self, request: RecordedRequest) -> tuple[int, Any]:
        body = request.json()
        self.declared_concurrency.append(request.headers.get("x-euclid-expected-concurrency"))
        upload_id = f"upload-{len(self.uploads) + 1}"
        self.uploads[upload_id] = {"bucketErn": body["bucketErn"], "key": body["key"], "parts": {}}
        return 200, {"uploadId": upload_id, "bucketErn": body["bucketErn"], "key": body["key"]}

    def upload_part(self, request: RecordedRequest) -> tuple[int, Any]:
        upload = self.uploads.get(request.headers.get("x-euclid-upload-id", ""))
        if upload is None:
            return 404, {"error": "Upload not found"}
        upload["parts"][int(request.headers["x-euclid-part-number"])] = request.body
        return 200, {"partNumber": int(request.headers["x-euclid-part-number"])}

    def complete_upload(self, request: RecordedRequest) -> tuple[int, Any]:
        upload = self.uploads.pop(request.json().get("uploadId", ""), None)
        if upload is None:
            return 404, {"error": "Upload not found"}
        key = (upload["bucketErn"], upload["key"])
        self._store(key, b"".join(part for _, part in sorted(upload["parts"].items())), request)
        return 200, self._stored(key)

    # -- multipart download ----------------------------------------------------------------------

    def create_download(self, request: RecordedRequest) -> tuple[int, Any]:
        body = request.json()
        self.declared_concurrency.append(request.headers.get("x-euclid-expected-concurrency"))
        key = (body["bucketErn"], body["key"])
        data = self.objects.get(key)
        if data is None:
            return 404, {"error": "Object not found"}
        download_id = f"download-{len(self.downloads) + 1}"
        self.downloads[download_id] = key
        return 200, {"downloadId": download_id, "bucketErn": key[0], "key": key[1],
                     "ern": f"{key[0]}/{key[1]}", "size": len(data),
                     "contentType": "application/octet-stream"}

    def download_part(self, request: RecordedRequest) -> tuple[int, Any]:
        key = self.downloads.get(request.headers.get("x-euclid-download-id", ""))
        if key is None:
            return 404, {"error": "Download not found"}
        number = int(request.headers["x-euclid-part-number"])
        size = int(request.headers["x-euclid-part-size"])
        return 200, self.objects[key][(number - 1) * size:number * size]

    def complete_download(self, request: RecordedRequest) -> tuple[int, Any]:
        if self.downloads.pop(request.json().get("downloadId", ""), None) is None:
            return 404, {"error": "Download not found"}
        return 200, {}

    # -- listings --------------------------------------------------------------------------------

    def list_objects(self, request: RecordedRequest) -> tuple[int, Any]:
        body = request.json()
        matching = [key for key in sorted(self.objects)
                    if key[0] == body.get("bucketErn") and key[1].startswith(body.get("prefix", ""))]
        return 200, {"objects": [self._stored(key) for key in matching], "total": len(matching)}

    def delete_object(self, request: RecordedRequest) -> tuple[int, Any]:
        return 200, {"ern": request.json().get("ern", "")}

    # -- internals -------------------------------------------------------------------------------

    def _store(self, key: tuple[str, str], data: bytes, request: RecordedRequest) -> None:
        self.objects[key] = data
        self.attribute_headers[key] = request.headers.get("x-euclid-attributes")
        self.system_attribute_headers[key] = request.headers.get("x-euclid-system-attributes")

    def _stored(self, key: tuple[str, str]) -> dict[str, Any]:
        data = self.objects[key]
        return {"ern": f"{key[0]}/{key[1]}", "bucketErn": key[0], "key": key[1], "size": len(data),
                "status": "STORED", "contentType": "application/octet-stream",
                "md5Sum": hashlib.md5(data).hexdigest()}
