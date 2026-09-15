"""Bound a reference fixture's committed effects with protected database barriers.

Not a polling application-row counter. The installed ALWAYS trigger holds a shared lock
until its transaction ends. Exclusive start/end barriers drain those transactions before
fresh history snapshots. Writes run normally between boundaries, including create/delete.
This measures only this installed database/fixture, not email, payment or external sinks.
The database administrator is trusted, as in effect_audit. Retention/authentication and
binding the window to the actual execution are still the controller's responsibility.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from time import monotonic_ns
from typing import Any
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus

from accessforge_domain.canonical import digest
from accessforge_domain.effect_monitor import EffectCoverage, EffectInterval, EffectWindow

from .effect_audit import (
    CREATION_BARRIER_KEY,
    AuditUnavailable,
    CreationHistory,
    read_creation_history,
)


def reference_effect_scope_digest(installation_id: str, fixture_nonce: str) -> str:
    """Freeze this exact narrow resource scope before provisioning an execution."""
    if (
        str(UUID(installation_id)) != installation_id
        or re.fullmatch(r"[A-Za-z0-9_-]{16,64}", fixture_nonce) is None
    ):
        raise ValueError("exact audit installation and reserved fixture required")
    return digest(
        {
            "kind": "PROTECTED_REFERENCE_COMMITTED_INSERTIONS_V1",
            "installationId": installation_id,
            "fixtureNonce": fixture_nonce,
        }
    )


class ReferenceEffectCollector:
    """Single-use, exclusive owner of an already connected independent observer connection.

    begin() must finish before dispatch. finish() is called only after independent STOP
    closure; neither method starts/stops a runner. The connection must use dict rows and
    the installed observer identity. It is closed on finish, failure or abort; no reconnect,
    retry, privileged role switching, installation, deletion or live migration is performed.
    A failed boundary produces no coverage. Call abort() on every abandoned execution.
    """

    def __init__(
        self,
        conn: psycopg.Connection[Any],
        *,
        application_role: str,
        installation_id: str,
        fixture_nonce: str,
        run_id: str,
        attempt_id: str,
        clock_epoch: str,
        clock: Callable[[], int] = monotonic_ns,
    ) -> None:
        scope = reference_effect_scope_digest(installation_id, fixture_nonce)
        # Validate the complete supervisor-provisioned identity without observing a clock.
        self._identity = dict(
            run_id=run_id,
            attempt_id=attempt_id,
            scope_digest=scope,
            clock_epoch=clock_epoch,
            effect="CREATE_TEST_REQUEST",
        )
        EffectWindow(**self._identity, start_ns=0, end_ns=1)
        if conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
            raise AuditUnavailable("collector requires an idle explicit observer connection")
        self._conn = conn
        self._application_role = application_role
        self._installation = installation_id
        self._nonce = fixture_nonce
        self._clock = clock
        self._state = "NEW"
        self._start = 0
        self._installed_at = ""

    def _tick(self) -> int:
        value = self._clock()
        if type(value) is not int or not 0 <= value <= 2**63 - 1:
            raise AuditUnavailable("collector monotonic clock unavailable")
        return value

    def _barrier(self) -> None:
        # A session lock must precede a NEW history snapshot. Acquiring an xact lock
        # inside REPEATABLE READ could establish a snapshot before waiting for commits.
        with self._conn.transaction():
            self._conn.execute("SET LOCAL statement_timeout='5s'")
            self._conn.execute("SELECT pg_advisory_lock(%s::bigint)", (CREATION_BARRIER_KEY,))

    def _history(self) -> CreationHistory:
        result = read_creation_history(
            self._conn,
            application_role=self._application_role,
            installation_id=self._installation,
            fixture_nonce=self._nonce,
        )
        self._conn.commit()
        return result

    def begin(self) -> int:
        if self._state != "NEW":
            self.abort()
            raise AuditUnavailable("collector cannot be started or replayed")
        self._state = "STARTING"
        try:
            # No inherited/reentrant session locks may survive one unlock operation.
            with self._conn.transaction():
                held = self._conn.execute(
                    "SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid() "
                    "AND locktype='advisory' LIMIT 1"
                ).fetchone()
                if held is not None:
                    raise AuditUnavailable("observer connection already owns advisory locks")
            self._barrier()
            initial = self._history()
            if initial.committed_insertions != 0:
                raise AuditUnavailable("fixture has prior effects; no reset or count subtraction")
            self._installed_at = initial.installed_at
            self._start = self._tick()
            with self._conn.transaction():
                unlocked = self._conn.execute(
                    "SELECT pg_advisory_unlock(%s::bigint) AS released",
                    (CREATION_BARRIER_KEY,),
                ).fetchone()
                if unlocked is None or unlocked["released"] is not True:
                    raise AuditUnavailable("initial boundary release unconfirmed")
            self._state = "ACTIVE"
            return self._start
        except Exception as error:
            self.abort()
            raise AuditUnavailable("effect collector startup unavailable") from error

    def finish(self) -> EffectCoverage:
        if self._state != "ACTIVE":
            self.abort()
            raise AuditUnavailable("effect collector has no active window")
        self._state = "CLOSING"
        try:
            self._barrier()
            end = self._tick()
            if end <= self._start or end - self._start > 1800 * 1_000_000_000:
                raise AuditUnavailable("collector clock reversed or execution window expired")
            final = self._history()
            if final.installed_at != self._installed_at:
                raise AuditUnavailable("original audit installation changed")
            window = EffectWindow(**self._identity, start_ns=self._start, end_ns=end)
            coverage = EffectCoverage(
                window, (EffectInterval(self._start, end, final.committed_insertions),)
            )
            # Close releases the end barrier. No writes admitted after this boundary
            # are part of this window. The original connection is never reused.
            self._conn.close()
            self._state = "CLOSED"
            return coverage
        except Exception as error:
            self.abort()
            raise AuditUnavailable("effect collector closure unavailable; no coverage") from error

    def abort(self) -> None:
        self._state = "CLOSED"
        self._conn.close()
