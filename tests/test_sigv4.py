"""SigV4 canonicalisation and signing.

The first three tests are AWS's own published test-suite cases (get-vanilla,
get-vanilla-query-order-key-case, post-x-www-form-urlencoded) with their published signatures. They
are here because they are the only checks in this file that are not self-referential: a round trip
of our own sign() against our own verify() would pass just as happily if both had the same bug,
whereas these fix the canonical form against an implementation nobody involved here wrote. euclid's
C++ and Java implementations are pinned by the same vectors, which is what makes the three
interoperate.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from euclid.auth import SignableRequest
from euclid.auth import sigv4

SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
ACCESS_KEY_ID = "AKIDEXAMPLE"
DATE_STAMP = "20150830"
AMZ_DATE = "20150830T123600Z"
REGION = "us-east-1"
SERVICE = "service"
CREDENTIAL_SCOPE = "20150830/us-east-1/service/aws4_request"


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def signature_for(canonical_request: str) -> str:
    string_to_sign = sigv4.build_string_to_sign(AMZ_DATE, CREDENTIAL_SCOPE, sha256_hex(canonical_request))
    key = sigv4.derive_signing_key(SECRET, DATE_STAMP, REGION, SERVICE)
    return hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def test_get_vanilla():
    """GET /, no query, host and x-amz-date signed, empty body."""
    headers = {"host": "example.amazonaws.com", "x-amz-date": AMZ_DATE}
    signed = ["host", "x-amz-date"]

    canonical = sigv4.build_canonical_request("GET", "/", "", headers, signed, sha256_hex(""))

    assert canonical == ("GET\n/\n\nhost:example.amazonaws.com\nx-amz-date:20150830T123600Z\n\n"
                         "host;x-amz-date\n"
                         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    assert sigv4.build_string_to_sign(AMZ_DATE, CREDENTIAL_SCOPE, sha256_hex(canonical)) == (
        "AWS4-HMAC-SHA256\n20150830T123600Z\n20150830/us-east-1/service/aws4_request\n"
        "bb579772317eb040ac9ed261061d46c1f17a8133879d6129b6e1c25292927e63")
    assert signature_for(canonical) == "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"


def test_get_vanilla_query_order_key_case():
    """Query parameters sort by key, whatever order they arrived in."""
    headers = {"host": "example.amazonaws.com", "x-amz-date": AMZ_DATE}
    signed = ["host", "x-amz-date"]

    canonical_query = sigv4.canonicalize_query_string("Param2=value2&Param1=value1")
    assert canonical_query == "Param1=value1&Param2=value2"

    canonical = sigv4.build_canonical_request("GET", "/", canonical_query, headers, signed, sha256_hex(""))

    assert canonical == ("GET\n/\nParam1=value1&Param2=value2\nhost:example.amazonaws.com\n"
                         "x-amz-date:20150830T123600Z\n\nhost;x-amz-date\n"
                         "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    assert signature_for(canonical) == "b97d918cfa904a5beff61c982a1b6f458b799221646efd99d3219ec94cdf2500"


def test_post_x_www_form_urlencoded():
    """A non-empty body, and a signed-header list that is not euclid's fixed one."""
    body = "Param1=value1"
    assert sha256_hex(body) == "9095672bbd1f56dfc5b65f3e153adc8731a4a654192329106275f4c7b24d0b6e"

    headers = {"content-length": "13", "content-type": "application/x-www-form-urlencoded",
               "host": "example.amazonaws.com", "x-amz-date": AMZ_DATE}
    signed = ["content-length", "content-type", "host", "x-amz-date"]

    canonical = sigv4.build_canonical_request("POST", "/", "", headers, signed, sha256_hex(body))

    assert canonical == ("POST\n/\n\ncontent-length:13\n"
                         "content-type:application/x-www-form-urlencoded\n"
                         "host:example.amazonaws.com\nx-amz-date:20150830T123600Z\n\n"
                         "content-length;content-type;host;x-amz-date\n"
                         "9095672bbd1f56dfc5b65f3e153adc8731a4a654192329106275f4c7b24d0b6e")
    assert signature_for(canonical) == "fec50118d90ecf934441dd37fb9a49bd7f5adb6450802ca3a0977623bbb7c27f"


def test_query_canonicalisation_normalises_encoding():
    """Each name and value is percent-decoded and re-encoded to SigV4's rules, then sorted."""
    # Lowercase escapes are rewritten to uppercase, so two spellings of one value agree.
    assert sigv4.canonicalize_query_string("a=b%2fc") == "a=b%2Fc"
    # A space is %20; "+" is a literal plus in a SigV4 query and encodes to %2B.
    assert sigv4.canonicalize_query_string("a=b c") == "a=b%20c"
    assert sigv4.canonicalize_query_string("a=b+c") == "a=b%2Bc"
    # Sorting is by name, then value.
    assert sigv4.canonicalize_query_string("b=2&a=1") == "a=1&b=2"
    assert sigv4.canonicalize_query_string("a=2&a=1") == "a=1&a=2"
    # A name with no "=" still gets one, with an empty value.
    assert sigv4.canonicalize_query_string("flag") == "flag="


def euclid_request() -> SignableRequest:
    request = SignableRequest("POST", "/")
    request.header("host", "example.amazonaws.com")
    request.header("x-euclid-target", "eam")
    request.header("x-euclid-action", "list-users")
    request.header("x-euclid-region", "eu-central-1")
    request.header("x-euclid-account-id", "863459426936")
    request.header("x-euclid-user-id", "alice")
    request.set_body('{"prefix":""}')
    return request


def lookup(key_id: str) -> str | None:
    return SECRET if key_id == ACCESS_KEY_ID else None


def test_sign_then_verify_round_trips():
    request = euclid_request()
    sigv4.sign(request, ACCESS_KEY_ID, SECRET, REGION, "eam")

    assert sigv4.verify(request, lookup) == ACCESS_KEY_ID
    assert request.get("authorization").startswith("AWS4-HMAC-SHA256 ")
    assert request.get("x-amz-content-sha256") == hashlib.sha256(request.body).hexdigest()


def test_a_tampered_body_does_not_verify():
    request = euclid_request()
    sigv4.sign(request, ACCESS_KEY_ID, SECRET, REGION, "eam")

    request.set_body('{"prefix":"pwned"}')
    assert sigv4.verify(request, lookup) is None


def test_a_retargeted_request_does_not_verify():
    """The routing headers are signed precisely so a middlebox cannot re-aim the request."""
    request = euclid_request()
    sigv4.sign(request, ACCESS_KEY_ID, SECRET, REGION, "eam")

    request.header("x-euclid-target", "esm")
    assert sigv4.verify(request, lookup) is None


def test_an_unknown_key_does_not_verify():
    request = euclid_request()
    sigv4.sign(request, "AKIDNOTFOUND", SECRET, REGION, "eam")
    assert sigv4.verify(request, lookup) is None


def test_a_narrowed_signed_header_list_is_refused():
    """A signature is not allowed to declare that it covers less than euclid's fixed set."""
    request = euclid_request()
    sigv4.sign(request, ACCESS_KEY_ID, SECRET, REGION, "eam")

    authorization = request.get("authorization")
    narrowed = authorization.replace("SignedHeaders=" + ";".join(sigv4.signed_header_names()),
                                     "SignedHeaders=host;x-amz-date")
    request.header("Authorization", narrowed)
    assert sigv4.verify(request, lookup) is None


def test_a_stale_signature_is_refused():
    from datetime import timedelta

    request = euclid_request()
    sigv4.sign(request, ACCESS_KEY_ID, SECRET, REGION, "eam")
    assert sigv4.verify(request, lookup, max_skew=timedelta(seconds=-1)) is None


@pytest.mark.parametrize("header", ["", "Bearer x", "AWS4-HMAC-SHA256 Credential=a/b, Signature=c"])
def test_malformed_authorization_headers_are_refused(header: str):
    assert sigv4.parse_authorization_header(header) is None
