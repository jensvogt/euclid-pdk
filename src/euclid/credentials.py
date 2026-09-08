"""The ``~/.euclid/credentials`` file, shared with euclid-cli and euclid-jdk.

All three clients read and write the same file, so a login from any of them is picked up by the
others. That makes the field names a wire format rather than an implementation detail: the
namespace key is ``namespace`` (not ``nameSpace``), ``isAdmin`` travels alongside the token, and an
absent namespace is written as an empty string rather than null so the CLI's string reader can take
it. ``baseUrl`` is the one field the CLI has no equivalent for - it is what lets a cached session be
recognised as belonging to the server being talked to now.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["CachedCredentials", "credentials_path", "load", "save", "update_namespace", "is_token_valid"]


def credentials_path() -> Path:
    """Where the credentials live.

    Resolved per call rather than captured at import, mirroring euclid-cli's
    ``Credentials::FilePath()``: the home directory is read when the file is actually touched, so a
    process that changes it is not left talking to a stale path. ``EUCLID_CREDENTIALS_FILE``
    overrides it, which is also how a euclid-managed application is handed its own credentials.
    """
    override = os.environ.get("EUCLID_CREDENTIALS_FILE")
    if override:
        return Path(override)
    return Path.home() / ".euclid" / "credentials"


@dataclass
class CachedCredentials:
    """One cached login."""

    token: str = ""
    user_id: str = ""
    account_id: str = ""
    region: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    is_admin: bool = False
    base_url: str = ""
    namespace: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_json(document: dict[str, Any]) -> "CachedCredentials":
        return CachedCredentials(
            token=document.get("token") or "",
            user_id=document.get("userId") or "",
            account_id=document.get("accountId") or "",
            region=document.get("region") or "",
            access_key_id=document.get("accessKeyId") or "",
            secret_access_key=document.get("secretAccessKey") or "",
            is_admin=bool(document.get("isAdmin", False)),
            base_url=document.get("baseUrl") or "",
            namespace=document.get("namespace") or "",
            raw=document,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "userId": self.user_id,
            "accountId": self.account_id,
            "region": self.region,
            "accessKeyId": self.access_key_id,
            "secretAccessKey": self.secret_access_key,
            "isAdmin": self.is_admin,
            "baseUrl": self.base_url,
            "namespace": self.namespace,
        }


def load() -> CachedCredentials | None:
    """The cached credentials, or None when there is no readable, well-formed file."""
    path = credentials_path()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    return CachedCredentials.from_json(document)


def save(credentials: CachedCredentials) -> None:
    """Writes the credentials, readable by their owner alone."""
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(credentials.to_json()), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - not every filesystem has POSIX permissions
        pass


def update_namespace(base_url: str, namespace: str) -> None:
    """Patches the cached namespace in place, if what is cached belongs to ``base_url``.

    Keeps the file in step when a session's namespace changes outside of a login. A no-op when
    nothing is cached for that server, in keeping with the best-effort nature of the cache: failing
    to record a namespace is not a reason to fail the call that changed it.
    """
    cached = load()
    if cached is None or cached.base_url != base_url:
        return
    cached.namespace = namespace or ""
    save(cached)


def is_token_valid(token: str) -> bool:
    """Whether a JWT is well-formed and its ``exp`` is still in the future.

    Checked locally to avoid a round trip that would only tell us what the token already says. The
    signature is not verified - the client does not hold the server's secret, and a token the
    client forged for itself would be rejected on arrival anyway.
    """
    parts = token.split(".")
    if len(parts) < 2:
        return False
    try:
        payload = json.loads(_base64_url_decode(parts[1]))
    except (ValueError, TypeError):
        return False
    expiry = payload.get("exp") if isinstance(payload, dict) else None
    return isinstance(expiry, (int, float)) and time.time() < expiry


def _base64_url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded)
