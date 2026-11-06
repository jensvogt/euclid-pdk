"""The shapes ESM sends back.

Plain dataclasses, parsed defensively, exactly as :mod:`euclid.dto.eam` does and for the same
reasons. Field names are the server's (``dto/include/euclid/dto/esm``), converted to snake_case;
where the two differ, the JSON name is the one on the wire.

One rename is deliberate: the server's ``async`` flag - "the server answered before it had
finished" - is ``background`` here, because ``async`` is a Python keyword and a field nobody can
type is worse than a field spelled differently from the wire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import _json
from .com import Variant

__all__ = [
    "Bucket",
    "EsmObject",
    "Subscription",
    "BucketEvent",
    "ObjectAttribute",
    "CreateBucketResult",
    "ListBucketsResult",
    "ListObjectsResult",
    "RenameBucketResult",
    "SetBucketInternalResult", "SetBucketPriorityResult",
    "PurgeBucketResult",
    "DeleteObjectsResult",
    "AbortUploadResult",
    "TouchObjectResult",
    "EnableEncryptionResult",
    "DisableEncryptionResult",
    "SubscribeResult",
    "StoredObject",
    "CreateUploadResult",
    "CreateDownloadResult",
    "STANDARD", "ARCHIVED",
    "LifecycleRule",
    "BucketLifecycleResult",
    "BucketVersioningResult",
    "BucketStoppedResult",
    "ObjectLifecycleResult",
    "ObjectVersion",
    "ListObjectVersionsResult",
    "ObjectProblem",
    "VerifyObjectsResult",
]

#: What an object's ``lifecycle_state`` reads as: its bytes are where a read expects them...
STANDARD = "STANDARD"
#: ...or they have been moved to the archive and a read has to wait for a restore.
ARCHIVED = "ARCHIVED"


# -- resources -----------------------------------------------------------------------------------


@dataclass
class Bucket:
    """A bucket: a named container of objects, scoped to an account and a namespace."""

    name: str = ""
    ern: str = ""
    owner: str = ""
    size: int = 0
    objects: int = 0
    tags: dict[str, str] = field(default_factory=dict)
    #: Whether objects written from now on are encrypted at rest. Says nothing about the ones
    #: already stored - see :meth:`euclid.modules.esm.EuclidEsm.enable_encryption`.
    encrypted: bool = False
    encryption_key_ern: str = ""
    #: One of euclid's own buckets rather than somebody's. Left out of a listing unless asked for.
    internal: bool = False
    #: Priority the notifications this bucket sends are given, empty when it sets none. Nothing about
    #: the bucket depends on it - see
    #: :meth:`euclid.modules.esm.EuclidEsm.set_bucket_priority`.
    priority: str = ""
    #: Whether the bucket is refusing writes - see
    #: :meth:`euclid.modules.esm.EuclidEsm.stop_bucket`. What it already holds stays readable.
    stopped: bool = False
    #: Whether overwriting a key keeps the version it replaced - see
    #: :meth:`euclid.modules.esm.EuclidEsm.set_bucket_versioning`.
    versioning: bool = False
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Bucket":
        return Bucket(
            _json.text(document, "name"), _json.text(document, "ern"), _json.text(document, "owner"),
            _json.number(document, "size"), _json.number(document, "objects"),
            _json.string_map(document, "tags"), _json.flag(document, "encrypted"),
            _json.text(document, "encryptionKeyErn"), _json.flag(document, "internal"),
            _json.text(document, "priority"), _json.flag(document, "stopped"),
            _json.flag(document, "versioning"),
            _json.text(document, "created"), _json.text(document, "modified"))


@dataclass
class EsmObject:
    """One stored object, as a listing describes it.

    ``key`` is opaque: a bucket has directories only in the sense that keys share a prefix, which
    is why listing them is something a caller asks for rather than something that happens.
    """

    ern: str = ""
    bucket_ern: str = ""
    key: str = ""
    size: int = 0
    status: str = ""
    content_type: str = ""
    md5_sum: str = ""
    encrypted: bool = False
    #: Which version of the key this is, on a bucket that keeps them. Empty on one that does not.
    version_id: str = ""
    #: :data:`STANDARD` or :data:`ARCHIVED`. An archived object's bytes are not where a read
    #: expects them - see :meth:`euclid.modules.esm.EuclidEsm.restore_object`.
    lifecycle_state: str = ""
    attributes: dict[str, Variant] = field(default_factory=dict)
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "EsmObject":
        attributes = document.get("attributes") if isinstance(document, dict) else None
        return EsmObject(
            _json.text(document, "ern"), _json.text(document, "bucketErn"), _json.text(document, "key"),
            _json.number(document, "size"), _json.text(document, "status"),
            _json.text(document, "contentType"), _json.text(document, "md5Sum"),
            _json.flag(document, "encrypted"), _json.text(document, "versionId"),
            _json.text(document, "lifecycleState"), Variant.map_from_json(attributes),
            _json.text(document, "created"), _json.text(document, "modified"))


@dataclass
class Subscription:
    """A standing instruction to announce a bucket's object events to a queue or a topic."""

    ern: str = ""
    source_ern: str = ""
    type: str = ""
    target_ern: str = ""
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Subscription":
        return Subscription(
            _json.text(document, "ern"), _json.text(document, "sourceErn"), _json.text(document, "type"),
            _json.text(document, "targetErn"), _json.text(document, "created"),
            _json.text(document, "modified"))


@dataclass
class BucketEvent:
    """What a subscription delivers: one object event, as the body of an ordinary message."""

    event_type: str = ""
    bucket_ern: str = ""
    key: str = ""
    ern: str = ""
    size: int = 0
    content_type: str = ""
    md5_sum: str = ""

    @staticmethod
    def from_json(document: Any) -> "BucketEvent":
        return BucketEvent(
            _json.text(document, "eventType"), _json.text(document, "bucketErn"),
            _json.text(document, "key"), _json.text(document, "ern"), _json.number(document, "size"),
            _json.text(document, "contentType"), _json.text(document, "md5Sum"))


@dataclass
class ObjectAttribute:
    """One user-defined attribute of an object, as the server stored it."""

    ern: str = ""
    name: str = ""
    value: Variant = field(default_factory=lambda: Variant("string", ""))

    @staticmethod
    def from_json(document: Any) -> "ObjectAttribute":
        value = document.get("value") if isinstance(document, dict) else None
        return ObjectAttribute(_json.text(document, "ern"), _json.text(document, "name"),
                               Variant.from_json(value))


# -- what the actions answer with ------------------------------------------------------------------


@dataclass
class CreateBucketResult:
    """A newly created bucket: its name, and the ERN everything else names it by."""

    name: str = ""
    ern: str = ""

    @staticmethod
    def from_json(document: Any) -> "CreateBucketResult":
        return CreateBucketResult(_json.text(document, "name"), _json.text(document, "ern"))


@dataclass
class ListBucketsResult:
    """One page of buckets, and how many exist in total."""

    buckets: list[Bucket] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListBucketsResult":
        return ListBucketsResult([Bucket.from_json(b) for b in _json.documents(document, "buckets")],
                                 _json.number(document, "total"))


@dataclass
class ListObjectsResult:
    """One page of objects, and how many the bucket holds in total."""

    objects: list[EsmObject] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListObjectsResult":
        return ListObjectsResult([EsmObject.from_json(o) for o in _json.documents(document, "objects")],
                                 _json.number(document, "total"))


@dataclass
class RenameBucketResult:
    """A renamed bucket, and how much was repointed at it.

    The ERN is new as well as the name, so this is the one later calls have to use - nothing
    answers to the old one afterwards.
    """

    name: str = ""
    ern: str = ""
    objects: int = 0
    subscriptions: int = 0

    @staticmethod
    def from_json(document: Any) -> "RenameBucketResult":
        return RenameBucketResult(_json.text(document, "name"), _json.text(document, "ern"),
                                  _json.number(document, "objects"), _json.number(document, "subscriptions"))


@dataclass
class SetBucketInternalResult:
    """A bucket and the flag it now carries."""

    ern: str = ""
    name: str = ""
    internal: bool = False

    @staticmethod
    def from_json(document: Any) -> "SetBucketInternalResult":
        return SetBucketInternalResult(_json.text(document, "ern"), _json.text(document, "name"),
                                       _json.flag(document, "internal"))


@dataclass
class SetBucketPriorityResult:
    """A bucket and the priority its notifications now carry."""

    ern: str = ""
    name: str = ""
    #: As stored - upper case, or empty when it was cleared.
    priority: str = ""

    @staticmethod
    def from_json(document: Any) -> "SetBucketPriorityResult":
        return SetBucketPriorityResult(_json.text(document, "ern"), _json.text(document, "name"),
                                       _json.text(document, "priority"))


@dataclass
class DeleteBucketResult:
    """What a background bucket deletion took on.

    Only a deletion asked to run in the background answers with anything at all - a bucket deleted
    inline is simply gone by the time the call returns. So ``background`` is true whenever this is
    worth reading, ``count`` is how many objects the bucket held when the work was taken on, and
    ``job_id`` names the job doing it, which outlives the instance that started it.

    The bucket itself goes when the emptying finishes, so it stays listed - and still deletable -
    until it is genuinely gone.
    """

    ern: str = ""
    #: How many objects the bucket held when the deletion was taken on.
    count: int = 0
    #: The background job doing the work.
    job_id: str = ""
    #: Whether the server is still working through it.
    background: bool = False

    @staticmethod
    def from_json(document: Any) -> "DeleteBucketResult":
        return DeleteBucketResult(_json.text(document, "ern"), _json.number(document, "objects"),
                                  _json.text(document, "jobId"), _json.flag(document, "async"))


@dataclass
class PurgeBucketResult:
    """A purged bucket, and how many objects went.

    ``background`` says the server answered before doing any of it, in which case ``count`` is how
    many objects the bucket held when the purge was taken on rather than how many have gone, and
    ``job_id`` names the job doing it. That job outlives the instance that started it - one stopped
    by the autoscaler, or lost to a crash, leaves a job another instance picks up and carries on.
    """

    ern: str = ""
    count: int = 0
    #: The background job doing the work, empty unless ``background``.
    job_id: str = ""
    #: Whether the server is still working through the objects.
    background: bool = False

    @staticmethod
    def from_json(document: Any) -> "PurgeBucketResult":
        # "count" when the purge ran inline, "objects" when it was taken on: the same figure at two
        # points in the same work, and a caller reading ``count`` should get it either way rather
        # than a zero that only means the other field name was used.
        count = _json.number(document, "count") or _json.number(document, "objects")
        return PurgeBucketResult(_json.text(document, "ern"), count,
                                 _json.text(document, "jobId"), _json.flag(document, "async"))


@dataclass
class DeleteObjectsResult:
    """How many keys were asked for and how many objects went.

    The two differ when a key named nothing, which is not an error - it simply was not there to
    delete - so a caller that cares compares them. ``background`` says the server answered before
    it had finished, in which case ``objects`` is what it took on rather than what it removed.
    """

    ern: str = ""
    asked: int = 0
    objects: int = 0
    background: bool = False

    @staticmethod
    def from_json(document: Any) -> "DeleteObjectsResult":
        return DeleteObjectsResult(_json.text(document, "ern"), _json.number(document, "asked"),
                                   _json.number(document, "objects"), _json.flag(document, "async"))


@dataclass
class AbortUploadResult:
    """What an abandoned upload was, and what became of the object it was writing.

    ``object_removed`` is true for a first upload, whose row described bytes that never arrived,
    and false for a re-upload, where the row is the previous version - still published, still
    readable, and not this upload's to delete. Worth reading rather than assuming: "the upload is
    gone" and "the object is gone" are different outcomes, and a caller cleaning up after a failure
    needs to know which one they got.
    """

    upload_id: str = ""
    bucket_ern: str = ""
    key: str = ""
    parts: int = 0
    object_removed: bool = False

    @staticmethod
    def from_json(document: Any) -> "AbortUploadResult":
        return AbortUploadResult(_json.text(document, "uploadId"), _json.text(document, "bucketErn"),
                                 _json.text(document, "key"), _json.number(document, "parts"),
                                 _json.flag(document, "objectRemoved"))


@dataclass
class TouchObjectResult:
    """The bucket whose objects were re-announced, and how many of them there were."""

    ern: str = ""
    bucket_name: str = ""
    prefix: str = ""
    objects: int = 0
    background: bool = False

    @staticmethod
    def from_json(document: Any) -> "TouchObjectResult":
        return TouchObjectResult(_json.text(document, "ern"), _json.text(document, "bucketName"),
                                 _json.text(document, "prefix"), _json.number(document, "objects"),
                                 _json.flag(document, "async"))


@dataclass
class EnableEncryptionResult:
    """The key a bucket now encrypts under, and how many objects predate the change.

    Those objects are not re-encrypted and not touched: each one records the key it was written
    under and is read back through it. ``key_created`` says the key is new and belongs to EKM from
    that moment on - deleting it there is what makes this bucket's objects unrecoverable.
    """

    ern: str = ""
    name: str = ""
    key_ern: str = ""
    key_id: str = ""
    algorithm: str = ""
    key_created: bool = False
    existing_objects: int = 0

    @staticmethod
    def from_json(document: Any) -> "EnableEncryptionResult":
        return EnableEncryptionResult(
            _json.text(document, "ern"), _json.text(document, "name"), _json.text(document, "keyErn"),
            _json.text(document, "keyId"), _json.text(document, "algorithm"),
            _json.flag(document, "keyCreated"), _json.number(document, "existingObjects"))


@dataclass
class DisableEncryptionResult:
    """The key a bucket was encrypting under, and how many objects are still under it."""

    ern: str = ""
    name: str = ""
    previous_key_ern: str = ""
    previous_key_id: str = ""
    encrypted_objects: int = 0

    @staticmethod
    def from_json(document: Any) -> "DisableEncryptionResult":
        return DisableEncryptionResult(
            _json.text(document, "ern"), _json.text(document, "name"),
            _json.text(document, "previousKeyErn"), _json.text(document, "previousKeyId"),
            _json.number(document, "encryptedObjects"))


@dataclass
class SubscribeResult:
    """A new subscription. ``ern`` is the subscription's own - what ``unsubscribe`` takes."""

    ern: str = ""
    source_ern: str = ""
    type: str = ""
    target_ern: str = ""

    @staticmethod
    def from_json(document: Any) -> "SubscribeResult":
        return SubscribeResult(_json.text(document, "ern"), _json.text(document, "sourceErn"),
                               _json.text(document, "type"), _json.text(document, "targetErn"))


@dataclass
class StoredObject:
    """An object as it was stored, which is what both ways of writing one answer with.

    ``put-object`` and ``complete-upload`` return the same payload, so a caller that needs the
    object's ERN does not have to take the multipart path to get one.
    """

    ern: str = ""
    bucket_ern: str = ""
    key: str = ""
    size: int = 0
    status: str = ""
    content_type: str = ""
    md5_sum: str = ""

    @staticmethod
    def from_json(document: Any) -> "StoredObject":
        return StoredObject(
            _json.text(document, "ern"), _json.text(document, "bucketErn"), _json.text(document, "key"),
            _json.number(document, "size"), _json.text(document, "status"),
            _json.text(document, "contentType"), _json.text(document, "md5Sum"))


@dataclass
class CreateUploadResult:
    """An opened multipart upload: the ID every part of it carries."""

    upload_id: str = ""
    bucket_ern: str = ""
    key: str = ""

    @staticmethod
    def from_json(document: Any) -> "CreateUploadResult":
        return CreateUploadResult(_json.text(document, "uploadId"), _json.text(document, "bucketErn"),
                                  _json.text(document, "key"))


@dataclass
class CreateDownloadResult:
    """An opened multipart download, and how large the object turned out to be.

    The size is what says how many parts there are to ask for: unlike an upload, whose source the
    caller has already stat'd, a download's size is not known until the server is asked.
    """

    download_id: str = ""
    bucket_ern: str = ""
    key: str = ""
    ern: str = ""
    size: int = 0
    content_type: str = ""

    @staticmethod
    def from_json(document: Any) -> "CreateDownloadResult":
        return CreateDownloadResult(
            _json.text(document, "downloadId"), _json.text(document, "bucketErn"),
            _json.text(document, "key"), _json.text(document, "ern"), _json.number(document, "size"),
            _json.text(document, "contentType"))


# -- lifecycle, versioning and verification ------------------------------------------------------


@dataclass
class LifecycleRule:
    """One rule saying when a bucket's objects are archived, and for how long a restore lasts.

    ``prefix`` is what the rule applies to, and an empty one is the whole bucket. Where several
    rules match a key the longest prefix wins, so a general rule can be given an exception without
    rewriting it.
    """

    id: str = ""
    prefix: str = ""
    #: How old an object has to be before the sweep archives it. Zero archives nothing, which is
    #: how a rule is written for its ``restore_days`` alone.
    archive_after_days: int = 0
    #: How long a restore keeps an object readable before it goes back to the archive.
    restore_days: int = 0
    #: A rule that is off is kept and not applied, which is how one is suspended rather than lost.
    enabled: bool = True

    @staticmethod
    def from_json(document: Any) -> "LifecycleRule":
        return LifecycleRule(
            _json.text(document, "id"), _json.text(document, "prefix"),
            _json.number(document, "archiveAfterDays"), _json.number(document, "restoreDays"),
            _json.flag(document, "enabled", True))

    def to_json(self) -> dict[str, Any]:
        """This rule as the server reads it."""
        return {"id": self.id, "prefix": self.prefix,
                "archiveAfterDays": self.archive_after_days, "restoreDays": self.restore_days,
                "enabled": self.enabled}


@dataclass
class BucketLifecycleResult:
    """A bucket's lifecycle rules as they now stand. Empty after they are deleted."""

    ern: str = ""
    name: str = ""
    rules: list[LifecycleRule] = field(default_factory=list)

    @staticmethod
    def from_json(document: Any) -> "BucketLifecycleResult":
        return BucketLifecycleResult(
            _json.text(document, "ern"), _json.text(document, "name"),
            [LifecycleRule.from_json(r) for r in _json.documents(document, "rules")])


@dataclass
class BucketVersioningResult:
    """Whether a bucket keeps the version an overwrite replaced."""

    ern: str = ""
    name: str = ""
    versioning: bool = False

    @staticmethod
    def from_json(document: Any) -> "BucketVersioningResult":
        return BucketVersioningResult(_json.text(document, "ern"), _json.text(document, "name"),
                                      _json.flag(document, "versioning"))


@dataclass
class BucketStoppedResult:
    """Whether a bucket is refusing writes."""

    ern: str = ""
    name: str = ""
    stopped: bool = False

    @staticmethod
    def from_json(document: Any) -> "BucketStoppedResult":
        return BucketStoppedResult(_json.text(document, "ern"), _json.text(document, "name"),
                                   _json.flag(document, "stopped"))


@dataclass
class ObjectLifecycleResult:
    """Where one object's bytes are, and what the last archive or restore did to them.

    ``restored_until`` is the half worth reading after a restore: a restore is temporary, and the
    object goes back to the archive when it runs out.
    """

    ern: str = ""
    bucket_ern: str = ""
    key: str = ""
    #: :data:`STANDARD` or :data:`ARCHIVED`.
    lifecycle_state: str = ""
    archived_at: str = ""
    restored_at: str = ""
    #: When a restore expires. Empty on an object that is not restored.
    restored_until: str = ""
    #: Where the archived copy lives. Empty while the object is in standard storage.
    archive_file: str = ""

    @staticmethod
    def from_json(document: Any) -> "ObjectLifecycleResult":
        return ObjectLifecycleResult(
            _json.text(document, "ern"), _json.text(document, "bucketErn"),
            _json.text(document, "key"), _json.text(document, "lifecycleState"),
            _json.text(document, "archivedAt"), _json.text(document, "restoredAt"),
            _json.text(document, "restoredUntil"), _json.text(document, "archiveFile"))

    @property
    def is_archived(self) -> bool:
        """Whether a read would have to wait for a restore."""
        return self.lifecycle_state == ARCHIVED


@dataclass
class ObjectVersion:
    """One stored version of a key."""

    version_id: str = ""
    #: Whether this is the version a plain read of the key returns.
    latest: bool = False
    size: int = 0
    content_type: str = ""
    status: str = ""
    lifecycle_state: str = ""
    owner: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "ObjectVersion":
        return ObjectVersion(
            _json.text(document, "versionId"), _json.flag(document, "latest"),
            _json.number(document, "size"), _json.text(document, "contentType"),
            _json.text(document, "status"), _json.text(document, "lifecycleState"),
            _json.text(document, "owner"), _json.text(document, "modified"))


@dataclass
class ListObjectVersionsResult:
    """Every version stored under one key, newest first.

    ``versioning`` is the bucket's setting rather than this key's, and it is what explains a single
    version: a bucket that never kept them has one per key whatever its history.
    """

    bucket_ern: str = ""
    key: str = ""
    ern: str = ""
    versioning: bool = False
    versions: list[ObjectVersion] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListObjectVersionsResult":
        return ListObjectVersionsResult(
            _json.text(document, "bucketErn"), _json.text(document, "key"),
            _json.text(document, "ern"), _json.flag(document, "versioning"),
            [ObjectVersion.from_json(v) for v in _json.documents(document, "versions")],
            _json.number(document, "total"))


@dataclass
class ObjectProblem:
    """One object whose stored bytes do not match what the row says about them.

    ``problem`` is the server's own word for what is wrong - a file that is missing, or one whose
    size disagrees with the row. The sizes are reported beside each other rather than reduced to a
    verdict, because which of the two is right is not something the check can know.
    """

    key: str = ""
    ern: str = ""
    problem: str = ""
    #: What the object row says the size is.
    recorded_size: int = 0
    #: What the file on disk actually is.
    file_size: int = 0
    #: What the file should be, which differs from ``recorded_size`` for an encrypted object.
    expected_file_size: int = 0
    internal_name: str = ""
    internal_dir: str = ""
    created: str = ""
    #: Whatever more the server had to say. Sent only when there is something.
    detail: str = ""

    @staticmethod
    def from_json(document: Any) -> "ObjectProblem":
        return ObjectProblem(
            _json.text(document, "key"), _json.text(document, "ern"),
            _json.text(document, "problem"), _json.number(document, "recordedSize"),
            _json.number(document, "fileSize"), _json.number(document, "expectedFileSize"),
            _json.text(document, "internalName"), _json.text(document, "internalDir"),
            _json.text(document, "created"), _json.text(document, "detail"))


@dataclass
class VerifyObjectsResult:
    """What one pass of a bucket's integrity check looked at, and what it found.

    One page rather than the whole bucket: the check reads a file per object, so it is paged
    deliberately and ``next_after_key`` is where the next pass starts. Empty means the end.

    ``scanned`` is the rows read, ``checked`` the files actually opened, and ``skipped`` the ones
    passed over - an archived object has no file where one is expected, which is not a problem.
    """

    bucket_ern: str = ""
    after_key: str = ""
    scanned: int = 0
    checked: int = 0
    skipped: int = 0
    problems: list[ObjectProblem] = field(default_factory=list)
    #: Where to resume - pass it back as ``after_key``. Empty when the page was short, which is the
    #: end of the bucket.
    next_key: str = ""

    @staticmethod
    def from_json(document: Any) -> "VerifyObjectsResult":
        return VerifyObjectsResult(
            _json.text(document, "bucketErn"), _json.text(document, "afterKey"),
            _json.number(document, "scanned"), _json.number(document, "checked"),
            _json.number(document, "skipped"),
            [ObjectProblem.from_json(p) for p in _json.documents(document, "problems")],
            _json.text(document, "nextKey"))
