"""RFC8785 canonicalization and digest agreement.

The vectors are shared with the TypeScript implementation and owned by neither language. If these
two implementations ever disagree, every digest-bound guarantee in the product — manifest sealing,
approval binding, evidence chaining — silently stops meaning anything.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from accessforge_domain.canonical import (
    CanonicalizationError,
    canonicalize,
    digest,
)

VECTORS = json.loads(
    (
        Path(__file__).resolve().parents[2] / "packages/contracts/vectors/canonicalization.json"
    ).read_text(encoding="utf-8")
)
CASES = VECTORS["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_canonical_form_matches_shared_vector(case: dict) -> None:
    assert canonicalize(case["input"]) == case["canonical"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_digest_matches_sha256_of_canonical_form(case: dict) -> None:
    expected = hashlib.sha256(case["canonical"].encode("utf-8")).hexdigest()
    assert digest(case["input"]) == expected
    assert digest(case["input"]) == digest(case["input"]).lower()
    assert len(digest(case["input"])) == 64


def test_key_order_is_by_utf16_code_unit_not_code_point() -> None:
    """These orders differ above the BMP, and RFC8785 requires the UTF-16 one.

    By code point, U+FF00 (0xFF00) sorts before U+1F600 (0x1F600). By UTF-16 code unit the order
    reverses, because U+1F600 is the surrogate pair 0xD83D 0xDE00 and 0xD83D < 0xFF00. A Python
    implementation that used the natural string ordering would silently disagree with a JavaScript
    one, which sorts by code unit.
    """
    payload = {chr(0x1F600): 1, chr(0xFF00): 2}
    out = canonicalize(payload)
    assert out.index(chr(0x1F600)) < out.index(chr(0xFF00)), (
        "keys must be ordered by UTF-16 code unit, not code point"
    )


def test_property_order_does_not_affect_the_digest() -> None:
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})


def test_array_order_does_affect_the_digest() -> None:
    assert digest({"k": [1, 2]}) != digest({"k": [2, 1]})


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        2**53,  # first unsafe integer
        -(2**53),
        2**63,
    ],
)
def test_unrepresentable_and_overflowing_numbers_are_rejected(value: float | int) -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize({"v": value})


@pytest.mark.parametrize("value", [9007199254740991, -9007199254740991, 0, -1])
def test_safe_integers_are_accepted(value: int) -> None:
    # Allowed-path control: a canonicalizer that rejected everything would fail here.
    assert canonicalize({"v": value}) == f'{{"v":{value}}}'


def test_non_integral_floats_are_rejected() -> None:
    """Deliberate restriction, documented in ADR 0003.

    Cross-language agreement on shortest-round-trip float formatting is difficult to guarantee, and
    CONTRACTS section 4 states that no floating-point value may grant authority or decide an
    outcome. Refusing them is safer than risking a silent digest divergence.
    """
    with pytest.raises(CanonicalizationError, match="non-integral"):
        canonicalize({"confidence": 0.87})


def test_integral_floats_are_normalized_to_integers() -> None:
    assert canonicalize({"v": 5.0}) == '{"v":5}'
    assert canonicalize({"v": -0.0}) == '{"v":0}'
    assert digest({"v": -0.0}) == digest({"v": 0})


def test_non_string_object_keys_are_rejected() -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize({1: "a"})


def test_unsupported_types_are_rejected() -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize({"v": {1, 2}})
    with pytest.raises(CanonicalizationError):
        canonicalize({"v": b"bytes"})
