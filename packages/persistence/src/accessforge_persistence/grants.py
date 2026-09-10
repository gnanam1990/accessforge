"""Standing execution grants: create, read, revoke, and revalidate after a restore.

A grant is the envelope inside which a schedule may keep requesting runs without a person approving
each one. It is the only authority in this system that acts while nobody is watching, which is why
almost everything here is about narrowing it rather than using it.

**A grant authorizes runs and nothing else.** Not a patch, not a publication. Scopes do not nest:
`RUN_EFFECTS` is not a weaker `PATCH_APPLY`, it is a different authorization about a different
action. `schedules.assert_grant_cannot_authorize` states it as a refusal so a caller reaching for a
grant to justify something else finds a check rather than an absence.

**Revocation is a revision, not a delete.** A deleted grant is indistinguishable from one that never
existed, and the question an auditor asks after an incident is "what was this allowed to do, and
when did that stop" -- which a missing row cannot answer.

**A restored grant is unusable until a person says otherwise.** Module 27's reconciliation sets
`revalidation_required` on every grant a restore brings back, because a grant revoked an hour after
the snapshot is live in the backup and revoked in the world, and nothing in that data distinguishes
the two. `revalidate` is the door out of that state -- and it existed nowhere when the flag was
introduced, which made every restored grant permanently dead. The flag is cleared by a named person
against a revision they have read, never by a background process and never as a side effect of
anything else.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

from accessforge_domain.authority import ExecutionGrant
from accessforge_domain.timestamps import to_rfc3339_utc

#: Hard ceilings on what a single standing grant may authorize, applied at creation.
#:
#: Not configuration. A grant is the authority that acts unattended, so the worst case is not "a
#: person approved too much" but "a person approved something reasonable and a bug multiplied it".
#: These bound the blast radius of the whole mechanism; a deployment that needs more than this needs
#: a person in the loop, not a larger number.
MAX_ACTION_BUDGET = 10_000
MAX_WALL_SECONDS = 24 * 60 * 60
MAX_GRANT_DAYS = 365


class GrantError(Exception):
    """A grant operation was refused."""


class NoSuchGrant(GrantError):
    """No grant with that id is visible in this workspace."""


class StaleGrantRevision(GrantError):
    """The caller's revision is not the current one, so their decision was about older state."""


@dataclass(frozen=True, slots=True)
class StoredGrant:
    """A grant row, plus the two facts that are about the row rather than the authority."""

    grant: ExecutionGrant
    created_at: str
    revoked_at: str | None
    revalidation_required: bool
    revalidated_at: str | None
    revalidated_by: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "grantId": self.grant.grant_id,
            "projectId": self.grant.project_id,
            "environment": self.grant.environment,
            "allowedJourneyVersionIds": sorted(self.grant.allowed_journey_version_ids),
            "allowedPolicyVersionIds": sorted(self.grant.allowed_policy_version_ids),
            "permittedEffects": sorted(self.grant.permitted_effects),
            "actionBudget": self.grant.action_budget,
            "wallTimeBudgetSeconds": self.grant.wall_time_budget_seconds,
            "expiresAt": self.grant.expires_at,
            "revision": self.grant.revision,
            "revoked": self.grant.revoked,
            "revokedAt": self.revoked_at,
            "createdAt": self.created_at,
            "revalidationRequired": self.revalidation_required,
            "revalidatedAt": self.revalidated_at,
            "revalidatedBy": self.revalidated_by,
            "usable": self.usable_detail is None,
            "unusableBecause": self.usable_detail,
            "authorizes": (
                "Runs of the listed journey versions in the named environment, and nothing else. "
                "A standing grant never authorizes a patch or a publication, however many runs it "
                "has already permitted."
            ),
        }

    @property
    def usable_detail(self) -> str | None:
        """Why this grant cannot currently be used, or None.

        Computed from the same `check_usable` the dispatch path calls, rather than re-derived here.
        A listing that decided usability by its own rules would eventually disagree with the code
        that enforces it, and the disagreement would show up as a grant the UI calls usable and
        every run refuses.
        """
        from accessforge_domain.authority import AuthorityError

        try:
            self.grant.check_usable(now=to_rfc3339_utc(datetime.now(UTC)))
        except AuthorityError as exc:
            return str(exc)
        return None


def _row_to_stored(row: dict[str, Any]) -> StoredGrant:
    return StoredGrant(
        grant=ExecutionGrant(
            grant_id=str(row["id"]),
            workspace_id=str(row["workspace_id"]),
            project_id=str(row["project_id"]),
            environment=str(row["environment"]),
            allowed_journey_version_ids=frozenset(row["allowed_journey_versions"]),
            allowed_policy_version_ids=frozenset(row["allowed_policy_versions"]),
            permitted_effects=frozenset(row["permitted_effects"]),
            action_budget=int(row["action_budget"]),
            wall_time_budget_seconds=int(row["wall_time_budget_seconds"]),
            expires_at=to_rfc3339_utc(row["expires_at"]),
            revision=int(row["revision"]),
            revoked=row["revoked_at"] is not None,
            revalidation_required=bool(row["revalidation_required"]),
        ),
        created_at=to_rfc3339_utc(row["created_at"]),
        revoked_at=to_rfc3339_utc(row["revoked_at"]) if row["revoked_at"] else None,
        revalidation_required=bool(row["revalidation_required"]),
        revalidated_at=(to_rfc3339_utc(row["revalidated_at"]) if row["revalidated_at"] else None),
        revalidated_by=str(row["revalidated_by"]) if row["revalidated_by"] else None,
    )


_COLUMNS = (
    "id, workspace_id, project_id, environment, allowed_journey_versions, "
    "allowed_policy_versions, permitted_effects, action_budget, wall_time_budget_seconds, "
    "revision, created_at, expires_at, revoked_at, revalidation_required, revalidated_at, "
    "revalidated_by"
)


def create_grant(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    project_id: str,
    environment: str,
    allowed_journey_version_ids: list[str],
    allowed_policy_version_ids: list[str],
    permitted_effects: list[str],
    action_budget: int,
    wall_time_budget_seconds: int,
    expires_at: str,
    now: datetime | None = None,
) -> StoredGrant:
    """Mint a standing grant, bounded on every axis.

    There is no unbounded option anywhere in this signature, and that is deliberate: a NULL meaning
    "unlimited" on a grant that acts unattended is an open-ended licence to drive somebody's
    desktop. Expiry is required for the same reason -- a standing authorization that never lapses
    is one nobody revisits.
    """
    moment = now or datetime.now(UTC)

    if not allowed_journey_version_ids:
        raise GrantError(
            "a grant must name at least one journey version. A grant allowing none authorizes "
            "nothing and would sit in the list looking like permission."
        )
    if not allowed_policy_version_ids:
        raise GrantError("a grant must name at least one policy version")
    if not 0 < action_budget <= MAX_ACTION_BUDGET:
        raise GrantError(
            f"actionBudget must be between 1 and {MAX_ACTION_BUDGET}. This bounds the whole "
            "mechanism rather than one grant: the risk is not an over-generous approval, it is a "
            "reasonable approval multiplied by a bug in something that runs unattended."
        )
    if not 0 < wall_time_budget_seconds <= MAX_WALL_SECONDS:
        raise GrantError(f"wallTimeBudgetSeconds must be between 1 and {MAX_WALL_SECONDS}")

    expiry = _parse_expiry(expires_at)
    if expiry <= moment:
        raise GrantError(
            "expiresAt is in the past; a grant that has already lapsed authorizes work "
            "nobody can do"
        )
    # total_seconds(), not .days: `timedelta.days` truncates, so 365 days and 23 hours reads as 365
    # and slips past a 365-day ceiling. A bound that can be exceeded by rounding is not a bound.
    if (expiry - moment).total_seconds() > MAX_GRANT_DAYS * 86_400:
        raise GrantError(
            f"a grant may not run longer than {MAX_GRANT_DAYS} days. A standing authorization with "
            "a distant expiry is one nobody revisits, and revisiting it is the only thing that "
            "keeps it accurate."
        )

    grant_id = str(uuid.uuid4())
    row = conn.execute(
        f"""
        INSERT INTO execution_grant
            (id, workspace_id, project_id, environment, allowed_journey_versions,
             allowed_policy_versions, permitted_effects, action_budget,
             wall_time_budget_seconds, revision, created_at, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
        RETURNING {_COLUMNS}
        """,  # noqa: S608 - _COLUMNS is a module constant, not caller input
        (
            grant_id,
            workspace_id,
            project_id,
            environment,
            list(allowed_journey_version_ids),
            list(allowed_policy_version_ids),
            list(permitted_effects),
            action_budget,
            wall_time_budget_seconds,
            moment,
            expiry,
        ),
    ).fetchone()
    if row is None:  # pragma: no cover - RETURNING always yields on a successful insert
        raise GrantError("the grant was not created")
    return _row_to_stored(row)


def _parse_expiry(value: str) -> datetime:
    from accessforge_domain.timestamps import parse_rfc3339_utc

    return parse_rfc3339_utc(value, field="expiresAt")


def load_grant(
    conn: psycopg.Connection[dict[str, Any]], *, grant_id: str, for_update: bool = False
) -> StoredGrant:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM execution_grant WHERE id = %s"  # noqa: S608 - module constant
        + (" FOR UPDATE" if for_update else ""),
        (grant_id,),
    ).fetchone()
    if row is None:
        raise NoSuchGrant("no such execution grant is available to you")
    return _row_to_stored(row)


def list_grants(
    conn: psycopg.Connection[dict[str, Any]], *, workspace_id: str, limit: int = 50
) -> list[StoredGrant]:
    """Every grant, revoked ones included.

    Revoked grants stay in the listing because the question after an incident is "what was allowed
    to run, and when did that stop" -- and a listing that hid them would answer only the first half.
    """
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM execution_grant "  # noqa: S608 - module constant
        "WHERE workspace_id = %s ORDER BY created_at DESC, id LIMIT %s",
        (workspace_id, limit),
    ).fetchall()
    return [_row_to_stored(r) for r in rows]


def revoke_grant(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    grant_id: str,
    expected_revision: int,
    now: datetime | None = None,
) -> StoredGrant:
    """Withdraw a grant. Idempotent, revisioned, and never a delete.

    Idempotent because the operator most likely to revoke twice is the one who is not sure the first
    attempt landed, and that is exactly when a second call must not be an error. Revisioned because
    revoking a grant somebody widened while you were reading it is a decision about state you never
    saw.
    """
    moment = now or datetime.now(UTC)
    stored = load_grant(conn, grant_id=grant_id, for_update=True)

    if stored.grant.revision != expected_revision:
        raise StaleGrantRevision(
            f"this grant is at revision {stored.grant.revision} and you supplied "
            f"{expected_revision}; it changed since you read it"
        )
    if stored.revoked_at is not None:
        # Already revoked. Returned rather than refused: the revision is unchanged, so a retry of a
        # request whose response was lost gets the same answer as the original.
        return stored

    row = conn.execute(
        f"""
        UPDATE execution_grant
           SET revoked_at = %s, revision = revision + 1
         WHERE id = %s
        RETURNING {_COLUMNS}
        """,  # noqa: S608 - module constant
        (moment, grant_id),
    ).fetchone()
    if row is None:  # pragma: no cover - the row was locked above
        raise NoSuchGrant("no such execution grant is available to you")
    return _row_to_stored(row)


def revalidate_grant(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    grant_id: str,
    expected_revision: int,
    revalidated_by: str,
    now: datetime | None = None,
) -> StoredGrant:
    """Confirm that a grant a restore brought back is still authorized.

    The door out of `revalidation_required`. When module 27 introduced that flag it introduced no
    way to clear it, so a restored grant was permanently unusable -- a fail-closed control with no
    door, which is a different bug from a missing control and a worse one, because the system looks
    like it is working.

    Three properties, each of which is the reason a background job cannot do this:

    * **A person.** `revalidated_by` is a user id and it is recorded. "Somebody confirmed this" with
      no name attached is not a confirmation.
    * **Against a revision they read.** A grant widened between the operator reading it and clearing
      the flag is a grant they did not actually approve.
    * **Only when it is required.** Revalidating a grant that was never restored is meaningless, and
      accepting it would make the audit trail carry confirmations of nothing.
    """
    moment = now or datetime.now(UTC)
    stored = load_grant(conn, grant_id=grant_id, for_update=True)

    if stored.grant.revision != expected_revision:
        raise StaleGrantRevision(
            f"this grant is at revision {stored.grant.revision} and you supplied "
            f"{expected_revision}; it changed since you read it, so your confirmation is about "
            "an authorization that no longer exists in that form"
        )
    if stored.revoked_at is not None:
        raise GrantError(
            "this grant is revoked. Revalidation confirms that a restored grant is still "
            "authorized; it does not bring a withdrawn one back, which would turn a restore into a "
            "way to undo a revocation."
        )
    if not stored.revalidation_required:
        raise GrantError(
            "this grant does not require revalidation. Accepting the confirmation anyway would "
            "record somebody vouching for something nobody had questioned."
        )

    row = conn.execute(
        f"""
        UPDATE execution_grant
           SET revalidation_required = false,
               revalidated_at = %s,
               revalidated_by = %s,
               revision = revision + 1
         WHERE id = %s
        RETURNING {_COLUMNS}
        """,  # noqa: S608 - module constant
        (moment, revalidated_by, grant_id),
    ).fetchone()
    if row is None:  # pragma: no cover - the row was locked above
        raise NoSuchGrant("no such execution grant is available to you")
    return _row_to_stored(row)
