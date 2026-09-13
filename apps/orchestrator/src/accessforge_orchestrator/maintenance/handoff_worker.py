"""Recover abandoned manual handoffs without re-sending or claiming a physical STOP.

Each configured workspace is scanned under RLS. A committed handoff whose unconsumed ticket has
expired, whose ticket was revoked, or whose desktop lease was lost becomes INTERRUPTED, with the
exact desktop quarantined. An accepted ticket expiring is not by itself an expired execution lease.
Healthy live attempts and acknowledged stops are left to their execution/finalization owners.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import signal
import threading
import uuid
from dataclasses import dataclass

import psycopg
from psycopg.conninfo import make_conninfo

from accessforge_persistence import runners, workspace_connection

log = logging.getLogger("accessforge.handoff_worker")

_CANDIDATES = """
SELECT t.id,t.run_id,t.attempt_id,t.runner_id,t.lease_id,t.epoch
FROM supervisor_dispatch_ticket t
JOIN run r ON r.id=t.run_id AND r.workspace_id=t.workspace_id
JOIN runner d ON d.id=t.runner_id AND d.workspace_id=t.workspace_id
JOIN desktop_lease l ON l.id=t.lease_id AND l.workspace_id=t.workspace_id
WHERE t.workspace_id=current_workspace_id()
AND r.status='RUNNING' AND r.lease_epoch=t.epoch AND d.lease_epoch=t.epoch
AND (l.stop_acknowledged_at IS NULL OR l.stop_acknowledged_epoch IS DISTINCT FROM l.epoch)
AND (t.revoked_at IS NOT NULL OR l.released_at IS NOT NULL OR l.deadline_at<=clock_timestamp()
     OR (t.accepted_at IS NULL AND t.expires_at<=clock_timestamp()))
"""


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    inspected: int
    interrupted: int
    deferred: int


def recover_once(database_url: str, workspace_id: str, *, limit: int = 100) -> RecoveryReport:
    """Bounded discovery, then one independently committed recovery per exact attempt.

    No elevated discovery role, OS process control, action dispatch, automatic reset or evidence
    fabrication. Lock/statement timeouts defer contended work to the next pass; successful earlier
    recoveries stay committed. Identity and eligibility are re-read after locks, not trusted from
    the discovery snapshot. Multiple workers may safely scan the same configured workspace.
    """
    workspace_id = str(uuid.UUID(workspace_id))
    if isinstance(limit, bool) or not 1 <= limit <= 500:
        raise ValueError("recovery limit must be between 1 and 500")
    database_url = make_conninfo(database_url, connect_timeout=5)
    with workspace_connection(database_url, workspace_id) as conn:
        conn.execute("SET LOCAL statement_timeout='5s'")
        candidates = conn.execute(
            _CANDIDATES + " ORDER BY t.expires_at,t.id LIMIT %s", (limit,)
        ).fetchall()
    interrupted = deferred = 0
    for candidate in candidates:
        try:
            with workspace_connection(database_url, workspace_id) as conn:
                conn.execute("SET LOCAL statement_timeout='5s'")
                conn.execute("SET LOCAL lock_timeout='1s'")
                # Admission/controller/acceptance lock runner before run. Skip a busy owner.
                owned = conn.execute(
                    "SELECT id FROM runner WHERE id=%s FOR UPDATE SKIP LOCKED",
                    (candidate["runner_id"],),
                ).fetchone()
                if owned is None:
                    deferred += 1
                    continue
                conn.execute("SELECT id FROM run WHERE id=%s FOR UPDATE", (candidate["run_id"],))
                # Heartbeat can update the lease without a runner lock. Serialize with it before
                # deciding that the lease expired; a previously observed deadline is insufficient.
                conn.execute(
                    "SELECT id FROM desktop_lease WHERE id=%s FOR UPDATE",
                    (candidate["lease_id"],),
                )
                current = conn.execute(_CANDIDATES + " AND t.id=%s", (candidate["id"],)).fetchone()
                if current is None:
                    continue
                runners.interrupt_manual_handoff(
                    conn,
                    workspace_id=workspace_id,
                    run_id=str(current["run_id"]),
                    attempt_id=str(current["attempt_id"]),
                    runner_id=str(current["runner_id"]),
                    lease_id=str(current["lease_id"]),
                    epoch=int(current["epoch"]),
                )
            interrupted += 1
        except (psycopg.Error, runners.DispatchRefused):
            # Do not print SQL/connection details, identifiers or credential-bearing exceptions.
            deferred += 1
            log.warning("manual handoff recovery deferred; no dispatch or reset attempted")
    return RecoveryReport(len(candidates), interrupted, deferred)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", action="append", type=uuid.UUID, required=True)
    parser.add_argument("--interval-seconds", type=float, default=5)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.interval_seconds) or not 1 <= args.interval_seconds <= 300:
        parser.error("interval must be between 1 and 300 seconds")
    database_url = os.environ.get("ACCESSFORGE_DATABASE_URL")
    if not database_url:
        parser.error("ACCESSFORGE_DATABASE_URL is required")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stopping = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    while not stopping.is_set():
        failed = False
        for workspace in dict.fromkeys(args.workspace_id):
            if stopping.is_set():
                break
            try:
                report = recover_once(database_url, str(workspace))
                failed |= report.deferred > 0
                if report.interrupted or report.deferred:
                    log.info(
                        "manual handoff recovery: interrupted=%d deferred=%d",
                        report.interrupted,
                        report.deferred,
                    )
            except psycopg.Error:
                failed = True
                log.error("manual handoff recovery unavailable; no dispatch or reset attempted")
        if args.once:
            raise SystemExit(1 if failed else 0)
        stopping.wait(args.interval_seconds)


if __name__ == "__main__":
    main()
