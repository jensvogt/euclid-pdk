"""Request signing and verification for euclid.

Two schemes, one credential pair. :mod:`euclid.auth.sigv4` is what euclid has always spoken;
:mod:`euclid.auth.rfc9421` is the standard scheme replacing it. Both are keyed by the access key
ID and secret a login hands back, and :class:`SigningScheme` is how a client picks between them.

Verification is here as well as signing, because it is the only way to demonstrate that the two
canonicalisations are the same one, and because a Python service fronting euclid needs to check the
signatures it receives with the same rules the server applies.
"""

from . import rfc9421, sigv4
from .scheme import SigningScheme
from .signable import SignableRequest

__all__ = ["SignableRequest", "SigningScheme", "sigv4", "rfc9421"]
