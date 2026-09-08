"""The shapes more than one euclid module speaks.

:class:`Variant` is the only one so far, and it is here rather than in the module that needed it
first for the same reason the server keeps it in ``Euclid::Dto::COM``: a queue message attribute, a
topic message attribute and a storage object attribute are the same typed value on the wire.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any, Mapping

from . import _json

__all__ = ["Variant", "INT", "LONG", "DOUBLE", "FLOAT", "BOOL", "STRING", "BINARY",
           "PRIORITY_LOW", "PRIORITY_MIDDLE", "PRIORITY_HIGH"]

#: What a message is delivered at, and the only three values euclid accepts. Shared for the same
#: reason :class:`Variant` is: a queue message, a topic message and the ``priority`` system
#: attribute of a stored object all mean the same thing by it.
PRIORITY_LOW = "LOW"
PRIORITY_MIDDLE = "MIDDLE"
PRIORITY_HIGH = "HIGH"

#: The type tags ``Euclid::Dto::COM::Variant`` round-trips a value through.
INT = "int"
LONG = "long"
DOUBLE = "double"
FLOAT = "float"
BOOL = "bool"
STRING = "string"
BINARY = "binary"


@dataclass(frozen=True)
class Variant:
    """A typed value: what it is, and what it holds.

    The tag is what makes the round trip lossless. JSON has one number type and euclid's server has
    several, so an attribute stored as a ``long`` would come back as a ``double`` - or an ``int``,
    depending on the reader - if the type travelled only in the shape of the value.

    ``binary`` is base64 on the wire and :class:`bytes` here; :meth:`from_json` decodes it and
    :meth:`to_json` encodes it again, so a caller never sees the encoding.
    """

    type: str
    value: Any

    @staticmethod
    def of(value: Any) -> "Variant":
        """A variant from a plain Python value, tagged with the type euclid stores it under.

        An :class:`int` becomes a ``long`` rather than an ``int`` because Python's integers have no
        width and the 64-bit tag is the one that can hold all of the ones that fit; a
        :class:`float` becomes a ``double`` for the same reason. Booleans are checked before
        integers, since in Python a ``bool`` is one.

        A :class:`Variant` is returned unchanged, so a caller may mix the two spellings in the same
        attribute map.
        """
        if isinstance(value, Variant):
            return value
        if isinstance(value, bool):
            return Variant(BOOL, value)
        if isinstance(value, int):
            return Variant(LONG, value)
        if isinstance(value, float):
            return Variant(DOUBLE, value)
        if isinstance(value, str):
            return Variant(STRING, value)
        if isinstance(value, (bytes, bytearray, memoryview)):
            return Variant(BINARY, bytes(value))
        raise TypeError(f"a {type(value).__name__} has no euclid variant type - pass a Variant with "
                        f"the tag it should carry")

    def to_json(self) -> dict[str, Any]:
        """This variant as the server reads it."""
        if self.type == BINARY and isinstance(self.value, (bytes, bytearray, memoryview)):
            return {"type": self.type, "value": base64.b64encode(bytes(self.value)).decode("ascii")}
        return {"type": self.type, "value": self.value}

    @staticmethod
    def from_json(document: Any) -> "Variant":
        """One variant as the server sent it. An unreadable one reads as an empty string value."""
        if not isinstance(document, dict):
            return Variant(STRING, "")
        tag = _json.text(document, "type")
        value = document.get("value")
        if tag == BINARY and isinstance(value, str):
            try:
                return Variant(BINARY, base64.b64decode(value, validate=True))
            except (binascii.Error, ValueError):
                # Handed back as the text it arrived as rather than raising: a caller that can make
                # sense of it is better served than one that gets an exception instead of the object
                # the attribute was hanging off.
                return Variant(BINARY, value)
        return Variant(tag, value)

    @staticmethod
    def map_from_json(document: Any) -> dict[str, "Variant"]:
        """An object of variants keyed by name, empty when absent."""
        if not isinstance(document, dict):
            return {}
        return {name: Variant.from_json(value) for name, value in document.items()}

    @staticmethod
    def map_to_json(attributes: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
        """An attribute map as the server reads it, taking plain Python values or variants."""
        if not attributes:
            return {}
        return {name: Variant.of(value).to_json() for name, value in attributes.items()}
