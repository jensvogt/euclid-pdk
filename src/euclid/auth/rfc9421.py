"""RFC 9421 HTTP Message Signatures, the scheme meant to take over from SigV4.

Same credentials, same threat model, standard wire format: the caller still presents an access key
ID and its secret, and the signature is still an HMAC-SHA256 over a canonical rendering of the
request, but what gets rendered and how it travels is the IETF's rather than AWS's. The signature
goes in ``Signature`` and ``Signature-Input`` instead of ``Authorization``, which is what lets the
two schemes coexist during a migration - SigV4 owns ``Authorization``, this owns its own headers,
and a server can accept either without guessing which one a request meant.

**Algorithm.** Only ``hmac-sha256``, because that is the algorithm that takes euclid's existing
access-key/secret pairs unchanged: the HMAC key is the secret's UTF-8 bytes directly (RFC 9421
§3.3.3), with none of SigV4's key-derivation chain to reproduce.

**Covered components.** RFC 9421 lets a signer choose what its signature covers and declare it in
``Signature-Input``. Taken literally that would let a request decide how little of itself to
authenticate, so it is not taken literally here: the set is fixed, :func:`verify` rejects a
signature covering anything else in any other order, and :func:`sign` refuses to sign a request
that cannot supply all of it. The list is a wire format shared with euclid's
``Core::HttpSignature::CoveredComponents()`` and the two only ever change together.

See RFC 9421 and RFC 9530 (Content-Digest).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from .signable import SignableRequest

__all__ = [
    "ALGORITHM",
    "TAG",
    "SignatureParams",
    "ParsedSignature",
    "required_components",
    "covered_components",
    "signature_header_names",
    "content_digest",
    "signature_base",
    "serialize_signature_params",
    "parse_signature",
    "parse_signature_params",
    "sign",
    "verify",
]

ALGORITHM = "hmac-sha256"
TAG = "euclid"
LABEL = "sig1"
DEFAULT_MAX_SKEW = timedelta(minutes=15)

# The derived components pin the request line and host, content-digest pins the body, and the
# x-euclid-* headers pin what the request asks for and on whose behalf - which for euclid lives in
# headers rather than in the URI.
#
# Two absences are deliberate. "@query" is not covered because euclid POSTs everything to "/" and
# carries its arguments in the body, so there is no query string to protect. "x-euclid-namespace"
# is not covered either, and that one is a real gap rather than a simplification: the namespace
# scopes what a request may touch and it currently travels unsigned. Closing it means adding the
# component to euclid's server and to this list in the same release.
_REQUIRED_COMPONENTS = (
    "@method",
    "@path",
    "@authority",
    "content-digest",
    "x-euclid-account-id",
    "x-euclid-action",
    "x-euclid-region",
    "x-euclid-target",
    "x-euclid-user-id",
)

SIGNATURE_HEADER_NAMES = ("Content-Digest", "Signature-Input", "Signature")


@dataclass(frozen=True)
class SignatureParams:
    """The parameters of one signature, as carried in ``Signature-Input``.

    ``raw`` is the parameter string exactly as received. It is kept because that is what the
    signature was computed over: re-serialising the parsed fields would not reliably reproduce
    another implementation's byte-for-byte choices, and a base rebuilt from a re-serialisation
    would fail to verify a perfectly good signature.
    """

    components: tuple[str, ...]
    created: int | None
    expires: int | None
    key_id: str
    algorithm: str | None
    nonce: str | None
    tag: str | None
    raw: str


@dataclass(frozen=True)
class ParsedSignature:
    """A signature and its parameters, paired by the dictionary label they share."""

    label: str
    params: SignatureParams
    signature: bytes


def required_components() -> tuple[str, ...]:
    """The components every euclid signature covers, in signing order."""
    return _REQUIRED_COMPONENTS


def covered_components(req: SignableRequest) -> tuple[str, ...]:
    """The components a signature over this request must cover.

    Takes the request because RFC 9421 allows a signer to cover a field only when the message
    carries one, and a future version of euclid may want that back. It does not vary today.
    """
    return _REQUIRED_COMPONENTS


def signature_header_names() -> tuple[str, ...]:
    """The headers :func:`sign` writes, in the case they should be sent in."""
    return SIGNATURE_HEADER_NAMES


def content_digest(body: bytes | str) -> str:
    """The RFC 9530 ``Content-Digest`` value binding a body to its signature."""
    raw = body.encode("utf-8") if isinstance(body, str) else body
    return "sha-256=:" + base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii") + ":"


def serialize_signature_params(components: tuple[str, ...] | list[str], created: int, key_id: str, nonce: str) -> str:
    """Serialises the ``Signature-Input`` value for a set of covered components, without a label."""
    inner = " ".join(_quote(component) for component in components)
    return (f"({inner});created={created};keyid={_quote(key_id)};alg={_quote(ALGORITHM)}"
            f";nonce={_quote(nonce)};tag={_quote(TAG)}")


def signature_base(req: SignableRequest, components: tuple[str, ...] | list[str], params: str) -> str | None:
    """Builds the §2.5 signature base: one line per component, then ``@signature-params``.

    The last line carries no trailing newline. Returns None if any covered component is absent from
    the request - §2.1 forbids signing over a field that is not there, and producing a base with a
    blank in its place would sign something the verifier will never reconstruct.
    """
    lines = []
    for component in components:
        value = _component_value(req, component)
        if value is None:
            return None
        lines.append(f'"{component}": {value}')
    lines.append(f'"@signature-params": {params}')
    return "\n".join(lines)


def sign(req: SignableRequest, access_key_id: str, secret_access_key: str) -> None:
    """Signs a request in place: sets Content-Digest, Signature-Input and Signature.

    Call it once every header the signature covers and the body are already set. Unlike SigV4 this
    leaves Authorization alone, so a request can carry a bearer token and a signature at once if a
    deployment wants both during a migration.

    Raises ValueError when a component that must be covered is missing, which for a client means it
    was configured without a region, an account id or a user id and so has nothing to sign with.
    """
    req.header("Content-Digest", content_digest(req.body))

    components = covered_components(req)
    params = serialize_signature_params(components, int(time.time()), access_key_id, _new_nonce())
    base = signature_base(req, components, params)
    if base is None:
        missing = [c for c in components if _component_value(req, c) is None]
        raise ValueError(f"request cannot be signed, it is missing {missing} - euclid signs a fixed set of "
                         f"components, so a client configured without a region, account id or user id has "
                         f"nothing to sign with")

    signature = hmac.new(secret_access_key.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).digest()
    req.header("Signature-Input", f"{LABEL}={params}")
    req.header("Signature", f"{LABEL}=:{base64.b64encode(signature).decode('ascii')}:")


def verify(req: SignableRequest, lookup_secret: Callable[[str], str | None],
           max_skew: timedelta = DEFAULT_MAX_SKEW) -> str | None:
    """Verifies an RFC 9421-signed request, returning the key ID it was signed with.

    Rebuilds the base from the request exactly as received - including the parameters verbatim -
    and compares the HMAC in constant time. Returns None on every failure without saying which,
    as :func:`euclid.auth.sigv4.verify` does.

    The checks mirror euclid's ``Core::HttpSignature::Verify``: the covered set must be exactly
    :func:`required_components`, in that order; ``alg`` must be ``hmac-sha256``; ``created`` must
    be within the skew window; and ``Content-Digest`` must match the body actually received, since
    that header is the only thing the signature covers the body through.

    ``tag`` is checked only when present, and euclid's own signer emits none. Requiring it would
    reject signatures made by the server itself.
    """
    parsed = parse_signature(req.get("signature-input"), req.get("signature"))
    if parsed is None:
        return None
    params = parsed.params

    # Only one algorithm exists here, so an unexpected alg is a rejection rather than a branch -
    # which is also what stops a signature being reinterpreted under a weaker one.
    if params.algorithm != ALGORITHM:
        return None
    if params.tag is not None and params.tag != TAG:
        return None

    # Fixed policy, not client-negotiated, and order-sensitive because the server is.
    if params.components != covered_components(req):
        return None

    now = int(time.time())
    if params.created is None or abs(now - params.created) > int(max_skew.total_seconds()):
        return None
    if params.expires is not None and params.expires < now:
        return None

    if not req.has("content-digest") or not hmac.compare_digest(req.get("content-digest"), content_digest(req.body)):
        return None

    secret = lookup_secret(params.key_id)
    if secret is None:
        return None

    base = signature_base(req, params.components, params.raw)
    if base is None:
        return None

    expected = hmac.new(secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, parsed.signature):
        return None

    return params.key_id


def parse_signature(signature_input_header: str, signature_header: str) -> ParsedSignature | None:
    """Parses a matching pair of ``Signature-Input`` and ``Signature`` values.

    Both must hold exactly one signature under the same label. RFC 9421 allows several - a proxy
    adding its own alongside the client's - but euclid has no use for more than one, and refusing
    to choose among them means never verifying the wrong one.
    """
    input_member = _single_dictionary_member(signature_input_header)
    signature_member = _single_dictionary_member(signature_header)
    if input_member is None or signature_member is None or input_member[0] != signature_member[0]:
        return None

    params = parse_signature_params(input_member[1])
    signature = _parse_byte_sequence(signature_member[1])
    if params is None or signature is None:
        return None
    return ParsedSignature(input_member[0], params, signature)


def parse_signature_params(value: str) -> SignatureParams | None:
    """Parses a ``Signature-Input`` value: an inner list of components, then the parameters."""
    if not value or value[0] != "(":
        return None

    components: list[str] = []
    pos = 1
    while True:
        if pos >= len(value):
            return None
        char = value[pos]
        if char == ")":
            pos += 1
            break
        if char == " ":
            pos += 1
            continue
        if char != '"':
            return None
        end = _end_of_quoted_string(value, pos)
        if end < 0:
            return None
        # A component carrying parameters of its own ("x";req and friends) means something this
        # module does not implement, so it is rejected rather than quietly read as plain.
        if end + 1 < len(value) and value[end + 1] not in (" ", ")"):
            return None
        components.append(_unquote(value[pos:end + 1]))
        pos = end + 1

    if not components:
        return None

    parameters = _parse_parameters(value[pos:])
    if parameters is None:
        return None

    key_id = parameters.get("keyid")
    if not isinstance(key_id, str):
        return None
    created = _timestamp(parameters, "created")
    expires = _timestamp(parameters, "expires")
    if created is False or expires is False:
        return None

    return SignatureParams(tuple(components), created, expires, key_id, _str_or_none(parameters.get("alg")),
                           _str_or_none(parameters.get("nonce")), _str_or_none(parameters.get("tag")), value)


# -- internals -------------------------------------------------------------------------------


def _component_value(req: SignableRequest, component: str) -> str | None:
    """One covered component's value for the base: §2.2's derived values, or the header."""
    if not component.startswith("@"):
        return req.get(component) if req.has(component) else None

    path, sep, query = req.target.partition("?")
    if component == "@method":
        return req.method.upper()
    if component == "@authority":
        return _authority(req)
    if component == "@scheme":
        return req.scheme
    if component == "@path":
        # §2.2.6: the path is "/" when empty.
        return path if path else "/"
    if component == "@query":
        # §2.2.7: the query keeps its leading "?", and is that character alone when there is none.
        return "?" + query if sep else "?"
    if component == "@request-target":
        return req.target
    if component == "@target-uri":
        authority = _authority(req)
        return None if authority is None else f"{req.scheme}://{authority}{req.target}"
    return None


def _authority(req: SignableRequest) -> str | None:
    """§2.2.3: the host header lowercased, minus the port when it is the scheme's default."""
    host = req.get("host").lower()
    if not host:
        return None
    default_port = ":443" if req.scheme == "https" else ":80" if req.scheme == "http" else None
    if default_port and host.endswith(default_port):
        host = host[:-len(default_port)]
    return host


def _single_dictionary_member(value: str) -> tuple[str, str] | None:
    """Splits a structured-field dictionary that must hold exactly one member into label and value.

    Respects quoting and nesting, so a comma inside a component name or a base64 signature cannot
    be mistaken for a member separator.
    """
    if not value:
        return None

    members = _split_top_level(value, ",")
    if len(members) != 1:
        return None

    member = members[0].strip()
    equals = _index_of_top_level(member, "=")
    if equals <= 0 or equals == len(member) - 1:
        return None
    return member[:equals].strip(), member[equals + 1:].strip()


def _split_top_level(value: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    in_quotes = False
    start = 0
    i = 0
    while i < len(value):
        char = value[i]
        if in_quotes:
            if char == "\\":
                i += 2
                continue
            if char == '"':
                in_quotes = False
        elif char == '"':
            in_quotes = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == delimiter and depth == 0:
            parts.append(value[start:i])
            start = i + 1
        i += 1
    parts.append(value[start:])
    return parts


def _index_of_top_level(value: str, char_to_find: str) -> int:
    in_quotes = False
    i = 0
    while i < len(value):
        char = value[i]
        if in_quotes:
            if char == "\\":
                i += 2
                continue
            if char == '"':
                in_quotes = False
        elif char == '"':
            in_quotes = True
        elif char == char_to_find:
            return i
        i += 1
    return -1


def _end_of_quoted_string(value: str, start: int) -> int:
    """The index of the closing quote of the string opening at ``start``, or -1 if unterminated."""
    i = start + 1
    while i < len(value):
        if value[i] == "\\":
            i += 2
            continue
        if value[i] == '"':
            return i
        i += 1
    return -1


def _parse_parameters(rest: str) -> dict[str, Any] | None:
    """Parses ``;name=value;name2=value2`` into a mapping, or None if it is not well-formed."""
    parameters: dict[str, Any] = {}
    pos = 0
    while pos < len(rest):
        if rest[pos] != ";":
            return None
        pos += 1
        while pos < len(rest) and rest[pos] == " ":
            pos += 1

        name_start = pos
        while pos < len(rest) and rest[pos] not in ("=", ";"):
            pos += 1
        name = rest[name_start:pos].strip()
        if not name:
            return None

        if pos >= len(rest) or rest[pos] == ";":
            # A bare parameter is a boolean true in structured fields.
            parameters[name] = True
            continue

        pos += 1  # the '='
        if pos < len(rest) and rest[pos] == '"':
            end = _end_of_quoted_string(rest, pos)
            if end < 0:
                return None
            parameters[name] = _unquote(rest[pos:end + 1])
            pos = end + 1
        elif pos < len(rest) and rest[pos] == ":":
            end = rest.find(":", pos + 1)
            if end < 0:
                return None
            parameters[name] = _decode_base64(rest[pos + 1:end])
            pos = end + 1
        else:
            value_start = pos
            while pos < len(rest) and rest[pos] != ";":
                pos += 1
            raw = rest[value_start:pos].strip()
            parameters[name] = _as_int_or_token(raw)
    return parameters


def _parse_byte_sequence(value: str) -> bytes | None:
    """Parses a structured-field byte sequence, ``:<base64>:``."""
    value = value.strip()
    if len(value) < 2 or value[0] != ":" or value[-1] != ":":
        return None
    return _decode_base64(value[1:-1])


def _decode_base64(value: str) -> bytes | None:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return None


def _as_int_or_token(raw: str) -> Any:
    try:
        return int(raw)
    except ValueError:
        return raw


def _timestamp(parameters: dict[str, Any], name: str) -> int | None | bool:
    """The parameter as a Unix timestamp; None when absent, False when present but not an integer."""
    if name not in parameters:
        return None
    value = parameters[name]
    return value if isinstance(value, int) and not isinstance(value, bool) else False


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unquote(quoted: str) -> str:
    return quoted[1:-1].replace('\\"', '"').replace("\\\\", "\\")


def _new_nonce() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")
