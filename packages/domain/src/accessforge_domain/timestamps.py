"""RFC3339 UTC timestamp parsing and comparison.

Authority decisions turn on "is this expired yet", so how timestamps compare is a security
question, not a formatting one.

An earlier version compared RFC3339 strings lexicographically, on the reasoning that UTC "Z"
timestamps sort correctly as text. They do — but only when both sides have the same lexical
shape, and the wire schema deliberately permits optional fractional seconds. Given

    expires_at = "2026-09-09T12:00:00Z"
    now        = "2026-09-09T12:00:00.000001Z"

the strings first differ at "." (0x2E) versus "Z" (0x5A). Since "." sorts lower, `now >=
expires_at` is False and an approval that expired a microsecond ago is reported as still valid —
failing open, against INV-08.

That is not a contrived input: `datetime.now(UTC).isoformat()` emits the fractional part only when
the microsecond field is non-zero, so a service mixing it with any whole-second timestamp produces
exactly this mismatch, intermittently.

Timestamps are therefore parsed before comparison, and the format is enforced here rather than
assumed from upstream schema validation. This package is pure domain logic that can be called
directly, so it defends its own invariants.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

# Matches the rfc3339Utc definition in packages/contracts/schemas/common.schema.json. UTC only:
# a local offset would reintroduce the comparison hazard this module exists to remove.
_RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


class TimestampError(ValueError):
    """A timestamp is not a well-formed RFC3339 UTC instant."""


def parse_rfc3339_utc(value: str, *, field: str = "timestamp") -> datetime:
    """Parse an RFC3339 UTC timestamp, or raise.

    Rejects local offsets, naive timestamps and anything malformed. Callers get a comparable
    instant rather than text whose ordering depends on incidental formatting.
    """
    if not isinstance(value, str) or not _RFC3339_UTC.match(value):
        raise TimestampError(
            f"{field} must be an RFC3339 UTC timestamp ending in Z "
            f"(for example 2026-09-09T12:00:00Z or 2026-09-09T12:00:00.000001Z), got {value!r}"
        )
    try:
        # The pattern above accepts shapes that are not real instants, such as month 13 or
        # 30 February. Those must surface as TimestampError, not a raw ValueError.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TimestampError(f"{field} is not a valid instant: {value!r} ({exc})") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(None):
        raise TimestampError(f"{field} must be UTC, got {value!r}")
    return parsed


def is_expired(*, now: str, expires_at: str) -> bool:
    """Whether ``expires_at`` has been reached at ``now``.

    Expiry is inclusive of the boundary: an authorization is not valid *at* the instant it
    expires. Both sides are parsed, so a difference in fractional-second precision cannot change
    the answer.
    """
    return parse_rfc3339_utc(now, field="now") >= parse_rfc3339_utc(expires_at, field="expires_at")


def is_after(*, later: str, earlier: str) -> bool:
    """Whether ``later`` is strictly after ``earlier``, both parsed."""
    return parse_rfc3339_utc(later) > parse_rfc3339_utc(earlier)
