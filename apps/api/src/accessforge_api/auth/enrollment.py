"""Runner enrollment.

A desktop runner has to get credentials somehow, and the shape of that exchange decides what an
attacker gets from stealing any one piece:

* The **enrollment token is single-use and short-lived.** Redemption is a conditional UPDATE, so the
  database decides which of two concurrent redemptions wins rather than the application racing.
* Enrollment produces a **device record, not run authority.** A permanent device secret is
  deliberately not equivalent to an active run authorization — that is granted per run, elsewhere.
* **Admission and credential validity revoke independently.** A device can be barred from new work
  while its credential stays valid (so it can still report a stop acknowledgement), or its
  credential can be killed without forgetting the device.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

# Short by design. An enrollment token is used once, minutes after it is created, by someone sitting
# at the machine being enrolled.
ENROLLMENT_LIFETIME = timedelta(minutes=15)


class EnrollmentError(Exception):
    """Enrollment was refused."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class IssuedEnrollment:
    credential_id: str
    token: str
    expires_at: datetime


def create_enrollment(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    created_by: str,
    now: datetime | None = None,
) -> IssuedEnrollment:
    """Mint a single-use enrollment token.

    The caller is responsible for having checked INFRASTRUCTURE_OPERATE first; this function does
    not re-derive authority, and its signature makes that explicit by demanding the acting user id.
    """
    moment = now or datetime.now(UTC)
    credential_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    expires_at = moment + ENROLLMENT_LIFETIME

    conn.execute(
        """
        INSERT INTO enrollment_credential
            (id, workspace_id, token_hash, created_by, created_at, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (credential_id, workspace_id, _hash(token), created_by, moment, expires_at),
    )
    return IssuedEnrollment(credential_id=credential_id, token=token, expires_at=expires_at)


@dataclass(frozen=True, slots=True)
class EnrolledDevice:
    device_id: str
    workspace_id: str


def redeem_enrollment(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    token: str,
    label: str,
    platform: str,
    now: datetime | None = None,
) -> EnrolledDevice:
    """Redeem an enrollment token exactly once and register the device.

    The UPDATE carries every validity condition in its WHERE clause, so the database resolves
    concurrency: two simultaneous redemptions of the same token produce one winner and one
    ``EnrollmentError``, with no application-level locking and no window where both see the token as
    unredeemed.
    """
    moment = now or datetime.now(UTC)
    device_id = str(uuid.uuid4())

    row = conn.execute(
        """
        UPDATE enrollment_credential
        SET redeemed_at = %s, redeemed_by_device = %s
        WHERE token_hash = %s
          AND redeemed_at IS NULL
          AND revoked_at IS NULL
          AND expires_at > %s
        RETURNING id, workspace_id
        """,
        (moment, device_id, _hash(token), moment),
    ).fetchone()

    if row is None:
        # Unknown, already redeemed, revoked and expired are one message: distinguishing them would
        # confirm to a caller that a token was once real.
        raise EnrollmentError("enrollment token is not valid")

    workspace_id = str(row["workspace_id"])
    conn.execute(
        """
        INSERT INTO runner_device (id, workspace_id, label, platform, enrolled_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (device_id, workspace_id, label, platform, moment),
    )
    return EnrolledDevice(device_id=device_id, workspace_id=workspace_id)


def revoke_device_admission(
    conn: psycopg.Connection[dict[str, Any]], *, device_id: str, now: datetime | None = None
) -> None:
    """Bar a device from being admitted to new work.

    Its credential stays valid on purpose, so the device can still report a stop acknowledgement for
    work already in flight. Killing the credential instead would leave a run permanently ambiguous.
    """
    conn.execute(
        "UPDATE runner_device SET admission_revoked_at = %s WHERE id = %s "
        "AND admission_revoked_at IS NULL",
        (now or datetime.now(UTC), device_id),
    )


def revoke_device_credential(
    conn: psycopg.Connection[dict[str, Any]], *, device_id: str, now: datetime | None = None
) -> None:
    """Invalidate a device's credential without forgetting the device."""
    conn.execute(
        "UPDATE runner_device SET credential_revoked_at = %s WHERE id = %s "
        "AND credential_revoked_at IS NULL",
        (now or datetime.now(UTC), device_id),
    )


def device_may_be_admitted(conn: psycopg.Connection[dict[str, Any]], *, device_id: str) -> bool:
    """Whether a device may take on new work right now.

    Both revocations block admission; only admission revocation leaves the credential usable for
    winding down.
    """
    row = conn.execute(
        """
        SELECT admission_revoked_at, credential_revoked_at
        FROM runner_device
        WHERE id = %s
        """,
        (device_id,),
    ).fetchone()
    if row is None:
        return False
    return row["admission_revoked_at"] is None and row["credential_revoked_at"] is None
