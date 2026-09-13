"""Interpret retained supervisor runtime checks and server-resolved build measurements.

Input must already have passed the finalizer's original-artifact/canonical-stream verification.
This module neither accepts uploads nor derives other observed identities from sealed metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from accessforge_domain.canonical import digest
from accessforge_domain.runners.preflight import REQUIRED_PREFLIGHT_CHECKS
from accessforge_domain.timestamps import parse_rfc3339_utc
from accessforge_orchestrator.execution_artifacts import Refused


@dataclass(frozen=True)
class RuntimeEvidence:
    preflight_passed: bool
    observed_build: str | None
    reasons: tuple[str, ...]


def _build(receipt: Any, source: dict[str, Any], context: dict[str, Any]) -> str | None:
    if receipt is None:
        return None
    if not isinstance(receipt, dict) or not isinstance(receipt.get("receipt"), dict):
        raise Refused("runtime build receipt malformed")
    payload = receipt["receipt"]
    fingerprint = digest(payload)
    if (
        receipt.get("receiptDigest") != fingerprint
        or receipt.get("receiptId")
        != str(uuid5(NAMESPACE_URL, "accessforge:artifact-observation:" + fingerprint))
        or any(
            payload.get(key) != value
            for key, value in {
                "workspaceId": str(context["workspace_id"]),
                "runId": str(context["run_id"]),
                "leaseId": str(context["lease_id"]),
                "leaseEpoch": int(context["epoch"]),
                "meaning": "BUILD_RECEIPT_CONTEXT_NOT_CANONICAL_EXECUTION_EVIDENCE",
            }.items()
        )
    ):
        raise Refused("runtime build receipt context or digest differs")
    observation = payload.get("observation")
    if (
        not isinstance(observation, dict)
        or observation.get("meaning") != "DEPLOYED_FILESYSTEM_MEASUREMENT_NOT_EXECUTION_ATTESTATION"
    ):
        raise Refused("runtime build measurement unavailable")
    build = observation.get("artifactDigest")
    if (
        not isinstance(build, str)
        or len(build) != 64
        or any(char not in "0123456789abcdef" for char in build)
    ):
        raise Refused("runtime build identity malformed")
    try:
        captured = parse_rfc3339_utc(source["capturedAtUtc"])
        measured = parse_rfc3339_utc(observation["observedAt"])
    except (KeyError, ValueError, TypeError) as exc:
        raise Refused("runtime measurement time unavailable") from exc
    if not captured - timedelta(seconds=10) <= measured <= captured:
        raise Refused("runtime build measurement is stale or after the preflight")
    return build


def interpret(snapshots: dict[str, Any], context: dict[str, Any]) -> RuntimeEvidence:
    intents: dict[str, tuple[int, int]] = {}
    results: dict[str, int] = {}
    for event in snapshots["ACTION_TRACE"]["records"]:
        payload, source = event["payload"], event["payload"]["sourceRecord"]
        if payload.get("serviceIdentity") != "SUPERVISOR":
            raise Refused("runtime action provenance differs")
        action_id = source["actionId"]
        if event["eventType"] == "ACTION_INTENT":
            if action_id in intents:
                raise Refused("duplicate runtime action")
            intents[action_id] = (source["sequence"], event["sequence"])
        elif event["eventType"] == "ACTION_RESULT":
            if action_id in results:
                raise Refused("duplicate runtime result")
            results[action_id] = event["sequence"]
    if not intents or set(results) != set(intents):
        raise Refused("complete runtime action boundaries required")
    reports: dict[str, dict[str, Any]] = {}
    builds: list[str] = []
    for event in snapshots["PREFLIGHT_RECORD"]["records"]:
        payload = event["payload"]
        if payload.get("provenance") != "RUNTIME_PROBE_REPORT":
            continue  # Stored admission checks and session receipts are not runtime observations.
        source = payload["sourceRecord"]
        action_id = source.get("actionId")
        if (
            event["eventType"] != "PREFLIGHT_RESULT"
            or payload.get("serviceIdentity") != "SUPERVISOR"
            or action_id not in intents
            or action_id in reports
        ):
            raise Refused("runtime preflight action/provenance differs")
        ordinal, intent_at = intents[action_id]
        checks = source.get("checks")
        if (
            type(source.get("actionSequence")) is not int
            or source.get("actionSequence") != ordinal
            or not intent_at < event["sequence"] < results[action_id]
            or not isinstance(checks, dict)
            or set(checks) != set(REQUIRED_PREFLIGHT_CHECKS)
            or any(value not in ("TRUE", "FALSE", "UNKNOWN") for value in checks.values())
        ):
            raise Refused("runtime preflight coverage or action ordering differs")
        reports[action_id] = checks
        build = _build(payload.get("buildArtifactReceipt"), source, context)
        if build is not None and checks["BUILD_IDENTITY_MATCHES_MANIFEST"] == "TRUE":
            builds.append(build)
    reasons: list[str] = []
    covered = set(reports) == set(intents)
    passed = covered and all(
        value == "TRUE" for checks in reports.values() for value in checks.values()
    )
    if not covered:
        reasons.append("runtime preflight reports do not cover every original action")
    elif not passed:
        reasons.append("runtime preflight contains FALSE or UNKNOWN checks")
    if len(builds) != len(intents) or len(set(builds)) != 1:
        reasons.append("matching runtime build measurements do not cover every original action")
        observed_build = None
    else:
        observed_build = builds[0]
    return RuntimeEvidence(passed, observed_build, tuple(reasons))
