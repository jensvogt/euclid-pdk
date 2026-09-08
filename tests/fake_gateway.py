"""A stand-in euclid server, for tests that need one.

Small enough to read in one sitting, and deliberately strict about the parts the SDK has to get
right: it authenticates the way ``Core::HttpActionServer::Authenticate`` does - an RFC 9421
signature first, then a bearer token, then SigV4 - dispatches on ``x-euclid-action``, and answers
errors in the ``{"error": "..."}`` shape the real server uses.

It records every request it saw, so a test can assert on what actually went over the wire rather
than on what the client meant to send.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from euclid.auth import SignableRequest, SigningScheme

Handler = Callable[["RecordedRequest"], "tuple[int, Any]"]


@dataclass
class RecordedRequest:
    """One request as the server received it."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes
    target: str = ""
    action: str = ""
    #: How the request authenticated: "rfc9421", "sigv4", "bearer" or "" for none.
    auth: str = ""
    #: The user the request was authenticated as, once it was.
    subject: str = ""

    def json(self) -> Any:
        return json.loads(self.body) if self.body.strip() else {}


@dataclass
class FakeGateway:
    """A running fake server. Use it as a context manager; :attr:`base_url` is where it listens."""

    #: access key ID -> (secret, user id). A signature is accepted when the key is in here.
    access_keys: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: Bearer tokens that authenticate, mapped to the user they authenticate as.
    tokens: dict[str, str] = field(default_factory=dict)
    #: (target, action) -> handler. A missing one is a 400, as on the real server.
    handlers: dict[tuple[str, str], Handler] = field(default_factory=dict)
    #: Actions that need no authentication at all. ``login`` is the real one.
    public: set[tuple[str, str]] = field(default_factory=lambda: {("eam", "login")})
    #: Every request seen, in order.
    requests: list[RecordedRequest] = field(default_factory=list)

    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------------------------

    def start(self) -> "FakeGateway":
        gateway = self

        class RequestHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:  # keep the test output readable
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                headers = {name.lower(): value for name, value in self.headers.items()}
                recorded = RecordedRequest(self.command, self.path, headers, body,
                                           headers.get("x-euclid-target", ""),
                                           headers.get("x-euclid-action", ""))
                gateway.requests.append(recorded)
                status, payload = gateway._dispatch(recorded)
                self._respond(status, payload)

            def _respond(self, status: int, payload: Any) -> None:
                encoded = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> "FakeGateway":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    @property
    def base_url(self) -> str:
        assert self._server is not None, "the gateway is not running"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    # -- behaviour ---------------------------------------------------------------------------

    def on(self, target: str, action: str, handler: Handler) -> "FakeGateway":
        """Registers what to answer for one action."""
        self.handlers[(target, action)] = handler
        return self

    def answer(self, target: str, action: str, payload: Any, status: int = 200) -> "FakeGateway":
        """Registers a fixed answer for one action."""
        return self.on(target, action, lambda _request: (status, payload))

    def last(self) -> RecordedRequest:
        """The most recent request. Raises if there was none, which is itself a useful failure."""
        return self.requests[-1]

    # -- internals ---------------------------------------------------------------------------

    def _dispatch(self, request: RecordedRequest) -> tuple[int, Any]:
        if not request.action:
            return 400, {"error": "Missing x-euclid-action header"}

        if (request.target, request.action) not in self.public:
            denial = self._authenticate(request)
            if denial is not None:
                return denial

        handler = self.handlers.get((request.target, request.action))
        if handler is None:
            return 400, {"error": f"Unknown action {request.action}"}
        return handler(request)

    def _authenticate(self, request: RecordedRequest) -> tuple[int, Any] | None:
        """None when the request authenticates, otherwise the response to send instead."""
        signable = self._signable(request)
        scheme = SigningScheme.of(signable)

        if scheme is not None:
            key_id = scheme.verify(signable, lambda key: self._secret_for(key))
            if key_id is None:
                return 403, {"error": "Signature does not match"}
            request.auth = "rfc9421" if scheme is SigningScheme.RFC9421 else "sigv4"
            request.subject = self.access_keys[key_id][1]
            return None

        authorization = request.headers.get("authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization[len("Bearer "):]
            subject = self.tokens.get(token)
            if subject is None:
                return 401, {"error": "Bearer token expired"}
            request.auth = "bearer"
            request.subject = subject
            return None

        return 401, {"error": "Missing or invalid bearer token"}

    def _secret_for(self, access_key_id: str) -> str | None:
        entry = self.access_keys.get(access_key_id)
        return entry[0] if entry else None

    def _signable(self, request: RecordedRequest) -> SignableRequest:
        signable = SignableRequest(request.method, request.path)
        signable.headers_from(request.headers)
        signable.set_body(request.body)
        signable.set_scheme("http")
        return signable
