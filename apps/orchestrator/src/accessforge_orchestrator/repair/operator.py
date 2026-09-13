"""One-shot repair dispatch; explicit operator cost flag and stored human consent are required."""

import argparse
import asyncio
import json
import os
import signal
from pathlib import Path
from threading import Event
from uuid import UUID

from .delivery import deliver_requested


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Dispatch one stored repair request using original Git objects"
    )
    parser.add_argument("--workspace-id", type=UUID, required=True)
    parser.add_argument("--request-id", type=UUID, required=True)
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--allow-billable-model-call", action="store_true")
    args = parser.parse_args(argv)
    if not args.allow_billable_model_call:
        parser.error("--allow-billable-model-call is required; no worker invocation started")
    fence = Event()
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    started = False
    try:
        for sig in handlers:
            signal.signal(sig, lambda _sig, _frame: fence.set())
        repository = args.repository.resolve(strict=True)
        if not repository.is_dir():
            raise ValueError("operator repository must be a directory")
        from accessforge_orchestrator.maintenance.purge_worker import _store_from_environment

        database_url = os.environ["ACCESSFORGE_DATABASE_URL"]
        store = _store_from_environment()
        if fence.is_set():
            raise ValueError("cancelled before repair worker entry")
        started = True
        result = asyncio.run(
            deliver_requested(
                database_url,
                store,
                workspace_id=str(args.workspace_id),
                request_id=str(args.request_id),
                repositories={str(args.project_id): repository},
                cancel_signal=fence,
            )
        )
    except Exception:
        if not started:
            parser.exit(2, "REPAIR_DISPATCH_REFUSED; no worker invocation started.\n")
        parser.exit(
            1,
            "REPAIR_UNCONFIRMED; read the original request. Do not create another request "
            "to retry an uncertain provider call.\n",
        )
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
    # A receipt contains identities only, not model text, repository paths, DSNs or source bytes.
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
