"""Runner enrollment, desktop lease admission, the action journal and quarantine.

The one thing this module must get right is INV-10: at most one admitted active attempt per physical
interactive desktop. Everything else here is in service of that, or of the honesty requirements
around it — a lease that expired is not a stop, an acknowledgement from a superseded supervisor is
not an acknowledgement, and an action whose result never arrived is not a failure, it is an unknown.

Exclusivity is enforced by a partial unique index on
``(workspace_id, session_key) WHERE released_at IS NULL``. That is the whole mechanism: the database
refuses the second lease whatever the application believes, and an integration test proves it by
inserting a competing lease directly, bypassing every check in this module.

This started out as two layers — the index, plus a `pg_advisory_xact_lock` on the session key —
described as "the index is the guarantee, the lock is the ergonomics". Mutation testing showed that
was wrong twice over. Removing the *index* failed exactly one test, which is correct. Removing the
*lock* failed nothing at all, because :func:`admit_lease` already takes ``SELECT ... FOR UPDATE`` on
the runner row, and that row lock serializes every contender for the same desktop on its own. The
lock's supposed ergonomic value was not real either: the unique-violation branch converts a losing
race into the same :class:`SessionBusy` the lock's own check raises.

The one case the row lock does not cover is two runner rows sharing a desktop, reachable only by
revoking a runner mid-lease and re-enrolling the same screen. That window is now closed at its
source — :func:`revoke_runner` refuses while a lease is active — rather than papered over with a
lock no test could distinguish. Module 04 recorded that exact confound; keeping a second mechanism
nothing can tell apart from the first would have repeated it.

Requirements: FR-004, FR-005, FR-014, FR-015, FR-021.
Invariants: INV-01, INV-06, INV-07, INV-08, INV-09, INV-10, INV-13, INV-14.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from accessforge_domain import reducers
from accessforge_domain.authority import (
    AuthorityError,
    ChildAuthorization,
    ExecutionGrant,
    check_child_at_dispatch,
)
from accessforge_domain.runners import (
    AmbiguityReason,
    PhysicalSession,
    PreflightResult,
    QuarantineReason,
    RunnerProfile,
    assert_runner_transition,
)
from accessforge_domain.states import ApprovalScope, RunnerStatus
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc

from . import runs

#: A desktop lease is short. A long lease is a long window in which a partitioned supervisor can
#: still be typing while the server has moved on, and the cost of a short one is a heartbeat.
DEFAULT_LEASE_SECONDS = 300

#: Enrollment tokens are single use and measured in minutes, not days. The window only has to cover
#: an operator starting a supervisor on a desktop they are sitting at.
DEFAULT_ENROLLMENT_TOKEN_SECONDS = 600

#: Bounded queue. Backpressure is visible: a workspace at the limit is told so, rather than having
#: work accepted into a queue that will never drain.
MAX_QUEUED_RUNS_PER_WORKSPACE = 50

#: Namespace for deriving operation ids from what an operation is *about*.
#:
#: The outbox deduplicates on operation id, so a retried terminalization must present the same one:
#: a random uuid per attempt would publish a second `run.interrupted` message for the same
#: interruption, and a consumer would act on it twice. UUIDv5 makes the id a function of the action
#: or run it concerns, which is exactly the identity that must not change between retries.
_OPERATION_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def _operation_id(what: str) -> str:
    return str(uuid.uuid5(_OPERATION_NAMESPACE, what))


class RunnerError(Exception):
    """A runner operation was refused."""


class SessionBusy(RunnerError):
    """Another attempt already holds this physical desktop (INV-10)."""


class RunnerUnavailable(RunnerError):
    """No runner can satisfy this request, with a reason an operator can act on.

    Carries a machine-readable ``reason`` because the module prompt requires unsupported profiles to
    "return visible unavailable reasons, never silent fallback to virtual/browser-only execution".
    A fallback would produce a confident result from a browser that no screen-reader user ever used.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class QueueFull(RunnerError):
    """The workspace is at its queue limit."""


def _now(now: str | None) -> str:
    return now or to_rfc3339_utc(datetime.now(UTC))


def _digest_token(token: str) -> str:
    """Hash an enrollment token for storage.

    SHA-256 of the raw token rather than a password hash: this is a 256-bit random value with a
    ten-minute life, not a human-chosen secret, so there is no dictionary to stretch against. The
    property that matters is that the database never holds the usable credential.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- enrollment ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EnrollmentToken:
    """A freshly issued enrollment credential.

    ``token`` is returned once, here, and is never stored or recoverable. A caller that loses it
    issues another; there is no endpoint that reveals it, because an endpoint that could reveal it
    would be a way to take over a tenant's desktop.
    """

    token_id: str
    token: str
    expires_at: str


def issue_enrollment_token(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    created_by: str,
    ttl_seconds: int = DEFAULT_ENROLLMENT_TOKEN_SECONDS,
    now: str | None = None,
) -> EnrollmentToken:
    moment = _now(now)
    if ttl_seconds < 1 or ttl_seconds > 3600:
        raise RunnerError(
            "an enrollment token lives between one second and one hour; a long-lived enrollment "
            "credential is a standing right to join a tenant as a runner"
        )
    token = secrets.token_urlsafe(32)
    token_id = str(uuid.uuid4())
    expires_at = to_rfc3339_utc(
        parse_rfc3339_utc(moment, field="now") + timedelta(seconds=ttl_seconds)
    )
    conn.execute(
        """
        INSERT INTO runner_enrollment_token
            (id, workspace_id, token_digest, expires_at, created_by, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (token_id, workspace_id, _digest_token(token), expires_at, created_by, moment),
    )
    return EnrollmentToken(token_id=token_id, token=token, expires_at=expires_at)


@dataclass(frozen=True, slots=True)
class EnrolledRunner:
    runner_id: str
    session_key: str
    status: RunnerStatus
    profile_digest: str


def enroll_runner(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    token: str,
    name: str,
    session: PhysicalSession,
    profile: RunnerProfile,
    now: str | None = None,
) -> EnrolledRunner:
    """Redeem a single-use token and register one physical desktop.

    A new runner is PREFLIGHT_REQUIRED, never READY. Enrollment establishes *identity*; preflight
    establishes *readiness*, and the module prompt is explicit that a self-asserted READY is not
    acceptable. There is no argument to this function that could make it return a ready runner.
    """
    moment = _now(now)
    if profile.platform != session.platform:
        raise RunnerError(
            f"profile platform {profile.platform!r} and session platform {session.platform!r} "
            "disagree; one of them is describing a different machine"
        )

    # Single use, enforced in the UPDATE rather than in a preceding SELECT: a check-then-act would
    # let two supervisors redeem one token concurrently, each having seen it unredeemed.
    redeemed = conn.execute(
        """
        UPDATE runner_enrollment_token
           SET redeemed_at = %s
         WHERE workspace_id = %s
           AND token_digest = %s
           AND redeemed_at IS NULL
           AND expires_at > %s
        RETURNING id
        """,
        (moment, workspace_id, _digest_token(token), moment),
    ).fetchone()
    if redeemed is None:
        # One message for all three causes on purpose: telling an unauthenticated caller which of
        # "unknown", "already used" and "expired" applies is telling it whether it guessed a real
        # token.
        raise RunnerError("enrollment token is unknown, already redeemed, or expired")

    runner_id = str(uuid.uuid4())
    try:
        conn.execute(
            """
            INSERT INTO runner
                (id, workspace_id, name, status, session_key, platform, device_id,
                 interactive_session_id, console, profile_digest, profile, created_at, updated_at)
            VALUES (%s, %s, %s, 'PREFLIGHT_REQUIRED', %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                runner_id,
                workspace_id,
                name,
                session.key,
                session.platform,
                session.device_id,
                session.interactive_session_id,
                session.console,
                profile.digest,
                json.dumps(
                    {
                        "browserName": profile.browser_name,
                        "browserVersion": profile.browser_version,
                        "keyboardLayout": profile.keyboard_layout,
                        "locale": profile.locale,
                        "platform": profile.platform,
                        "readerName": profile.reader_name,
                        "readerVersion": profile.reader_version,
                    }
                ),
                moment,
                moment,
            ),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise RunnerError(
            "this physical desktop session is already enrolled and not revoked. Two registrations "
            "for one screen would each believe they could be leased independently."
        ) from exc

    conn.execute(
        "UPDATE runner_enrollment_token SET redeemed_by_runner = %s WHERE id = %s",
        (runner_id, redeemed["id"]),
    )
    return EnrolledRunner(
        runner_id=runner_id,
        session_key=session.key,
        status=RunnerStatus.PREFLIGHT_REQUIRED,
        profile_digest=profile.digest,
    )


def revoke_runner(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    runner_id: str,
    now: str | None = None,
) -> None:
    """Take a desktop out of service permanently.

    The row is retained: it is referenced by leases and actions that are evidence. Revocation
    removes it from the partial unique index, so the same physical desktop can be enrolled afresh.

    Refused while a lease is active, and the reason is not tidiness. Revoking here would leave the
    old registration holding a live lease on a desktop that could then be enrolled again as a
    *different runner row* -- two rows, one screen, and the per-runner row lock that serializes
    concurrent admission no longer serializing anything, because the two contenders would lock
    different rows. The partial unique index on `session_key` still refuses the second lease, so
    the invariant holds, but the window has no business existing: an operator retiring a machine
    that is mid-run should stop the run first. Reset is the operation for a desktop that is stuck.
    """
    moment = _now(now)
    active = conn.execute(
        """
        SELECT id, run_id, epoch FROM desktop_lease
         WHERE runner_id = %s AND released_at IS NULL
        """,
        (runner_id,),
    ).fetchone()
    if active is not None:
        raise RunnerError(
            f"this runner holds an active lease at epoch {active['epoch']} for run "
            f"{active['run_id']}. Revoking now would retire the registration while the desktop is "
            "still leased. Terminalize the run, or reset the runner, and then revoke."
        )

    updated = conn.execute(
        """
        UPDATE runner SET revoked_at = %s, status = 'OFFLINE', updated_at = %s,
               revision = revision + 1,
               quarantine_reason = NULL, quarantined_at = NULL
         WHERE id = %s AND revoked_at IS NULL
        RETURNING id
        """,
        (moment, moment, runner_id),
    ).fetchone()
    if updated is None:
        raise RunnerError("no such runner in this workspace, or it is already revoked")


# --- preflight -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PreflightRecord:
    preflight_id: str
    successful: bool
    runner_status: RunnerStatus
    refusal_summary: str


def record_preflight(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    runner_id: str,
    result: PreflightResult,
    now: str | None = None,
) -> PreflightRecord:
    """Record a preflight submission and let it decide the runner's status.

    ``successful`` is computed here from the submitted checks. The runner has no way to set it, and
    a submission that omits a required check is unsuccessful rather than partially successful.

    Profile drift is checked explicitly rather than left to the runner's own
    ``READER_VERSION_MATCHES_PROFILE`` check, because a supervisor that lies about its reader
    version would also lie about whether its version matches. The server holds the enrolled profile
    and compares the observed values against it.
    """
    moment = _now(now)
    runner = conn.execute(
        "SELECT id, status, lease_epoch, profile_digest, profile, revoked_at, revision "
        "FROM runner WHERE id = %s FOR UPDATE",
        (runner_id,),
    ).fetchone()
    if runner is None:
        raise RunnerError("no such runner in this workspace")
    if runner["revoked_at"] is not None:
        raise RunnerError("this runner has been revoked and cannot become ready")

    successful = result.is_successful()
    refusal = result.refusal_summary()
    drift = _profile_drift(dict(runner["profile"]), result)
    if drift:
        successful = False
        refusal = "; ".join(filter(None, (refusal, drift)))
    if result.runner_profile_digest != str(runner["profile_digest"]):
        successful = False
        refusal = "; ".join(
            filter(
                None,
                (
                    refusal,
                    "the preflight names a different runner profile digest than the one this "
                    "runner enrolled with, so it is evidence about a different environment",
                ),
            )
        )

    preflight_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO runner_preflight
            (id, workspace_id, runner_id, lease_epoch, runner_profile_digest,
             environment_config_digest, manifest_digest, successful, refusal_summary, checks,
             observed, observed_at, recorded_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            preflight_id,
            workspace_id,
            runner_id,
            int(runner["lease_epoch"]),
            result.runner_profile_digest,
            result.environment_config_digest,
            result.manifest_digest,
            successful,
            refusal,
            json.dumps({str(k): str(v) for k, v in result.checks.items()}),
            json.dumps(
                {
                    "browserVersion": result.observed_browser_version,
                    "desktopSessionKey": result.desktop_session_key,
                    "keyboardLayout": result.observed_keyboard_layout,
                    "locale": result.observed_locale,
                    "readerVersion": result.observed_reader_version,
                }
            ),
            result.observed_at,
            moment,
        ),
    )

    current = RunnerStatus(str(runner["status"]))
    if successful:
        target = RunnerStatus.READY
        assert_runner_transition(current, target, preflight=result)
        conn.execute(
            "UPDATE runner SET status = 'READY', quarantine_reason = NULL, quarantined_at = NULL, "
            "updated_at = %s, revision = revision + 1 WHERE id = %s",
            (moment, runner_id),
        )
        return PreflightRecord(preflight_id, True, RunnerStatus.READY, "")

    reason = QuarantineReason.PROFILE_DRIFTED if drift else QuarantineReason.PREFLIGHT_FAILED
    _quarantine(conn, runner_id=runner_id, reason=reason, now=moment)
    return PreflightRecord(preflight_id, False, RunnerStatus.QUARANTINED, refusal)


def _profile_drift(enrolled: dict[str, Any], result: PreflightResult) -> str:
    """Compare what was observed against what was enrolled, field by field.

    A reader or browser upgrade between enrollment and preflight changes what is announced and
    therefore what a run means. Locale and keyboard layout change which key chords reach the
    application at all.
    """
    comparisons = (
        ("reader version", enrolled.get("readerVersion"), result.observed_reader_version),
        ("browser version", enrolled.get("browserVersion"), result.observed_browser_version),
        ("locale", enrolled.get("locale"), result.observed_locale),
        ("keyboard layout", enrolled.get("keyboardLayout"), result.observed_keyboard_layout),
    )
    drifted = [
        f"{label} drifted from {expected!r} to {observed!r}"
        for label, expected, observed in comparisons
        if str(expected) != str(observed)
    ]
    return "; ".join(drifted)


def _quarantine(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    runner_id: str,
    reason: QuarantineReason,
    now: str,
) -> None:
    """Quarantine a desktop. Reachable from every status, by design.

    The contract permits QUARANTINED from anywhere because the reasons for it — an expired lease, an
    ambiguous action, a failed reset — are discovered rather than planned, and a desktop that might
    still have a process typing into it must be fenceable from whatever state it is in.
    """
    conn.execute(
        """
        UPDATE runner
           SET status = 'QUARANTINED', quarantine_reason = %s, quarantined_at = %s,
               updated_at = %s, revision = revision + 1
         WHERE id = %s
        """,
        (str(reason), now, now, runner_id),
    )


# --- capability matching and backpressure --------------------------------------------------------


def match_runners(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    platform: str,
    reader_name: str,
    reader_version: str | None = None,
) -> list[dict[str, Any]]:
    """Find READY runners that actually satisfy a journey's requirements.

    Raises :class:`RunnerUnavailable` with a specific reason rather than returning an empty list,
    and the reasons are distinguished: "no runner for this reader" and "the only runner for it is
    quarantined" lead an operator to completely different actions. Returning an empty list would
    invite a caller to fall back to something that is not a screen reader, which is the single
    outcome this product must never produce (INV-02).
    """
    rows = conn.execute(
        """
        SELECT id, name, status, profile, quarantine_reason
          FROM runner
         WHERE revoked_at IS NULL
           AND platform = %s
           AND profile ->> 'readerName' = %s
        """,
        (platform, reader_name),
    ).fetchall()

    if not rows:
        raise RunnerUnavailable(
            "NO_RUNNER_FOR_READER",
            f"no runner is enrolled for {reader_name} on {platform}. This journey requires that "
            "reader; it cannot be run on another one, and it will not be run in a browser without "
            "a screen reader.",
        )

    if reader_version is not None:
        rows = [r for r in rows if str(dict(r["profile"]).get("readerVersion")) == reader_version]
        if not rows:
            raise RunnerUnavailable(
                "NO_RUNNER_FOR_READER_VERSION",
                f"no enrolled runner has {reader_name} {reader_version}; a different version "
                "announces differently and is not a substitute",
            )

    ready = [r for r in rows if str(r["status"]) == RunnerStatus.READY]
    if ready:
        return [dict(r) for r in ready]

    quarantined = [r for r in rows if str(r["status"]) == RunnerStatus.QUARANTINED]
    if quarantined:
        reasons = sorted({str(r["quarantine_reason"]) for r in quarantined})
        raise RunnerUnavailable(
            "ALL_MATCHING_RUNNERS_QUARANTINED",
            f"every runner for {reader_name} on {platform} is quarantined ({', '.join(reasons)}). "
            "A quarantined desktop is released by a trusted reset and a fresh preflight, not by "
            "waiting.",
        )

    statuses = sorted({str(r["status"]) for r in rows})
    raise RunnerUnavailable(
        "NO_READY_RUNNER",
        f"runners for {reader_name} on {platform} exist but none is ready ({', '.join(statuses)})",
    )


def assert_queue_capacity(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    limit: int = MAX_QUEUED_RUNS_PER_WORKSPACE,
) -> int:
    """Refuse new work once the queue is full, visibly.

    An unbounded queue is not generosity: a desktop runs one attempt at a time, so a thousand queued
    runs is a thousand promises the system cannot keep, made silently.
    """
    row = conn.execute("SELECT count(*) AS n FROM run WHERE status = 'QUEUED'").fetchone()
    queued = int(row["n"]) if row else 0
    if queued >= limit:
        raise QueueFull(
            f"{queued} runs are already queued and the limit is {limit}. A desktop executes one "
            "attempt at a time; accepting more would be a queue that cannot drain."
        )
    return queued


# --- lease admission -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AdmittedLease:
    lease_id: str
    runner_id: str
    session_key: str
    epoch: int
    deadline_at: str


def admit_lease(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    runner_id: str,
    run_id: str,
    attempt_id: str,
    ttl_seconds: int = DEFAULT_LEASE_SECONDS,
    now: str | None = None,
) -> AdmittedLease:
    """Admit exactly one attempt to one physical desktop.

    Must be called inside a transaction: the advisory lock is transaction-scoped, and the epoch
    increment and the lease insert have to commit together or a runner's epoch counter would advance
    without a lease to show for it.
    """
    moment = _now(now)
    if ttl_seconds < 1 or ttl_seconds > 3600:
        raise RunnerError("a desktop lease lives between one second and one hour")

    runner = conn.execute(
        "SELECT id, status, session_key, lease_epoch, revoked_at, quarantine_reason "
        "FROM runner WHERE id = %s FOR UPDATE",
        (runner_id,),
    ).fetchone()
    if runner is None:
        raise RunnerError("no such runner in this workspace")
    if runner["revoked_at"] is not None:
        raise RunnerError("this runner has been revoked")

    # The FOR UPDATE above is what serializes concurrent admission: contenders for this desktop
    # contend for this runner's row, and the loser reads the winner's committed state below.
    session_key = str(runner["session_key"])

    status = RunnerStatus(str(runner["status"]))
    if status is RunnerStatus.QUARANTINED:
        raise SessionBusy(
            f"this desktop is quarantined ({runner['quarantine_reason']}). It is not available for "
            "a new attempt until a trusted reset proves the previous activity cannot continue and "
            "a fresh preflight succeeds."
        )

    # The held lease is checked before the READY status, and the order matters for diagnosis. A
    # runner holding a lease is also BUSY, so checking the status first would refuse the second
    # attempt with "this runner is BUSY" -- true, but it describes the shadow rather than the thing.
    # The contention is what the caller needs to see: which run holds this desktop, at which epoch,
    # and until when.
    held = conn.execute(
        """
        SELECT id, run_id, epoch, deadline_at FROM desktop_lease
         WHERE session_key = %s AND released_at IS NULL
        """,
        (session_key,),
    ).fetchone()
    if held is not None:
        raise SessionBusy(
            f"attempt on run {held['run_id']} already holds this desktop at epoch {held['epoch']} "
            f"until {held['deadline_at']}. One physical session has at most one admitted attempt."
        )

    if status is not RunnerStatus.READY:
        raise RunnerError(
            f"a runner must be READY to be leased; this one is {status}. A runner becomes READY by "
            "submitting a successful preflight, not by asking."
        )

    epoch = int(runner["lease_epoch"]) + 1
    deadline_at = to_rfc3339_utc(
        parse_rfc3339_utc(moment, field="now") + timedelta(seconds=ttl_seconds)
    )
    lease_id = str(uuid.uuid4())
    try:
        conn.execute(
            """
            INSERT INTO desktop_lease
                (id, workspace_id, runner_id, session_key, run_id, attempt_id, epoch,
                 granted_at, deadline_at, heartbeat_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                lease_id,
                workspace_id,
                runner_id,
                session_key,
                run_id,
                attempt_id,
                epoch,
                moment,
                deadline_at,
                moment,
            ),
        )
    except psycopg.errors.UniqueViolation as exc:
        # Reached when the index catches a race the row lock did not, or when a caller bypassed this
        # function's checks. Either way the database, not this code, is the guarantee.
        raise SessionBusy(
            "another attempt was admitted to this physical desktop concurrently"
        ) from exc

    conn.execute(
        "UPDATE runner SET lease_epoch = %s, status = 'BUSY', updated_at = %s, "
        "revision = revision + 1 WHERE id = %s",
        (epoch, moment, runner_id),
    )

    # The run learns its epoch here, in the same transaction that grants the lease. Anywhere else
    # and there is a window in which a desktop is held by a run that does not know which session
    # holds it -- and a run whose epoch is wrong cannot have a stop acknowledged for it at all,
    # because acknowledgement is checked against exactly this number.
    state = runs.load_run(conn, run_id=run_id).state
    runs.apply_transition(
        conn,
        run_id=run_id,
        reducer=lambda s: reducers.admit_to_desktop(s, epoch=epoch, expected_revision=s.revision),
        operation_id=_operation_id(f"lease:{lease_id}"),
        topic="run.leased",
        expected_revision=state.revision,
        actor_service="runner-control-plane",
        audit_action="DESKTOP_LEASE_ADMITTED",
        now=parse_rfc3339_utc(moment, field="now"),
    )
    return AdmittedLease(
        lease_id=lease_id,
        runner_id=runner_id,
        session_key=session_key,
        epoch=epoch,
        deadline_at=deadline_at,
    )


def heartbeat_lease(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    lease_id: str,
    epoch: int,
    extend_seconds: int = DEFAULT_LEASE_SECONDS,
    now: str | None = None,
) -> str:
    """Extend a lease, but only for the supervisor that actually holds it.

    The epoch is a parameter rather than read from the row: a heartbeat that looked up the current
    epoch and then used it would let a stale supervisor refresh the lease of the one that superseded
    it, which is precisely backwards.
    """
    moment = _now(now)
    deadline_at = to_rfc3339_utc(
        parse_rfc3339_utc(moment, field="now") + timedelta(seconds=extend_seconds)
    )
    row = conn.execute(
        """
        UPDATE desktop_lease SET heartbeat_at = %s, deadline_at = %s
         WHERE id = %s AND epoch = %s AND released_at IS NULL
        RETURNING deadline_at
        """,
        (moment, deadline_at, lease_id, epoch),
    ).fetchone()
    if row is None:
        raise RunnerError(
            f"no active lease {lease_id} at epoch {epoch}; it has been released or superseded"
        )
    return deadline_at


# --- cancellation and stop proof -----------------------------------------------------------------


def request_cancellation(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    lease_id: str,
    cancellation_revision: int,
    now: str | None = None,
) -> None:
    """Record that cancellation was requested. This is not a terminal state.

    CONTRACTS section 4: while the acknowledgement is absent, display cancellation *requested*, not
    physically stopped. The run stays RUNNING. What changes is that no further action is admitted
    and no new lease will be granted — and that is recorded here so the supervisor's own gate can
    see it on its next poll.
    """
    moment = _now(now)
    row = conn.execute(
        """
        UPDATE desktop_lease
           SET cancel_requested_at = COALESCE(cancel_requested_at, %s),
               cancellation_revision = COALESCE(cancellation_revision, %s)
         WHERE id = %s AND released_at IS NULL
        RETURNING cancel_requested_at
        """,
        (moment, cancellation_revision, lease_id),
    ).fetchone()
    if row is None:
        raise RunnerError("no active lease to cancel")


def acknowledge_stop(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    lease_id: str,
    epoch: int,
    now: str | None = None,
) -> None:
    """Accept a stop acknowledgement, with three conditions, all of them necessary.

    The acknowledgement must be for the *current* epoch of this lease; cancellation must actually
    have been requested; and no action may still be unresolved. The third is the one that is easy to
    forget and expensive to get wrong — an acknowledgement while a TYPE_TEXT is in flight says the
    supervisor has stopped *asking*, not that the keystroke did not land.
    """
    moment = _now(now)
    lease = conn.execute(
        "SELECT id, epoch, cancel_requested_at, released_at FROM desktop_lease "
        "WHERE id = %s FOR UPDATE",
        (lease_id,),
    ).fetchone()
    if lease is None:
        raise RunnerError("no such lease in this workspace")
    if lease["released_at"] is not None:
        raise RunnerError("this lease has already been released")
    if int(lease["epoch"]) != epoch:
        raise RunnerError(
            f"stop acknowledgement is for epoch {epoch} but this lease is at epoch "
            f"{lease['epoch']}; an acknowledgement from a superseded supervisor says nothing about "
            "the current one"
        )
    if lease["cancel_requested_at"] is None:
        raise RunnerError("nothing was cancelled, so there is nothing to acknowledge")

    unresolved = conn.execute(
        "SELECT count(*) AS n FROM runner_action WHERE lease_id = %s AND result_at IS NULL",
        (lease_id,),
    ).fetchone()
    if unresolved is not None and int(unresolved["n"]) > 0:
        raise RunnerError(
            f"{unresolved['n']} dispatched action(s) are still unresolved. Accepting a stop now "
            "would record a clean cancellation over an action whose effect nobody knows."
        )

    conn.execute(
        """
        UPDATE desktop_lease
           SET stop_acknowledged_at = %s, stop_acknowledged_epoch = %s,
               released_at = %s, release_reason = 'STOP_ACKNOWLEDGED'
         WHERE id = %s
        """,
        (moment, epoch, moment, lease_id),
    )
    # A cleanly stopped desktop is still not READY: the next run needs a fresh preflight, because
    # the browser is on whatever page the cancelled run left it on.
    conn.execute(
        """
        UPDATE runner SET status = 'PREFLIGHT_REQUIRED', updated_at = %s, revision = revision + 1
         WHERE id = (SELECT runner_id FROM desktop_lease WHERE id = %s)
        """,
        (moment, lease_id),
    )


def may_cancel_immediately(conn: psycopg.Connection[dict[str, Any]], *, run_id: str) -> bool:
    """Whether a run can go straight to terminal CANCELLED.

    True only for a run that was never leased at all — provably never admitted to a desktop, so
    provably never able to have typed anything. Any run that held a lease needs acknowledgement,
    even if the lease has since been released: release is not proof that nothing happened.
    """
    row = conn.execute(
        "SELECT count(*) AS n FROM desktop_lease WHERE run_id = %s", (run_id,)
    ).fetchone()
    return row is not None and int(row["n"]) == 0


# --- the action journal --------------------------------------------------------------------------


def record_action_intent(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    lease_id: str,
    run_id: str,
    attempt_id: str,
    epoch: int,
    action_sequence: int,
    action: str,
    origin: str,
    key_chord: str | None = None,
    text_value: str | None = None,
    now: str | None = None,
) -> str:
    """Write the intent before anything reaches the operating system.

    The ordering is the whole mechanism. If the supervisor crashes after this commit and before the
    result, the row that exists says "we asked for this and do not know what happened" — which is
    the truth, and which is what makes INV-09's refusal to retry an informed decision rather than a
    guess. Without the row, the same crash would look like nothing ever happened.
    """
    moment = _now(now)
    action_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO runner_action
            (id, workspace_id, lease_id, run_id, attempt_id, epoch, action_sequence, action,
             key_chord, text_value, origin, intent_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            action_id,
            workspace_id,
            lease_id,
            run_id,
            attempt_id,
            epoch,
            action_sequence,
            action,
            key_chord,
            text_value,
            origin,
            moment,
        ),
    )
    return action_id


def mark_action_dispatched(
    conn: psycopg.Connection[dict[str, Any]], *, action_id: str, now: str | None = None
) -> None:
    moment = _now(now)
    row = conn.execute(
        "UPDATE runner_action SET dispatched_at = %s WHERE id = %s AND dispatched_at IS NULL "
        "RETURNING id",
        (moment, action_id),
    ).fetchone()
    if row is None:
        raise RunnerError(
            "this action was already dispatched. Dispatching twice is the blind replay INV-09 "
            "forbids: the first dispatch may have taken effect."
        )


def record_action_result(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    action_id: str,
    status: str,
    detail: str = "",
    now: str | None = None,
) -> None:
    if status not in {"SUCCEEDED", "FAILED"}:
        raise RunnerError(
            "an action result is SUCCEEDED or FAILED. An ambiguous action is recorded through "
            "mark_action_ambiguous, which also quarantines the desktop; making it an ordinary "
            "result value would let an unknown pass as a known one."
        )
    moment = _now(now)
    row = conn.execute(
        """
        UPDATE runner_action SET result_at = %s, result_status = %s, result_detail = %s
         WHERE id = %s AND result_at IS NULL
        RETURNING id
        """,
        (moment, status, detail, action_id),
    ).fetchone()
    if row is None:
        raise RunnerError("no unresolved action with that id; a result is recorded once")


def mark_action_ambiguous(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    action_id: str,
    reason: AmbiguityReason,
    detail: str = "",
    now: str | None = None,
) -> None:
    """Terminalize an action whose effect is unknown, and fence its desktop.

    This is the function that must never be tempting to skip. An ambiguous action quarantines the
    physical session, because the process that was asked to press a key may still be running and
    may still press it. The desktop is not handed to another run on the strength of a timeout.
    """
    moment = _now(now)
    row = conn.execute(
        """
        UPDATE runner_action
           SET result_at = %s, result_status = 'AMBIGUOUS', result_detail = %s,
               ambiguity_reason = %s
         WHERE id = %s AND result_at IS NULL
        RETURNING lease_id
        """,
        (moment, detail, str(reason), action_id),
    ).fetchone()
    if row is None:
        raise RunnerError("no unresolved action with that id")

    lease = conn.execute(
        """
        UPDATE desktop_lease
           SET released_at = COALESCE(released_at, %s),
               release_reason = COALESCE(release_reason, 'AMBIGUOUS_ACTION')
         WHERE id = %s
        RETURNING runner_id
        """,
        (moment, row["lease_id"]),
    ).fetchone()
    if lease is not None:
        _quarantine(
            conn,
            runner_id=str(lease["runner_id"]),
            reason=QuarantineReason.AMBIGUOUS_ACTION,
            now=moment,
        )


def unresolved_actions(
    conn: psycopg.Connection[dict[str, Any]], *, lease_id: str
) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT id, action_sequence, action, intent_at, dispatched_at FROM runner_action "
            "WHERE lease_id = %s AND result_at IS NULL ORDER BY action_sequence",
            (lease_id,),
        ).fetchall()
    ]


# --- expiry, reset and operator interfaces -------------------------------------------------------


def fence_expired_leases(
    conn: psycopg.Connection[dict[str, Any]], *, now: str | None = None
) -> list[str]:
    """Fence leases whose deadline has passed, and quarantine their desktops.

    The critical word is *fence*, not *reclaim*. TDD section 5: "The server must not reassign that
    session merely because a heartbeat expired: quarantine until explicit stop/reset/preflight
    proves the old actor cannot act." A partitioned supervisor with an expired server-side lease is
    not a stopped supervisor — it has its own monotonic deadline and it may be most of the way
    through a keystroke. Releasing the desktop to the next run here would put two attempts on one
    screen, which is the exact thing a lease exists to prevent.
    """
    moment = _now(now)
    rows = conn.execute(
        """
        UPDATE desktop_lease
           SET released_at = %s, release_reason = 'EXPIRED_WITHOUT_STOP_PROOF'
         WHERE released_at IS NULL AND deadline_at <= %s
        RETURNING id, runner_id
        """,
        (moment, moment),
    ).fetchall()
    for row in rows:
        _quarantine(
            conn,
            runner_id=str(row["runner_id"]),
            reason=QuarantineReason.LEASE_EXPIRED_WITHOUT_STOP_PROOF,
            now=moment,
        )
    return [str(r["id"]) for r in rows]


#: What an operator is told, in writing, before a reset runs. Stored with the reset record.
RESET_LOCAL_IMPACT_WARNING = (
    "A reset acts on one physical interactive desktop session. It closes the browser profile and "
    "restarts the supervisor and the screen reader within that session only. It does not kill "
    "processes outside the runner's own session, and it must not be performed on a desktop someone "
    "is using: anyone signed in to it will lose unsaved work in the affected applications."
)


@dataclass(frozen=True, slots=True)
class ResetOutcome:
    reset_id: str
    succeeded: bool
    runner_status: RunnerStatus
    fenced_epoch: int


def reset_runner(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    runner_id: str,
    requested_by: str,
    succeeded: bool,
    proof: dict[str, Any],
    now: str | None = None,
) -> ResetOutcome:
    """Attempt the trusted reset that is the only way out of quarantine.

    ``succeeded`` is supplied by the trusted reset procedure, not by the runner. A failed reset
    leaves the desktop quarantined with ``RESET_FAILED``: failing to prove the old actor cannot act
    is not the same as proving it can't, and the safe reading of "we tried and could not tell" is
    that the desktop stays fenced.

    A successful reset moves the runner to PREFLIGHT_REQUIRED — never to READY. It has proved that
    the old session is dead; it has not proved that the new one works.
    """
    moment = _now(now)
    runner = conn.execute(
        "SELECT id, status, lease_epoch, revoked_at FROM runner WHERE id = %s FOR UPDATE",
        (runner_id,),
    ).fetchone()
    if runner is None:
        raise RunnerError("no such runner in this workspace")
    if runner["revoked_at"] is not None:
        raise RunnerError("this runner has been revoked")

    fenced_epoch = int(runner["lease_epoch"])
    reset_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO runner_reset
            (id, workspace_id, runner_id, fenced_epoch, requested_by, succeeded, proof,
             local_impact_warning, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            reset_id,
            workspace_id,
            runner_id,
            fenced_epoch,
            requested_by,
            succeeded,
            json.dumps(proof),
            RESET_LOCAL_IMPACT_WARNING,
            moment,
        ),
    )

    # Any lease still open on this desktop is superseded by the reset, whatever its deadline says.
    conn.execute(
        """
        UPDATE desktop_lease
           SET released_at = %s, release_reason = 'SUPERSEDED_BY_RESET'
         WHERE runner_id = %s AND released_at IS NULL
        """,
        (moment, runner_id),
    )

    if not succeeded:
        _quarantine(conn, runner_id=runner_id, reason=QuarantineReason.RESET_FAILED, now=moment)
        return ResetOutcome(reset_id, False, RunnerStatus.QUARANTINED, fenced_epoch)

    conn.execute(
        """
        UPDATE runner
           SET status = 'PREFLIGHT_REQUIRED', quarantine_reason = NULL, quarantined_at = NULL,
               updated_at = %s, revision = revision + 1
         WHERE id = %s
        """,
        (moment, runner_id),
    )
    return ResetOutcome(reset_id, True, RunnerStatus.PREFLIGHT_REQUIRED, fenced_epoch)


def inspect_runner(conn: psycopg.Connection[dict[str, Any]], *, runner_id: str) -> dict[str, Any]:
    """Everything an operator needs to decide whether a desktop is safe to reset.

    Includes the unresolved actions, because "is anything still in flight" is the question that
    determines whether a reset is an inconvenience or an interruption of something in progress.
    """
    runner = conn.execute(
        """
        SELECT id, name, status, platform, device_id, interactive_session_id, console,
               session_key, profile, lease_epoch, quarantine_reason, quarantined_at, revoked_at,
               revision
          FROM runner WHERE id = %s
        """,
        (runner_id,),
    ).fetchone()
    if runner is None:
        raise RunnerError("no such runner in this workspace")

    lease = conn.execute(
        """
        SELECT id, run_id, attempt_id, epoch, granted_at, deadline_at, heartbeat_at,
               cancel_requested_at, stop_acknowledged_at
          FROM desktop_lease WHERE runner_id = %s AND released_at IS NULL
        """,
        (runner_id,),
    ).fetchone()

    ambiguous = conn.execute(
        """
        SELECT a.id, a.action, a.action_sequence, a.ambiguity_reason
          FROM runner_action a
          JOIN desktop_lease l ON l.id = a.lease_id
         WHERE l.runner_id = %s AND a.result_status = 'AMBIGUOUS'
         ORDER BY a.intent_at
        """,
        (runner_id,),
    ).fetchall()

    return {
        "runner": dict(runner),
        "activeLease": dict(lease) if lease is not None else None,
        "unresolvedActions": unresolved_actions(conn, lease_id=str(lease["id"]))
        if lease is not None
        else [],
        "ambiguousActions": [dict(r) for r in ambiguous],
        "localImpactWarning": RESET_LOCAL_IMPACT_WARNING,
    }


# --- pre-dispatch revalidation -------------------------------------------------------------------


class DispatchRefused(RunnerError):
    """The run is not authorized to start on this desktop, right now, as configured."""


def assert_dispatch_authorized(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    runner_id: str,
    lease_id: str,
    epoch: int,
    child: ChildAuthorization,
    parent: ExecutionGrant,
    workspace_id: str,
    run_id: str,
    expected_profile_digest: str,
    expected_environment_config_digest: str,
    expected_manifest_digest: str,
    now: str | None = None,
) -> None:
    """Every prerequisite, rechecked at the moment of dispatch. Missing means refused.

    The module prompt is blunt about the reason: "a standing grant alone cannot dispatch a run".
    Authorization is established at approval time and can have evaporated since — the grant revoked,
    its revision moved, the child expired, the reader upgraded, the desktop quarantined. Each of
    those makes the thing about to run something nobody approved.

    What this function adds over module 03's authority checks and module 05's seal revalidation is
    the *runner* half, which neither of them can see: that this desktop still has the profile the
    authorization was written against, and that its most recent preflight was performed under this
    lease's epoch rather than an earlier one. A preflight from a previous epoch proved a previous
    session was safe.

    Order matters for the same reason it does in the action gate: the refusal names the most
    fundamental thing wrong, so nobody is sent to fix a symptom.
    """
    moment = _now(now)

    # 1. Authority. Revocation, expiry, parent revision and exact scope containment, all rechecked
    #    rather than trusted from minting.
    try:
        check_child_at_dispatch(child, parent, now=moment, workspace_id=workspace_id, run_id=run_id)
    except AuthorityError as exc:
        raise DispatchRefused(f"authorization is no longer valid: {exc}") from exc

    if child.scope is not ApprovalScope.RUN_EFFECTS:
        raise DispatchRefused(
            f"this authorization has scope {child.scope}, not RUN_EFFECTS. Scopes do not nest "
            "and do not imply one another; nothing but RUN_EFFECTS authorizes executing a run."
        )

    # 2. The desktop. A quarantined or revoked runner is refused before anything else is inspected,
    #    because no amount of valid authorization makes an unfenced desktop safe.
    runner = conn.execute(
        "SELECT status, profile_digest, revoked_at, quarantine_reason, lease_epoch "
        "FROM runner WHERE id = %s",
        (runner_id,),
    ).fetchone()
    if runner is None:
        raise DispatchRefused("no such runner in this workspace")
    if runner["revoked_at"] is not None:
        raise DispatchRefused("this runner has been revoked")
    if str(runner["status"]) == RunnerStatus.QUARANTINED:
        raise DispatchRefused(
            f"this desktop is quarantined ({runner['quarantine_reason']}) and cannot be dispatched "
            "to until a trusted reset and a fresh preflight"
        )
    if str(runner["profile_digest"]) != expected_profile_digest:
        raise DispatchRefused(
            "the runner profile has changed since this run was authorized. A different reader, "
            "browser, locale or keyboard layout announces differently and presses differently, so "
            "the approved run would not be the run that executes (INV-03)."
        )

    # 3. The lease. It must be this lease, at this epoch, unreleased and uncancelled.
    lease = conn.execute(
        """
        SELECT epoch, released_at, release_reason, cancel_requested_at, deadline_at
          FROM desktop_lease WHERE id = %s AND runner_id = %s
        """,
        (lease_id, runner_id),
    ).fetchone()
    if lease is None:
        raise DispatchRefused("no such lease on this runner in this workspace")
    if int(lease["epoch"]) != epoch:
        raise DispatchRefused(
            f"dispatch claims epoch {epoch} and the lease is at epoch {lease['epoch']}"
        )
    if lease["released_at"] is not None:
        raise DispatchRefused(
            f"this lease was already released ({lease['release_reason']}); a released lease is not "
            "a standing right to act on the desktop"
        )
    if lease["cancel_requested_at"] is not None:
        raise DispatchRefused(
            "cancellation has been requested for this lease, so no new work is admitted (INV-13)"
        )

    # 4. Preflight, bound to this epoch. The most recent one, not any successful one ever recorded.
    preflight = conn.execute(
        """
        SELECT successful, lease_epoch, environment_config_digest, manifest_digest,
               runner_profile_digest, refusal_summary
          FROM runner_preflight
         WHERE runner_id = %s
         ORDER BY recorded_at DESC, id DESC
         LIMIT 1
        """,
        (runner_id,),
    ).fetchone()
    if preflight is None:
        raise DispatchRefused(
            "this runner has never submitted a preflight. Missing prerequisites fail closed: an "
            "unproven desktop is not a ready one."
        )
    if not bool(preflight["successful"]):
        raise DispatchRefused(
            "the most recent preflight did not establish readiness -- "
            f"{preflight['refusal_summary']}"
        )

    # The preflight is performed while the runner is not leased, so it is recorded against the epoch
    # in force at that time -- one less than the lease it clears the way for. An *older* epoch than
    # that means a lease has come and gone since, and that preflight vouched for another session.
    if int(preflight["lease_epoch"]) < epoch - 1:
        raise DispatchRefused(
            f"the most recent preflight was performed at epoch {preflight['lease_epoch']} and this "
            f"lease is epoch {epoch}. A lease has been granted and released since, so that "
            "preflight proved a previous session was safe, not this one."
        )
    if str(preflight["manifest_digest"]) != expected_manifest_digest:
        raise DispatchRefused(
            "the preflight was performed against a different sealed manifest than the one about to "
            "run; build identity was never confirmed for this one"
        )
    if str(preflight["environment_config_digest"]) != expected_environment_config_digest:
        raise DispatchRefused(
            "the preflight was performed against a different environment configuration; the "
            "permitted origins and reset strategy it verified are not this run's"
        )
    if str(preflight["runner_profile_digest"]) != expected_profile_digest:
        raise DispatchRefused(
            "the preflight names a different runner profile than the one authorized"
        )

    # 5. Budgets. Non-positive budgets are refused rather than treated as unlimited, which is the
    #    direction a missing value would otherwise fail in.
    if child.action_budget < 1 or child.wall_time_budget_seconds < 1:
        raise DispatchRefused(
            "this authorization carries no usable budget. A zero or negative budget is not an "
            "unlimited one, and work that cannot be bounded is not dispatched (INV-14)."
        )


# --- terminalizing an attempt --------------------------------------------------------------------
#
# The runner layer knows things the run reducer cannot see -- whether a desktop was ever leased,
# whether an action is unresolved, which epoch acknowledged a stop. These two functions are where
# that knowledge meets the reducers, and they exist so that no caller has to assemble the decision
# from parts and get one of them wrong.


def terminalize_ambiguous_attempt(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    run_id: str,
    action_id: str,
    reason: AmbiguityReason,
    expected_revision: int,
    detail: str = "",
    actor_service: str = "runner-control-plane",
    now: str | None = None,
) -> None:
    """An action's outcome is unknown: end the run INTERRUPTED and fence its desktop.

    Not FAILED and not CANCELLED. An interrupted run is INCONCLUSIVE because infrastructure
    ambiguity is not a reproduced accessibility defect (INV-02), and the reducer enforces that
    pairing rather than trusting this caller to supply it.

    The action record and the run transition land in one transaction with the desktop's quarantine.
    A committed quarantine beside a run still reading RUNNING would tell an operator the desktop is
    unsafe and the work is fine, and the two halves of that are the same fact.
    """
    moment = _now(now)
    mark_action_ambiguous(conn, action_id=action_id, reason=reason, detail=detail, now=moment)
    runs.apply_transition(
        conn,
        run_id=run_id,
        reducer=lambda state: reducers.interrupt(
            state, reason=str(reason), expected_revision=expected_revision
        ),
        operation_id=_operation_id(f"interrupt:{action_id}"),
        topic="run.interrupted",
        expected_revision=expected_revision,
        actor_service=actor_service,
        audit_action="RUN_INTERRUPTED_AMBIGUOUS_ACTION",
        now=parse_rfc3339_utc(moment, field="now"),
    )


def terminalize_cancellation(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    run_id: str,
    expected_revision: int,
    actor_service: str = "runner-control-plane",
    now: str | None = None,
) -> None:
    """Admit terminal CANCELLED, and only when something actually proves the desktop stopped.

    Two routes, and no third:

    * the run was never leased, so no action can ever have been admitted to a desktop -- provable
      from the absence of any lease row, which is why :func:`may_cancel_immediately` asks about
      every lease a run ever had rather than only the active one;
    * a stop was acknowledged for the lease's current epoch with no action left unresolved.

    Anything else raises. In particular an expired lease is not a route: CONTRACTS is explicit that
    "Lease timeout alone is not a verified stop", and a run whose supervisor went silent ends
    INTERRUPTED through :func:`terminalize_ambiguous_attempt`, not CANCELLED. Reporting a clean
    cancellation over a desktop that may still be typing is the specific lie this function refuses
    to tell.
    """
    moment = _now(now)
    stored = runs.load_run(conn, run_id=run_id)
    if not stored.state.cancellation_requested:
        raise RunnerError(
            "cancellation was never requested for this run, so there is nothing to terminalize"
        )

    if not may_cancel_immediately(conn, run_id=run_id):
        lease = conn.execute(
            """
            SELECT id, epoch, stop_acknowledged_at, stop_acknowledged_epoch
              FROM desktop_lease WHERE run_id = %s
             ORDER BY epoch DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if lease is None:  # pragma: no cover - may_cancel_immediately just proved one exists
            raise RunnerError("inconsistent lease state for this run")
        if lease["stop_acknowledged_at"] is None:
            raise RunnerError(
                "this run held a desktop and no stop has been acknowledged. Cancellation is "
                "requested, not terminal: a lease that expired or was released is not proof that "
                "the supervisor stopped, and reporting CANCELLED here would say it was."
            )
        if int(lease["stop_acknowledged_epoch"]) != int(lease["epoch"]):
            raise RunnerError(
                f"the stop was acknowledged at epoch {lease['stop_acknowledged_epoch']} and the "
                f"lease is at epoch {lease['epoch']}; a superseded supervisor's acknowledgement "
                "says nothing about the current one"
            )
        unresolved = conn.execute(
            "SELECT count(*) AS n FROM runner_action WHERE lease_id = %s AND result_at IS NULL",
            (str(lease["id"]),),
        ).fetchone()
        if unresolved is not None and int(unresolved["n"]) > 0:
            raise RunnerError(
                f"{unresolved['n']} action(s) remain unresolved; their effect is unknown and a "
                "clean cancellation would record otherwise"
            )

    runs.apply_transition(
        conn,
        run_id=run_id,
        reducer=lambda state: reducers.cancel(state, expected_revision=expected_revision),
        operation_id=_operation_id(f"cancel:{run_id}:{expected_revision}"),
        topic="run.cancelled",
        expected_revision=expected_revision,
        actor_service=actor_service,
        audit_action="RUN_CANCELLED",
        now=parse_rfc3339_utc(moment, field="now"),
    )
