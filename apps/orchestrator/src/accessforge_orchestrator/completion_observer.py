"""Trusted independent application-state measurement worker; no reader or OS capability.

Run as a separate operator-configured service. Application credentials never come from a supervisor
or navigator and must map to the sealed observer credential reference. This command records a
measurement, not a run verdict. Production deployment and least-privilege database roles are
operator-owned; this module does not create credentials, grants, fixtures, or application effects.
"""

from __future__ import annotations

import argparse
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo

from accessforge_domain.authorization import (
    MachinePrincipal,
    ServiceIdentity,
    assert_may_submit_event,
)
from accessforge_domain.canonical import digest
from accessforge_domain.evaluation.observer import ObserverError
from accessforge_domain.origins import normalize_origin
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import fixtures, projects, runners, sequencer, workspace_connection
from accessforge_persistence.evidence.observer import ApplicationObserver
from accessforge_persistence.evidence.session import observer_producer


class Refused(Exception):
    """No authoritative measurement context; no receipt was admitted."""


@dataclass(frozen=True)
class MeasurementReceipt:
    event_id: str
    known: bool
    replay: bool


def _context(
    conn: psycopg.Connection[Any],
    workspace_id: str,
    run_id: str,
    credential_ref: str,
) -> dict[str, Any]:
    ticket = conn.execute(
        "SELECT * FROM supervisor_dispatch_ticket WHERE run_id=%s AND workspace_id=%s",
        (run_id, workspace_id),
    ).fetchone()
    if ticket is None:
        raise Refused("no committed desktop attempt")
    conn.execute("SELECT id FROM runner WHERE id=%s FOR UPDATE", (ticket["runner_id"],))
    conn.execute("SELECT id FROM run WHERE id=%s FOR UPDATE", (run_id,))
    conn.execute("SELECT id FROM desktop_lease WHERE id=%s FOR UPDATE", (ticket["lease_id"],))
    conn.execute(
        "SELECT a.id FROM approval a JOIN run r ON r.authorization_id=a.id "
        "WHERE r.id=%s FOR SHARE OF a",
        (run_id,),
    )
    live = conn.execute(
        "SELECT 1 FROM supervisor_dispatch_ticket WHERE id=%s AND accepted_at IS NOT NULL "
        "AND revoked_at IS NULL FOR SHARE",
        (ticket["id"],),
    ).fetchone()
    if live is None:
        raise Refused("desktop handoff is not current")
    manifest = runners.assert_manual_attempt_authorized(
        conn,
        workspace_id=workspace_id,
        run_id=run_id,
        runner_id=str(ticket["runner_id"]),
        lease_id=str(ticket["lease_id"]),
        attempt_id=str(ticket["attempt_id"]),
        epoch=int(ticket["epoch"]),
    )
    environment = conn.execute(
        "SELECT e.* FROM environment_manifest e JOIN sealed_manifest s "
        "ON s.environment_manifest_id=e.id WHERE s.run_id=%s FOR SHARE OF e",
        (run_id,),
    ).fetchone()
    if environment is None or environment["observer_credential_ref"] != credential_ref:
        raise Refused("observer credential reference does not match the sealed environment")
    spec = projects.EnvironmentSpec(
        name=environment["name"],
        allowed_origins=frozenset(
            normalize_origin(origin) for origin in environment["allowed_origins"]
        ),
        fixture_reset_strategy=environment["fixture_reset_strategy"],
        observer_credential_ref=environment["observer_credential_ref"],
        reset_credential_ref=environment["reset_credential_ref"],
        permitted_effects=frozenset(environment["permitted_effects"]),
        expires_at=to_rfc3339_utc(environment["expires_at"]),
    )
    if spec.config_digest() != manifest["environmentConfigDigest"]:
        raise Refused("observer environment content differs from sealed identity")
    fixture = conn.execute(
        "SELECT * FROM run_fixture_instance WHERE run_id=%s FOR SHARE",
        (run_id,),
    ).fetchone()
    if (
        fixture is None
        or not isinstance(fixture["observer_config"], dict)
        or not isinstance(fixture["navigator_values"], dict)
        or fixture["observer_config"].get("effect") != "CREATE_TEST_REQUEST"
        or fixture["template_digest"] != manifest["fixtureDigest"]
    ):
        raise Refused("supported independent observer fixture unavailable")
    contract = fixtures.contract_digest(
        template_id=fixture["template_id"],
        template_digest=fixture["template_digest"],
        navigator_values=fixture["navigator_values"],
        observer_config=fixture["observer_config"],
    )
    if fixture["captured_contract_digest"] != contract:
        raise Refused("fixture creation-time contract differs from current values")
    actions = conn.execute(
        "SELECT count(*) AS n,coalesce(max(action_sequence),0) AS last,"
        "count(*) FILTER(WHERE result_at IS NULL OR result_status='AMBIGUOUS') AS unresolved "
        "FROM runner_action WHERE run_id=%s AND attempt_id=%s",
        (run_id, ticket["attempt_id"]),
    ).fetchone()
    if actions is None or not actions["n"] or actions["unresolved"]:
        raise Refused("observer requires a settled action boundary")
    last = conn.execute(
        "SELECT action,result_status FROM runner_action WHERE run_id=%s AND attempt_id=%s "
        "ORDER BY action_sequence DESC LIMIT 1",
        (run_id, ticket["attempt_id"]),
    ).fetchone()
    assert last is not None
    return {
        "workspace": workspace_id,
        "run": run_id,
        "attempt": str(ticket["attempt_id"]),
        "lease": str(ticket["lease_id"]),
        "epoch": int(ticket["epoch"]),
        "manifestDigest": digest(manifest),
        "fixtureId": str(fixture["id"]),
        "fixtureNonce": fixture["nonce"],
        "templateDigest": fixture["template_digest"],
        "fixtureContract": contract,
        "afterActionSequence": int(actions["last"]),
        "lastAction": last["action"],
        "lastResult": last["result_status"],
        "producer": observer_producer(credential_ref, str(ticket["attempt_id"])),
    }


def _existing(
    conn: psycopg.Connection[Any],
    context: dict[str, Any],
    source_record_id: str,
    final_sample: bool,
) -> MeasurementReceipt | None:
    row = conn.execute(
        "SELECT e.event_id,e.payload,e.lease_epoch,e.manifest_digest "
        "FROM producer_source_record p JOIN canonical_event e "
        "ON e.event_id=p.event_id WHERE p.run_id=%s AND p.attempt_id=%s AND p.producer_id=%s "
        "AND p.source_record_id=%s",
        (context["run"], context["attempt"], context["producer"], source_record_id),
    ).fetchone()
    if row is None:
        return None
    source = row["payload"].get("sourceRecord")
    if (
        not isinstance(source, dict)
        or source.get("measurement") not in {"KNOWN", "UNKNOWN"}
        or source.get("fixtureInstanceId") != context["fixtureId"]
        or source.get("afterActionSequence") != context["afterActionSequence"]
        or row["lease_epoch"] != context["epoch"]
        or row["manifest_digest"] != context["manifestDigest"]
        or source.get("finalSample", False) is not final_sample
    ):
        raise Refused("previous observation content is no longer available")
    return MeasurementReceipt(str(row["event_id"]), source["measurement"] == "KNOWN", True)


def measure_once(
    database_url: str,
    application_database_url: str,
    *,
    workspace_id: str,
    run_id: str,
    credential_ref: str,
    source_record_id: str,
    final_sample: bool = False,
) -> MeasurementReceipt:
    """Read application state outside product locks, then revalidate before atomic evidence commit.

    A reused source identity returns its retained observation, never a remeasured replacement.
    Any intervening action, fixture/epoch/authority change refuses admission. Unavailable app
    state becomes an explicit UNKNOWN receipt; unavailable control-plane authority records nothing.
    """
    workspace_id, run_id, source_record_id = (
        str(uuid.UUID(value)) for value in (workspace_id, run_id, source_record_id)
    )
    if not credential_ref or len(credential_ref) > 256:
        raise Refused("invalid local observer credential reference")
    database_url = make_conninfo(database_url, connect_timeout=5)
    with workspace_connection(database_url, workspace_id) as conn:
        conn.execute("SET LOCAL statement_timeout='5s'")
        conn.execute("SET LOCAL lock_timeout='1s'")
        before = _context(conn, workspace_id, run_id, credential_ref)
        if final_sample and (before["lastAction"] != "STOP" or before["lastResult"] != "SUCCEEDED"):
            raise Refused("a final observer sample requires successful STOP")
        existing = _existing(conn, before, source_record_id, final_sample)
        if existing is not None:
            return existing
    count: int | None = None
    observed_at = to_rfc3339_utc(datetime.now(UTC))
    try:
        measured = ApplicationObserver(application_database_url).count_effects(
            fixture_nonce=before["fixtureNonce"],
            effect="CREATE_TEST_REQUEST",
            expected_template_digest=before["templateDigest"],
        )
        if (
            measured.fixture_nonce != before["fixtureNonce"]
            or measured.effect != "CREATE_TEST_REQUEST"
        ):
            raise ObserverError("observer returned a different measurement identity")
        count, observed_at = measured.count, measured.observed_at
    except ObserverError:
        # No DSN, SQL errors, fixture nonce or receipt secrets enter the payload/logs.
        observed_at = to_rfc3339_utc(datetime.now(UTC))
    with workspace_connection(database_url, workspace_id) as conn:
        conn.execute("SET LOCAL statement_timeout='5s'")
        conn.execute("SET LOCAL lock_timeout='1s'")
        after = _context(conn, workspace_id, run_id, credential_ref)
        if after != before:
            raise Refused("execution identity changed during application observation")
        existing = _existing(conn, after, source_record_id, final_sample)
        if existing is not None:
            return existing
        principal = MachinePrincipal(
            service_identity=ServiceIdentity.OBSERVER,
            workspace_id=workspace_id,
            credential_id=credential_ref,
            run_id=run_id,
        )
        assert_may_submit_event(principal, "EFFECT_RECEIPT")
        stream = conn.execute(
            "SELECT admitted_through FROM producer_stream WHERE attempt_id=%s AND producer_id=%s",
            (after["attempt"], after["producer"]),
        ).fetchone()
        sequence = 1 if stream is None else int(stream["admitted_through"]) + 1
        if sequence > 128:
            raise Refused("independent observer record budget exhausted")
        source = {
            "effect": "CREATE_TEST_REQUEST",
            "fixtureInstanceId": after["fixtureId"],
            "afterActionSequence": after["afterActionSequence"],
            "observedAt": observed_at,
            "measurement": "UNKNOWN" if count is None else "KNOWN",
            "count": count,
            "finalSample": final_sample,
        }
        event = sequencer.admit_record(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            attempt_id=after["attempt"],
            lease_epoch=after["epoch"],
            producer_id=after["producer"],
            source_record_id=source_record_id,
            producer_sequence=sequence,
            event_type="EFFECT_RECEIPT",
            manifest_digest=after["manifestDigest"],
            payload={
                "producerId": after["producer"],
                "producerSequence": sequence,
                "sourceRecordId": source_record_id,
                "sourceRecordDigest": digest(source),
                "sourceRecord": source,
                "serviceIdentity": "OBSERVER",
            },
            source_time=datetime.fromisoformat(observed_at.replace("Z", "+00:00")),
        )
        if final_sample:
            sequencer.close_producer_stream(
                conn,
                workspace_id=workspace_id,
                run_id=run_id,
                attempt_id=after["attempt"],
                producer_id=after["producer"],
                final_producer_sequence=sequence,
            )
        return MeasurementReceipt(event.event_id, count is not None, False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", type=uuid.UUID, required=True)
    parser.add_argument("--run-id", type=uuid.UUID, required=True)
    parser.add_argument("--source-record-id", type=uuid.UUID, required=True)
    parser.add_argument("--observer-credential-ref", required=True)
    parser.add_argument(
        "--final", action="store_true", help="measure after STOP and close this observer's stream"
    )
    args = parser.parse_args()
    product = os.environ.get("ACCESSFORGE_DATABASE_URL")
    application = os.environ.get("ACCESSFORGE_OBSERVER_DATABASE_URL")
    if not product or not application:
        parser.error("product and independent observer database configuration are required")
    try:
        receipt = measure_once(
            product,
            application,
            workspace_id=str(args.workspace_id),
            run_id=str(args.run_id),
            source_record_id=str(args.source_record_id),
            credential_ref=args.observer_credential_ref,
            final_sample=args.final,
        )
    except (Refused, runners.RunnerError, psycopg.Error, ValueError, sequencer.SequencerError):
        print("observer measurement refused; no completion or retry claim")
        raise SystemExit(1) from None
    print("observer measurement retained: " + ("KNOWN" if receipt.known else "UNKNOWN"))
    raise SystemExit(0 if receipt.known else 3)


if __name__ == "__main__":
    main()
