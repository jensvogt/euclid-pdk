"""Reading fields out of a server response, defensively.

Every response type in this package parses the same way: a field the server did not send reads as
an empty string, an empty list or a zero rather than raising. A client that insisted on a field
being there would break on the release that adds one - the parse would reach a document shaped
slightly differently from the one it was written against - which is the failure mode these helpers
exist to avoid.

Private to :mod:`euclid.dto`, and shared across its modules so that "absent reads as empty" is one
implementation rather than one per module.
"""

from __future__ import annotations

from typing import Any

__all__ = ["text", "flag", "number", "strings", "string_map", "documents"]


def text(document: Any, name: str) -> str:
    """A string field, empty when absent or null."""
    if not isinstance(document, dict):
        return ""
    value = document.get(name)
    return value if isinstance(value, str) else ""


def flag(document: Any, name: str, default: bool = False) -> bool:
    """A boolean field, ``default`` when absent or not a boolean."""
    if not isinstance(document, dict):
        return default
    value = document.get(name)
    return value if isinstance(value, bool) else default


def number(document: Any, name: str) -> int:
    """An integer field, zero when absent. ``True`` is not a number here, whatever Python thinks."""
    if not isinstance(document, dict):
        return 0
    value = document.get(name)
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def strings(document: Any, name: str) -> list[str]:
    """An array of strings, empty when absent, with anything that is not a string left out."""
    if not isinstance(document, dict):
        return []
    values = document.get(name)
    return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []


def string_map(document: Any, name: str) -> dict[str, str]:
    """An object of strings - a tag set - empty when absent."""
    if not isinstance(document, dict):
        return {}
    values = document.get(name)
    if not isinstance(values, dict):
        return {}
    return {key: value for key, value in values.items() if isinstance(value, str)}


def documents(document: Any, name: str) -> list[Any]:
    """An array of sub-documents, empty when absent. The caller parses each one."""
    if not isinstance(document, dict):
        return []
    values = document.get(name)
    return list(values) if isinstance(values, list) else []
