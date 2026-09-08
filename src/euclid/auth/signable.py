"""The view of a request that the signing schemes work on."""

from __future__ import annotations

__all__ = ["SignableRequest"]


class SignableRequest:
    """A minimal, transport-agnostic request: method, target, headers and body.

    Deliberately not :class:`http.client.HTTPConnection`'s idea of a request. A signature has to be
    computed over headers that are already set and then written back as more headers, which needs a
    mutable object that exists before anything is sent - and it has to be the same object the
    verifier sees on the other side, so that signing and verification are demonstrably the same
    canonicalisation rather than two implementations of one description.

    Header names are stored lowercase and values stripped, because that is what both canonical
    forms are defined over: HTTP header names are case-insensitive, and neither SigV4 nor RFC 9421
    treats the surrounding whitespace of a value as part of it.
    """

    __slots__ = ("_method", "_target", "_headers", "_body", "_scheme")

    def __init__(self, method: str, target: str) -> None:
        self._method = method
        self._target = target
        self._headers: dict[str, str] = {}
        self._body: bytes = b""
        # https, because that is how euclid is reached anywhere the distinction can matter. A
        # plain-HTTP caller sets it so that both ends agree on whether the port belongs in
        # "@authority".
        self._scheme = "https"

    # -- accessors ---------------------------------------------------------------------------

    @property
    def method(self) -> str:
        return self._method

    @property
    def target(self) -> str:
        return self._target

    @property
    def body(self) -> bytes:
        return self._body

    @property
    def scheme(self) -> str:
        return self._scheme

    @property
    def headers(self) -> dict[str, str]:
        """The headers, keyed by lowercase name, in the order they were first set."""
        return dict(self._headers)

    # -- builders ----------------------------------------------------------------------------

    def header(self, name: str, value: str) -> "SignableRequest":
        """Sets a header, and returns self so calls can be chained."""
        self._headers[name.lower()] = value.strip()
        return self

    def headers_from(self, headers: dict[str, str]) -> "SignableRequest":
        """Copies every header of a mapping onto this request."""
        for name, value in headers.items():
            self.header(name, value)
        return self

    def get(self, name: str) -> str:
        """A header's value, or the empty string when the request does not carry it."""
        return self._headers.get(name.lower(), "")

    def has(self, name: str) -> bool:
        """Whether the request carries a header at all, which is not the same as it being non-empty."""
        return name.lower() in self._headers

    def set_body(self, body: bytes | str | None) -> "SignableRequest":
        """Sets the body. Text is encoded as UTF-8, which is what both ends hash."""
        if body is None:
            self._body = b""
        elif isinstance(body, str):
            self._body = body.encode("utf-8")
        else:
            self._body = bytes(body)
        return self

    def set_scheme(self, scheme: str | None) -> "SignableRequest":
        """Sets the URI scheme. Ignored when empty, leaving the https default in place."""
        if scheme:
            self._scheme = scheme.lower()
        return self

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SignableRequest({self._method!r}, {self._target!r}, headers={self._headers!r})"
