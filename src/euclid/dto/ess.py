"""The shapes ESS sends back.

Parsed the same defensive way as every other module's. One thing is deliberately absent from
:class:`Secret`: the value. Every action but ``get-secret`` answers with a secret's metadata only,
so a listing, a rotation and a tag change can be logged, printed and passed around without any of
them being the thing that leaks it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import _json

__all__ = ["Secret", "SecretValue", "ListSecretsResult", "DeleteSecretResult"]


@dataclass
class Secret:
    """A secret's metadata: everything about it except what it is.

    ``version`` is how many times the value has been replaced, and ``rotated`` when that last
    happened - which together are what an audit of "has this been rotated since the incident"
    actually reads.
    """

    name: str = ""
    ern: str = ""
    description: str = ""
    #: The EKM key the value is encrypted under. Deleting that key there is what makes this
    #: secret's value unrecoverable.
    encryption_key_ern: str = ""
    version: int = 0
    rotated: str = ""
    tags: dict[str, str] = field(default_factory=dict)
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Secret":
        return Secret(
            _json.text(document, "name"), _json.text(document, "ern"),
            _json.text(document, "description"), _json.text(document, "encryptionKeyErn"),
            _json.number(document, "version"), _json.text(document, "rotated"),
            _json.string_map(document, "tags"), _json.text(document, "created"),
            _json.text(document, "modified"))


@dataclass
class SecretValue:
    """A secret, decrypted: its value, and the metadata that goes with it.

    The only shape in this SDK that carries a secret's value, and the only action that produces one.
    Whatever a caller does with :attr:`value`, this object is the point at which the value entered
    the process - which is worth knowing when deciding what to log.
    """

    value: str = ""
    secret: Secret = field(default_factory=Secret)

    @staticmethod
    def from_json(document: Any) -> "SecretValue":
        secret = document.get("secret") if isinstance(document, dict) else None
        return SecretValue(_json.text(document, "value"), Secret.from_json(secret))


@dataclass
class ListSecretsResult:
    """One page of secrets - their metadata, never their values - and how many exist in total."""

    secrets: list[Secret] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListSecretsResult":
        return ListSecretsResult([Secret.from_json(s) for s in _json.documents(document, "secrets")],
                                 _json.number(document, "total"))


@dataclass
class DeleteSecretResult:
    """The name and ERN of a deleted secret."""

    name: str = ""
    ern: str = ""

    @staticmethod
    def from_json(document: Any) -> "DeleteSecretResult":
        return DeleteSecretResult(_json.text(document, "name"), _json.text(document, "ern"))
