"""Making a restored database safe to serve from.

A restore brings back a moment. Everything that was true at that moment is true again — including
the leases, the sessions, the enrollment tokens, the queued jobs and the unpublished outbox rows —
and almost none of it is true *now*. A system that came back up and carried on would:

* hand a desktop to a supervisor whose process died an hour ago, because its lease looks live;
* accept a session token somebody revoked after the snapshot was taken;
* redeliver outbox messages for work that has already happened;
* re-dispatch an action whose result was lost, which is the one thing INV-09 forbids;
* honour an execution grant that was revoked between the snapshot and the restore.

So a restored database is not served from until it has been **reconciled**. Reconciliation is
deliberately destructive in one direction only: it invalidates authority and preserves evidence. It
revokes every session, fences every lease, quarantines every ambiguous attempt, and marks queued
work for re-derivation from database state rather than from queue contents. It changes no run's
status, no verdict, no artifact and no audit row.

Two things it must never do, and the tests are mostly about these:

**It must not resurrect revoked authority.** A grant revoked after the snapshot is revoked in the
world and live in the backup, and the backup is the one being restored. Reconciliation cannot know
about the revocation — it is not in the data — so it treats *every* restored grant as unverified
and requires explicit revalidation before dispatch. Fail-closed is the only available answer.

**It must not let a terminal record change.** Terminal runs are immutable by trigger, and a restore
that "tidied" an interrupted run into a completed one would be a false PASS produced by an operator
holding a backup.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg


class RestoreError(RuntimeError):
    """Reconciliation could not complete."""


class CannotSeeEveryWorkspace(RestoreError):
    """The connection cannot see the rows reconciliation is about to change.

    Every tenant-owned table has `FORCE ROW LEVEL SECURITY`. A connection with no workspace scope
    sees **zero** rows of them — so an `UPDATE` that should fence every lease in the system affects
    nothing, reports success, and leaves every restored lease live. That is the worst possible
    outcome of this module: an operator following the runbook, seeing no error, and serving a
    database full of valid credentials.

    So the role is checked before anything is touched. Reconciliation is an operator action
    performed with the same elevated credentials that ran the restore, and requiring them here makes
    the silent no-op impossible rather than unlikely.
    """


def assert_can_reconcile(conn: psycopg.Connection[dict[str, Any]]) -> None:
    row = conn.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if row is None or not (bool(row["rolsuper"]) or bool(row["rolbypassrls"])):
        raise CannotSeeEveryWorkspace(
            "reconciliation must run as a role that bypasses row-level security. The connected "
            "role does not, so it sees none of the leases, runners or jobs it is meant to "
            "invalidate — every statement below would report success and change nothing."
        )


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """What reconciliation did, in numbers an operator can check against the runbook."""

    sessions_revoked: int
    leases_fenced: int
    runners_quarantined: int
    enrollment_tokens_expired: int
    jobs_released: int
    outbox_messages_suppressed: int
    attempts_quarantined: int
    grants_requiring_revalidation: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"revoked {self.sessions_revoked} sessions, expired "
            f"{self.enrollment_tokens_expired} enrollment tokens, fenced {self.leases_fenced} "
            f"leases, quarantined {self.runners_quarantined} runners and "
            f"{self.attempts_quarantined} ambiguous attempts, released {self.jobs_released} jobs "
            f"and suppressed {self.outbox_messages_suppressed} undelivered messages. "
            f"{len(self.grants_requiring_revalidation)} execution grants require revalidation "
            "before anything may be dispatched under them."
        )


RECONCILIATION_MARKER = "restore-reconciliation"

UNIDENTIFIED_RESTORE = "unidentified"
"""The restore identifier used when a caller supplies none.

Callers that pass nothing get once-only behaviour scoped to this name, which is the conservative
reading: two restores that both decline to identify themselves are refused rather than silently
treated as different. `scripts/restore.py` always passes the archive's own stream id.
"""


def assert_not_reconciled(
    conn: psycopg.Connection[dict[str, Any]], *, restore_id: str = UNIDENTIFIED_RESTORE
) -> None:
    """Refuse to reconcile *this* restore twice.

    Running it again on the same restore is not harmless: the second pass would fence leases that
    were legitimately granted after the first, and quarantine runners an operator had just reset.
    Once is a recovery step; twice is an outage.

    Scoped to the restore rather than to the database, and the difference is not academic. The first
    version of this checked for *any* `restore-reconciliation` row, which is a trap that springs
    much later: reconcile production once, and that audit row is in every backup taken afterwards --
    so the next real restore, possibly years on during an actual incident, would refuse to reconcile
    and leave the target holding live sessions and granted leases. The check has to distinguish "we
    already did this restore" from "this database has been restored before at some point", and only
    the first is a reason to stop.
    """
    row = conn.execute(
        "SELECT 1 FROM global_audit_event "
        " WHERE action = %s AND detail ->> 'restoreId' = %s LIMIT 1",
        (RECONCILIATION_MARKER, restore_id),
    ).fetchone()
    if row is not None:
        raise RestoreError(
            f"this database has already been reconciled for restore {restore_id!r}. Running it "
            "again would fence leases granted since, and quarantine runners somebody has already "
            "reset. A different restore of a different archive carries a different identifier and "
            "is not blocked by this."
        )


def reconcile(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    operator: str,
    restore_id: str = UNIDENTIFIED_RESTORE,
    now: datetime | None = None,
) -> Reconciliation:
    """Make a restored database safe to serve from.

    Requires a connection that can see every workspace — a role with `BYPASSRLS`, or a superuser.
    Reconciliation is workspace-independent: a restore restores every tenant, and a per-workspace
    pass would leave whichever tenants the operator forgot holding live credentials. It is also the
    same elevated credential the restore itself needed, so this asks for nothing new.

    `assert_can_reconcile` refuses anything weaker rather than running and changing nothing.

    `restore_id` identifies *this* restore -- `scripts/restore.py` passes the archive's own stream
    id. It scopes the once-only guarantee to the restore rather than to the database, because the
    audit row this writes travels in every subsequent backup, and a database-wide check would make
    the second real restore refuse to reconcile during an incident.
    """
    moment = now or datetime.now(UTC)
    assert_can_reconcile(conn)
    assert_not_reconciled(conn, restore_id=restore_id)

    # 1. Every session. A token minted before the snapshot is a token somebody may have revoked
    #    after it, and the revocation is not in this data.
    sessions = conn.execute(
        "UPDATE user_session SET revoked_at = %s WHERE revoked_at IS NULL", (moment,)
    ).rowcount

    # 2. Every unredeemed enrollment token, expired as of now. These grant desktop input authority
    #    and are single-use; one restored from a backup may already have been redeemed on a machine
    #    this database cannot see, and its redemption is not in this data. There is no `revoked_at`
    #    on the table — expiry is the mechanism it already has, and adding a second one would give
    #    the redemption path two conditions to check and one to forget.
    tokens = conn.execute(
        "UPDATE runner_enrollment_token SET expires_at = %s "
        "WHERE redeemed_at IS NULL AND expires_at > %s",
        (moment, moment),
    ).rowcount

    # 3. Every lease, released with a reason that says what happened. Not reassigned: the previous
    #    supervisor may still be running against a desktop this database can no longer see, and
    #    handing that desktop to a new attempt is how two actors drive one machine.
    leases = conn.execute(
        "UPDATE desktop_lease SET released_at = %s, release_reason = 'RESTORED_DATABASE' "
        "WHERE released_at IS NULL",
        (moment,),
    ).rowcount

    # 4. Every runner that held one, quarantined. A quarantine is released by a trusted reset and a
    #    fresh preflight, which is exactly the proof a restore cannot supply.
    runners = conn.execute(
        """
        UPDATE runner
           SET status = 'QUARANTINED',
               quarantine_reason = 'restored from a backup; the previous actor cannot be proven '
                                   'to have stopped',
               quarantined_at = %s
         WHERE revoked_at IS NULL AND status <> 'QUARANTINED'
        """,
        (moment,),
    ).rowcount

    # 5. Ambiguous attempts stay ambiguous. An action dispatched with no recorded result may have
    #    taken effect; a restore is not evidence that it did not (INV-09).
    ambiguous = conn.execute(
        """
        SELECT count(*) AS n FROM run
         WHERE status IN ('LEASED', 'RUNNING', 'FINALIZING')
           AND (unresolved_action
                OR (cancel_requested_at IS NOT NULL AND stop_acknowledged_at IS NULL))
        """
    ).fetchone()
    attempts_quarantined = conn.execute(
        """
        UPDATE run SET quarantined = true
         WHERE status IN ('LEASED', 'RUNNING', 'FINALIZING')
           AND (unresolved_action
                OR (cancel_requested_at IS NOT NULL AND stop_acknowledged_at IS NULL))
        """
    ).rowcount
    assert ambiguous is None or int(ambiguous["n"]) == attempts_quarantined

    # 6. Claimed jobs are released rather than deleted. A job is a database operation and re-reading
    #    state makes a repeat harmless; the claim is what is stale, not the work.
    jobs = conn.execute(
        "UPDATE job SET status = 'PENDING', claim_expires_at = NULL, claimed_by = NULL "
        "WHERE status = 'CLAIMED'"
    ).rowcount

    # 7. Undelivered outbox messages are marked published without being delivered. They announce
    #    state changes that already happened; a consumer re-reads authoritative state anyway, and
    #    redelivering a backup's worth of announcements is how a restored system tells the world a
    #    day of events is happening again right now.
    outbox = conn.execute(
        "UPDATE outbox_message SET published_at = %s WHERE published_at IS NULL", (moment,)
    ).rowcount

    # 8. Execution grants: marked unusable, not merely listed. An earlier version of this function
    #    returned the list and left the grants working, which is a report rather than a control --
    #    and a grant revoked an hour after the snapshot is live in this data and revoked in the
    #    world. The flag is what makes "requires revalidation" a thing a person has to clear rather
    #    than a line in a runbook, and `schedules.admit_occurrence` reads it from the row rather
    #    than from whatever object a caller hands it.
    grants = [
        str(r["id"])
        for r in conn.execute(
            "UPDATE execution_grant SET revalidation_required = true, "
            "    revalidated_at = NULL, revalidated_by = NULL "
            " WHERE revoked_at IS NULL "
            " RETURNING id"
        ).fetchall()
    ]
    grants.sort()

    conn.execute(
        """
        INSERT INTO global_audit_event
            (actor_user, actor_service, action, target_kind, target_id, outcome, occurred_at,
             detail)
        VALUES (NULL, %s, %s, 'database', NULL, 'ALLOWED', %s, %s)
        """,
        (
            operator,
            RECONCILIATION_MARKER,
            moment,
            json.dumps(
                {
                    "restoreId": restore_id,
                    "sessionsRevoked": sessions,
                    "enrollmentTokensExpired": tokens,
                    "leasesFenced": leases,
                    "runnersQuarantined": runners,
                    "attemptsQuarantined": attempts_quarantined,
                    "jobsReleased": jobs,
                    "outboxSuppressed": outbox,
                    "grantsRequiringRevalidation": len(grants),
                }
            ),
        ),
    )

    return Reconciliation(
        sessions_revoked=sessions,
        leases_fenced=leases,
        runners_quarantined=runners,
        enrollment_tokens_expired=tokens,
        jobs_released=jobs,
        outbox_messages_suppressed=outbox,
        attempts_quarantined=attempts_quarantined,
        grants_requiring_revalidation=grants,
    )


@dataclass(frozen=True, slots=True)
class SchemaState:
    applied: tuple[str, ...]
    latest: str | None


def schema_state(conn: psycopg.Connection[dict[str, Any]]) -> SchemaState:
    """Which migrations this database records as applied.

    Read from the database rather than from the tree, because the question a restore asks is what
    the *restored data* was written by — and a backup taken two releases ago carries a schema two
    releases old regardless of which code is about to connect to it.
    """
    rows = conn.execute("SELECT name FROM schema_migration ORDER BY name").fetchall()
    applied = tuple(str(r["name"]) for r in rows)
    return SchemaState(applied=applied, latest=applied[-1] if applied else None)


def restore_is_forward_compatible(
    conn: psycopg.Connection[dict[str, Any]], *, expected: tuple[str, ...]
) -> tuple[bool, str]:
    """Whether this restored database can be migrated forward by the current tree.

    Forward only. A restore whose schema is *ahead* of the code is refused rather than downgraded:
    a code rollback does not reverse a data migration, and running old code against a newer schema
    reads columns it does not know about as absent. The honest answer is to bring the code forward,
    not to take the data back.
    """
    state = schema_state(conn)
    unknown = [name for name in state.applied if name not in expected]
    if unknown:
        return False, (
            f"this database has migrations the current tree does not contain: "
            f"{', '.join(unknown)}. It was written by a newer release. A code rollback does not "
            "reverse a data migration, so the recovery is to bring the code forward rather than "
            "to serve this data from older code."
        )
    pending = [name for name in expected if name not in state.applied]
    if pending:
        return True, (
            f"this database is {len(pending)} migration(s) behind the current tree "
            f"({', '.join(pending)}). Migrating forward is supported; the reverse is not."
        )
    return True, "the restored schema matches the current tree exactly"
