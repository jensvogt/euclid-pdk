"""AWS Signature Version 4 request signing and verification.

Ported from euclid's ``Core::SigV4`` (C++, ``core/src/SigV4.cpp``). Unlike real AWS, the target
service and the action live in ``x-euclid-*`` headers rather than in the URI, so those headers are
always part of the signed set: a fixed list rather than the client-chosen ``SignedHeaders`` that
AWS allows, so that nothing in transit can narrow what a signature actually covers and still have
it verify.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping, NamedTuple

from .signable import SignableRequest

__all__ = [
    "ALGORITHM",
    "CredentialScope",
    "ParsedAuthorization",
    "signed_header_names",
    "parse_authorization_header",
    "canonicalize_query_string",
    "build_canonical_request",
    "build_string_to_sign",
    "derive_signing_key",
    "sign",
    "verify",
]

ALGORITHM = "AWS4-HMAC-SHA256"
SCOPE_TERMINATOR = "aws4_request"
DEFAULT_MAX_SKEW = timedelta(minutes=15)
AMZ_DATE_FORMAT = "%Y%m%dT%H%M%SZ"

# Always part of the signature, in the (already alphabetical) order the canonical request needs.
# host and the two x-amz-* headers give the usual SigV4 transport and payload integrity; the
# x-euclid-* headers are in there because that is where euclid carries the routing information AWS
# would put in the URI, and a signature that left them out would authenticate a request without
# authenticating what it asks for.
_SIGNED_HEADER_NAMES = (
    "host",
    "x-amz-content-sha256",
    "x-amz-date",
    "x-euclid-account-id",
    "x-euclid-action",
    "x-euclid-region",
    "x-euclid-target",
    "x-euclid-user-id",
)

# Signature headers, in the case they are sent in, for callers that copy them onto some other
# request object afterwards.
SIGNATURE_HEADER_NAMES = ("x-amz-date", "x-amz-content-sha256", "Authorization")

_UNRESERVED = re.compile(rb"[A-Za-z0-9\-_.~]")


class CredentialScope(NamedTuple):
    """The ``<key>/<date>/<region>/<service>/aws4_request`` scope out of an Authorization header."""

    access_key_id: str
    date_stamp: str
    region: str
    service: str


class ParsedAuthorization(NamedTuple):
    """An Authorization header split into its parts."""

    scope: CredentialScope
    signed_headers: str
    signature: str


def signed_header_names() -> tuple[str, ...]:
    """The headers a SigV4 signature always covers, lowercase, in signing order."""
    return _SIGNED_HEADER_NAMES


def parse_authorization_header(header_value: str | None) -> ParsedAuthorization | None:
    """Parses ``AWS4-HMAC-SHA256 Credential=..., SignedHeaders=..., Signature=...``.

    Returns None when the header is absent or not well-formed, which the caller treats exactly as
    it treats a signature that does not match - see :func:`verify`.
    """
    if not header_value or not header_value.startswith(ALGORITHM):
        return None

    credential = signed_headers = signature = ""
    for part in header_value[len(ALGORITHM):].strip().split(","):
        trimmed = part.strip()
        if trimmed.startswith("Credential="):
            credential = trimmed[len("Credential="):]
        elif trimmed.startswith("SignedHeaders="):
            signed_headers = trimmed[len("SignedHeaders="):]
        elif trimmed.startswith("Signature="):
            signature = trimmed[len("Signature="):]

    if not credential or not signed_headers or not signature:
        return None

    parts = credential.split("/")
    if len(parts) != 5 or parts[4] != SCOPE_TERMINATOR:
        return None

    return ParsedAuthorization(CredentialScope(parts[0], parts[1], parts[2], parts[3]), signed_headers, signature)


def canonicalize_query_string(raw_query: str) -> str:
    """Percent-decodes each name and value, re-encodes them per SigV4's rules, and sorts by name.

    euclid POSTs everything to ``/`` and carries its arguments in the body, so in practice this
    canonicalises the empty string. It is here because the canonical request has a slot for it and
    a signature computed with a different filling for that slot does not verify.
    """
    if not raw_query:
        return ""

    params: list[tuple[str, str]] = []
    for pair in raw_query.split("&"):
        if not pair:
            continue
        name, sep, value = pair.partition("=")
        params.append((_uri_encode(_percent_decode(name), True), _uri_encode(_percent_decode(value if sep else ""), True)))

    params.sort()
    return "&".join(f"{name}={value}" for name, value in params)


def build_canonical_request(method: str, canonical_uri: str, canonical_query: str,
                            headers: Mapping[str, str], header_names: tuple[str, ...] | list[str],
                            payload_hash_hex: str) -> str:
    """Builds the SigV4 canonical request string."""
    canonical_headers = "".join(f"{name}:{headers.get(name, '')}\n" for name in header_names)
    return "\n".join((method, canonical_uri, canonical_query, canonical_headers, ";".join(header_names), payload_hash_hex))


def build_string_to_sign(amz_date: str, credential_scope: str, canonical_request_hash_hex: str) -> str:
    """Builds the SigV4 string-to-sign."""
    return "\n".join((ALGORITHM, amz_date, credential_scope, canonical_request_hash_hex))


def derive_signing_key(secret_access_key: str, date_stamp: str, region: str, service: str) -> bytes:
    """Derives the signing key via the kDate -> kRegion -> kService -> kSigning HMAC chain."""
    k_date = _hmac(("AWS4" + secret_access_key).encode("utf-8"), date_stamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, SCOPE_TERMINATOR)


def sign(req: SignableRequest, access_key_id: str, secret_access_key: str, region: str, service: str) -> None:
    """Signs a request in place: sets x-amz-date, x-amz-content-sha256 and Authorization.

    Call it once every other header the signature covers - the ``x-euclid-*`` ones and host - and
    the body are already set on ``req``. Anything set afterwards is not covered, and on the server
    that is indistinguishable from an attacker having added it.
    """
    now = datetime.now(timezone.utc)
    amz_date = now.strftime(AMZ_DATE_FORMAT)
    date_stamp = amz_date[:8]

    req.header("x-amz-date", amz_date)
    req.header("x-amz-content-sha256", _sha256_hex(req.body))

    canonical_uri, canonical_query = _split_target(req.target)
    canonical_request = build_canonical_request(req.method, canonical_uri, canonical_query, req.headers,
                                                _SIGNED_HEADER_NAMES, req.get("x-amz-content-sha256"))

    credential_scope = f"{date_stamp}/{region}/{service}/{SCOPE_TERMINATOR}"
    string_to_sign = build_string_to_sign(amz_date, credential_scope, _sha256_hex(canonical_request.encode("utf-8")))

    signature = _hmac(derive_signing_key(secret_access_key, date_stamp, region, service), string_to_sign).hex()

    req.header("Authorization", f"{ALGORITHM} Credential={access_key_id}/{credential_scope}, "
                                f"SignedHeaders={';'.join(_SIGNED_HEADER_NAMES)}, Signature={signature}")


def verify(req: SignableRequest, lookup_secret: Callable[[str], str | None],
           max_skew: timedelta = DEFAULT_MAX_SKEW) -> str | None:
    """Verifies a SigV4-signed request, returning the access key ID it was signed with.

    Recomputes the signature from the request exactly as received and compares it in constant time,
    so any change to a signed header or to the body between signing and here makes this fail.

    Returns None on every failure - missing or malformed header, unknown key, stale timestamp,
    mismatched signature - without saying which, the way token verification collapses its failure
    modes into one rejection. A caller that could tell them apart would be an oracle.
    """
    parsed = parse_authorization_header(req.get("authorization"))
    if parsed is None:
        return None

    # Fixed policy rather than client-negotiated: a request does not get to choose how little of
    # itself it authenticates.
    if parsed.signed_headers != ";".join(_SIGNED_HEADER_NAMES):
        return None

    secret = lookup_secret(parsed.scope.access_key_id)
    if secret is None:
        return None

    headers = req.headers
    amz_date = headers.get("x-amz-date", "")
    if len(amz_date) < 8 or amz_date[:8] != parsed.scope.date_stamp:
        return None

    request_time = _parse_amz_date(amz_date)
    if request_time is None or abs(datetime.now(timezone.utc) - request_time) > max_skew:
        return None

    payload_hash = headers.get("x-amz-content-sha256")
    if payload_hash is None or payload_hash != _sha256_hex(req.body):
        return None

    canonical_uri, canonical_query = _split_target(req.target)
    canonical_request = build_canonical_request(req.method, canonical_uri, canonical_query, headers,
                                                _SIGNED_HEADER_NAMES, payload_hash)

    credential_scope = f"{parsed.scope.date_stamp}/{parsed.scope.region}/{parsed.scope.service}/{SCOPE_TERMINATOR}"
    string_to_sign = build_string_to_sign(amz_date, credential_scope, _sha256_hex(canonical_request.encode("utf-8")))
    expected = _hmac(derive_signing_key(secret, parsed.scope.date_stamp, parsed.scope.region, parsed.scope.service),
                     string_to_sign).hex()

    if not hmac.compare_digest(expected, parsed.signature):
        return None

    return parsed.scope.access_key_id


# -- internals -------------------------------------------------------------------------------


def _split_target(target: str) -> tuple[str, str]:
    path, sep, query = target.partition("?")
    return path, canonicalize_query_string(query) if sep else ""


def _parse_amz_date(amz_date: str) -> datetime | None:
    try:
        return datetime.strptime(amz_date, AMZ_DATE_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _uri_encode(value: bytes, encode_slash: bool) -> str:
    """SigV4's URI-encoding: everything but the unreserved set, uppercase hex, byte by byte."""
    out = []
    for byte in value:
        char = bytes((byte,))
        if _UNRESERVED.fullmatch(char) or (byte == 0x2F and not encode_slash):
            out.append(char.decode("ascii"))
        else:
            out.append(f"%{byte:02X}")
    return "".join(out)


def _percent_decode(value: str) -> bytes:
    out = bytearray()
    i = 0
    while i < len(value):
        if value[i] == "%" and i + 2 < len(value):
            try:
                out.append(int(value[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.extend(value[i].encode("utf-8"))
        i += 1
    return bytes(out)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, data: str) -> bytes:
    return hmac.new(key, data.encode("utf-8"), hashlib.sha256).digest()
