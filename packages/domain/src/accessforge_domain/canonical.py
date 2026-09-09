"""RFC8785 (JSON Canonicalization Scheme) serialization and SHA-256 digests.

Every digest-bound guarantee in AccessForge rests on this file: sealed manifests, approval binding,
and the evidence hash chain. A TypeScript implementation in packages/contracts/ts must produce
byte-identical output; shared vectors in packages/contracts/vectors/canonicalization.json are the
arbiter, and neither language owns them.

Two deliberate restrictions, recorded in ADR 0003:

* **Non-integral floats are rejected.** Guaranteeing identical shortest-round-trip float formatting
  across languages is difficult, and CONTRACTS section 4 states that no floating-point value may
  grant authority or decide an outcome. Refusing them is better than a silent digest divergence.
* **Integers outside the JSON safe range are rejected**, per the same section's overflow rule.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

MAX_SAFE_INTEGER = 2**53 - 1
MIN_SAFE_INTEGER = -(2**53) + 1

# RFC8785 uses the JSON short escapes where they exist and lower-case \u00xx otherwise.
_SHORT_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


class CanonicalizationError(ValueError):
    """A value cannot be canonicalized deterministically.

    Raised rather than coerced. A canonicalizer that guessed would produce a digest that two
    implementations could disagree about, which is the failure this module exists to prevent.
    """


def _reject_lone_surrogates(value: str) -> None:
    """Refuse text containing an unpaired surrogate code unit.

    A lone surrogate has no UTF-8 encoding. Left unchecked, Python raised a raw
    UnicodeEncodeError from inside digest() while TypeScript silently substituted U+FFFD and
    returned a confident, wrong hash — the more dangerous of the two, and precisely the kind of
    successful-looking wrong answer this product exists to prevent. Both implementations now
    refuse at the same point with the same error.
    """
    index = 0
    length = len(value)
    while index < length:
        code = ord(value[index])
        if 0xD800 <= code <= 0xDBFF:  # high surrogate: must be followed by a low surrogate
            if index + 1 >= length or not (0xDC00 <= ord(value[index + 1]) <= 0xDFFF):
                raise CanonicalizationError(
                    f"unpaired high surrogate U+{code:04X} at index {index}: no UTF-8 encoding "
                    "exists, so no digest over it can be meaningful"
                )
            index += 2
            continue
        if 0xDC00 <= code <= 0xDFFF:  # low surrogate with no preceding high surrogate
            raise CanonicalizationError(
                f"unpaired low surrogate U+{code:04X} at index {index}: no UTF-8 encoding exists"
            )
        index += 1


def _serialize_string(value: str) -> str:
    _reject_lone_surrogates(value)
    out = ['"']
    for char in value:
        code = ord(char)
        escape = _SHORT_ESCAPES.get(code)
        if escape is not None:
            out.append(escape)
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _serialize_number(value: int | float) -> str:
    if isinstance(value, bool):  # bool is a subclass of int; handled by the caller
        raise CanonicalizationError("bool must be serialized as a literal, not a number")

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise CanonicalizationError(f"{value!r} is not representable in JSON")
        if not value.is_integer():
            raise CanonicalizationError(
                f"refusing to canonicalize the non-integral float {value!r}: cross-language "
                "formatting agreement is not guaranteed, and floating-point values may not "
                "decide an outcome (CONTRACTS section 4)"
            )
        # Integral floats normalize to integers, which also renders -0.0 as 0 exactly as ES6
        # Number::toString does.
        value = int(value)

    if not MIN_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
        raise CanonicalizationError(
            f"integer {value} is outside the JSON safe-integer range "
            f"[{MIN_SAFE_INTEGER}, {MAX_SAFE_INTEGER}]; overflow must be rejected"
        )
    return str(value)


def _utf16_sort_key(key: str) -> bytes:
    """Sort key ordering object properties by UTF-16 code unit, as RFC8785 requires.

    Python's natural string ordering compares code points, which differs above the BMP. By code
    point U+FF00 (0xFF00) sorts before U+1F600 (0x1F600); by UTF-16 code unit the order reverses,
    because U+1F600 is the surrogate pair 0xD83D 0xDE00 and 0xD83D < 0xFF00.
    """
    return key.encode("utf-16-be")


def _serialize(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _serialize_string(value)
    if isinstance(value, (int, float)):
        return _serialize_number(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_serialize(item) for item in value) + "]"
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise CanonicalizationError(
                    f"object keys must be strings, found {type(key).__name__}"
                )
            # Validate before sorting: _utf16_sort_key encodes, and encoding a lone surrogate
            # raises UnicodeEncodeError before serialization would have caught it.
            _reject_lone_surrogates(key)
        items = sorted(value.items(), key=lambda kv: _utf16_sort_key(kv[0]))
        return "{" + ",".join(f"{_serialize_string(k)}:{_serialize(v)}" for k, v in items) + "}"
    raise CanonicalizationError(f"type {type(value).__name__} cannot be canonicalized")


def canonicalize(value: Any) -> str:
    """Return the RFC8785 canonical JSON form of ``value``."""
    return _serialize(value)


def digest(value: Any) -> str:
    """Lower-case hex SHA-256 of the canonical form."""
    return hashlib.sha256(canonicalize(value).encode("utf-8")).hexdigest()
