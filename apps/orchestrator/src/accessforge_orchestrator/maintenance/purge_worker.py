"""The worker that removes bytes a committed deletion promised to remove.

FR-020 records a deletion, marks the artifacts and queues every object key, and the
report it returns says in words that anything still queued will be taken by a later
pass. Until this worker existed there was no later pass. A deletion whose object
store was unreachable waited for a person to notice and call the retry route, and
`objectsStillPresent` stayed above zero indefinitely -- the database reporting
evidence as deleted while the store still held it.

Three properties this is built around.

**It finds work with a role that can see it.** The queue is workspace-scoped under
`FORCE ROW LEVEL SECURITY`, so a sweeper on an ordinary application role sees an
empty queue no matter what is in it, reports nothing to do, and exits successfully
for ever. `assert_can_sweep` refuses that role outright rather than letting a silent
no-op look like a clean run. Discovery is the only elevated step; every deletion is
performed on a connection scoped to one workspace.

**It commits per batch.** `drain_purge_queue` takes a connection factory and opens a
transaction per batch, so a batch that succeeded stays succeeded and row locks are
held for a few store calls rather than a whole queue.

**A store outage is a delay, not a failure.** A batch that removes nothing ends that
workspace's sweep; the keys keep their recorded errors and attempt counts, and the
next tick tries again. Nothing here raises on a store error, because there is nobody
to tell and the queue already records what happened.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from accessforge_persistence import (
    deletion,
    rate_limits,
    unscoped_connection,
    workspace_connection,
)
from accessforge_persistence.evidence import S3ArtifactStore, S3Settings
from accessforge_persistence.evidence.objectstore import ArtifactStore

log = logging.getLogger("accessforge.purge_worker")

DEFAULT_INTERVAL_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class WorkspaceSweep:
    """What one workspace's sweep achieved."""

    workspace_id: str
    purged: int
    still_pending: int

    @property
    def summary(self) -> str:
        return (
            f"{self.workspace_id}: released {self.purged} object(s), "
            f"{self.still_pending} still pending"
        )


#: How long a rate-limit bucket may sit untouched before the sweep removes it. Comfortably longer
#: than any bucket takes to refill completely, because pruning a bucket mid-refill would hand its
#: caller a fresh allowance -- which is the one thing a limiter must not do under load.
IDLE_BUCKET_RETENTION = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class SweepReport:
    """What one tick achieved, in numbers an operator can check.

    `still_pending` above zero is not an error. It means the store has not released
    those bytes yet and the next tick will try again. It is only alarming if it stops
    going down, which is why the number is reported per workspace rather than summed
    into a single reassuring total.
    """

    started_at: str
    workspaces: tuple[WorkspaceSweep, ...] = field(default_factory=tuple)
    idle_buckets_pruned: int = 0
    """Rate-limit buckets removed. Pure derived state: the next request recreates one, full, which
    is exactly what an idle bucket already was."""

    @property
    def purged(self) -> int:
        return sum(sweep.purged for sweep in self.workspaces)

    @property
    def still_pending(self) -> int:
        return sum(sweep.still_pending for sweep in self.workspaces)

    @property
    def summary(self) -> str:
        buckets = f"; pruned {self.idle_buckets_pruned} idle rate-limit bucket(s)"
        if not self.workspaces:
            return f"nothing was queued for removal{buckets}"
        return (
            f"released {self.purged} object(s) across {len(self.workspaces)} workspace(s); "
            f"{self.still_pending} still pending{buckets}"
        )


def sweep_once(
    database_url: str,
    store: ArtifactStore,
    *,
    sweep_url: str | None = None,
    batch_size: int = 10,
    now: datetime | None = None,
) -> SweepReport:
    """One pass: find the workspaces holding pending keys, then drain each.

    `sweep_url` is the connection used for discovery only, and must bypass row-level
    security. It defaults to `database_url`, which is correct when the worker runs as
    a maintenance role and wrong -- loudly, via `assert_can_sweep` -- when it does
    not.
    """
    moment = now or datetime.now(UTC)
    with unscoped_connection(sweep_url or database_url) as conn:
        deletion.assert_can_sweep(conn)
        workspaces = deletion.workspaces_with_pending_purges(conn)

    sweeps: list[WorkspaceSweep] = []
    for workspace_id in workspaces:

        @contextmanager
        def connect(
            workspace_id: str = workspace_id,
        ) -> Iterator[object]:
            # Scoped per workspace, deliberately. Discovery needed to see across tenants; the
            # deletions themselves run under the same row-level security as every other write, so
            # a bug here cannot reach another workspace's evidence.
            with workspace_connection(database_url, workspace_id) as conn:
                yield conn

        outcome = deletion.drain_purge_queue(
            connect,  # type: ignore[arg-type]
            store,
            batch_size=batch_size,
            now=moment,
        )
        sweeps.append(
            WorkspaceSweep(
                workspace_id=workspace_id,
                purged=outcome.purged,
                still_pending=outcome.still_pending,
            )
        )
        log.info("purge sweep %s", sweeps[-1].summary)

    # Bucket rows are not workspace-scoped for principals, so the sweep connection -- which already
    # has to see across tenants to find work -- is the one that can remove them.
    with unscoped_connection(sweep_url or database_url) as conn:
        pruned = rate_limits.prune_idle_buckets(conn, idle_for=IDLE_BUCKET_RETENTION, now=moment)

    report = SweepReport(
        started_at=moment.isoformat(), workspaces=tuple(sweeps), idle_buckets_pruned=pruned
    )
    log.info("purge sweep finished: %s", report.summary)
    return report


def _store_from_environment() -> S3ArtifactStore:
    return S3ArtifactStore(
        S3Settings(
            endpoint_url=os.environ["ACCESSFORGE_EVIDENCE_ENDPOINT_URL"],
            access_key=os.environ["ACCESSFORGE_EVIDENCE_ACCESS_KEY"],
            secret_key=os.environ["ACCESSFORGE_EVIDENCE_SECRET_KEY"],
            bucket=os.environ.get("ACCESSFORGE_EVIDENCE_BUCKET", "accessforge-evidence"),
        )
    )


def main() -> None:
    """Sweep on an interval until asked to stop.

    Stops on SIGTERM and SIGINT between ticks rather than mid-batch: a batch is a
    committed unit of work, and tearing one down to exit a second sooner is how a
    container restart turns into an object the queue no longer claims.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    database_url = os.environ["ACCESSFORGE_DATABASE_URL"]
    sweep_url = os.environ.get("ACCESSFORGE_MAINTENANCE_DATABASE_URL") or database_url
    interval = float(os.environ.get("ACCESSFORGE_PURGE_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS))
    store = _store_from_environment()

    stopping = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        log.info("signal %s received; stopping after this tick", signum)
        stopping.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    while not stopping.is_set():
        try:
            sweep_once(database_url, store, sweep_url=sweep_url)
        except Exception:  # noqa: BLE001 - a failed tick must not end the worker
            # Logged with its traceback and retried on the next tick. Exiting here would stop
            # every future sweep over one transient failure, and the queue would go back to
            # waiting for a human.
            log.exception("purge sweep failed; retrying on the next tick")
        stopping.wait(interval)

    log.info("purge worker stopped")


if __name__ == "__main__":
    main()
