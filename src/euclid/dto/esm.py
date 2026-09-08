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
    "SetBucketInternalResult",
    "PurgeBucketResult",
    "DeleteObjectsResult",
    "TouchObjectResult",
    "EnableEncryptionResult",
    "DisableEncryptionResult",
    "SubscribeResult",
    "StoredObject",
    "CreateUploadResult",
    "CreateDownloadResult",
]


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
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Bucket":
        return Bucket(
            _json.text(document, "name"), _json.text(document, "ern"), _json.text(document, "owner"),
            _json.number(document, "size"), _json.number(document, "objects"),
            _json.string_map(document, "tags"), _json.flag(document, "encrypted"),
            _json.text(document, "encryptionKeyErn"), _json.flag(document, "internal"),
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
            _json.flag(document, "encrypted"), Variant.map_from_json(attributes),
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
class PurgeBucketResult:
    """A purged bucket, and how many objects went."""

    ern: str = ""
    count: int = 0

    @staticmethod
    def from_json(document: Any) -> "PurgeBucketResult":
        return PurgeBucketResult(_json.text(document, "ern"), _json.number(document, "count"))


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
