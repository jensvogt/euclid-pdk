"""RFC 9421 signature base, parameters, signing and verification.

The signature base is asserted literally rather than only round-tripped, because it is the wire
format euclid's ``Core::HttpSignature::BuildSignatureBase`` builds independently: a change here that
both sign() and verify() agreed on would still break every request to a real server. The digest
vector is RFC 9530's own.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import timedelta

from euclid.auth import SignableRequest, rfc9421

KEY_ID = "AKIDEXAMPLE"
SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"


def euclid_request(body: str = '{"prefix":""}') -> SignableRequest:
    request = SignableRequest("POST", "/")
    request.header("host", "euclid.example.com")
    request.header("x-euclid-target", "eam")
    request.header("x-euclid-action", "list-users")
    request.header("x-euclid-region", "eu-central-1")
    request.header("x-euclid-account-id", "863459426936")
    request.header("x-euclid-user-id", "alice")
    request.set_body(body)
    return request


def lookup(key_id: str) -> str | None:
    return SECRET if key_id == KEY_ID else None


def test_content_digest_matches_rfc_9530():
    """The empty-body digest, spelled out, because every signature carries it."""
    assert rfc9421.content_digest("") == "sha-256=:47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=:"
    assert rfc9421.content_digest(b"{}") == "sha-256=:RBNvo1WzZ4oRRq0W9+hknpT7T8If536DEMBg9hyq/4o=:"


def test_signature_base_is_built_line_by_line():
    request = euclid_request()
    request.header("content-digest", rfc9421.content_digest(request.body))
    params = '("@method" "@path" "@authority" "content-digest" "x-euclid-account-id" ' \
             '"x-euclid-action" "x-euclid-region" "x-euclid-target" "x-euclid-user-id");created=1;keyid="k"'

    base = rfc9421.signature_base(request, rfc9421.required_components(), params)

    assert base == "\n".join([
        '"@method": POST',
        '"@path": /',
        '"@authority": euclid.example.com',
        f'"content-digest": {rfc9421.content_digest(request.body)}',
        '"x-euclid-account-id": 863459426936',
        '"x-euclid-action": list-users',
        '"x-euclid-region": eu-central-1',
        '"x-euclid-target": eam',
        '"x-euclid-user-id": alice',
        f'"@signature-params": {params}',
    ])


def test_the_default_port_is_dropped_from_the_authority():
    """RFC 9421 §2.2.3. Signing "host:443" and sending "host" would fail on arrival otherwise."""
    request = euclid_request()
    request.header("host", "Euclid.Example.com:443")
    request.set_scheme("https")
    request.header("content-digest", rfc9421.content_digest(request.body))

    base = rfc9421.signature_base(request, ("@authority",), "()")
    assert base is not None and base.startswith('"@authority": euclid.example.com\n')

    # A non-default port stays, because it is part of the authority.
    request.header("host", "euclid.example.com:8443")
    base = rfc9421.signature_base(request, ("@authority",), "()")
    assert base is not None and base.startswith('"@authority": euclid.example.com:8443\n')


def test_sign_then_verify_round_trips():
    request = euclid_request()
    rfc9421.sign(request, KEY_ID, SECRET)

    assert rfc9421.verify(request, lookup) == KEY_ID
    assert request.get("signature-input").startswith("sig1=(")
    assert request.get("signature").startswith("sig1=:")
    assert request.get("content-digest") == rfc9421.content_digest(request.body)


def test_the_signature_is_an_hmac_of_the_base():
    """Not a round trip: the signature is recomputed from the base by hand and must match."""
    request = euclid_request()
    rfc9421.sign(request, KEY_ID, SECRET)

    params = request.get("signature-input").split("=", 1)[1]
    base = rfc9421.signature_base(request, rfc9421.required_components(), params)
    assert base is not None

    expected = hmac.new(SECRET.encode(), base.encode(), hashlib.sha256).digest()
    assert request.get("signature") == "sig1=:" + base64.b64encode(expected).decode() + ":"


def test_a_tampered_body_does_not_verify():
    request = euclid_request()
    rfc9421.sign(request, KEY_ID, SECRET)

    # The digest is left alone, as a middlebox rewriting only the body would leave it.
    request.set_body('{"prefix":"pwned"}')
    assert rfc9421.verify(request, lookup) is None


def test_a_tampered_body_with_a_matching_digest_does_not_verify_either():
    """Rewriting the digest to match is the obvious next attempt; the digest is itself signed."""
    request = euclid_request()
    rfc9421.sign(request, KEY_ID, SECRET)

    request.set_body('{"prefix":"pwned"}')
    request.header("content-digest", rfc9421.content_digest(request.body))
    assert rfc9421.verify(request, lookup) is None


def test_a_retargeted_request_does_not_verify():
    request = euclid_request()
    rfc9421.sign(request, KEY_ID, SECRET)

    request.header("x-euclid-action", "delete-user")
    assert rfc9421.verify(request, lookup) is None


def test_a_narrowed_covered_set_is_refused():
    """A signature may not declare that it covers less than euclid's fixed list."""
    request = euclid_request()
    rfc9421.sign(request, KEY_ID, SECRET)

    params = request.get("signature-input").split("=", 1)[1]
    narrowed = params.replace('("@method" "@path" "@authority" "content-digest" ', '("@method" ')
    signature = hmac.new(SECRET.encode(),
                         rfc9421.signature_base(request, ("@method",), narrowed).encode(),
                         hashlib.sha256).digest()
    request.header("Signature-Input", "sig1=" + narrowed)
    request.header("Signature", "sig1=:" + base64.b64encode(signature).decode() + ":")

    # Genuinely signed, just over less than euclid requires - which is exactly what must not pass.
    assert rfc9421.verify(request, lookup) is None


def test_a_reordered_covered_set_is_refused():
    """euclid's server compares the component list for equality, order included."""
    request = euclid_request()
    rfc9421.sign(request, KEY_ID, SECRET)

    params = request.get("signature-input").split("=", 1)[1]
    swapped = params.replace('"@method" "@path"', '"@path" "@method"')
    request.header("Signature-Input", "sig1=" + swapped)
    assert rfc9421.verify(request, lookup) is None


def test_an_unknown_key_does_not_verify():
    request = euclid_request()
    rfc9421.sign(request, "AKIDNOTFOUND", SECRET)
    assert rfc9421.verify(request, lookup) is None


def test_a_stale_signature_is_refused():
    request = euclid_request()
    rfc9421.sign(request, KEY_ID, SECRET)
    assert rfc9421.verify(request, lookup, max_skew=timedelta(seconds=-1)) is None


def test_an_expired_signature_is_refused():
    request = euclid_request()
    rfc9421.sign(request, KEY_ID, SECRET)

    params = request.get("signature-input").split("=", 1)[1]
    expired = params + f";expires={int(time.time()) - 60}"
    signature = hmac.new(SECRET.encode(),
                         rfc9421.signature_base(request, rfc9421.required_components(), expired).encode(),
                         hashlib.sha256).digest()
    request.header("Signature-Input", "sig1=" + expired)
    request.header("Signature", "sig1=:" + base64.b64encode(signature).decode() + ":")

    assert rfc9421.verify(request, lookup) is None


def test_signing_without_the_routing_headers_says_what_is_missing():
    """§2.1 forbids signing a field that is not there, so this fails loudly rather than silently."""
    request = SignableRequest("POST", "/")
    request.header("host", "euclid.example.com")
    request.set_body("{}")

    try:
        rfc9421.sign(request, KEY_ID, SECRET)
    except ValueError as error:
        assert "x-euclid-region" in str(error)
    else:  # pragma: no cover - the assertion below is the failure message
        raise AssertionError("signing a request missing covered components should have raised")


def test_euclid_server_style_parameters_are_accepted():
    """euclid's own C++ signer emits no nonce and no tag; requiring either would reject it."""
    request = euclid_request()
    request.header("content-digest", rfc9421.content_digest(request.body))
    params = ("(" + " ".join(f'"{c}"' for c in rfc9421.required_components()) + ")"
              + f';created={int(time.time())};keyid="{KEY_ID}";alg="hmac-sha256"')
    base = rfc9421.signature_base(request, rfc9421.required_components(), params)
    signature = hmac.new(SECRET.encode(), base.encode(), hashlib.sha256).digest()
    request.header("Signature-Input", "sig1=" + params)
    request.header("Signature", "sig1=:" + base64.b64encode(signature).decode() + ":")

    assert rfc9421.verify(request, lookup) == KEY_ID


def test_a_foreign_tag_is_refused():
    """A signature made for some other application must not be replayable at euclid."""
    request = euclid_request()
    request.header("content-digest", rfc9421.content_digest(request.body))
    params = ("(" + " ".join(f'"{c}"' for c in rfc9421.required_components()) + ")"
              + f';created={int(time.time())};keyid="{KEY_ID}";alg="hmac-sha256";tag="elsewhere"')
    base = rfc9421.signature_base(request, rfc9421.required_components(), params)
    signature = hmac.new(SECRET.encode(), base.encode(), hashlib.sha256).digest()
    request.header("Signature-Input", "sig1=" + params)
    request.header("Signature", "sig1=:" + base64.b64encode(signature).decode() + ":")

    assert rfc9421.verify(request, lookup) is None


def test_two_signatures_are_refused_rather_than_chosen_between():
    request = euclid_request()
    rfc9421.sign(request, KEY_ID, SECRET)

    request.header("Signature-Input", request.get("signature-input") + ', sig2=("@method");created=1;keyid="k"')
    assert rfc9421.verify(request, lookup) is None


def test_parse_signature_params_reads_a_full_parameter_set():
    params = ('("@method" "@path");created=1700000000;keyid="AKIA";alg="hmac-sha256";'
              'nonce="abc";tag="euclid"')
    parsed = rfc9421.parse_signature_params(params)

    assert parsed is not None
    assert parsed.components == ("@method", "@path")
    assert parsed.created == 1700000000
    assert parsed.key_id == "AKIA"
    assert parsed.algorithm == "hmac-sha256"
    assert parsed.nonce == "abc"
    assert parsed.tag == "euclid"
    # Kept verbatim, because that is what the signature was computed over.
    assert parsed.raw == params


def test_parse_signature_params_rejects_malformed_input():
    assert rfc9421.parse_signature_params("") is None
    assert rfc9421.parse_signature_params("@method") is None
    assert rfc9421.parse_signature_params('("@method"') is None
    assert rfc9421.parse_signature_params("();created=1") is None
    # No keyid: nothing says which secret to check against.
    assert rfc9421.parse_signature_params('("@method");created=1') is None
    # A component with parameters of its own is not implemented, so it is refused.
    assert rfc9421.parse_signature_params('("@method";req);created=1;keyid="k"') is None
