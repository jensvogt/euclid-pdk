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

# Aliased rather than imported under their own names so that the parsing below reads as it did when
# these lived here: they moved to be shared with the other modules' response types, not to change.
from ._json import documents as _documents
from ._json import flag as _flag
from ._json import number as _number
from ._json import strings as _strings
from ._json import text as _text

__all__ = [
    "Metadata",
    "AccessKey",
    "Role",
    "ListRolesResult",
    "Grant",
    "ListGrantsResult",
    "PermissionCheck",
    "PermissionVocabulary",
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
class Role:
    """A named set of permissions, belonging to one account.

    ``builtin`` says whether this is one of the roles every installation has without anybody
    creating them, which is what decides whether it can be changed: the built-ins are computed from
    the permission vocabulary rather than stored, so create, update and delete all refuse them. A
    caller that had to know the seven names to work that out would go stale the day an eighth
    appears.
    """

    name: str = ""
    ern: str = ""
    account_id: str = ""
    region: str = ""
    description: str = ""
    permissions: list[str] = field(default_factory=list)
    builtin: bool = False
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "Role":
        return Role(_text(document, "name"), _text(document, "ern"), _text(document, "accountId"),
                    _text(document, "region"), _text(document, "description"),
                    _strings(document, "permissions"), _flag(document, "builtin"),
                    _text(document, "created"), _text(document, "modified"))


@dataclass
class ListRolesResult:
    """One page of an account's own roles, with the built-ins in front of it.

    ``total`` counts the stored roles only, and the built-ins are outside the paging entirely -
    they are computed rather than stored, so there is no page to put them on. A caller paging
    through sees the same seven at the top of every page, which is the honest rendering of
    something that belongs to no page.
    """

    roles: list[Role] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListRolesResult":
        return ListRolesResult([Role.from_json(r) for r in _documents(document, "roles")],
                               _number(document, "total"))


@dataclass
class Grant:
    """One role, given to one principal, somewhere.

    The only thing that grants anything, and the only thing that carries scope. Replaced the
    per-user ``accountGrants``/``resourceGrants`` lists: what a user may do is the union of the
    grants held by them and by every group they belong to.

    ``grant_id`` is what :meth:`~euclid.modules.eam.EuclidSession.revoke_role` takes - not the
    (role, principal) pair, since the same role may be granted to the same principal twice with
    different scope and revoking has to say which.
    """

    grant_id: str = ""
    role: str = ""
    principal: str = ""
    account_id: str = ""
    namespaces: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    granted: str = ""
    granted_by: str = ""

    @staticmethod
    def from_json(document: Any) -> "Grant":
        return Grant(_text(document, "grantId"), _text(document, "role"), _text(document, "principal"),
                     _text(document, "accountId"), _strings(document, "namespaces"),
                     _strings(document, "resources"), _text(document, "granted"),
                     _text(document, "grantedBy"))


@dataclass
class ListGrantsResult:
    """The grants matching a principal, a role, or a whole account."""

    grants: list[Grant] = field(default_factory=list)
    total: int = 0

    @staticmethod
    def from_json(document: Any) -> "ListGrantsResult":
        return ListGrantsResult([Grant.from_json(g) for g in _documents(document, "grants")],
                                _number(document, "total"))


@dataclass
class PermissionCheck:
    """Whether a user would be allowed to do something, and what decided it.

    ``reason`` is the point of the whole action. A permission system that cannot say *why* gets
    worked around by making everybody an administrator, so the answer names what applied: the
    administrator group, the grant that matched, or the absence of one.

    ``role`` is the role whose grant allowed it, and is empty on a refusal - and on an allow that
    no grant decided, which is what an installation administrator's looks like.
    """

    allowed: bool = False
    reason: str = ""
    role: str = ""

    @staticmethod
    def from_json(document: Any) -> "PermissionCheck":
        return PermissionCheck(_flag(document, "allowed"), _text(document, "reason"),
                               _text(document, "role"))


@dataclass
class PermissionVocabulary:
    """Every permission a role can hold, as ``<module>:<action>``.

    Derived from what the modules actually dispatch rather than written by hand, so a permission
    cannot be granted for an action that does not exist and a new action cannot be left out.

    ``unbindable_modules`` is why something is missing rather than a gap: EMM exposes every
    module's process pool and raw collections, and EMD *is* the document store and would let a
    caller past every check the owning module makes. No role names their actions, and ``*:*`` does
    not reach them. Naming them here means the listing describes exactly what can be granted.
    """

    permissions: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    unbindable_modules: list[str] = field(default_factory=list)

    @staticmethod
    def from_json(document: Any) -> "PermissionVocabulary":
        return PermissionVocabulary(_strings(document, "permissions"), _strings(document, "modules"),
                                    _strings(document, "unbindableModules"))


@dataclass
class User:
    """A user. ``password`` is a hash when the server sends one at all - never the plaintext."""

    user_id: str = ""
    ern: str = ""
    password: str = ""
    email: str = ""
    account_id: str = ""
    region: str = ""
    created: str = ""
    modified: str = ""

    @staticmethod
    def from_json(document: Any) -> "User":
        return User(
            _text(document, "userId"), _text(document, "ern"), _text(document, "password"),
            _text(document, "email"), _text(document, "accountId"), _text(document, "region"),
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
