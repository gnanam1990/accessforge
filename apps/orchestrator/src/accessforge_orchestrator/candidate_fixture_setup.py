"""Attach original isolated seed evidence to a separately approved queued candidate run.

No reset, setup HTTP, model call or desktop operation occurs here. In particular the later
run-side attachment is not misrepresented as the earlier pre-seed nonce reservation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from accessforge_domain.canonical import digest
from accessforge_persistence import candidate_fixture_setups, candidate_runs, workspace_connection

from .reference_fixture_setup import Refused, _context


def prepare(
    database_url: str,
    *,
    workspace_id: str,
    run_id: str,
    origin: str,
    reset_credential_ref: str,
    observer_credential_ref: str,
    reset_values: dict[str, str],
    observer_config: dict[str, str],
) -> dict[str, Any]:
    """One atomic attachment; original RUN_EFFECTS approval is mandatory, never issued here."""
    with workspace_connection(database_url, workspace_id) as conn:
        conn.execute("SET LOCAL statement_timeout='5s'")
        conn.execute("SET LOCAL lock_timeout='1s'")
        if candidate_runs.assert_live(conn, run_id=run_id) is None:
            raise Refused("candidate setup requires its original live isolated binding")
        context = _context(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            origin=origin,
            reset_credential_ref=reset_credential_ref,
            observer_credential_ref=observer_credential_ref,
            reset_values=reset_values,
            observer_config=observer_config,
            create=False,
        )
        seed = candidate_fixture_setups.for_run(conn, run_id=run_id)
        if seed is None:
            raise Refused("candidate seed evidence unavailable")
        if any(
            seed["context"][key] != context[key] for key in ("nonce", "templateDigest", "variant")
        ):
            raise Refused("candidate seed differs from the approved fixture")
        endpoint = conn.execute(
            "SELECT origin FROM candidate_endpoint WHERE attempt_id=%s",
            (seed["context"]["regressionAttemptId"],),
        ).fetchone()
        if endpoint is None or origin != endpoint["origin"]:
            raise Refused("candidate setup origin differs from its exact endpoint")
        fingerprint = digest(context)
        observation = {
            "meaning": "INDEPENDENT_INITIAL_EMPTY_FIXTURE_NOT_DESKTOP_ATTESTATION",
            "contextDigest": fingerprint,
            "application": seed["observation"]["application"],
            "reservedAt": seed["reservedAt"],
            "candidateSeed": seed,
        }
        conn.execute(
            "INSERT INTO fixture_setup_reservation(run_id,workspace_id,context,context_digest) "
            "VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (run_id, workspace_id, Jsonb(context), fingerprint),
        )
        row = conn.execute(
            "SELECT * FROM fixture_setup_reservation WHERE run_id=%s", (run_id,)
        ).fetchone()
        assert row is not None
        if row["context"] != context or row["context_digest"] != fingerprint:
            raise Refused("a different original setup reservation owns the candidate run")
        if row["observation"] is not None:
            if row["observation"] != observation or row["observation_digest"] != digest(
                observation
            ):
                raise Refused("candidate setup attachment differs from original seed evidence")
            return observation
        candidate_runs.assert_live(conn, run_id=run_id)
        conn.execute(
            "UPDATE fixture_setup_reservation SET observation=%s,observation_digest=%s,"
            "observed_at=clock_timestamp() WHERE run_id=%s",
            (Jsonb(observation), digest(observation), run_id),
        )
        return observation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--reset-credential-ref", required=True)
    parser.add_argument("--observer-credential-ref", required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    args = parser.parse_args()
    try:
        with args.configuration.open() as source:
            config = json.load(source)
        result = prepare(
            os.environ["ACCESSFORGE_DATABASE_URL"],
            workspace_id=args.workspace_id,
            run_id=args.run_id,
            origin=args.origin,
            reset_credential_ref=args.reset_credential_ref,
            observer_credential_ref=args.observer_credential_ref,
            reset_values=config["resetValues"],
            observer_config=config["observerConfig"],
        )
    except Exception:
        parser.exit(
            1, "Candidate setup not attached; inspect original approval and seed evidence.\n"
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
