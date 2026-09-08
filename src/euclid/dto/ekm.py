"""The shapes EKM sends back.

Parsed the same defensive way as every other module's. Field names are the server's
(``dto/include/euclid/dto/ekm``), converted to snake_case.

Nothing here carries key material: a key's bytes never leave the server, which is the point of
having a key module rather than a table of keys. What a caller gets is a handle - the key's name to
encrypt with, its ERN to administer - and the description it was given, which is what answers,
months later, whether the key can be deleted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import _json

__all__ = [
    "Key",
    "Certificate",
    "CreateKeyResult",
    "ListKeysResult",
    "DeleteKeyResult",
    "RevokeKeyResult",
    "KeyDescriptionResult",
    "ListCertificatesResult",
    "DeleteCertificateResult",
]


# -- resources -----------------------------------------------------------------------------------


@dataclass
class Key:
    """An encryption key, described rather than disclosed.

    ``name`` is the ID the server minted, and what :meth:`~euclid.modules.ekm.EuclidEkm.encrypt`
    takes; ``ern`` is what the administrative actions take. The two are not interchangeable, which
    is the one thing about EKM worth remembering.
    """

    name: str = ""
    ern: str = ""
    description: str = ""
    algorithm: str = ""
    length: int = 0
    #: ``AVAILABLE``, ``REVOKED`` or ``PENDING_DELETION``. Only an available key encrypts; a revoked
    #: one and one scheduled for deletion still decrypt what they wrote.
    status: str = ""
    tags: dict[str, str] = field(default_factory=dict)
    #: When a key scheduled for deletion goes for good. Empty for a key that is not.
    deletion_date: str = ""
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Key":
        return Key(
            _json.text(document, "name"), _json.text(document, "ern"),
            _json.text(document, "description"), _json.text(document, "algorithm"),
            _json.number(document, "length"), _json.text(document, "status"),
            _json.string_map(document, "tags"), _json.text(document, "deletionDate"),
            _json.text(document, "created"), _json.text(document, "modified"))


@dataclass
class Certificate:
    """A stored X.509 certificate.

    ``certificate`` is the PEM, which is public and comes back. The private key does not: it goes in
    once and no action returns it, so there is no field for it here.
    """

    name: str = ""
    ern: str = ""
    description: str = ""
    #: The PEM-encoded certificate.
    certificate: str = ""
    subject: str = ""
    issuer: str = ""
    serial_number: str = ""
    fingerprint: str = ""
    subject_alt_names: list[str] = field(default_factory=list)
    #: Whether euclid generated and signed this itself, in which case nobody else has vouched for
    #: it and a client still has to be told to trust it.
    generated: bool = False
    not_before: str = ""
    not_after: str = ""
    tags: dict[str, str] = field(default_factory=dict)
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Certificate":
        return Certificate(
            _json.text(document, "name"), _json.text(document, "ern"),
            _json.text(document, "description"), _json.text(document, "certificate"),
            _json.text(document, "subject"), _json.text(document, "issuer"),
            _json.text(document, "serialNumber"), _json.text(document, "fingerprint"),
            _json.strings(document, "subjectAltNames"), _json.flag(document, "generated"),
            _json.text(document, "notBefore"), _json.text(document, "notAfter"),
            _json.string_map(document, "tags"), _json.text(document, "created"),
            _json.text(document, "modified"))


# -- what the actions answer with ------------------------------------------------------------------


@dataclass
class CreateKeyResult:
    """A newly created key. ``name`` is the ID the server minted - the only handle to it."""

    name: str = ""
    ern: str = ""
    description: str = ""
    algorithm: str = ""
    length: int = 0
    status: str = ""

    @staticmethod
    def from_json(document: Any) -> "CreateKeyResult":
        return CreateKeyResult(
            _json.text(document, "name"), _json.text(document, "ern"),
            _json.text(document, "description"), _json.text(document, "algorithm"),
            _json.number(document, "length"), _json.text(document, "status"))


@dataclass
class ListKeysResult:
    """One page of keys, and how many exist in total."""

    keys: list[Key] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListKeysResult":
        return ListKeysResult([Key.from_json(k) for k in _json.documents(document, "keys")],
                              _json.number(document, "total"))


@dataclass
class DeleteKeyResult:
    """A key scheduled for deletion, and the date it goes for good.

    Scheduled rather than deleted: everything the key encrypted becomes unreadable when that date
    passes, and the window is the only chance anybody gets to notice.
    """

    name: str = ""
    ern: str = ""
    deletion_date: str = ""
    status: str = ""

    @staticmethod
    def from_json(document: Any) -> "DeleteKeyResult":
        return DeleteKeyResult(_json.text(document, "name"), _json.text(document, "ern"),
                               _json.text(document, "deletionDate"), _json.text(document, "status"))


@dataclass
class RevokeKeyResult:
    """A revoked key: it encrypts nothing further, and still decrypts what it wrote."""

    name: str = ""
    ern: str = ""
    status: str = ""

    @staticmethod
    def from_json(document: Any) -> "RevokeKeyResult":
        return RevokeKeyResult(_json.text(document, "name"), _json.text(document, "ern"),
                               _json.text(document, "status"))


@dataclass
class KeyDescriptionResult:
    """A key as it now reads. Only the description changed - not the material, and not the life."""

    name: str = ""
    ern: str = ""
    description: str = ""

    @staticmethod
    def from_json(document: Any) -> "KeyDescriptionResult":
        return KeyDescriptionResult(_json.text(document, "name"), _json.text(document, "ern"),
                                    _json.text(document, "description"))


@dataclass
class ListCertificatesResult:
    """One page of certificates, and how many exist in total."""

    certificates: list[Certificate] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListCertificatesResult":
        return ListCertificatesResult(
            [Certificate.from_json(c) for c in _json.documents(document, "certificates")],
            _json.number(document, "total"))


@dataclass
class DeleteCertificateResult:
    """The name and ERN of a deleted certificate."""

    name: str = ""
    ern: str = ""

    @staticmethod
    def from_json(document: Any) -> "DeleteCertificateResult":
        return DeleteCertificateResult(_json.text(document, "name"), _json.text(document, "ern"))
