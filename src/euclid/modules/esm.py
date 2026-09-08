"""ESM - euclid's storage module: buckets, objects, attributes, subscriptions and transfers.

One object, :class:`EuclidEsm`, built from a session that has already logged in::

    esm = Euclid.for_server(url).login("jens", "secret").esm()
    bucket = esm.create_bucket("reports")
    esm.upload_file(bucket.ern, "2026/q3.pdf", "q3.pdf")

Most of what it does is the same JSON action every other module speaks. Four actions are not:
``put-object``, ``get-object``, ``upload-part`` and ``download-part`` carry the object's bytes
themselves, with the bucket, the key and the part number riding as headers instead of in a body -
which is what keeps a 5 MiB part 5 MiB on the wire rather than a third larger as base64 inside
JSON.

Those four also authenticate differently: they present the session's bearer token rather than a
signature, which is what euclid-cli and euclid-jdk do for the same four actions, so all three
clients write objects the same way. A session that asked for :data:`~euclid.AUTH_SIGNATURE` signs
them anyway - it asked not to be handed a token silently, and a signature over raw bytes is exact
here in a way it is not in every language.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable, Iterator, Mapping, Sequence

from ..dto.com import Variant
from ..dto.esm import (BucketEvent, CreateBucketResult, CreateDownloadResult, CreateUploadResult,
                       DeleteObjectsResult, DisableEncryptionResult, EnableEncryptionResult, EsmObject,
                       ListBucketsResult, ListObjectsResult, ObjectAttribute, PurgeBucketResult,
                       RenameBucketResult, SetBucketInternalResult, StoredObject, SubscribeResult,
                       Subscription, TouchObjectResult)
from ..exceptions import EuclidServiceError
from ..http.client import Response
from .base import ModuleClient
from .eam import EuclidSession

__all__ = ["EuclidEsm", "parse_bucket_event", "TARGET", "QUEUE", "TOPIC",
           "OBJECT_CREATED", "OBJECT_UPDATED", "OBJECT_DELETED",
           "DEFAULT_PART_SIZE", "DEFAULT_CONCURRENCY"]

TARGET = "esm"

#: A subscription's target is a queue...
QUEUE = "SQS"
#: ...or a topic. The two name different modules, and this is what says which.
TOPIC = "SNS"

#: The object events a subscription can ask for. Asking for none asks for all of them.
OBJECT_CREATED = "esm.object.created"
OBJECT_UPDATED = "esm.object.updated"
OBJECT_DELETED = "esm.object.deleted"

#: How much of a file goes into one part. Larger parts mean fewer round trips and more memory in
#: flight; 5 MiB is what euclid-cli and euclid-jdk use, which is what makes a file uploaded by one
#: of the three arrive in the same pieces as one uploaded by another.
DEFAULT_PART_SIZE = 5 * 1024 * 1024

#: How many parts travel at once.
DEFAULT_CONCURRENCY = 4

#: How many attempts one step of a transfer gets. Transfers are long and made of many steps, so a
#: transient failure in any of them would otherwise throw away everything already transferred.
MAX_PART_ATTEMPTS = 4

#: The delay before retrying, multiplied by the attempt number.
PART_RETRY_BASE_DELAY = 0.5

#: What ``get-object`` answers when the object is at or above the size the caller said it would
#: accept. Not an error in :meth:`EuclidEsm.download_file`: it is the server saying the object
#: needs the multipart path.
PAYLOAD_TOO_LARGE = 413

#: The actions that carry raw bytes rather than JSON - see this module's docstring.
BYTE_ACTIONS = frozenset({"put-object", "get-object", "upload-part", "download-part"})


def parse_bucket_event(message_body: str | bytes) -> BucketEvent:
    """The notification a bucket subscription delivered, out of the message that carried it.

    A subscription puts its notification into a queue or a topic as an ordinary message, so nothing
    about receiving it is special::

        for message in eqs.receive_messages(queue_ern).messages:
            event = parse_bucket_event(message.body)
            ...
            eqs.delete_message(message.receipt_handle)

    :raises ValueError: if the body is not JSON at all.
    """
    return BucketEvent.from_json(json.loads(message_body))


class EuclidEsm(ModuleClient):
    """ESM's operations, on the credentials of the session that created it.

    Built by :meth:`euclid.EuclidSession.esm` rather than directly, so that it shares that
    session's identity, namespace and connection settings - and follows them as they change.
    """

    target = TARGET
    byte_actions = BYTE_ACTIONS

    def __init__(self, session: EuclidSession) -> None:
        super().__init__(session)

        #: How long the byte-carrying actions may take, in seconds, or None for the session's own
        #: timeout. Worth raising: the session's default is sized for an action that answers from a
        #: database, and a 5 MiB part on a slow link is not that.
        self.transfer_timeout: float | None = None

    # -- buckets -----------------------------------------------------------------------------

    def create_bucket(self, name: str, internal: bool = False) -> CreateBucketResult:
        """Creates a bucket, and returns the ERN everything else names it by.

        ``internal`` marks it as euclid's own plumbing rather than somebody's bucket, which leaves
        it out of an ordinary listing - see :meth:`set_bucket_internal`, which is how a bucket that
        already exists changes its mind about that.
        """
        return CreateBucketResult.from_json(self._call("create-bucket", {"name": name, "internal": internal}))

    def delete_bucket(self, ern: str) -> None:
        """Deletes a bucket. It has to be empty; :meth:`purge_bucket` is what makes it so."""
        self._call("delete-bucket", {"ern": ern})

    def list_buckets(self, prefix: str = "", page_size: int = 10, page_index: int = 0,
                     sort_column: str = "name", sort_direction: str = "asc",
                     include_internal: bool = False) -> ListBucketsResult:
        """One page of buckets, and how many exist in total.

        euclid's own buckets are left out unless ``include_internal`` asks for them, so a listing
        shows what a person would recognise rather than the artifact bucket applications are
        deployed from.
        """
        return ListBucketsResult.from_json(self._call("list-buckets", {
            "prefix": prefix, "pageSize": page_size, "pageIndex": page_index,
            "sortColumn": sort_column, "sortDirection": sort_direction,
            "includeInternal": include_internal}))

    def get_bucket_ern(self, name: str) -> str:
        """The ERN of the bucket of this name, in the session's account and namespace."""
        return self._text("get-bucket-ern", {"name": name}, "ern")

    def get_bucket_size(self, ern: str) -> int:
        """How many bytes a bucket holds."""
        return self._number("get-bucket-size", {"ern": ern}, "size")

    def rename_bucket(self, ern: str, new_name: str) -> RenameBucketResult:
        """Renames a bucket, and with it every object and subscription that named the old one.

        The ERN changes too, and nothing answers to the old one afterwards, so the one in the
        result is what later calls have to use. Refused rather than merged when a bucket of the new
        name already exists.
        """
        return RenameBucketResult.from_json(self._call("rename-bucket", {"ern": ern, "newName": new_name}))

    def set_bucket_internal(self, ern: str, internal: bool = True) -> SetBucketInternalResult:
        """Marks a bucket as euclid's own plumbing, or stops doing so.

        Separate from creating one because the bucket this exists for usually predates anybody
        thinking about it, and reversible for the same reason: a flag that can only be set is one
        nobody dares set.
        """
        return SetBucketInternalResult.from_json(self._call("set-bucket-internal", {
            "ern": ern, "internal": internal}))

    def purge_bucket(self, ern: str, prefix: str = "") -> PurgeBucketResult:
        """Deletes a bucket's objects, leaving the bucket itself in place.

        A prefix narrows it to the keys that start with that; an empty one purges everything.
        """
        return PurgeBucketResult.from_json(self._call("purge-bucket", {"ern": ern, "prefix": prefix}))

    def enable_encryption(self, bucket_ern: str, key_id: str = "") -> EnableEncryptionResult:
        """Encrypts every object written to this bucket from now on, under an EKM key.

        What it does not do is touch the objects already there: their bytes stay as they were
        stored, each one records the key it is under, and the result says how many such objects
        there are. Re-encrypting them is a decision for whoever owns the data.

        A named key has to exist and be usable for encryption. An unnamed one is created here as
        AES-256 and belongs to EKM from that moment on - which means deleting it there is what
        makes this bucket's objects unrecoverable.
        """
        return EnableEncryptionResult.from_json(self._call("enable-encryption", {
            "bucketErn": bucket_ern, "keyId": key_id}))

    def disable_encryption(self, bucket_ern: str) -> DisableEncryptionResult:
        """Stops encrypting new objects written to a bucket.

        The mirror image of :meth:`enable_encryption` in one respect and no other: it says what
        happens to the next upload, and it is not an undo. Nothing already stored is decrypted or
        rewritten, and the key is left alone rather than revoked - those objects are still under it.
        """
        return DisableEncryptionResult.from_json(self._call("disable-encryption", {"bucketErn": bucket_ern}))

    def add_bucket_tag(self, bucket_ern: str, key: str, value: str) -> None:
        """Tags a bucket. A key that is already tagged keeps its value - :meth:`set_bucket_tag`
        overwrites."""
        self._call("add-bucket-tag", {"ern": bucket_ern, "key": key, "value": value})

    def set_bucket_tag(self, bucket_ern: str, key: str, value: str) -> None:
        """Tags a bucket, overwriting any value the key already had."""
        self._call("set-bucket-tag", {"ern": bucket_ern, "key": key, "value": value})

    def delete_bucket_tag(self, bucket_ern: str, key: str) -> None:
        """Removes a tag from a bucket."""
        self._call("delete-bucket-tag", {"ern": bucket_ern, "key": key})

    # -- objects -----------------------------------------------------------------------------

    def list_objects(self, bucket_ern: str, prefix: str = "", page_size: int = 10, page_index: int = 0,
                     sort_column: str = "name", sort_direction: str = "asc",
                     include_directories: bool = False) -> ListObjectsResult:
        """One page of a bucket's objects, and how many it holds in total.

        Keys are opaque strings, so a bucket only has "directories" in the sense that keys share a
        prefix; the markers for them are left out unless ``include_directories`` asks for them.
        """
        return ListObjectsResult.from_json(self._call("list-objects", {
            "bucketErn": bucket_ern, "prefix": prefix, "pageSize": page_size, "pageIndex": page_index,
            "sortColumn": sort_column, "sortDirection": sort_direction,
            "includeDirectories": include_directories}))

    def get_object_count(self, bucket_ern: str, prefix: str = "") -> int:
        """How many objects a bucket holds. Cheaper than listing them when only the number matters -
        the server counts rather than paging every object back to the caller."""
        return self._number("get-object-count", {"ern": bucket_ern, "prefix": prefix}, "count")

    def delete_object(self, ern: str) -> None:
        """Deletes one object, by its own ERN."""
        self._call("delete-object", {"ern": ern})

    def delete_objects(self, bucket_ern: str, keys: Sequence[str],
                       background: bool = False) -> DeleteObjectsResult:
        """Deletes several named objects from a bucket in one call.

        A key that names no object is not an error, so the result reports both how many keys were
        asked for and how many objects went. Deleting everything under a prefix is
        :meth:`purge_bucket` rather than a variant of this - the server refuses keys and a prefix in
        the same request, since answering both would delete more than either.

        ``background`` has the server answer as soon as it has taken the work on rather than when it
        has finished, in which case the count is what it took on.
        """
        return DeleteObjectsResult.from_json(self._call("delete-objects", {
            "ern": bucket_ern, "keys": list(keys), "async": background}))

    def copy_object(self, source_bucket_ern: str, source_key: str, target_bucket_ern: str,
                    target_key: str) -> EsmObject:
        """Copies an object, leaving the source in place.

        The copy gets its own bytes on disk and its own ERN, so the two are independent from here
        on. Both ends are permission-checked, and an existing object at the target is refused with
        HTTP 409 rather than silently replaced.
        """
        return self._transfer_object("copy-object", source_bucket_ern, source_key,
                                     target_bucket_ern, target_key)

    def move_object(self, source_bucket_ern: str, source_key: str, target_bucket_ern: str,
                    target_key: str) -> EsmObject:
        """Moves an object to another bucket or key, removing the source.

        The bytes are not copied - the same file answers to a different key from now on - so this
        costs the same whatever the object's size. Refuses an existing target exactly as
        :meth:`copy_object` does.
        """
        return self._transfer_object("move-object", source_bucket_ern, source_key,
                                     target_bucket_ern, target_key)

    def rename_object(self, bucket_ern: str, key: str, new_key: str) -> EsmObject:
        """Renames an object within its bucket - a :meth:`move_object` that cannot leave it, which
        is the whole difference between the two."""
        return EsmObject.from_json(self._call("rename-object", {
            "bucketErn": bucket_ern, "key": key, "newKey": new_key}))

    def touch_object(self, bucket_ern: str, prefix: str = "", background: bool = False) -> TouchObjectResult:
        """Re-announces objects already in a bucket, so a listener that missed their creation events
        hears about them now.

        Nothing about the objects changes - not a byte, not their modified time. "Touch" here means
        what it does to listeners, not what it does to storage: a timestamp is something consumers
        compare against, and moving it would make this destructive in exactly the way it is trying
        not to be.

        ``background`` is what a bucket of any size wants: the announcement is per object, and
        holding a request open for all of them is a request that times out.
        """
        return TouchObjectResult.from_json(self._call("touch-object", {
            "ern": bucket_ern, "prefix": prefix, "async": background}))

    def _transfer_object(self, action: str, source_bucket_ern: str, source_key: str,
                         target_bucket_ern: str, target_key: str) -> EsmObject:
        """copy-object and move-object take the same request and differ only in whether the source
        survives."""
        return EsmObject.from_json(self._call(action, {
            "sourceBucketErn": source_bucket_ern, "sourceKey": source_key,
            "targetBucketErn": target_bucket_ern, "targetKey": target_key}))

    # -- object attributes -------------------------------------------------------------------

    def add_object_attribute(self, ern: str, name: str, value: Any) -> ObjectAttribute:
        """Adds a user-defined attribute to an object. One of that name already there keeps its
        value - :meth:`set_object_attribute` overwrites.

        The value is a :class:`~euclid.dto.com.Variant` or a plain Python value to be tagged as one.
        """
        return self._object_attribute("add-object-attribute", ern, name, value)

    def set_object_attribute(self, ern: str, name: str, value: Any) -> ObjectAttribute:
        """Sets a user-defined attribute on an object, overwriting any value it already had."""
        return self._object_attribute("set-object-attribute", ern, name, value)

    def list_object_attributes(self, ern: str) -> dict[str, Variant]:
        """Every user-defined attribute of an object, keyed by name."""
        return Variant.map_from_json(self._call("list-object-attributes", {"ern": ern}).get("attributes"))

    def delete_object_attribute(self, ern: str, name: str) -> None:
        """Deletes one user-defined attribute from an object."""
        self._call("delete-object-attribute", {"ern": ern, "name": name})

    def _object_attribute(self, action: str, ern: str, name: str, value: Any) -> ObjectAttribute:
        return ObjectAttribute.from_json(self._call(action, {
            "ern": ern, "name": name, "value": Variant.of(value).to_json()}))

    # -- subscriptions -----------------------------------------------------------------------

    def subscribe(self, bucket_ern: str, target_type: str, target_ern: str,
                  event_types: Iterable[str] = (), prefix: str = "",
                  directories: bool = False) -> SubscribeResult:
        """Announces a bucket's object events to a queue or a topic from now on.

        What lands there is a :class:`~euclid.dto.esm.BucketEvent`, carried as the body of an
        ordinary message - see :func:`parse_bucket_event`. The filters are applied by the server as
        it publishes, so a subscription only ever delivers what it asked for rather than the target
        receiving everything and discarding most of it.

        Not idempotent: a second call registers a second subscription and the target then receives
        every matching event twice, so a caller that may run twice checks
        :meth:`list_subscriptions` first.

        :param target_type: :data:`QUEUE` or :data:`TOPIC`, which is also what decides how a bare
            target name is resolved.
        :param event_types: :data:`OBJECT_CREATED`, :data:`OBJECT_UPDATED`, :data:`OBJECT_DELETED`,
            or none of them for all of them.
        :param directories: whether the zero-byte directory markers are delivered too.
        """
        return SubscribeResult.from_json(self._call("subscribe", {
            "sourceErn": bucket_ern, "type": target_type, "targetErn": target_ern,
            "eventTypes": list(event_types), "prefix": prefix, "directories": directories}))

    def unsubscribe(self, ern: str) -> None:
        """Removes a subscription, by the ERN :meth:`subscribe` returned - not the bucket's, and not
        the target's."""
        self._call("unsubscribe", {"ern": ern})

    def list_subscriptions(self, bucket_ern: str) -> list[Subscription]:
        """Every subscription currently registered on a bucket."""
        subscriptions = self._call("list-subscriptions", {"bucketErn": bucket_ern}).get("subscriptions")
        return [Subscription.from_json(s) for s in subscriptions] if isinstance(subscriptions, list) else []

    #: :func:`parse_bucket_event`, reachable from the client so that the call that reads a
    #: subscription's messages is found next to the call that created the subscription.
    parse_bucket_event = staticmethod(parse_bucket_event)

    # -- objects, in bytes -------------------------------------------------------------------

    def put_object(self, bucket_ern: str, key: str, data: bytes, attributes: Mapping[str, Any] | None = None,
                   system_attributes: Mapping[str, Any] | None = None) -> StoredObject:
        """Uploads an object in a single request, skipping the multipart sequence entirely.

        Two attribute maps, and they are not the same one. ``attributes`` are the caller's own,
        listed back by :meth:`list_object_attributes` and meaningless to euclid. ``system_attributes``
        are euclid's envelope: they travel with the object across every hop and are never mixed into
        the caller's. The one euclid acts on is ``priority`` - an object written with
        ``system_attributes={"priority": "LOW"}`` produces a notification carrying it, which is how a
        producer's decision survives a hop through a bucket.
        """
        headers = {"x-euclid-bucket-ern": bucket_ern, "x-euclid-key": key}
        headers.update(self._attribute_headers(attributes, system_attributes))
        # Answers with JSON even though the request carried bytes: the same payload complete-upload
        # answers with, so a caller that needs the ERN does not have to take the multipart path.
        response = self._post_bytes("put-object", data, headers)
        return StoredObject.from_json(self._result("put-object", response))

    def get_object(self, bucket_ern: str, key: str, max_inline_size: int = DEFAULT_PART_SIZE) -> bytes:
        """Downloads an object's bytes in a single request.

        The size limit is the server's to enforce rather than this client's: a download's size is
        not known until the server is asked, unlike an upload's, so the caller declares how large a
        response it is willing to take and an object at or above that comes back as HTTP 413.
        :meth:`download_file` uses exactly that to decide whether an object needs the multipart path.
        """
        response = self._get_object(bucket_ern, key, max_inline_size)
        if not response.ok:
            raise EuclidServiceError(TARGET, "get-object", response.status, response.text)
        return response.content

    def upload_file(self, bucket_ern: str, key: str, file: str | Path, part_size: int = DEFAULT_PART_SIZE,
                    concurrency: int = DEFAULT_CONCURRENCY, attributes: Mapping[str, Any] | None = None,
                    system_attributes: Mapping[str, Any] | None = None) -> StoredObject:
        """Uploads a local file in parts, several at a time.

        The file is read a part at a time rather than into memory, and no more than ``concurrency``
        parts are ever in flight, so the memory this costs is bounded by the two together whatever
        the file's size. An empty file is one empty part, so that the object exists.

        Attributes belong on the upload rather than added afterwards: completing an upload is
        finished off in the background, and the object row written at the end carries what this call
        supplied - an attribute added between here and there is overwritten and silently lost. See
        :meth:`put_object` for what separates the two maps.

        :raises EuclidServiceError: if a part or one of the calls bracketing them failed for good.
        :raises ValueError: if ``part_size`` is less than a byte, which the server rejects too.
        """
        _check_part_size(part_size)
        concurrency = max(1, concurrency)
        upload = self._create_upload(bucket_ern, key, concurrency)
        with open(file, "rb") as source:
            _run_bounded(self._upload_parts(upload.upload_id, source, part_size), concurrency)
        return self._complete_upload(upload.upload_id, attributes, system_attributes)

    def download_file(self, bucket_ern: str, key: str, file: str | Path, part_size: int = DEFAULT_PART_SIZE,
                      concurrency: int = DEFAULT_CONCURRENCY) -> int:
        """Downloads an object to a local file, fetching its parts several at a time.

        An object that fits in one part skips multipart entirely. Unlike an upload - whose source
        this client has already stat'd - a download's size is not known before asking, so the
        single-request path is tried first and HTTP 413 is what says the object was too large for it.

        Missing parent directories are created. Returns the number of bytes written.

        :raises ValueError: if ``part_size`` is less than a byte, which the server rejects too.
        """
        _check_part_size(part_size)
        concurrency = max(1, concurrency)
        path = Path(file)
        path.parent.mkdir(parents=True, exist_ok=True)

        inline = self._get_object(bucket_ern, key, part_size)
        if inline.status != PAYLOAD_TOO_LARGE:
            if not inline.ok:
                raise EuclidServiceError(TARGET, "get-object", inline.status, inline.text)
            path.write_bytes(inline.content)
            return len(inline.content)

        download = self._create_download(bucket_ern, key, concurrency)
        # Sized up front so that each part can be written at its own offset whatever order the parts
        # arrive in - the download's answer to upload_file() reading its source in order while
        # letting the parts themselves complete out of order.
        with open(path, "wb") as sink:
            sink.truncate(download.size)

        _run_bounded(self._download_parts(download.download_id, download.size, part_size, path), concurrency)
        self._complete_download(download.download_id)
        return download.size

    # -- monitoring --------------------------------------------------------------------------

    def metrics(self) -> dict[str, Any]:
        """ESM's own metrics, as the server collects them. Returned unparsed - the shape belongs to
        the monitoring module rather than to ESM."""
        return self._call("get-metrics")

    # -- the multipart sequence --------------------------------------------------------------

    def _create_upload(self, bucket_ern: str, key: str, concurrency: int) -> CreateUploadResult:
        """Opens a multipart upload, declaring the concurrency it is about to use so that the
        gateway's autoscaler can ramp storage instances toward it rather than discover the load.

        Retried on 5xx: the object row the server seeds is keyed on the bucket and key, so a second
        attempt updates the same row, and the only cost of a repeat is the scratch directory the
        abandoned upload ID left behind.
        """
        return CreateUploadResult.from_json(self._call_with_retry(
            "create-upload", {"bucketErn": bucket_ern, "key": key},
            {"x-euclid-expected-concurrency": str(concurrency)}))

    def _upload_parts(self, upload_id: str, source: BinaryIO,
                      part_size: int) -> Iterator[Callable[[], None]]:
        """One callable per part, produced as the file is read.

        A generator rather than a list because the runner acquires its slot before pulling the next
        one: a list would read the whole file into memory before the first part had gone out.
        """
        number = 0
        while True:
            chunk = source.read(part_size)
            if not chunk and number:
                return
            number += 1
            yield partial(self._upload_part, upload_id, number, chunk)
            if not chunk:
                return

    def _upload_part(self, upload_id: str, number: int, data: bytes) -> None:
        self._with_retry("upload-part", lambda: self._post_bytes("upload-part", data, {
            "x-euclid-upload-id": upload_id, "x-euclid-part-number": str(number)}))

    def _complete_upload(self, upload_id: str, attributes: Mapping[str, Any] | None,
                         system_attributes: Mapping[str, Any] | None) -> StoredObject:
        """Assembles the parts into the object.

        The attributes ride on this request because the background pass that finishes the upload
        builds the object row from what this call was given. Retried on 5xx like the create: failing
        here discards every part already uploaded, and an upload the server did accept fails a retry
        with 404 rather than being assembled twice.
        """
        return StoredObject.from_json(self._call_with_retry(
            "complete-upload", {"uploadId": upload_id},
            self._attribute_headers(attributes, system_attributes)))

    def _create_download(self, bucket_ern: str, key: str, concurrency: int) -> CreateDownloadResult:
        """Opens a multipart download, which stages the object and says how large it is.

        Retried on 5xx: the session it opens is scratch state keyed by a fresh download ID, so a
        retried attempt starts a new one and the abandoned session is simply never used.
        """
        return CreateDownloadResult.from_json(self._call_with_retry(
            "create-download", {"bucketErn": bucket_ern, "key": key},
            {"x-euclid-expected-concurrency": str(concurrency)}))

    def _download_parts(self, download_id: str, total: int, part_size: int,
                        path: Path) -> Iterator[Callable[[], None]]:
        for number, _ in enumerate(range(0, total, part_size), start=1):
            yield partial(self._download_part, download_id, number, part_size, path)

    def _download_part(self, download_id: str, number: int, part_size: int, path: Path) -> None:
        response = self._with_retry("download-part", lambda: self._post_bytes("download-part", b"", {
            "x-euclid-download-id": download_id, "x-euclid-part-number": str(number),
            "x-euclid-part-size": str(part_size)}))
        # Its own handle rather than one shared between the parts: each writes its own
        # non-overlapping range, so sharing a file position would only mean contending for it.
        with open(path, "r+b") as sink:
            sink.seek((number - 1) * part_size)
            sink.write(response.content)

    def _complete_download(self, download_id: str) -> None:
        """Releases the download's server-side scratch state. Retried on 5xx for the same reason
        completing an upload is: failing here throws away every part already fetched."""
        self._call_with_retry("complete-download", {"downloadId": download_id})

    def _get_object(self, bucket_ern: str, key: str, max_inline_size: int) -> Response:
        """The raw response, so a caller can tell an object that was too large from one that failed."""
        return self._post_bytes("get-object", b"", {
            "x-euclid-bucket-ern": bucket_ern, "x-euclid-key": key,
            "x-euclid-part-size": str(max_inline_size)})

    # -- transport ---------------------------------------------------------------------------

    def _call_with_retry(self, action: str, payload: Mapping[str, Any] | None = None,
                         headers: Mapping[str, str] | None = None) -> dict[str, Any]:
        """One of the JSON actions bracketing a transfer, retried the way the parts between them are.

        They run once per transfer rather than once per part, but giving up on a transient failure
        in one of them discards the whole file, which is what makes them worth the same treatment.
        """
        response = self._with_retry(action, lambda: self._post(action, payload, headers))
        return self._result(action, response)

    def _with_retry(self, action: str, send: Callable[[], Response]) -> Response:
        """Sends one step of a transfer, retrying while it looks transient.

        A 4xx means the request itself is wrong and a repeat would be answered identically, so only
        a 5xx and a request that never got an answer are tried again.
        """
        for attempt in range(1, MAX_PART_ATTEMPTS + 1):
            last = attempt == MAX_PART_ATTEMPTS
            try:
                response = send()
            except OSError:
                if last:
                    raise
            else:
                if response.status < 500 or last:
                    if not response.ok:
                        raise EuclidServiceError(TARGET, action, response.status, response.text)
                    return response
            time.sleep(PART_RETRY_BASE_DELAY * attempt)
        raise AssertionError("unreachable: the last attempt either returns or raises")  # pragma: no cover

    def _post_bytes(self, action: str, data: bytes, headers: Mapping[str, str] | None = None,
                    timeout: float | None = None) -> Response:
        """One of the four actions that carry bytes, on this client's transfer timeout rather than
        the session's - a 5 MiB part on a slow link is not an action that answers from a database."""
        return super()._post_bytes(action, data, headers, self.transfer_timeout)

    def _attribute_headers(self, attributes: Mapping[str, Any] | None,
                           system_attributes: Mapping[str, Any] | None) -> dict[str, str]:
        """The two attribute maps, as the headers that carry them.

        Headers rather than body fields because the actions that take them are the ones whose body
        is either the object's bytes or nothing at all. An empty map is left out entirely, so a
        request that has nothing to say about attributes says nothing.
        """
        headers = {}
        if attributes:
            headers["x-euclid-attributes"] = json.dumps(Variant.map_to_json(attributes))
        if system_attributes:
            headers["x-euclid-system-attributes"] = json.dumps(Variant.map_to_json(system_attributes))
        return headers


def _check_part_size(part_size: int) -> None:
    """Refused here rather than on arrival: a part size of zero would otherwise upload a file as one
    empty part and call it stored, which is a corrupt object rather than an error."""
    if part_size < 1:
        raise ValueError("part_size must be at least 1 byte")


def _run_bounded(tasks: Iterator[Callable[[], None]], concurrency: int) -> None:
    """Runs each task on a pool of ``concurrency`` threads, no more than that many outstanding.

    The bound is a semaphore taken before the next task is pulled rather than the pool's queue,
    because the tasks are produced as a file is read: an unbounded queue would hold the whole file
    in memory as parts waiting for a thread.

    The first failure is raised to the caller, after the pool has drained - a part still in flight
    when another one failed is one the server is already writing, and abandoning the thread would
    not stop it.
    """
    slots = threading.Semaphore(concurrency)

    def guarded(task: Callable[[], None]) -> None:
        try:
            task()
        finally:
            slots.release()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = []
        for task in tasks:
            slots.acquire()
            futures.append(pool.submit(guarded, task))
        for future in futures:
            future.result()
