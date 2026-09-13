"""Explicit one-shot operator dispatch. Nothing runs at import or without the billable-call flag."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
from pathlib import Path
from threading import Event
from uuid import UUID

from .delivery import deliver_requested
from .provisioning import load_source_scope


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Dispatch one human diagnosis request with a private frozen-source allowlist"
    )
    parser.add_argument("--workspace-id", type=UUID, required=True)
    parser.add_argument("--request-id", type=UUID, required=True)
    parser.add_argument("--scope-file", type=Path, required=True)
    parser.add_argument(
        "--allow-billable-model-call",
        action="store_true",
        help="explicitly permit provider disclosure/cost for this already-approved request only",
    )
    args = parser.parse_args(argv)
    if not args.allow_billable_model_call:
        parser.error("dispatch requires --allow-billable-model-call; no provider work was started")
    fence = Event()

    def stop(signum: int, frame: object) -> None:
        del signum, frame
        fence.set()

    handlers = {kind: signal.getsignal(kind) for kind in (signal.SIGINT, signal.SIGTERM)}
    worker_started = False
    try:
        for kind in handlers:
            signal.signal(kind, stop)
        scope = load_source_scope(
            args.scope_file,
            workspace_id=str(args.workspace_id),
            request_id=str(args.request_id),
        )
        from accessforge_orchestrator.maintenance.purge_worker import _store_from_environment

        database_url = os.environ["ACCESSFORGE_DATABASE_URL"]
        store = _store_from_environment()
        if fence.is_set():
            raise ValueError("dispatch cancelled before worker entry")
        worker_started = True
        result = asyncio.run(
            deliver_requested(
                database_url,
                store,
                workspace_id=str(args.workspace_id),
                request_id=str(args.request_id),
                source_scope=scope,
                cancel_signal=fence,
            )
        )
    except Exception:
        # No credential-bearing SDK errors, paths, source bytes or hypotheses on a shared terminal.
        if not worker_started:
            parser.exit(2, "DIAGNOSIS_DISPATCH_REFUSED; no worker invocation started.\n")
        parser.exit(
            1,
            "DIAGNOSIS_UNCONFIRMED; inspect the original request before another dispatch. "
            "Do not create a new request merely to retry an uncertain provider call.\n",
        )
    finally:
        for kind, handler in handlers.items():
            signal.signal(kind, handler)
    print(f"DIAGNOSIS_RETAINED finding={result['findingId']} diagnosis={result['diagnosisId']}")


if __name__ == "__main__":
    main()
