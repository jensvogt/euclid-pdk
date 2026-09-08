"""Exceptions raised by the SDK.

The server answers every failure the same way - a non-2xx status and a JSON body of the form
``{"error": "..."}`` (``Core::HttpActionServer::ErrorResponse``) - so the message a caller sees is
pulled out of that body when it is there, and falls back to the raw body when it is not.
"""

from __future__ import annotations

import json

__all__ = ["EuclidError", "EuclidAuthenticationError", "EuclidServiceError"]


def _reason(body: str | None) -> str:
    """The server's error message, or the body verbatim when it is not the shape we expect."""
    if not body:
        return ""
    try:
        parsed = json.loads(body)
    except ValueError:
        return body.strip()
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
        return parsed["error"]
    return body.strip()


class EuclidError(Exception):
    """Base class for everything this SDK raises."""


class EuclidAuthenticationError(EuclidError):
    """A login was refused.

    Separate from :class:`EuclidServiceError` because it is the one failure a caller can nearly
    always do something about: the password is wrong, the account is disabled, or the user is a
    technical user the server refuses to log in interactively.
    """

    def __init__(self, status: int, body: str | None = None) -> None:
        self.status = status
        self.body = body
        self.reason = _reason(body)
        super().__init__(f"login failed with HTTP {status}" + (f": {self.reason}" if self.reason else ""))


class EuclidServiceError(EuclidError):
    """A module refused or failed an action.

    Carries the target and action alongside the status so a caller catching one of these knows
    which call failed without having to have wrapped each one individually.
    """

    def __init__(self, target: str, action: str, status: int, body: str | None = None) -> None:
        self.target = target
        self.action = action
        self.status = status
        self.body = body
        self.reason = _reason(body)
        super().__init__(f"{target}/{action} failed with HTTP {status}" + (f": {self.reason}" if self.reason else ""))
