"""The shapes EAM sends back.

Plain dataclasses rather than dicts, so a typo in a field name is an AttributeError here instead of
a None that travels. Each one parses defensively - a field the server did not send reads as an
empty string or an empty list - because a client that raised on an unfamiliar response would break
on the release that adds a field rather than the release that removes one.

Field names are the server's (``dto/include/euclid/dto/eam``), converted to snake_case; where the
two differ, the JSON name is the one on the wire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Metadata",
    "AccessKey",
    "AccountGrant",
    "User",
    "UserGroup",
    "Account",
    "Namespace",
    "LoginResult",
    "ListUsersResult",
    "ListUserGroupsResult",
    "ListAccountsResult",
    "ListNamespacesResult",
    "CreateAccessKeyResult",
]


def _text(document: Any, name: str) -> str:
    """A string field, empty when absent or null."""
    if not isinstance(document, dict):
        return ""
    value = document.get(name)
    return value if isinstance(value, str) else ""


def _flag(document: Any, name: str, default: bool = False) -> bool:
    if not isinstance(document, dict):
        return default
    value = document.get(name)
    return value if isinstance(value, bool) else default


def _number(document: Any, name: str) -> int:
    if not isinstance(document, dict):
        return 0
    value = document.get(name)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _strings(document: Any, name: str) -> list[str]:
    if not isinstance(document, dict):
        return []
    values = document.get(name)
    return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []


@dataclass
class Metadata:
    """The caller identity a response echoes back, from the server's ``BaseDto``."""

    region: str = ""
    account_id: str = ""
    user: str = ""

    @staticmethod
    def from_json(document: Any) -> "Metadata":
        return Metadata(_text(document, "region"), _text(document, "accountId"), _text(document, "user"))


@dataclass
class AccessKey:
    """A signing credential. The secret is returned once, at creation, and never again."""

    access_key_id: str = ""
    active: bool = True
    created_at: str = ""

    @staticmethod
    def from_json(document: Any) -> "AccessKey":
        return AccessKey(_text(document, "accessKeyId"), _flag(document, "active", True),
                         _text(document, "createdAt"))


@dataclass
class AccountGrant:
    """What a user may reach in one account: which namespaces, and whether they administer it."""

    account_id: str = ""
    namespaces: list[str] = field(default_factory=list)
    is_admin: bool = False
    granted: str = ""

    @staticmethod
    def from_json(document: Any) -> "AccountGrant":
        return AccountGrant(_text(document, "accountId"), _strings(document, "namespaces"),
                            _flag(document, "isAdmin"), _text(document, "granted"))


@dataclass
class User:
    """A user. ``password`` is a hash when the server sends one at all - never the plaintext."""

    user_id: str = ""
    ern: str = ""
    password: str = ""
    email: str = ""
    account_id: str = ""
    region: str = ""
    account_grants: list[AccountGrant] = field(default_factory=list)
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "User":
        grants = document.get("accountGrants") if isinstance(document, dict) else None
        return User(
            _text(document, "userId"), _text(document, "ern"), _text(document, "password"),
            _text(document, "email"), _text(document, "accountId"), _text(document, "region"),
            [AccountGrant.from_json(g) for g in grants] if isinstance(grants, list) else [],
            _text(document, "created"), _text(document, "modified"))


@dataclass
class UserGroup:
    """A named set of users. Membership in the ``administrator`` group is what makes an admin."""

    name: str = ""
    ern: str = ""
    account_id: str = ""
    region: str = ""
    description: str = ""
    user_ids: list[str] = field(default_factory=list)
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "UserGroup":
        return UserGroup(_text(document, "name"), _text(document, "ern"), _text(document, "accountId"),
                         _text(document, "region"), _text(document, "description"),
                         _strings(document, "userIds"), _text(document, "created"),
                         _text(document, "modified"))


@dataclass
class Account:
    """A tenant. Namespaces live under it, and everything else is scoped by the pair."""

    account_id: str = ""
    name: str = ""
    ern: str = ""
    description: str = ""
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Account":
        return Account(_text(document, "accountId"), _text(document, "name"), _text(document, "ern"),
                       _text(document, "description"), _text(document, "created"),
                       _text(document, "modified"))


@dataclass
class Namespace:
    """A namespace within an account, unique by name within it."""

    account_id: str = ""
    name: str = ""
    ern: str = ""
    description: str = ""
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Namespace":
        return Namespace(_text(document, "accountId"), _text(document, "name"), _text(document, "ern"),
                         _text(document, "description"), _text(document, "created"),
                         _text(document, "modified"))


@dataclass
class LoginResult:
    """What ``login`` answers with: a bearer token and, usually, an access key to sign with."""

    token: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    created_at: str = ""
    is_admin: bool = False
    metadata: Metadata = field(default_factory=Metadata)
    raw: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_json(document: Any) -> "LoginResult":
        return LoginResult(
            _text(document, "token"), _text(document, "accessKeyId"), _text(document, "secretAccessKey"),
            _text(document, "createdAt"), _flag(document, "isAdmin"),
            Metadata.from_json(document.get("metadata") if isinstance(document, dict) else None),
            document if isinstance(document, dict) else {})


@dataclass
class ListUsersResult:
    """One page of users, and how many exist in total."""

    users: list[User] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListUsersResult":
        items = document.get("users") if isinstance(document, dict) else None
        return ListUsersResult([User.from_json(u) for u in items] if isinstance(items, list) else [],
                               _number(document, "total"))


@dataclass
class ListUserGroupsResult:
    """One page of user groups, and how many exist in total."""

    user_groups: list[UserGroup] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListUserGroupsResult":
        items = document.get("userGroups") if isinstance(document, dict) else None
        return ListUserGroupsResult([UserGroup.from_json(g) for g in items] if isinstance(items, list) else [],
                                    _number(document, "total"))


@dataclass
class ListAccountsResult:
    """One page of accounts, and how many exist in total."""

    accounts: list[Account] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListAccountsResult":
        items = document.get("accounts") if isinstance(document, dict) else None
        return ListAccountsResult([Account.from_json(a) for a in items] if isinstance(items, list) else [],
                                  _number(document, "total"))


@dataclass
class ListNamespacesResult:
    """One page of namespaces, and how many exist in total."""

    namespaces: list[Namespace] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListNamespacesResult":
        items = document.get("namespaces") if isinstance(document, dict) else None
        return ListNamespacesResult([Namespace.from_json(n) for n in items] if isinstance(items, list) else [],
                                    _number(document, "total"))


@dataclass
class CreateAccessKeyResult:
    """A newly created access key. This is the only time the secret is ever returned."""

    access_key_id: str = ""
    secret_access_key: str = ""
    created_at: str = ""
    metadata: Metadata = field(default_factory=Metadata)

    @staticmethod
    def from_json(document: Any) -> "CreateAccessKeyResult":
        return CreateAccessKeyResult(
            _text(document, "accessKeyId"), _text(document, "secretAccessKey"), _text(document, "createdAt"),
            Metadata.from_json(document.get("metadata") if isinstance(document, dict) else None))
