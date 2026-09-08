"""The connection to a euclid server.

euclid speaks one request shape: ``POST /`` with a JSON body, the module named in
``x-euclid-target`` and the operation in ``x-euclid-action``. Everything else - which module, which
action, who is asking - travels in headers, which is why the signing schemes cover the headers they
do. This client knows that shape and nothing about any particular module.

Built on :mod:`http.client` rather than a third-party HTTP library so that installing this SDK does
not drag a transitive dependency tree into an application that only wanted to call euclid.
"""

from __future__ import annotations

import http.client
import json
import os
import ssl
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

__all__ = ["EuclidHttpClient", "Response", "DEFAULT_CA_CERT_PATH"]

# Where euclid installs the certificate its gateway presents. Applied only when the file is there,
# so this is a no-op on a machine with no euclid deployment, and the same default euclid-cli uses.
DEFAULT_CA_CERT_PATH = "/etc/euclid/euclid_cert.crt"

DEFAULT_TIMEOUT = 10.0


@dataclass
class Response:
    """One HTTP response, with the body already read."""

    status: int
    reason: str
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""

    @property
    def ok(self) -> bool:
        """Whether the server answered 2xx."""
        return self.status // 100 == 2

    @property
    def text(self) -> str:
        """The body decoded as UTF-8, replacing anything that is not."""
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        """The body parsed as JSON. An empty body reads as an empty object."""
        return json.loads(self.content) if self.content.strip() else {}


class EuclidHttpClient:
    """Sends euclid's action requests, and keeps one connection per thread to send them over.

    Connections are cached per thread rather than shared behind a lock: a lock would serialise
    every call made by an application that uses one client from several threads, and a fresh
    connection per call would pay for a TLS handshake on every action. Per-thread reuse costs one
    idle socket per thread that has actually made a call, which is the cheap side of that trade.

    Two retries are built in, and both are deliberately narrow:

    * A connection closed while it sat idle - by the server's timeout, a proxy, a load balancer -
      is not visible until the next write, and surfaces as a failure with no response at all. That
      is retried once on a fresh connection. It is not quite the same as knowing the request was
      never processed, so this trades a possible repeat for the far commoner case of a socket that
      was already gone.
    * A 401 whose body says the credentials had expired is retried once with rebuilt headers, if
      :meth:`header_factory` was given one and it produces different headers. A wrong password or a
      missing permission is answered once, as before; and a token nobody has refreshed comes back
      identical, so there is nothing to retry with.
    """

    def __init__(self, ca_cert_path: str | None = None, timeout: float = DEFAULT_TIMEOUT,
                 verify: bool = True) -> None:
        """
        :param ca_cert_path: a PEM CA certificate to trust *alongside* the system trust store, for
            reaching a euclid server that presents its own certificate. None uses only the system
            store. Mirrors euclid-cli's ``--ca-cert``.
        :param timeout: how long to wait for a response, in seconds, unless a call overrides it.
        :param verify: whether to verify the server certificate at all. Turning it off is for a
            development server with a certificate nothing vouches for, and for nothing else.
        """
        self._ca_cert_path = ca_cert_path
        self._timeout = timeout
        self._verify = verify
        self._ssl_context = self._build_ssl_context(ca_cert_path, verify)
        self._local = threading.local()
        self._header_factory: Callable[[str, bytes], dict[str, str]] | None = None

    # -- configuration -----------------------------------------------------------------------

    def header_factory(self, factory: Callable[[str, bytes], dict[str, str]] | None) -> "EuclidHttpClient":
        """Registers how to rebuild the authentication headers for an ``(action, body)`` pair.

        Without one, a request whose credentials expired between the header being built and the
        server reading it fails like any other error - which for a long-lived application is a
        business operation lost to a token that a second attempt would have carried correctly.
        """
        self._header_factory = factory
        return self

    @property
    def ca_cert_path(self) -> str | None:
        return self._ca_cert_path

    # -- requests ----------------------------------------------------------------------------

    def post(self, url: str, body: bytes | str, target: str, action: str,
             headers: Mapping[str, str] | None = None, timeout: float | None = None) -> Response:
        """Sends one euclid action request.

        :param url: the full URL to post to, normally the server's base URL plus ``/``.
        :param body: the JSON request body.
        :param target: the module to route to, e.g. ``"eam"``.
        :param action: the operation, e.g. ``"list-users"``.
        :param headers: authentication and routing headers, as a module client builds them.
        :param timeout: overrides this client's timeout, for the actions a server answers slowly
            on purpose - a long poll is told how many seconds to hold the request open, and a
            caller unwilling to wait that long would abandon a request still being served.
        """
        payload = body.encode("utf-8") if isinstance(body, str) else body
        sent = dict(headers or {})
        sent["x-euclid-target"] = target
        sent["x-euclid-action"] = action

        response = self._send("POST", url, payload, sent, timeout)

        refreshed = self._refreshed_headers(response, action, payload, sent)
        if refreshed is None:
            return response
        return self._send("POST", url, payload, refreshed, timeout)

    def close(self) -> None:
        """Closes the connection this thread was holding, if any."""
        connections = getattr(self._local, "connections", None)
        if not connections:
            return
        for connection in connections.values():
            try:
                connection.close()
            except OSError:  # pragma: no cover - a socket already gone is already closed
                pass
        connections.clear()

    def __enter__(self) -> "EuclidHttpClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- internals ---------------------------------------------------------------------------

    def _refreshed_headers(self, response: Response, action: str, body: bytes,
                           headers: dict[str, str]) -> dict[str, str] | None:
        """The headers to retry with, or None if this response should not be retried.

        Kept narrow so it never turns one real rejection into two: only 401, only when the server
        said the credentials had expired, and only when the rebuilt headers actually differ.
        """
        if self._header_factory is None or response.status != 401:
            return None
        if "expired" not in response.text.lower():
            return None

        refreshed = dict(headers)
        refreshed.update(self._header_factory(action, body))
        return None if refreshed == headers else refreshed

    def _send(self, method: str, url: str, body: bytes, headers: Mapping[str, str],
              timeout: float | None) -> Response:
        parts = urlsplit(url)
        key = (parts.scheme, parts.hostname or "", parts.port)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query

        try:
            return self._exchange(key, method, path, body, headers, timeout)
        except _RETRYABLE as error:
            # No response at all came back, which is what a connection closed while it was idle
            # looks like from here. One more attempt, on a socket that is definitely new.
            self._drop(key)
            try:
                return self._exchange(key, method, path, body, headers, timeout)
            except _RETRYABLE:
                self._drop(key)
                raise error from None

    def _exchange(self, key: tuple[str, str, int | None], method: str, path: str, body: bytes,
                  headers: Mapping[str, str], timeout: float | None) -> Response:
        connection = self._connection(key, timeout)
        try:
            connection.request(method, path, body=body, headers=dict(headers))
            raw = connection.getresponse()
            content = raw.read()
        except Exception:
            self._drop(key)
            raise
        return Response(raw.status, raw.reason or "", {k.lower(): v for k, v in raw.getheaders()}, content)

    def _connection(self, key: tuple[str, str, int | None],
                    timeout: float | None) -> http.client.HTTPConnection:
        connections = getattr(self._local, "connections", None)
        if connections is None:
            connections = self._local.connections = {}

        effective = self._timeout if timeout is None else timeout
        connection = connections.get(key)
        if connection is not None:
            # The timeout is a property of the socket, so a call that wants longer than the one the
            # cached connection was opened with needs its own.
            if connection.timeout == effective:
                return connection
            self._drop(key)

        scheme, host, port = key
        if scheme == "https":
            connection = http.client.HTTPSConnection(host, port, timeout=effective, context=self._ssl_context)
        else:
            connection = http.client.HTTPConnection(host, port, timeout=effective)
        connections[key] = connection
        return connection

    def _drop(self, key: tuple[str, str, int | None]) -> None:
        connections = getattr(self._local, "connections", None)
        if not connections:
            return
        connection = connections.pop(key, None)
        if connection is not None:
            try:
                connection.close()
            except OSError:  # pragma: no cover
                pass

    @staticmethod
    def _build_ssl_context(ca_cert_path: str | None, verify: bool) -> ssl.SSLContext:
        context = ssl.create_default_context()
        if not verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            return context
        # Added to the system trust store rather than replacing it, so a certificate is accepted if
        # either root set vouches for it - the same union euclid-cli builds with
        # set_default_verify_paths() followed by load_verify_file().
        if ca_cert_path and os.path.isfile(ca_cert_path):
            context.load_verify_locations(cafile=ca_cert_path)
        return context


# Failures that mean nothing came back. A reset, a broken pipe, or an end of stream where the
# status line should have been is a dead cached socket, and a fresh one usually answers. A refused
# connection is not in here: that one means the server is not listening, and a second attempt a
# microsecond later would tell the caller the same thing more slowly.
_RETRYABLE = (
    http.client.RemoteDisconnected,
    http.client.BadStatusLine,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
)
