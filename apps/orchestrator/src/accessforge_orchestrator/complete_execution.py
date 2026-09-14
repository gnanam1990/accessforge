"""Resume post-STOP evidence retention and evaluation without replaying desktop execution.

For baseline sessions, call only after the protected runtime has finished and published its
functional receipt. No build, fixture reset, reader startup or model invocation occurs here.
"""

from __future__ import annotations

import argparse
import os
import uuid
from typing import Any

from accessforge_persistence import evaluations, workspace_connection

from .execution_artifacts import ExecutionArtifactStore, read_private_journal, retain_bundle
from .finalize_execution import finalize


def complete(
    database_url: str,
    store: ExecutionArtifactStore,
    *,
    workspace_id: str,
    run_id: str,
    journal_path: str,
) -> dict[str, Any]:
    """Reconcile a committed snapshot first; otherwise resume with the exact original spool.

    There is intentionally no automatic retry after an exception. A lost acknowledgement may
    have committed objects or an evaluation. Invoke again to reconcile, never restart the run.
    Historical replay is not a new claim about current object retention.
    """
    with workspace_connection(database_url, workspace_id) as conn:
        existing = evaluations.read(conn, run_id=run_id)
    if existing is not None:
        return existing
    journal = read_private_journal(journal_path)
    retain_bundle(database_url, store, workspace_id=workspace_id, run_id=run_id, journal=journal)
    return finalize(database_url, store, workspace_id=workspace_id, run_id=run_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", type=uuid.UUID, required=True)
    parser.add_argument("--run-id", type=uuid.UUID, required=True)
    parser.add_argument("--journal", required=True, help="original private supervisor spool")
    args = parser.parse_args()
    try:
        from accessforge_orchestrator.maintenance.purge_worker import _store_from_environment

        result = complete(
            os.environ["ACCESSFORGE_DATABASE_URL"],
            _store_from_environment(),
            workspace_id=str(args.workspace_id),
            run_id=str(args.run_id),
            journal_path=args.journal,
        )
    except Exception:  # noqa: BLE001 - never log private spool or credential-bearing SDK errors
        print("COMPLETION_UNCONFIRMED; reconcile with this command; do not restart execution")
        raise SystemExit(1) from None
    print("ORIGINAL_EVALUATION_SNAPSHOT outcome=" + result["snapshot"]["outcome"])


if __name__ == "__main__":
    main()
