"""EKM - euclid's key management module: encryption keys, and the certificates a deployment serves.

One object, :class:`EuclidEkm`, built from a session that has already logged in::

    ekm = Euclid.for_server(url).login("jens", "secret").ekm()
    key = ekm.create_key(description="customer exports")

    sealed = ekm.encrypt(key.name, b"account 4711")
    assert ekm.decrypt(key.name, sealed) == b"account 4711"

Key material never leaves the server: :meth:`~EuclidEkm.encrypt` and :meth:`~EuclidEkm.decrypt` send
the bytes to the key rather than fetching the key to the bytes. That is what makes a key deletable
as a unit - and what makes deleting one final, since nothing anywhere else has a copy.

A key is named two ways, and they are not interchangeable. ``name`` is the ID the server minted and
is what encrypts and decrypts; the ERN is what revokes, describes and tags. Both are on every
:class:`~euclid.dto.ekm.Key` a listing returns.

``encrypt`` and ``decrypt`` carry raw bytes rather than JSON, and authenticate with the session's
bearer token for the same reason ESM's transfer actions do - see
:class:`euclid.modules.base.ModuleClient`.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..dto.ekm import (Certificate, CreateKeyResult, DeleteCertificateResult, DeleteKeyResult,
                       KeyDescriptionResult, ListCertificatesResult, ListKeysResult, RevokeKeyResult)
from ..exceptions import EuclidServiceError
from .base import ModuleClient

__all__ = ["EuclidEkm", "TARGET", "AES", "DEFAULT_KEY_LENGTH", "DEFAULT_PENDING_WINDOW_DAYS"]

TARGET = "ekm"

#: The only algorithm the server generates so far; anything else is refused with HTTP 400.
AES = "AES"

#: The key length this SDK asks for when the caller does not say. 128 is the other one the server
#: accepts, and what euclid-jdk's no-argument ``createKey()`` mints; 256 is what euclid itself
#: creates when a bucket asks to be encrypted, which is the better default to inherit.
DEFAULT_KEY_LENGTH = 256

#: How long a key scheduled for deletion stays alive by default, in days. The server's own default
#: when the field is left out, restated here because it is the one number in this module that
#: decides whether a mistake can be caught.
DEFAULT_PENDING_WINDOW_DAYS = 7

#: The actions that carry raw bytes rather than JSON.
BYTE_ACTIONS = frozenset({"encrypt", "decrypt"})


class EuclidEkm(ModuleClient):
    """EKM's operations, on the credentials of the session that created it.

    Built by :meth:`euclid.EuclidSession.ekm` rather than directly, so that it shares that
    session's identity, namespace and connection settings - and follows them as they change.
    """

    target = TARGET
    byte_actions = BYTE_ACTIONS

    # -- keys --------------------------------------------------------------------------------

    def create_key(self, algorithm: str = AES, length: int = DEFAULT_KEY_LENGTH,
                   description: str = "") -> CreateKeyResult:
        """Creates a key, and returns the ID the server minted for it.

        The description is worth supplying. A key is identified by that generated ID, which says
        nothing about what the key protects, and a key outlives the reason it was made - so months
        later this is the only thing that answers whether it can be deleted, and deleting one is not
        a mistake that can be undone.

        :param algorithm: ``"AES"``; the server generates nothing else so far.
        :param length: 128 or 256 bits.
        :param description: what the key is for. Free text, never interpreted.
        """
        return CreateKeyResult.from_json(self._call("create-key", {
            "algorithm": algorithm, "length": length, "description": description}))

    def list_keys(self, prefix: str = "", page_size: int = 10, page_index: int = 0,
                  sort_column: str = "name", sort_direction: str = "asc") -> ListKeysResult:
        """One page of keys, and how many exist in total. Never their material."""
        return ListKeysResult.from_json(self._call("list-keys", {
            "prefix": prefix, "pageSize": page_size, "pageIndex": page_index,
            "sortColumn": sort_column, "sortDirection": sort_direction}))

    def delete_key(self, key_id: str,
                   pending_window_in_days: int = DEFAULT_PENDING_WINDOW_DAYS) -> DeleteKeyResult:
        """Schedules a key for deletion, and returns the date it goes for good.

        Scheduled rather than immediate, because this is the one action here that cannot be undone
        by any other: everything the key encrypted - a bucket's objects, a secret's value - becomes
        unreadable when the date passes, and the window is the only chance anybody gets to notice.
        A key inside its window still decrypts.

        Takes the key's ID rather than its ERN, as :meth:`encrypt` does.
        """
        return DeleteKeyResult.from_json(self._call("delete-key", {
            "keyId": key_id, "pendingWindowInDays": pending_window_in_days}))

    def revoke_key(self, ern: str) -> RevokeKeyResult:
        """Stops a key encrypting anything further, without touching what it already wrote.

        The difference from :meth:`delete_key` is that nothing becomes unreadable: a revoked key
        still decrypts, so this is what to reach for when a key should no longer be used but the
        data under it is still wanted.

        Takes the key's ERN rather than its ID.
        """
        return RevokeKeyResult.from_json(self._call("revoke-key", {"ern": ern}))

    def set_key_description(self, ern: str, description: str) -> KeyDescriptionResult:
        """Changes what a key says it is for.

        Only the description changes: the material, algorithm, length, status and any scheduled
        deletion are untouched, so describing a key neither prolongs nor shortens its life. An empty
        string clears the description rather than leaving it alone - otherwise there would be no way
        to remove one.

        Takes the key's ERN rather than its ID.
        """
        return KeyDescriptionResult.from_json(self._call("set-key-description", {
            "ern": ern, "description": description}))

    def add_key_tag(self, ern: str, key: str, value: str) -> None:
        """Tags a key. The tag is upserted, so one already there has its value replaced - EKM has no
        separate set-key-tag action to distinguish the two."""
        self._call("add-key-tag", {"ern": ern, "key": key, "value": value})

    def delete_key_tag(self, ern: str, key: str) -> None:
        """Removes a tag from a key."""
        self._call("delete-key-tag", {"ern": ern, "key": key})

    # -- using a key -------------------------------------------------------------------------

    def encrypt(self, key_id: str, plaintext: bytes) -> bytes:
        """Encrypts bytes with a key the server holds, and returns ``IV || ciphertext || tag``.

        Those are the exact bytes :meth:`decrypt` takes back; nothing here needs to be unpacked or
        re-assembled. Only a key whose status is ``AVAILABLE`` encrypts - a revoked one, or one
        scheduled for deletion, is refused with HTTP 403.

        Takes the key's ID - the ``name`` :meth:`create_key` returned - rather than its ERN.
        """
        return self._transform("encrypt", key_id, plaintext)

    def decrypt(self, key_id: str, ciphertext: bytes) -> bytes:
        """Decrypts what :meth:`encrypt` produced.

        Works for a revoked key and for one scheduled for deletion, right up until its deletion date
        passes - which is the whole difference between revoking a key and deleting it.
        """
        return self._transform("decrypt", key_id, ciphertext)

    def _transform(self, action: str, key_id: str, data: bytes) -> bytes:
        """encrypt and decrypt differ only in direction: both send opaque bytes, name their key in a
        header, and answer with opaque bytes."""
        response = self._post_bytes(action, data, {"x-euclid-key-id": key_id})
        if not response.ok:
            raise EuclidServiceError(TARGET, action, response.status, response.text)
        return response.content

    # -- certificates ------------------------------------------------------------------------

    def import_certificate(self, name: str, certificate_pem: str, private_key_pem: str,
                           description: str = "") -> Certificate:
        """Stores a certificate somebody else issued, together with the private key that proves it.

        Both halves are required and the server checks them against each other: a certificate stored
        with a key that is not its own is accepted silently by every step after this one and only
        shows itself as a handshake that fails for every caller. A mismatch is HTTP 400 here instead.

        The private key stays with EKM. It goes in and is never handed back - no action returns one.
        """
        return self._certificate("import-certificate", {
            "name": name, "description": description,
            "certificate": certificate_pem, "privateKey": private_key_pem})

    def create_certificate(self, name: str, common_name: str = "",
                           subject_alt_names: Iterable[str] = (), valid_days: int = 0,
                           key_bits: int = 0, description: str = "") -> Certificate:
        """Generates a self-signed certificate, for an installation that has to serve HTTPS before
        anybody has bought it a real one.

        Nobody has vouched for the result - :attr:`~euclid.dto.ekm.Certificate.generated` says so,
        and a client still has to be told to trust it.

        :param name: what to store it under, and the common name when none is given: for a listener
            certificate those are usually the same word, and a certificate with an empty subject is
            refused by everything that reads it.
        :param subject_alt_names: the other names it should be valid for.
        :param valid_days: how long it is valid, or 0 for the server's default of 825 days.
        :param key_bits: the RSA key length, or 0 for the server's default of 2048.
        """
        payload: dict[str, Any] = {"name": name, "description": description,
                                   "commonName": common_name,
                                   "subjectAltNames": list(subject_alt_names)}
        if valid_days:
            payload["validDays"] = valid_days
        if key_bits:
            payload["keyBits"] = key_bits
        return self._certificate("create-certificate", payload)

    def get_certificate(self, name: str) -> Certificate:
        """One stored certificate, by name. The PEM comes back; the private key does not."""
        return self._certificate("get-certificate", {"name": name})

    def list_certificates(self, prefix: str = "", page_size: int = 10, page_index: int = 0,
                          sort_column: str = "name",
                          sort_direction: str = "asc") -> ListCertificatesResult:
        """One page of certificates, and how many exist in total."""
        return ListCertificatesResult.from_json(self._call("list-certificates", {
            "prefix": prefix, "pageSize": page_size, "pageIndex": page_index,
            "sortColumn": sort_column, "sortDirection": sort_direction}))

    def delete_certificate(self, name: str) -> DeleteCertificateResult:
        """Deletes a certificate, outright and with no grace period.

        Unlike :meth:`delete_key` this needs none: nothing becomes unreadable, because a certificate
        is public. A listener already serving it keeps the copy it loaded until it is restarted,
        which is what makes this recoverable - import a replacement under the same name.
        """
        return DeleteCertificateResult.from_json(self._call("delete-certificate", {"name": name}))

    def _certificate(self, action: str, payload: Mapping[str, Any]) -> Certificate:
        """The three actions that answer with one certificate, wrapped in a ``certificate`` field."""
        return Certificate.from_json(self._call(action, payload).get("certificate"))
