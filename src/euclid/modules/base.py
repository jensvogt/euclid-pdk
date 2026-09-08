"""What every module client has in common.

euclid speaks one request shape, so most of a module client is the same client three times over:
post JSON to ``/``, name the module in ``x-euclid-target`` and the operation in
``x-euclid-action``, authenticate the way the session that created it authenticates, and turn a
non-2xx into a :class:`~euclid.exceptions.EuclidServiceError`. That part lives here; what is left in
:mod:`euclid.modules.esm`, :mod:`euclid.modules.eqs` and :mod:`euclid.modules.ens` is the operations
themselves.

Subclassing this is also how an application reaches a module this SDK has not wrapped: give the
subclass a ``target`` and call :meth:`~ModuleClient.call`.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..exceptions import EuclidServiceError
from ..http.client import EuclidHttpClient, Response
from .eam import AUTH_SIGNATURE, EuclidSession

__all__ = ["ModuleClient"]


class ModuleClient:
    """One euclid module, on the credentials of the session that created it.

    Holds the session rather than a copy of what it knew at the time, so the client follows it: a
    :meth:`~euclid.EuclidSession.change_namespace` between two calls scopes the second one, and a
    token the session refreshes is the token the next request carries.
    """

    #: The module this client talks to - what travels in ``x-euclid-target``.
    target: str = ""

    #: The actions of this module that carry raw bytes rather than JSON, and so authenticate with
    #: the session's bearer token rather than a signature - see :meth:`_auth_headers`. ESM's four
    #: transfer actions and EKM's encrypt/decrypt are the ones there are.
    byte_actions: frozenset[str] = frozenset()

    def __init__(self, session: EuclidSession, *, client: EuclidHttpClient | None = None,
                 headers: Mapping[str, str] | None = None) -> None:
        """
        :param session: the logged-in session whose credentials, namespace and connection settings
            this client uses.
        :param client: a connection to share rather than open. Only for a second view of the same
            module - see :meth:`euclid.modules.eqs.EuclidEqs.as_internal` - where two clients that
            differ by a header have no reason to differ by a socket.
        :param headers: headers every request of this client carries on top of the session's own.
        """
        if not self.target:
            raise TypeError(f"{type(self).__name__} must set a target, e.g. target = 'eqs'")
        self._session = session
        self._headers = dict(headers or {})
        self._client = session.new_client(self._auth_headers) if client is None else client

    @property
    def session(self) -> EuclidSession:
        """The session this client authenticates as."""
        return self._session

    def close(self) -> None:
        """Releases the connection this client was holding. Closing the session calls it."""
        self._client.close()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"{type(self).__name__}(user_id={self._session.user_id!r}, "
                f"namespace={self._session.namespace!r})")

    # -- transport ---------------------------------------------------------------------------

    def call(self, action: str, payload: Mapping[str, Any] | None = None,
             timeout: float | None = None) -> dict[str, Any]:
        """Sends any action of this module, for one this SDK does not wrap yet.

        Public on purpose: a server that gains an action should be reachable without waiting for a
        release here.
        """
        return self._call(action, payload, timeout)

    def _call(self, action: str, payload: Mapping[str, Any] | None = None,
              timeout: float | None = None, headers: Mapping[str, str] | None = None) -> dict[str, Any]:
        """One action, sent and read: the answer as a dictionary, or an exception."""
        return self._result(action, self._post(action, payload, headers, timeout))

    def _post(self, action: str, payload: Mapping[str, Any] | None = None,
              headers: Mapping[str, str] | None = None, timeout: float | None = None) -> Response:
        """One action, sent. The raw response, for a caller that reads a status this does not."""
        body = json.dumps(dict(payload) if payload else {}).encode("utf-8")
        sent = self.routing_headers()
        if headers:
            sent.update(headers)
        sent.update(self._auth_headers(action, body))
        return self._client.post(self._session.base_url + "/", body, self.target, action, sent, timeout)

    def routing_headers(self) -> dict[str, str]:
        """Who is asking, what they are scoped to, and whatever this client adds to that."""
        headers = self._session.routing_headers()
        headers.update(self._headers)
        return headers

    def _post_bytes(self, action: str, data: bytes, headers: Mapping[str, str] | None = None,
                    timeout: float | None = None) -> Response:
        """One of the actions whose body is bytes rather than JSON, described by its headers instead.

        The content type is the only routing header that changes, and neither signing scheme covers
        it - both sign a fixed list of headers, which is what lets this differ without the signature
        having to know.
        """
        sent = self.routing_headers()
        sent["Content-Type"] = "application/octet-stream"
        if headers:
            sent.update(headers)
        sent.update(self._auth_headers(action, data))
        return self._client.post(self._session.base_url + "/", data, self.target, action, sent, timeout)

    def _auth_headers(self, action: str, body: bytes) -> dict[str, str]:
        """How this request authenticates - and what it is rebuilt with when the server says the
        credentials had expired.

        A :attr:`byte_actions` action presents the session's bearer token rather than a signature,
        which is what euclid-cli and euclid-jdk do for the same actions, so all three clients write
        an object - or encrypt a block - the same way. A session that asked for
        :data:`~euclid.AUTH_SIGNATURE` signs them anyway: it asked not to be handed a token
        silently, and a signature over raw bytes is exact here in a way it is not in every language.
        """
        if action in self.byte_actions and self._session.auth != AUTH_SIGNATURE:
            return self._session.bearer_headers()
        return self._session.auth_headers(self.target, action, body)

    def _result(self, action: str, response: Response) -> dict[str, Any]:
        """The response body as a dictionary, or the server's own reason for refusing."""
        if not response.ok:
            raise EuclidServiceError(self.target, action, response.status, response.text)
        result = response.json()
        return result if isinstance(result, dict) else {"result": result}

    # -- reading answers ---------------------------------------------------------------------

    def _text(self, action: str, payload: Mapping[str, Any] | None, field: str) -> str:
        """An action whose answer is one string - an ERN, mostly."""
        value = self._call(action, payload).get(field)
        return value if isinstance(value, str) else ""

    def _number(self, action: str, payload: Mapping[str, Any] | None, field: str) -> int:
        """An action whose answer is one number - a size, a count."""
        value = self._call(action, payload).get(field)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
