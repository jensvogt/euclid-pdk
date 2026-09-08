"""ESS - euclid's secret store: values kept encrypted under an EKM key, fetched one at a time.

One object, :class:`EuclidEss`, built from a session that has already logged in::

    ess = Euclid.for_server(url).login("jens", "secret").ess()
    ess.create_secret("db-password", "hunter2", description="the reporting database")

    password = ess.get_secret("db-password").value

The value is encrypted with EKM before it is stored, so a secret's life is tied to a key's: deleting
that key there is what makes the value unrecoverable, whatever ESS still says about it.

Only :meth:`~EuclidEss.get_secret` returns a value. Everything else - listing, rotating, tagging -
answers with metadata alone, so those calls can be logged and printed without being the thing that
leaks it.
"""

from __future__ import annotations

from typing import Any

from ..dto.ess import DeleteSecretResult, ListSecretsResult, Secret, SecretValue
from .base import ModuleClient

__all__ = ["EuclidEss", "TARGET"]

TARGET = "ess"


class EuclidEss(ModuleClient):
    """ESS's operations, on the credentials of the session that created it.

    Built by :meth:`euclid.EuclidSession.ess` rather than directly, so that it shares that
    session's identity, namespace and connection settings - and follows them as they change.
    """

    target = TARGET

    def create_secret(self, name: str, value: str, description: str = "",
                      key_ern: str = "") -> Secret:
        """Stores a secret, and returns its metadata - never the value it was just given.

        :param key_ern: the EKM key to encrypt it under. Left empty, the server picks the account's
            own; naming one puts this secret's life in the hands of that key, which is the point
            when a set of secrets should be revocable together.
        """
        return self._secret("create-secret", {"name": name, "value": value,
                                              "description": description, "keyErn": key_ern})

    def get_secret(self, name: str) -> SecretValue:
        """One secret, decrypted: ``.value`` is the value, ``.secret`` the metadata around it.

        The only call in this SDK that returns a secret's value, and so the point at which the value
        enters the process.
        """
        return SecretValue.from_json(self._call("get-secret", {"name": name}))

    def list_secrets(self, prefix: str = "", page_size: int = 10, page_index: int = 0,
                     sort_column: str = "name", sort_direction: str = "asc") -> ListSecretsResult:
        """One page of secrets, and how many exist in total. Metadata only."""
        return ListSecretsResult.from_json(self._call("list-secrets", {
            "prefix": prefix, "pageSize": page_size, "pageIndex": page_index,
            "sortColumn": sort_column, "sortDirection": sort_direction}))

    def rotate_secret(self, name: str, value: str) -> Secret:
        """Replaces a secret's value, which is what a rotation is and what bumps its version."""
        return self.update_secret(name, value=value)

    def update_secret(self, name: str, value: str | None = None, description: str | None = None,
                      key_ern: str = "") -> Secret:
        """Changes a secret that already exists: its value, its description, the key it is under, or
        any combination. Only what this names changes.

        The distinction the server draws is between a field being sent and not being sent, rather
        than between its values - so leaving ``description`` as None leaves the stored description
        alone, while passing ``""`` clears it. The same goes for ``value``: None leaves it, and an
        empty string stores an empty one, because an empty string is a value somebody may
        legitimately have.

        Naming a ``key_ern`` re-encrypts the value under that key, which is how a secret is moved
        off a key that is being retired.

        :raises ValueError: if none of the three was named, which the server refuses anyway - this
            just says so before the round trip.
        """
        if value is None and description is None and not key_ern:
            raise ValueError("update_secret needs a value, a description or a key_ern to change")

        payload: dict[str, Any] = {"name": name}
        if value is not None:
            payload["value"] = value
        if description is not None:
            payload["description"] = description
        if key_ern:
            payload["keyErn"] = key_ern
        return self._secret("update-secret", payload)

    def delete_secret(self, name: str) -> DeleteSecretResult:
        """Deletes a secret, outright. The value is gone; the key it was under is left alone."""
        return DeleteSecretResult.from_json(self._call("delete-secret", {"name": name}))

    def add_secret_tag(self, name: str, key: str, value: str) -> Secret:
        """Tags a secret, and returns it as it now reads. A tag already there has its value
        replaced - ESS has no separate set-secret-tag action to distinguish the two."""
        return self._secret("add-secret-tag", {"name": name, "key": key, "value": value})

    def delete_secret_tag(self, name: str, key: str) -> Secret:
        """Removes a tag from a secret, and returns it as it now reads."""
        return self._secret("delete-secret-tag", {"name": name, "key": key})

    def metrics(self) -> dict[str, Any]:
        """ESS's own metrics, as the server collects them. Returned unparsed - the shape belongs to
        the monitoring module rather than to ESS."""
        return self._call("get-metrics")

    def _secret(self, action: str, payload: dict[str, Any]) -> Secret:
        """The actions that answer with one secret's metadata, wrapped in a ``secret`` field."""
        return Secret.from_json(self._call(action, payload).get("secret"))
