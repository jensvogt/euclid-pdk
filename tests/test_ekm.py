"""EKM, end to end against a fake euclid server.

The key and certificate actions are checked the way the other modules' are: what went on the wire,
and what came back off it. Encrypt and decrypt are checked against a stand-in that really transforms
the bytes and hands them back as bytes, because those two are the only actions in this module whose
request is not JSON - and a client that sent them as JSON, or decoded the answer as JSON, would pass
every test whose server only ever speaks JSON.
"""

from __future__ import annotations

import pytest

from euclid import AUTH_SIGNATURE, Euclid, EuclidServiceError
from euclid.modules import ekm as ekm_module
from fake_gateway import RecordedRequest
from test_eam import prepared

KEY = "ern:euclid:ekm:eu-central-1:000000000000:key/key-1"
MASK = 0x5A


class FakeKeys:
    """A key module that transforms bytes rather than pretending to.

    The transform is a XOR, which is not encryption - what is being tested is that the client sends
    the caller's bytes and returns the server's, unchanged in both directions.
    """

    def __init__(self) -> None:
        self.key_ids: list[str] = []

    def install(self, gateway):
        gateway.on("ekm", "encrypt", self.encrypt)
        gateway.on("ekm", "decrypt", self.decrypt)
        return self

    def encrypt(self, request: RecordedRequest) -> tuple[int, bytes]:
        self.key_ids.append(request.headers.get("x-euclid-key-id", ""))
        return 200, b"IV" + bytes(byte ^ MASK for byte in request.body)

    def decrypt(self, request: RecordedRequest) -> tuple[int, bytes]:
        self.key_ids.append(request.headers.get("x-euclid-key-id", ""))
        return 200, bytes(byte ^ MASK for byte in request.body[2:])


@pytest.fixture
def keys(gateway):
    """A gateway that answers a login, with a key module behind it."""
    prepared(gateway)
    return FakeKeys().install(gateway)


@pytest.fixture
def ekm(gateway, keys):
    """An EKM client on a logged-in session, closed with it."""
    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        yield session.ekm()


# -- keys ----------------------------------------------------------------------------------------


def test_creating_a_key_asks_for_aes_256_unless_told_otherwise(gateway, ekm):
    """euclid-jdk's no-argument createKey() mints 128 bits; this one mints what euclid itself
    creates when a bucket asks to be encrypted."""
    gateway.answer("ekm", "create-key", {"name": "key-1", "ern": KEY, "description": "exports",
                                         "algorithm": "AES", "length": 256, "status": "AVAILABLE"})

    created = ekm.create_key(description="exports")

    assert gateway.last().json() == {"algorithm": "AES", "length": 256, "description": "exports"}
    assert (created.name, created.length, created.status) == ("key-1", 256, "AVAILABLE")
    # The name is the ID the server minted, and the only handle to the key.
    assert created.ern == KEY


def test_listing_keys_never_carries_material(gateway, ekm):
    gateway.answer("ekm", "list-keys", {"total": 2, "keys": [
        {"name": "key-1", "ern": KEY, "description": "exports", "algorithm": "AES", "length": 256,
         "status": "AVAILABLE", "tags": {"team": "finance"}, "created": "2026-01-01"},
        {"name": "key-2", "status": "PENDING_DELETION", "deletionDate": "2026-09-15T00:00:00Z"},
    ]})

    listed = ekm.list_keys(prefix="key", page_size=25, sort_direction="desc")

    assert gateway.last().json() == {"prefix": "key", "pageSize": 25, "pageIndex": 0,
                                     "sortColumn": "name", "sortDirection": "desc"}
    assert listed.total == 2
    assert [key.name for key in listed.keys] == ["key-1", "key-2"]
    assert listed.keys[0].tags == {"team": "finance"} and listed.keys[0].length == 256
    # Only present on a key scheduled for deletion; empty on one that is not.
    assert listed.keys[1].deletion_date == "2026-09-15T00:00:00Z"
    assert listed.keys[0].deletion_date == ""
    assert not hasattr(listed.keys[0], "key_material")


def test_deleting_a_key_is_scheduled_rather_than_immediate(gateway, ekm):
    """It is the one action here that no other can undo, so the window is the chance to notice."""
    gateway.answer("ekm", "delete-key", {"name": "key-1", "ern": KEY, "status": "PENDING_DELETION",
                                         "deletionDate": "2026-09-15T00:00:00Z"})

    scheduled = ekm.delete_key("key-1")

    assert gateway.last().json() == {"keyId": "key-1", "pendingWindowInDays": 7}
    assert (scheduled.status, scheduled.deletion_date) == ("PENDING_DELETION", "2026-09-15T00:00:00Z")

    ekm.delete_key("key-1", pending_window_in_days=30)
    assert gateway.last().json()["pendingWindowInDays"] == 30


def test_revoking_and_describing_take_the_ern(gateway, ekm):
    """Where encrypt and delete take the key's ID - the server's own split, and the one thing about
    EKM worth remembering."""
    gateway.answer("ekm", "revoke-key", {"name": "key-1", "ern": KEY, "status": "REVOKED"})
    gateway.answer("ekm", "set-key-description", {"name": "key-1", "ern": KEY,
                                                  "description": "retired after the 2026 audit"})

    assert ekm.revoke_key(KEY).status == "REVOKED"
    assert gateway.last().json() == {"ern": KEY}

    described = ekm.set_key_description(KEY, "retired after the 2026 audit")
    assert gateway.last().json() == {"ern": KEY, "description": "retired after the 2026 audit"}
    assert described.description == "retired after the 2026 audit"


def test_key_tags(gateway, ekm):
    gateway.answer("ekm", "add-key-tag", {})
    gateway.answer("ekm", "delete-key-tag", {})

    ekm.add_key_tag(KEY, "team", "finance")
    assert gateway.last().json() == {"ern": KEY, "key": "team", "value": "finance"}

    ekm.delete_key_tag(KEY, "team")
    assert gateway.last().json() == {"ern": KEY, "key": "team"}


# -- using a key ------------------------------------------------------------------------------------


def test_encrypt_and_decrypt_round_trip_the_bytes(gateway, ekm, keys):
    plaintext = bytes(range(256))

    sealed = ekm.encrypt("key-1", plaintext)

    assert sealed.startswith(b"IV") and sealed != plaintext
    assert gateway.last().headers["x-euclid-key-id"] == "key-1"
    assert gateway.last().headers["content-type"] == "application/octet-stream"
    # The plaintext went over the wire as bytes, not as base64 inside a JSON field.
    assert gateway.last().body == plaintext

    assert ekm.decrypt("key-1", sealed) == plaintext
    assert keys.key_ids == ["key-1", "key-1"]


def test_the_byte_actions_present_the_token_and_the_rest_are_signed(gateway, ekm, keys):
    gateway.answer("ekm", "list-keys", {"keys": [], "total": 0})

    ekm.list_keys()
    assert gateway.last().auth == "sigv4"
    assert gateway.last().headers["x-euclid-target"] == "ekm"

    ekm.encrypt("key-1", b"secret")
    assert gateway.last().auth == "bearer"


def test_a_session_that_asked_for_signatures_signs_the_bytes_too(gateway, keys):
    with Euclid.for_server(gateway.base_url).login("jens", "secret", auth=AUTH_SIGNATURE) as session:
        assert session.ekm().encrypt("key-1", b"secret").startswith(b"IV")

    assert gateway.last().auth == "sigv4"
    assert gateway.last().subject == "jens"


def test_a_key_that_may_not_encrypt_says_so(gateway, ekm):
    """A revoked key, or one scheduled for deletion: the answer is a JSON error even though the
    request carried bytes, and the reason is the server's own."""
    gateway.answer("ekm", "encrypt", {"error": "Key 'key-1' is not available, status: REVOKED"},
                   status=403)

    with pytest.raises(EuclidServiceError) as raised:
        ekm.encrypt("key-1", b"secret")

    assert (raised.value.target, raised.value.action, raised.value.status) == ("ekm", "encrypt", 403)
    assert raised.value.reason.startswith("Key 'key-1' is not available")


# -- certificates ------------------------------------------------------------------------------------


CERTIFICATE = {"name": "gateway", "ern": "ern:ekm:certificate/gateway", "description": "the listener",
               "certificate": "-----BEGIN CERTIFICATE-----\nMII...\n-----END CERTIFICATE-----\n",
               "subject": "CN=euclid.example.com", "issuer": "CN=euclid.example.com",
               "serialNumber": "01", "fingerprint": "ab:cd", "generated": True,
               "subjectAltNames": ["euclid.example.com", "localhost"],
               "notBefore": "2026-01-01", "notAfter": "2028-04-05", "tags": {}}


def test_importing_a_certificate_sends_both_halves(gateway, ekm):
    """The server checks them against each other; a mismatch here beats a handshake that fails for
    every caller later."""
    gateway.answer("ekm", "import-certificate", {"certificate": CERTIFICATE})

    certificate = ekm.import_certificate("gateway", "PEM-CERT", "PEM-KEY", description="the listener")

    assert gateway.last().json() == {"name": "gateway", "description": "the listener",
                                     "certificate": "PEM-CERT", "privateKey": "PEM-KEY"}
    assert certificate.subject == "CN=euclid.example.com"
    assert certificate.subject_alt_names == ["euclid.example.com", "localhost"]
    # The private key went in and does not come back - there is no field for it.
    assert not hasattr(certificate, "private_key")


def test_generating_a_certificate_leaves_the_servers_defaults_alone(gateway, ekm):
    gateway.answer("ekm", "create-certificate", {"certificate": CERTIFICATE})

    generated = ekm.create_certificate("gateway")

    assert gateway.last().json() == {"name": "gateway", "description": "", "commonName": "",
                                     "subjectAltNames": []}
    # Nobody vouched for it, and the certificate says so.
    assert generated.generated

    ekm.create_certificate("gateway", common_name="euclid.example.com",
                           subject_alt_names=["localhost"], valid_days=90, key_bits=4096)
    assert gateway.last().json() == {"name": "gateway", "description": "",
                                     "commonName": "euclid.example.com",
                                     "subjectAltNames": ["localhost"], "validDays": 90,
                                     "keyBits": 4096}


def test_getting_listing_and_deleting_certificates(gateway, ekm):
    gateway.answer("ekm", "get-certificate", {"certificate": CERTIFICATE})
    gateway.answer("ekm", "list-certificates", {"total": 1, "certificates": [CERTIFICATE]})
    gateway.answer("ekm", "delete-certificate", {"name": "gateway", "ern": "ern:ekm:certificate/gateway"})

    assert ekm.get_certificate("gateway").fingerprint == "ab:cd"
    assert gateway.last().json() == {"name": "gateway"}

    listed = ekm.list_certificates(prefix="gate")
    assert (listed.total, [c.name for c in listed.certificates]) == (1, ["gateway"])

    deleted = ekm.delete_certificate("gateway")
    assert (deleted.name, deleted.ern) == ("gateway", "ern:ekm:certificate/gateway")


# -- everything else ------------------------------------------------------------------------------------


def test_ekm_follows_the_session_and_reaches_unwrapped_actions(gateway, keys):
    gateway.answer("eam", "change-namespace", {})
    gateway.answer("ekm", "list-keys", {"keys": [], "total": 0})
    gateway.answer("ekm", "some-future-action", {"ok": True})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        ekm = session.ekm()
        ekm.list_keys()
        assert "x-euclid-namespace" not in gateway.last().headers

        session.change_namespace("development")
        ekm.list_keys()
        assert gateway.last().headers["x-euclid-namespace"] == "development"

        assert ekm.call("some-future-action", {"x": 1}) == {"ok": True}
        assert session.ekm() is ekm


def test_the_module_declares_which_of_its_actions_carry_bytes(gateway, ekm):
    """The rule lives in one place for every module rather than once per client."""
    assert ekm_module.EuclidEkm.byte_actions == {"encrypt", "decrypt"}
