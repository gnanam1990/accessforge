"""Load a live attempt's original reader-only projection for a trusted turn coordinator.

This read does not reserve a model invocation, grant provider consent or authorize desktop input.
It never reads observer payloads, source, DOM, screenshots or supervisor credentials. The next
coordinator must commit its one-shot invocation claim and recheck authority before a provider call.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from accessforge_contracts.reference_fixture import REFERENCE_FIXTURE_DIGEST
from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import parse_rfc3339_utc
from accessforge_navigation_tools import (
    NavigatorProjection,
    ReaderObservation,
    SealedNavigatorPolicy,
)
from accessforge_orchestrator.manual_dispatch import DispatchReference
from accessforge_persistence import journeys, runners, workspace_connection

from .destination import load_destination


class ProjectionRefused(ValueError):
    """The exact current attempt has no complete, original planning boundary."""


@dataclass(frozen=True, slots=True)
class RetainedNavigatorTurn:
    # Only projection.model_payload() may enter model context. The other fields bind the future
    # durable invocation claim to the exact source records and sealed configuration.
    projection: NavigatorProjection
    manifest_digest: str
    model_config_digest: str
    action_sequence: int
    reader_event_ids: tuple[str, ...]
    reader_payload_digests: tuple[str, ...]


def load_retained_turn(
    *, database_url: str, reference: DispatchReference, expected_action_sequence: int
) -> RetainedNavigatorTurn:
    """Read one complete boundary, including the empty initial projection before any action.

    expected_action_sequence is chosen by trusted coordination, not by a model. A concurrent or
    newer action makes this request stale. No historical projection is re-created after STOP or
    revoked authority, and CAPTURE_UNKNOWN remains explicitly unknown rather than fake speech.
    """
    if type(expected_action_sequence) is not int or not 0 <= expected_action_sequence < 500:
        raise ProjectionRefused("bounded expected action sequence required")
    with workspace_connection(database_url, reference.workspace_id) as conn:
        # Match the supervisor's lock order. Action admission cannot race this snapshot. This is
        # still a point-in-time read, not a lock held across a billed provider invocation.
        conn.execute("SELECT id FROM runner WHERE id=%s FOR UPDATE", (reference.runner_id,))
        conn.execute("SELECT id FROM run WHERE id=%s FOR UPDATE", (reference.run_id,))
        conn.execute("SELECT id FROM desktop_lease WHERE id=%s FOR UPDATE", (reference.lease_id,))
        conn.execute(
            "SELECT a.id FROM approval a JOIN run r ON r.authorization_id=a.id "
            "WHERE r.id=%s FOR SHARE OF a",
            (reference.run_id,),
        )
        manifest = runners.assert_manual_attempt_authorized(conn, **asdict(reference))
        session = conn.execute(
            "SELECT t.id,t.created_at,s.expires_at FROM supervisor_dispatch_ticket t "
            "JOIN supervisor_execution_session s ON s.ticket_id=t.id "
            "AND s.workspace_id=t.workspace_id "
            "WHERE t.workspace_id=%s AND t.run_id=%s AND t.attempt_id=%s "
            "AND t.runner_id=%s AND t.lease_id=%s AND t.epoch=%s "
            "AND t.accepted_at IS NOT NULL AND t.revoked_at IS NULL "
            "AND s.revoked_at IS NULL AND s.expires_at>clock_timestamp() FOR SHARE OF t,s",
            (
                reference.workspace_id,
                reference.run_id,
                reference.attempt_id,
                reference.runner_id,
                reference.lease_id,
                reference.epoch,
            ),
        ).fetchone()
        policy_row = conn.execute(
            "SELECT navigator_policy FROM journey_version WHERE id=%s",
            (manifest["journeyVersionId"],),
        ).fetchone()
        if session is None or policy_row is None:
            raise ProjectionRefused("live supervisor session and sealed policy required")
        raw_policy = policy_row["navigator_policy"]
        if digest(raw_policy) != manifest["navigatorPolicyDigest"]:
            raise ProjectionRefused("navigator policy differs from the exact seal")
        policy = SealedNavigatorPolicy.model_validate(raw_policy)
        try:
            logical_fixture = journeys.load_fixture_contract(
                conn,
                version_id=manifest["journeyVersionId"],
                expected_digest=manifest["fixtureDigest"],
            )
        except journeys.JourneyPersistenceError as exc:
            raise ProjectionRefused("original logical fixture contract unavailable") from exc
        fixture = conn.execute(
            "SELECT id,nonce,template_id,template_digest,navigator_values "
            "FROM run_fixture_instance "
            "WHERE run_id=%s AND workspace_id=%s FOR SHARE",
            (reference.run_id, reference.workspace_id),
        ).fetchone()
        if (
            fixture is None
            or logical_fixture["templateId"] != "service-request"
            or fixture["template_id"] != logical_fixture["templateId"]
            or fixture["template_digest"] != REFERENCE_FIXTURE_DIGEST
            or fixture["navigator_values"] != policy.fixture_values
        ):
            raise ProjectionRefused("runtime fixture values differ from the approved template")
        try:
            runtime_start_url = load_destination(
                conn,
                workspace_id=reference.workspace_id,
                run_id=reference.run_id,
                manifest=manifest,
                fixture=fixture,
                sealed_url=policy.start_url,
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise ProjectionRefused("confirmed runtime fixture destination unavailable") from exc
        expires = min(
            session["expires_at"],
            parse_rfc3339_utc(manifest["expiresAt"]),
            session["created_at"]
            + timedelta(seconds=min(policy.wall_time_seconds, manifest["wallTimeBudgetSeconds"])),
        )
        if expires <= datetime.now(UTC):
            raise ProjectionRefused("planning wall-time budget expired")
        actions = conn.execute(
            "SELECT id,action_sequence,action,lease_id,epoch,dispatched_at,result_at,result_status "
            "FROM runner_action WHERE run_id=%s AND attempt_id=%s "
            "ORDER BY action_sequence LIMIT 501",
            (reference.run_id, reference.attempt_id),
        ).fetchall()
        if len(actions) != expected_action_sequence or len(actions) >= min(
            policy.max_actions, manifest["actionBudget"]
        ):
            raise ProjectionRefused("planning boundary changed or action budget exhausted")
        for index, action in enumerate(actions, start=1):
            if (
                action["action_sequence"] != index
                or str(action["lease_id"]) != reference.lease_id
                or action["epoch"] != reference.epoch
                or action["dispatched_at"] is None
                or action["result_at"] is None
                or action["result_status"] not in {"SUCCEEDED", "FAILED"}
                or action["action"] == "STOP"
            ):
                raise ProjectionRefused("planning requires resolved non-STOP action history")
        producer = f"supervisor:{session['id']}:reader"
        stream = conn.execute(
            "SELECT admitted_through,closed_at_sequence FROM producer_stream "
            "WHERE attempt_id=%s AND producer_id=%s",
            (reference.attempt_id, producer),
        ).fetchone()
        if (
            stream is None
            or stream["closed_at_sequence"] is not None
            or stream["admitted_through"] != len(actions)
        ):
            raise ProjectionRefused("complete open original reader stream required")
        # Select only the assigned reader producer, never the adjacent observer/action payloads.
        records = conn.execute(
            "SELECT e.event_id,e.sequence,e.manifest_digest,e.lease_epoch,e.event_type,"
            "e.payload,e.payload_digest,p.producer_sequence,p.source_record_id,"
            "p.source_record_digest,p.canonical_sequence "
            "FROM producer_source_record p JOIN canonical_event e ON e.event_id=p.event_id "
            "AND e.workspace_id=p.workspace_id AND e.run_id=p.run_id AND e.attempt_id=p.attempt_id "
            "WHERE p.workspace_id=%s AND p.run_id=%s AND p.attempt_id=%s AND p.producer_id=%s "
            "ORDER BY p.producer_sequence LIMIT 501",
            (reference.workspace_id, reference.run_id, reference.attempt_id, producer),
        ).fetchall()
        if len(records) != len(actions):
            raise ProjectionRefused("original reader coverage missing")
        observations: list[ReaderObservation] = []
        manifest_digest = digest(manifest)
        for index, (record, action) in enumerate(zip(records, actions, strict=True), start=1):
            payload, source = record["payload"], record["payload"].get("sourceRecord")
            if (
                not isinstance(source, dict)
                or record["event_type"] != "READER_OBSERVATION"
                or record["manifest_digest"] != manifest_digest
                or record["lease_epoch"] != reference.epoch
                or record["producer_sequence"] != index
                or record["canonical_sequence"] != record["sequence"]
                or record["source_record_id"] != str(action["id"])
                or record["source_record_digest"] != record["payload_digest"]
                or record["payload_digest"] != digest(payload)
                or payload.get("sourceRecordDigest") != digest(source)
                or payload.get("producerId") != producer
                or payload.get("producerSequence") != index
                or payload.get("sourceRecordId") != str(action["id"])
                or payload.get("serviceIdentity") != "SUPERVISOR"
                or payload.get("eventType") != "READER_OBSERVATION"
                or payload.get("redaction") != "EXACT_FIXTURE_VALUES"
            ):
                raise ProjectionRefused("original reader binding or content changed")
            observations.append(_observation(source, action))
        # Validation is deliberately complete: oversized speech refuses this turn; it is never
        # silently truncated or replaced with a summary that pretends to be the reader.
        projection = NavigatorProjection.from_policy(
            run_ref="navigator:" + digest(asdict(reference)),
            policy=raw_policy,
            reader_observations=observations,
            runtime_start_url=runtime_start_url,
        )
        if expires <= datetime.now(UTC):
            raise ProjectionRefused("planning snapshot outlived its wall-time budget")
        return RetainedNavigatorTurn(
            projection=projection,
            manifest_digest=manifest_digest,
            model_config_digest=manifest["modelConfigDigest"],
            action_sequence=expected_action_sequence,
            reader_event_ids=tuple(str(row["event_id"]) for row in records),
            reader_payload_digests=tuple(row["payload_digest"] for row in records),
        )


def _observation(source: dict[str, Any], action: dict[str, Any]) -> ReaderObservation:
    unknown = source.get("provenance") == "CAPTURE_UNKNOWN"
    keys = {"actionId", "actionSequence", "capturedAtUtc"} | (
        {"provenance", "reason"} if unknown else {"phrase"}
    )
    if (
        set(source) != keys
        or source["actionId"] != str(action["id"])
        or type(source["actionSequence"]) is not int
        or source["actionSequence"] != action["action_sequence"]
    ):
        raise ProjectionRefused("reader action identity differs")
    captured = parse_rfc3339_utc(source["capturedAtUtc"])
    if (
        not action["dispatched_at"] - timedelta(seconds=5)
        <= captured
        <= action["result_at"] + timedelta(seconds=5)
    ):
        raise ProjectionRefused("reader timestamp differs from original dispatch/result")
    return ReaderObservation.model_validate(
        {**source, "provenance": "CAPTURE_UNKNOWN" if unknown else "ACTUAL_READER"}
    )
