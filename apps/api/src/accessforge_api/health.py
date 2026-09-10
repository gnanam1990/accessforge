"""Liveness and readiness.

These are deliberately different questions, because conflating them is how a product ends up
reporting itself healthy while being unable to do anything:

  liveness   this process is running and can serve a response
  readiness  every dependency this service needs is actually reachable right now

A missing database or evidence store must make readiness fail visibly. It must never be smoothed
over into a 200, and it must never fall back to ephemeral storage.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row


@dataclass(frozen=True)
class DependencyResult:
    name: str
    ok: bool
    detail: str


def check_database(database_url: str) -> DependencyResult:
    try:
        with psycopg.connect(database_url, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error as exc:
        # Class name only; driver messages can embed the connection string.
        return DependencyResult("postgresql", False, type(exc).__name__)
    return DependencyResult("postgresql", True, "reachable")


def check_evidence_store(endpoint_url: str) -> DependencyResult:
    """Confirm the evidence endpoint accepts a TCP connection.

    This proves reachability, not bucket existence or credential validity. Module 10 owns the
    real object-store contract; overstating what this check establishes would be exactly the kind
    of false green this product exists to prevent.
    """
    parsed = urlparse(endpoint_url)
    if not parsed.hostname:
        return DependencyResult("evidence-store", False, "unparseable endpoint url")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=3):
            pass
    except OSError as exc:
        return DependencyResult("evidence-store", False, type(exc).__name__)
    return DependencyResult("evidence-store", True, "tcp-reachable")


def check_schema_compatibility(database_url: str) -> DependencyResult:
    """Whether this build can serve the schema the database actually has.

    A reachable database is not a usable one. The two failures this catches are opposite and both
    silent without it:

    * **The database is behind.** A deploy that started serving before its migrations ran answers
      requests against missing columns, and the first person to notice is a user holding a 500.
    * **The database is ahead.** A rolled-back binary connected to a schema a newer release wrote
      reads columns it does not know about as absent, which is indistinguishable from a column
      being empty. That is how a rollback silently discards data rather than failing.

    Only the second is fatal in principle — the first is fixed by running the migrator. Both fail
    readiness, because a process that is not ready to serve correct answers is not ready, and the
    orchestrator's remedy (do not route traffic here) is the same in both cases.
    """
    from accessforge_persistence import expected_migrations
    from accessforge_persistence.restore import schema_state

    expected = expected_migrations()
    try:
        with psycopg.connect(database_url, connect_timeout=3, row_factory=dict_row) as conn:
            state = schema_state(conn)
    except psycopg.errors.UndefinedTable:
        # No ledger at all: an empty database between "it exists" and "the migrator has run". The
        # most common unready state there is, and the one a bare exception class name explains
        # worst -- an operator reading "UndefinedTable" goes looking for a bug rather than running
        # the migrator.
        return DependencyResult(
            "schema", False, f"database is behind this build by {len(expected)} migration(s)"
        )
    except psycopg.Error as exc:
        return DependencyResult("schema", False, type(exc).__name__)

    unknown = [name for name in state.applied if name not in expected]
    if unknown:
        return DependencyResult(
            "schema",
            False,
            f"database is ahead of this build by {len(unknown)} migration(s); "
            "serving would read unknown columns as absent",
        )
    pending = [name for name in expected if name not in state.applied]
    if pending:
        return DependencyResult(
            "schema", False, f"database is behind this build by {len(pending)} migration(s)"
        )
    return DependencyResult("schema", True, f"at {state.latest}")


def physical_runner_note() -> dict[str, str]:
    """What readiness deliberately does not answer.

    A control plane that is up can accept a run request. It cannot make a physical desktop exist,
    and a readiness probe that returned 200 for "the API is ready" while a caller read it as "the
    system can run a journey" would be the product's own headline failure committed in its health
    endpoint.

    So the runner's state is reported as unknown-by-construction rather than omitted. Omitting it
    leaves the reader to assume; saying it leaves them to check. A runner's actual availability is
    a lease-time question answered by `runner.status` and a fresh preflight, and nothing polled
    from here is evidence about a machine somebody may have unplugged one second ago.
    """
    return {
        "observed": "not-checked",
        "detail": (
            "readiness covers this control plane only. Whether any desktop runner is attached, "
            "unquarantined and able to drive a real screen reader is decided when a lease is "
            "granted, from that runner's own preflight -- never from this probe."
        ),
    }
