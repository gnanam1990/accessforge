"""RFC3339 UTC parsing and comparison.

How timestamps compare decides whether an authorization has expired, so this is a security
boundary rather than a formatting concern. Invariant: INV-08.
"""

from __future__ import annotations

import pytest

from accessforge_domain.timestamps import (
    TimestampError,
    is_after,
    is_expired,
    parse_rfc3339_utc,
)


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-09T12:00:00Z",
        "2026-09-09T12:00:00.0Z",
        "2026-09-09T12:00:00.000001Z",
        "2026-09-09T12:00:00.123456789Z",
        "2026-01-01T00:00:00Z",
    ],
)
def test_valid_utc_timestamps_are_accepted(value: str) -> None:
    # Allowed-path control: a parser rejecting everything would fail here.
    assert parse_rfc3339_utc(value).tzinfo is not None


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-09T12:00:00+05:30",  # local offset
        "2026-09-09T12:00:00-00:00",
        "2026-09-09T12:00:00",  # naive
        "2026-09-09 12:00:00Z",  # space separator
        "2026-09-09T12:00:00z",  # lower-case z
        "20260909T120000Z",  # basic format
        "not-a-timestamp",
        "",
    ],
)
def test_non_utc_or_malformed_timestamps_are_refused(value: str) -> None:
    with pytest.raises(TimestampError):
        parse_rfc3339_utc(value)


def test_fractional_precision_does_not_change_the_comparison() -> None:
    """The exact case that made expiry fail open.

    As text, "2026-09-09T12:00:00.000001Z" sorts BEFORE "2026-09-09T12:00:00Z", because "."
    (0x2E) is lower than "Z" (0x5A) — so a lexicographic check reported an expired authorization
    as still valid. Both are valid under the rfc3339Utc schema pattern.
    """
    later = "2026-09-09T12:00:00.000001Z"
    earlier = "2026-09-09T12:00:00Z"

    assert later < earlier, "the string ordering that caused the bug still holds"
    assert is_after(later=later, earlier=earlier), "chronological ordering must be the opposite"
    assert is_expired(now=later, expires_at=earlier)


def test_expiry_is_inclusive_of_the_boundary() -> None:
    at = "2026-09-09T12:00:00Z"
    assert is_expired(now=at, expires_at=at), (
        "an authorization is not valid at the instant it expires"
    )
    assert not is_expired(now="2026-09-09T11:59:59.999999Z", expires_at=at)


def test_equivalent_representations_compare_equal() -> None:
    assert not is_after(later="2026-09-09T12:00:00Z", earlier="2026-09-09T12:00:00.000000Z")
    assert not is_after(later="2026-09-09T12:00:00.000000Z", earlier="2026-09-09T12:00:00Z")


def test_comparison_refuses_malformed_input_rather_than_guessing() -> None:
    with pytest.raises(TimestampError):
        is_expired(now="whenever", expires_at="2026-09-09T12:00:00Z")
    with pytest.raises(TimestampError):
        is_expired(now="2026-09-09T12:00:00Z", expires_at="2026-09-09T12:00:00+00:00")


@pytest.mark.parametrize(
    "value",
    [
        "2026-13-45T99:99:99Z",  # matches the pattern, is not an instant
        "2026-02-30T12:00:00Z",  # February 30th
        "2026-00-01T12:00:00Z",  # month zero
    ],
)
def test_pattern_valid_but_impossible_instants_raise_the_domain_error(value: str) -> None:
    """The regex accepts shapes that are not real dates; those must not escape as ValueError."""
    with pytest.raises(TimestampError):
        parse_rfc3339_utc(value)
