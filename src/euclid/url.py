"""Small URL helpers, kept in one place because a signature depends on getting them right.

``host`` is a signed header under both schemes, and ``@authority`` is a signed component under
RFC 9421. If what a client signs is not byte-for-byte what it sends, the signature fails on
arrival and the failure says nothing about why - so the value that goes into the signature and the
value that goes on the wire are computed here, once, by the same function.
"""

from __future__ import annotations

from urllib.parse import urlsplit

__all__ = ["strip_trailing_slash", "scheme_of", "host_header_of", "authority_of"]


def strip_trailing_slash(url: str) -> str:
    """``https://host/`` and ``https://host`` name the same server; this picks the second spelling."""
    return url[:-1] if url.endswith("/") else url


def scheme_of(url: str) -> str:
    """The URL's scheme, lowercase, defaulting to https."""
    return (urlsplit(url).scheme or "https").lower()


def host_header_of(url: str) -> str:
    """The ``Host`` header for this URL: the authority as written, minus any userinfo.

    Deliberately preserves a port that was written out even when it is the scheme's default, and
    omits one that was not. The header is sent explicitly rather than left to :mod:`http.client`,
    because that library omits a default port and a signature made over the other spelling would
    not verify.
    """
    return urlsplit(url).netloc.rpartition("@")[2].lower()


def authority_of(url: str) -> str:
    """The RFC 9421 ``@authority``: the host header, minus the port when it is the scheme's default.

    §2.2.3 requires the default port to be dropped, which is why this is not simply the Host header.
    """
    host = host_header_of(url)
    scheme = scheme_of(url)
    default_port = ":443" if scheme == "https" else ":80" if scheme == "http" else None
    if default_port and host.endswith(default_port):
        host = host[:-len(default_port)]
    return host
