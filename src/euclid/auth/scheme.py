"""Which request-signing scheme a client uses when it authenticates with an access key."""

from __future__ import annotations

from enum import Enum
from typing import Callable

from . import rfc9421, sigv4
from .signable import SignableRequest

__all__ = ["SigningScheme"]


class SigningScheme(Enum):
    """SigV4 or RFC 9421.

    Two schemes exist because euclid is moving from one to the other, not because both are wanted
    in the end. :attr:`SIGV4` is what euclid has always spoken and stays the default, so no
    existing caller changes behaviour; :attr:`RFC9421` is the standard scheme meant to replace it.
    Choosing one is a per-client decision, so a deployment can move one service at a time and roll
    back by changing a line rather than a release.

    Neither is consulted when a client authenticates with a bearer token: with no access key there
    is nothing to sign with, and the token goes in Authorization as before.

    The two do not collide on the wire - SigV4 puts its signature in Authorization, RFC 9421 in
    Signature/Signature-Input - so a server can accept both at once and tell which a request used.
    :meth:`of` is that test.
    """

    SIGV4 = "sigv4"
    RFC9421 = "rfc9421"

    def sign(self, req: SignableRequest, access_key_id: str, secret_access_key: str,
             region: str, service: str) -> None:
        """Signs a request in place.

        ``region`` and ``service`` are ignored by :attr:`RFC9421`: SigV4 needs them to derive its
        signing key, whereas RFC 9421 signs the ``x-euclid-region`` and ``x-euclid-target`` headers
        that carry the same facts, and binding them twice would add a way for the two copies to
        disagree.
        """
        if self is SigningScheme.SIGV4:
            sigv4.sign(req, access_key_id, secret_access_key, region, service)
        else:
            rfc9421.sign(req, access_key_id, secret_access_key)

    def verify(self, req: SignableRequest, lookup_secret: Callable[[str], str | None]) -> str | None:
        """Verifies a request signed with this scheme, returning the access key ID or None."""
        if self is SigningScheme.SIGV4:
            return sigv4.verify(req, lookup_secret)
        return rfc9421.verify(req, lookup_secret)

    def signature_header_names(self) -> tuple[str, ...]:
        """The headers :meth:`sign` writes, for callers that copy them onto another request."""
        if self is SigningScheme.SIGV4:
            return sigv4.SIGNATURE_HEADER_NAMES
        return rfc9421.SIGNATURE_HEADER_NAMES

    @staticmethod
    def of(req: SignableRequest) -> "SigningScheme | None":
        """Which scheme, if either, a received request presents a signature for.

        A routing decision, not a verification: it says which :meth:`verify` to call and nothing
        about whether that call will succeed. None means the request presents no signature at all -
        a bearer-token request, or an unsigned one.
        """
        if req.get("signature-input"):
            return SigningScheme.RFC9421
        if req.get("authorization").startswith(sigv4.ALGORITHM):
            return SigningScheme.SIGV4
        return None
