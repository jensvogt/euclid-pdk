"""ESM, end to end against a fake euclid server.

The JSON actions are checked the way EAM's are - what went on the wire, and what came back off it.
The transfers are checked against a storage stand-in that actually assembles what it is sent
(``fake_storage.py``), because the part of a multipart upload a unit test cannot see is exactly the
part that corrupts an object: a part number off by one, a part size the two ends disagree about, or
a reassembly that depends on the order the parts happened to arrive in.
"""

from __future__ import annotations

import json

import pytest

from euclid import AUTH_SIGNATURE, Euclid, EuclidServiceError, Variant, parse_bucket_event
from euclid.modules import esm as esm_module
from fake_storage import FakeStorage, bucket_ern
from test_eam import prepared

BUCKET = bucket_ern("reports")


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    """Retries without the waiting. The delays are what make a retry kind to a struggling server,
    and what would make this suite take a minute to tell us the same thing."""
    monkeypatch.setattr(esm_module, "PART_RETRY_BASE_DELAY", 0)


@pytest.fixture
def storage(gateway):
    """A gateway that answers a login, with a storage module behind it."""
    prepared(gateway)
    return FakeStorage().install(gateway)


@pytest.fixture
def esm(gateway, storage):
    """An ESM client on a logged-in session, closed with it."""
    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        yield session.esm()


# -- buckets -----------------------------------------------------------------------------------


def test_create_and_list_buckets(gateway, esm):
    gateway.answer("esm", "create-bucket", {"name": "reports", "ern": BUCKET})
    gateway.answer("esm", "list-buckets", {"total": 2, "buckets": [
        {"name": "reports", "ern": BUCKET, "owner": "jens", "size": 2048, "objects": 3,
         "tags": {"team": "finance"}, "encrypted": True, "encryptionKeyErn": "ern:ekm:key/1",
         "created": "2026-01-01"},
        {"name": "euclid-artifacts", "internal": True},
    ]})

    created = esm.create_bucket("reports")
    assert (created.name, created.ern) == ("reports", BUCKET)
    assert gateway.last().json() == {"name": "reports", "internal": False}

    listed = esm.list_buckets(prefix="rep", page_size=25, include_internal=True)
    assert gateway.last().json() == {"prefix": "rep", "pageSize": 25, "pageIndex": 0,
                                     "sortColumn": "name", "sortDirection": "asc",
                                     "includeInternal": True}
    assert listed.total == 2
    assert [bucket.name for bucket in listed.buckets] == ["reports", "euclid-artifacts"]
    assert listed.buckets[0].tags == {"team": "finance"}
    assert listed.buckets[0].encrypted and listed.buckets[0].encryption_key_ern == "ern:ekm:key/1"
    assert listed.buckets[1].internal
    # A field the server did not send reads as empty rather than raising.
    assert listed.buckets[1].owner == "" and listed.buckets[1].tags == {}


def test_the_single_value_answers_come_back_as_values(gateway, esm):
    """An ERN, a size and a count are one number or one string; wrapping them would only mean the
    caller unwrapping them again."""
    gateway.answer("esm", "get-bucket-ern", {"ern": BUCKET})
    gateway.answer("esm", "get-bucket-size", {"ern": BUCKET, "size": 4096})
    gateway.answer("esm", "get-object-count", {"ern": BUCKET, "count": 12})

    assert esm.get_bucket_ern("reports") == BUCKET
    assert gateway.last().json() == {"name": "reports"}
    assert esm.get_bucket_size(BUCKET) == 4096
    assert esm.get_object_count(BUCKET, prefix="2026/") == 12
    assert gateway.last().json() == {"ern": BUCKET, "prefix": "2026/"}


def test_tags_rename_purge_and_the_internal_flag(gateway, esm):
    gateway.answer("esm", "add-bucket-tag", {})
    gateway.answer("esm", "set-bucket-tag", {})
    gateway.answer("esm", "delete-bucket-tag", {})
    gateway.answer("esm", "rename-bucket", {"name": "archive", "ern": bucket_ern("archive"),
                                            "objects": 7, "subscriptions": 1})
    gateway.answer("esm", "set-bucket-internal", {"ern": BUCKET, "name": "reports", "internal": True})
    gateway.answer("esm", "purge-bucket", {"ern": BUCKET, "count": 7})
    gateway.answer("esm", "delete-bucket", {})

    esm.add_bucket_tag(BUCKET, "team", "finance")
    assert gateway.last().json() == {"ern": BUCKET, "key": "team", "value": "finance"}
    esm.set_bucket_tag(BUCKET, "team", "ops")
    esm.delete_bucket_tag(BUCKET, "team")
    assert gateway.last().json() == {"ern": BUCKET, "key": "team"}

    renamed = esm.rename_bucket(BUCKET, "archive")
    assert (renamed.ern, renamed.objects, renamed.subscriptions) == (bucket_ern("archive"), 7, 1)

    assert esm.set_bucket_internal(BUCKET).internal
    assert gateway.last().json() == {"ern": BUCKET, "internal": True}

    assert esm.purge_bucket(BUCKET, prefix="2025/").count == 7
    assert gateway.last().json() == {"ern": BUCKET, "prefix": "2025/"}
    esm.delete_bucket(BUCKET)


def test_encryption_reports_what_it_did_not_touch(gateway, esm):
    """Both calls say what happens to the next upload; neither rewrites what is already stored, and
    the counts are how a caller finds out."""
    gateway.answer("esm", "enable-encryption", {"ern": BUCKET, "name": "reports",
                                                "keyErn": "ern:ekm:key/7", "keyId": "reports-key",
                                                "algorithm": "AES-256", "keyCreated": True,
                                                "existingObjects": 42})
    gateway.answer("esm", "disable-encryption", {"ern": BUCKET, "name": "reports",
                                                 "previousKeyErn": "ern:ekm:key/7",
                                                 "previousKeyId": "reports-key",
                                                 "encryptedObjects": 43})

    enabled = esm.enable_encryption(BUCKET)
    assert gateway.last().json() == {"bucketErn": BUCKET, "keyId": ""}
    assert (enabled.key_id, enabled.algorithm, enabled.key_created) == ("reports-key", "AES-256", True)
    assert enabled.existing_objects == 42

    disabled = esm.disable_encryption(BUCKET)
    assert disabled.previous_key_id == "reports-key"
    assert disabled.encrypted_objects == 43


# -- objects -----------------------------------------------------------------------------------


def test_copy_move_and_rename_name_both_ends(gateway, esm):
    stored = {"ern": f"{BUCKET}/2026/q3.pdf", "bucketErn": BUCKET, "key": "2026/q3.pdf",
              "size": 12, "status": "STORED", "contentType": "application/pdf"}
    gateway.answer("esm", "copy-object", stored)
    gateway.answer("esm", "move-object", stored)
    gateway.answer("esm", "rename-object", stored)

    assert esm.copy_object(BUCKET, "q3.pdf", BUCKET, "2026/q3.pdf").key == "2026/q3.pdf"
    assert gateway.last().json() == {"sourceBucketErn": BUCKET, "sourceKey": "q3.pdf",
                                     "targetBucketErn": BUCKET, "targetKey": "2026/q3.pdf"}
    assert esm.move_object(BUCKET, "q3.pdf", BUCKET, "2026/q3.pdf").size == 12
    assert gateway.last().action == "move-object"
    assert esm.rename_object(BUCKET, "q3.pdf", "2026/q3.pdf").content_type == "application/pdf"
    assert gateway.last().json() == {"bucketErn": BUCKET, "key": "q3.pdf", "newKey": "2026/q3.pdf"}


def test_deleting_many_objects_reports_asked_and_deleted(gateway, esm):
    """They differ when a key named nothing, which is not an error - so a caller that cares compares
    the two."""
    gateway.answer("esm", "delete-objects", {"ern": BUCKET, "asked": 3, "objects": 2})

    result = esm.delete_objects(BUCKET, ["a", "b", "gone"])

    assert gateway.last().json() == {"ern": BUCKET, "keys": ["a", "b", "gone"], "async": False}
    assert (result.asked, result.objects, result.background) == (3, 2, False)


def test_touching_a_bucket_in_the_background(gateway, esm):
    gateway.answer("esm", "touch-object", {"ern": BUCKET, "bucketName": "reports", "prefix": "2026/",
                                           "objects": 900, "async": True}, status=202)

    result = esm.touch_object(BUCKET, prefix="2026/", background=True)

    assert gateway.last().json() == {"ern": BUCKET, "prefix": "2026/", "async": True}
    assert (result.objects, result.background, result.bucket_name) == (900, True, "reports")


def test_listing_objects_parses_their_attributes(gateway, esm, storage):
    esm.put_object(BUCKET, "2026/q3.pdf", b"a report")

    listed = esm.list_objects(BUCKET, prefix="2026/")

    assert gateway.last().json() == {"bucketErn": BUCKET, "prefix": "2026/", "pageSize": 10,
                                     "pageIndex": 0, "sortColumn": "name", "sortDirection": "asc",
                                     "includeDirectories": False}
    assert [obj.key for obj in listed.objects] == ["2026/q3.pdf"]
    assert listed.objects[0].size == len(b"a report")
    assert listed.objects[0].status == "STORED"


# -- object attributes -------------------------------------------------------------------------


def test_attributes_are_typed_on_the_way_out_and_back(gateway, esm):
    """A plain Python value is tagged with the type euclid stores it under, so a caller only reaches
    for Variant when it wants a tag other than the obvious one."""
    gateway.answer("esm", "set-object-attribute", {"ern": "ern:esm:object/1", "name": "retries",
                                                   "value": {"type": "long", "value": 3}})
    gateway.answer("esm", "list-object-attributes", {"ern": "ern:esm:object/1", "total": 3,
                                                     "attributes": {
                                                         "tenant": {"type": "string", "value": "acme"},
                                                         "retries": {"type": "long", "value": 3},
                                                         "thumbnail": {"type": "binary", "value": "YWJj"}}})
    gateway.answer("esm", "delete-object-attribute", {})

    attribute = esm.set_object_attribute("ern:esm:object/1", "retries", 3)
    assert gateway.last().json() == {"ern": "ern:esm:object/1", "name": "retries",
                                     "value": {"type": "long", "value": 3}}
    assert attribute.value == Variant("long", 3)

    attributes = esm.list_object_attributes("ern:esm:object/1")
    assert attributes["tenant"].value == "acme"
    assert attributes["retries"] == Variant("long", 3)
    # binary is base64 on the wire and bytes here, so a caller never sees the encoding.
    assert attributes["thumbnail"].value == b"abc"

    esm.delete_object_attribute("ern:esm:object/1", "retries")
    assert gateway.last().json() == {"ern": "ern:esm:object/1", "name": "retries"}


def test_an_explicit_variant_keeps_the_tag_it_was_given(gateway, esm):
    gateway.answer("esm", "add-object-attribute", {"ern": "ern:esm:object/1", "name": "count",
                                                   "value": {"type": "int", "value": 3}})

    esm.add_object_attribute("ern:esm:object/1", "count", Variant("int", 3))

    assert gateway.last().json()["value"] == {"type": "int", "value": 3}


# -- subscriptions -----------------------------------------------------------------------------


def test_subscribing_a_queue_to_a_buckets_events(gateway, esm):
    gateway.answer("esm", "subscribe", {"ern": "ern:esm:subscription/1", "sourceErn": BUCKET,
                                        "type": "SQS", "targetErn": "ern:eqs:queue/reports"})
    gateway.answer("esm", "list-subscriptions", {"total": 1, "subscriptions": [
        {"ern": "ern:esm:subscription/1", "sourceErn": BUCKET, "type": "SQS",
         "targetErn": "ern:eqs:queue/reports", "created": "2026-01-01"}]})
    gateway.answer("esm", "unsubscribe", {})

    created = esm.subscribe(BUCKET, esm_module.QUEUE, "ern:eqs:queue/reports",
                            event_types=[esm_module.OBJECT_CREATED], prefix="2026/")

    assert gateway.last().json() == {"sourceErn": BUCKET, "type": "SQS",
                                     "targetErn": "ern:eqs:queue/reports",
                                     "eventTypes": ["esm.object.created"], "prefix": "2026/",
                                     "directories": False}
    assert created.ern == "ern:esm:subscription/1"

    assert [s.target_ern for s in esm.list_subscriptions(BUCKET)] == ["ern:eqs:queue/reports"]

    # The subscription's own ERN, not the bucket's and not the target's.
    esm.unsubscribe(created.ern)
    assert gateway.last().json() == {"ern": "ern:esm:subscription/1"}


def test_a_delivered_notification_reads_as_a_bucket_event():
    body = json.dumps({"eventType": "esm.object.created", "bucketErn": BUCKET, "key": "q3.pdf",
                       "ern": f"{BUCKET}/q3.pdf", "size": 8, "contentType": "application/pdf",
                       "md5Sum": "d41d8"})

    event = parse_bucket_event(body)

    assert (event.event_type, event.key, event.size) == ("esm.object.created", "q3.pdf", 8)
    assert esm_module.EuclidEsm.parse_bucket_event(body) == event


# -- objects, in bytes ---------------------------------------------------------------------------


def test_put_and_get_an_object(gateway, esm, storage):
    stored = esm.put_object(BUCKET, "q3.pdf", b"a report")

    assert storage.objects[(BUCKET, "q3.pdf")] == b"a report"
    assert (stored.key, stored.size, stored.status) == ("q3.pdf", 8, "STORED")
    # The bytes go over the wire as bytes, not as base64 inside a JSON field.
    assert gateway.last().body == b"a report"
    assert gateway.last().headers["x-euclid-bucket-ern"] == BUCKET
    assert gateway.last().headers["content-type"] == "application/octet-stream"

    assert esm.get_object(BUCKET, "q3.pdf") == b"a report"


def test_put_object_carries_both_attribute_maps(gateway, esm, storage):
    esm.put_object(BUCKET, "q3.pdf", b"a report", attributes={"tenant": "acme", "retries": 3},
                   system_attributes={"priority": "LOW"})

    assert json.loads(storage.attribute_headers[(BUCKET, "q3.pdf")]) == {
        "tenant": {"type": "string", "value": "acme"}, "retries": {"type": "long", "value": 3}}
    # euclid's own envelope, never mixed into the caller's: this is what carries a producer's
    # priority across a hop through a bucket.
    assert json.loads(storage.system_attribute_headers[(BUCKET, "q3.pdf")]) == {
        "priority": {"type": "string", "value": "LOW"}}


def test_a_request_with_no_attributes_says_nothing_about_them(gateway, esm, storage):
    esm.put_object(BUCKET, "q3.pdf", b"a report")

    assert storage.attribute_headers[(BUCKET, "q3.pdf")] is None
    assert "x-euclid-attributes" not in gateway.last().headers


def test_an_object_too_large_for_one_response_says_so(gateway, esm, storage):
    esm.put_object(BUCKET, "big", b"x" * 100)

    with pytest.raises(EuclidServiceError) as raised:
        esm.get_object(BUCKET, "big", max_inline_size=50)

    assert raised.value.status == 413
    assert raised.value.reason == "Object too large for a single response"


def test_uploading_and_downloading_a_file_in_parts(gateway, esm, storage, tmp_path):
    source = tmp_path / "q3.pdf"
    content = bytes(range(256)) * 47  # 12032 bytes: three parts, the last one short
    source.write_bytes(content)

    stored = esm.upload_file(BUCKET, "2026/q3.pdf", source, part_size=5000, concurrency=3)

    assert storage.objects[(BUCKET, "2026/q3.pdf")] == content
    assert stored.size == len(content)
    parts = [r for r in gateway.requests if r.action == "upload-part"]
    assert sorted(int(r.headers["x-euclid-part-number"]) for r in parts) == [1, 2, 3]
    assert [len(r.body) for r in sorted(parts, key=lambda r: int(r.headers["x-euclid-part-number"]))] \
        == [5000, 5000, 2032]
    # The concurrency is declared up front so the gateway's autoscaler can ramp toward it.
    assert storage.declared_concurrency[-1] == "3"

    target = tmp_path / "downloaded" / "q3.pdf"
    written = esm.download_file(BUCKET, "2026/q3.pdf", target, part_size=5000, concurrency=3)

    assert written == len(content)
    assert target.read_bytes() == content
    assert len([r for r in gateway.requests if r.action == "download-part"]) == 3
    assert [r.action for r in gateway.requests][-1] == "complete-download"


def test_a_small_object_is_downloaded_in_one_request(gateway, esm, storage, tmp_path):
    """A download's size is not known before asking, so the single-request path is tried first and
    413 is what says it was not enough."""
    esm.put_object(BUCKET, "small", b"a report")

    written = esm.download_file(BUCKET, "small", tmp_path / "small", part_size=5000)

    assert written == 8
    assert (tmp_path / "small").read_bytes() == b"a report"
    assert [r.action for r in gateway.requests if r.target == "esm"] == ["put-object", "get-object"]


def test_an_empty_file_still_becomes_an_object(gateway, esm, storage, tmp_path):
    empty = tmp_path / "empty"
    empty.write_bytes(b"")

    esm.upload_file(BUCKET, "empty", empty)

    assert storage.objects[(BUCKET, "empty")] == b""
    assert len([r for r in gateway.requests if r.action == "upload-part"]) == 1


def test_upload_attributes_ride_on_the_call_that_completes_it(gateway, esm, storage, tmp_path):
    """Rather than being added afterwards: the row the server writes when it finishes assembling is
    built from what completing the upload was given, so an attribute added later is overwritten."""
    source = tmp_path / "q3.pdf"
    source.write_bytes(b"a report")

    esm.upload_file(BUCKET, "q3.pdf", source, attributes={"tenant": "acme"},
                    system_attributes={"priority": "HIGH"})

    complete = [r for r in gateway.requests if r.action == "complete-upload"][-1]
    assert json.loads(complete.headers["x-euclid-attributes"]) == {
        "tenant": {"type": "string", "value": "acme"}}
    assert json.loads(complete.headers["x-euclid-system-attributes"]) == {
        "priority": {"type": "string", "value": "HIGH"}}


# -- retries -------------------------------------------------------------------------------------


def test_a_part_that_fails_transiently_is_sent_again(gateway, esm, storage, tmp_path):
    source = tmp_path / "q3.pdf"
    source.write_bytes(b"a report")
    storage.fail_next("upload-part", times=2)

    esm.upload_file(BUCKET, "q3.pdf", source, concurrency=1)

    assert storage.objects[(BUCKET, "q3.pdf")] == b"a report"
    assert len([r for r in gateway.requests if r.action == "upload-part"]) == 3


def test_the_calls_bracketing_a_transfer_are_retried_too(gateway, esm, storage, tmp_path):
    """They run once per transfer rather than once per part, but giving up on a transient failure
    there discards the whole file."""
    source = tmp_path / "q3.pdf"
    source.write_bytes(b"a report")
    storage.fail_next("create-upload", times=1)
    storage.fail_next("complete-upload", times=1)

    esm.upload_file(BUCKET, "q3.pdf", source)

    assert storage.objects[(BUCKET, "q3.pdf")] == b"a report"
    assert len([r for r in gateway.requests if r.action == "create-upload"]) == 2


def test_a_part_that_keeps_failing_gives_up_with_the_servers_reason(gateway, esm, storage, tmp_path):
    source = tmp_path / "q3.pdf"
    source.write_bytes(b"a report")
    storage.fail_next("upload-part", times=99)

    with pytest.raises(EuclidServiceError) as raised:
        esm.upload_file(BUCKET, "q3.pdf", source)

    assert (raised.value.target, raised.value.action, raised.value.status) == ("esm", "upload-part", 500)
    assert raised.value.reason == "Storage temporarily unavailable"
    assert len([r for r in gateway.requests if r.action == "upload-part"]) == esm_module.MAX_PART_ATTEMPTS


def test_a_rejected_part_is_not_retried(gateway, esm, storage, tmp_path):
    """A 4xx means the request itself is wrong, and a repeat would be answered identically."""
    source = tmp_path / "q3.pdf"
    source.write_bytes(b"a report")
    storage.fail_next("upload-part", times=99, status=400)

    with pytest.raises(EuclidServiceError) as raised:
        esm.upload_file(BUCKET, "q3.pdf", source)

    assert raised.value.status == 400
    assert len([r for r in gateway.requests if r.action == "upload-part"]) == 1


def test_a_part_size_of_nothing_is_refused_before_anything_is_sent(gateway, esm, storage, tmp_path):
    """It would otherwise upload the file as one empty part and call it stored, which is a corrupt
    object rather than an error."""
    source = tmp_path / "q3.pdf"
    source.write_bytes(b"a report")

    with pytest.raises(ValueError, match="part_size"):
        esm.upload_file(BUCKET, "q3.pdf", source, part_size=0)
    with pytest.raises(ValueError, match="part_size"):
        esm.download_file(BUCKET, "q3.pdf", tmp_path / "out", part_size=0)

    assert [r for r in gateway.requests if r.target == "esm"] == []


def test_downloading_an_object_that_is_not_there_opens_no_session(gateway, esm, storage, tmp_path):
    """The single-request attempt is also what finds out the object does not exist, and a 404 is not
    the 413 that means "too large for this path"."""
    with pytest.raises(EuclidServiceError) as raised:
        esm.download_file(BUCKET, "missing", tmp_path / "missing")

    assert raised.value.status == 404
    assert [r.action for r in gateway.requests if r.target == "esm"] == ["get-object"]


# -- authentication ------------------------------------------------------------------------------


def test_json_actions_are_signed_and_the_byte_actions_present_the_token(gateway, esm, storage):
    """The one place the three SDKs deliberately agree to use the token: an object's bytes are
    written the same way by euclid-cli, euclid-jdk and this client."""
    esm.list_objects(BUCKET)
    assert gateway.last().auth == "sigv4"
    assert gateway.last().headers["x-euclid-target"] == "esm"

    esm.put_object(BUCKET, "q3.pdf", b"a report")
    assert gateway.last().auth == "bearer"


def test_a_session_that_asked_for_signatures_signs_the_bytes_too(gateway, storage):
    """It asked not to be handed a token quietly, and hashing raw bytes here is exact - so the
    signature covers the object as it went over the wire, which the gateway verifies."""
    with Euclid.for_server(gateway.base_url).login("jens", "secret", auth=AUTH_SIGNATURE) as session:
        session.esm().put_object(BUCKET, "q3.pdf", b"a report")

    assert gateway.last().auth == "sigv4"
    assert gateway.last().subject == "jens"


def test_esm_follows_the_session_it_came_from(gateway, storage):
    """A namespace changed between two calls scopes the second one: the client reads the session
    rather than a copy of it taken when it was created."""
    gateway.answer("eam", "change-namespace", {})

    with Euclid.for_server(gateway.base_url).login("jens", "secret") as session:
        esm = session.esm()
        esm.list_objects(BUCKET)
        assert "x-euclid-namespace" not in gateway.last().headers

        session.change_namespace("development")
        esm.list_objects(BUCKET)
        assert gateway.last().headers["x-euclid-namespace"] == "development"

        # The same client each time, so an application that calls esm() per operation pays for one
        # connection rather than one per call.
        assert session.esm() is esm


# -- escape hatch ---------------------------------------------------------------------------------


def test_call_reaches_an_esm_action_this_sdk_does_not_wrap(gateway, esm):
    gateway.answer("esm", "some-future-action", {"ok": True})

    assert esm.call("some-future-action", {"x": 1}) == {"ok": True}
    assert gateway.last().json() == {"x": 1}
    assert gateway.last().auth == "sigv4"


def test_metrics_come_back_unparsed(gateway, esm):
    gateway.answer("esm", "get-metrics", {"items": [{"name": "esm-objects", "value": 3}]})

    assert esm.metrics() == {"items": [{"name": "esm-objects", "value": 3}]}
