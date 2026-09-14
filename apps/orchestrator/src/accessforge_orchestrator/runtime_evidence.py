"""Interpret retained supervisor runtime checks and server-resolved build measurements.

Input must already have passed the finalizer's original-artifact/canonical-stream verification.
This module neither accepts uploads nor derives other observed identities from sealed metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from accessforge_domain.canonical import digest
from accessforge_domain.evaluation.rules import ReaderSample
from accessforge_domain.navigator_runtime import validate_observation
from accessforge_domain.runners.preflight import REQUIRED_PREFLIGHT_CHECKS
from accessforge_domain.runners.runtime_profile import observed_profile
from accessforge_domain.timestamps import parse_rfc3339_utc
from accessforge_orchestrator.execution_artifacts import Refused


@dataclass(frozen=True)
class RuntimeEvidence:
    preflight_passed: bool
    observed_build: str | None
    reasons: tuple[str, ...]
    observed_source: str | None = None
    observed_runner_profile: str | None = None


def observed_model(snapshots: dict[str, Any], context: dict[str, Any]) -> str | None:
    """Use original retained SDK observations only when every action has an admitted model turn.

    This observes requested model configuration, not provider-internal weights or physical AT.
    Reservation/STARTED/checkpoint model names alone never establish a model identity.
    """
    model = snapshots.get("MODEL_RUNTIME")
    if model is None:
        return None
    if (
        not isinstance(model, dict)
        or model.get("format") != "accessforge.model-runtime.v1"
        or model.get("runId") != str(context["run_id"])
        or model.get("attemptId") != str(context["attempt_id"])
        or model.get("manifestDigest") != context["manifest_digest"]
    ):
        raise Refused("model runtime artifact context differs")
    turns = model.get("turns")
    if not isinstance(turns, list) or not 1 <= len(turns) <= 500:
        raise Refused("bounded model runtime turns unavailable")
    actions = [
        event["payload"]["sourceRecord"]
        for event in snapshots["ACTION_TRACE"]["records"]
        if event["eventType"] == "ACTION_INTENT"
    ]
    if len(turns) != len(actions):
        return None
    identities: set[str] = set()
    operations: set[str] = set()
    for sequence, (turn, action) in enumerate(zip(turns, actions, strict=True)):
        if (
            turn.get("status") != "RECORDED"
            or turn.get("observation") is None
            or turn.get("actionSequence") != sequence
            or action.get("sequence") != sequence + 1
            or turn.get("resolvedActionId") != action.get("actionId")
        ):
            return None
        try:
            observation = validate_observation(turn["observation"])
        except ValueError as exc:
            raise Refused("invalid original model runtime observation") from exc
        if (
            digest(observation) != turn.get("observationDigest")
            or digest(observation["profile"]) != turn.get("modelConfigDigest")
            or turn["operationId"] in operations
        ):
            raise Refused("model runtime observation digest or operation differs")
        operations.add(turn["operationId"])
        identities.add(digest(observation["profile"]))
    if len(identities) != 1:
        raise Refused("runtime model identity changed between actions")
    return identities.pop()


def reader_samples(snapshots: dict[str, Any]) -> tuple[ReaderSample, ...]:
    """Join already-verified original streams, never trust an observation's claimed action type.

    NEXT eligibility needs its successful result and canonical intent < speech < result.
    This does not upgrade an SDK result into independent physical or profile attestation.
    """
    intents: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    for event in snapshots["ACTION_TRACE"]["records"]:
        payload = event["payload"]
        if payload.get("serviceIdentity") != "SUPERVISOR":
            raise Refused("reader action provenance differs")
        if event["eventType"] not in {"ACTION_INTENT", "ACTION_RESULT"}:
            raise Refused("unexpected reader action boundary")
        destination = intents if event["eventType"] == "ACTION_INTENT" else results
        action_id = payload["sourceRecord"]["actionId"]
        if action_id in destination:
            raise Refused("duplicate reader action boundary")
        destination[action_id] = event
    samples = []
    for event in snapshots["SPEECH_TRANSCRIPT"]["records"]:
        payload, source = event["payload"], event["payload"]["sourceRecord"]
        if (
            event["eventType"] != "READER_OBSERVATION"
            or payload.get("serviceIdentity") != "SUPERVISOR"
        ):
            raise Refused("reader artifact provenance differs")
        intent, result = intents.get(source["actionId"]), results.get(source["actionId"])
        verified = False
        if intent is not None and result is not None:
            command, completed = (
                intent["payload"]["sourceRecord"],
                result["payload"]["sourceRecord"],
            )
            verified = (
                command.get("action") == "NEXT"
                and completed.get("status") == "SUCCEEDED"
                and type(source["actionSequence"]) is int
                and command.get("sequence") == source["actionSequence"] == completed.get("sequence")
                and intent["sequence"] < event["sequence"] < result["sequence"]
            )
        samples.append(
            ReaderSample(
                source["actionSequence"],
                event["eventId"],
                source.get("phrase"),
                capture_unknown=source.get("status") == "CAPTURE_UNKNOWN" or "phrase" not in source,
                redacted=payload.get("submittedSourceRecordDigest")
                != payload["sourceRecordDigest"],
                next_action_verified=verified,
                canonical_sequence=event["sequence"],
            )
        )
    return tuple(samples)


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


def _source_lineage(receipt: dict[str, Any], build: str) -> str | None:
    """Only after _build verifies this original, server-resolved measurement receipt."""
    payload = receipt["receipt"]
    lineage = payload.get("sourceLineage")
    if lineage is None:
        return None  # Older receipts cannot be enriched from today's mutable control-plane rows.
    fields = {
        "meaning",
        "workspaceId",
        "buildId",
        "sourceSnapshotId",
        "sourceTreeDigest",
        "sourceArchiveDigest",
        "buildArtifactId",
        "artifactDigest",
        "buildContainerId",
        "imageId",
        "daemonId",
        "sourceCapturedAt",
        "buildDispatchedAt",
        "buildFinishedAt",
        "artifactPublishedAt",
    }
    if (
        not isinstance(lineage, dict)
        or set(lineage) != fields
        or any(not isinstance(value, str) or not value for value in lineage.values())
        or lineage["meaning"] != "CAPTURED_BUILD_INPUT_LINEAGE_NOT_RUNTIME_SOURCE_READ"
        or lineage["workspaceId"] != payload["workspaceId"]
        or lineage["buildId"] != payload.get("buildId")
        or lineage["artifactDigest"] != build
        or lineage["imageId"] != payload["observation"].get("imageId")
        or lineage["daemonId"] != payload["observation"].get("daemonId")
        or any(
            len(lineage[key]) != 64 or any(char not in "0123456789abcdef" for char in lineage[key])
            for key in ("sourceTreeDigest", "sourceArchiveDigest", "buildContainerId")
        )
    ):
        raise Refused("runtime source lineage differs from the measured build")
    try:
        for key in ("sourceSnapshotId", "buildArtifactId", "buildId"):
            UUID(lineage[key])
        moments = [
            parse_rfc3339_utc(lineage[key])
            for key in (
                "sourceCapturedAt",
                "buildDispatchedAt",
                "buildFinishedAt",
                "artifactPublishedAt",
            )
        ] + [parse_rfc3339_utc(payload["observation"]["observedAt"])]
    except (KeyError, TypeError, ValueError) as exc:
        raise Refused("runtime source lineage identity or time malformed") from exc
    if any(left > right for left, right in zip(moments, moments[1:], strict=False)):
        raise Refused("runtime source lineage does not precede the measured deployment")
    return str(lineage["sourceTreeDigest"])


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
    sources: list[str] = []
    profiles: list[str] = []
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
        if "runnerProfile" in source:
            try:
                profile = observed_profile(source["runnerProfile"])
            except ValueError as exc:
                raise Refused("retained runtime profile malformed") from exc
            if all(
                checks[key] == "TRUE"
                for key in (
                    "READER_ACTIVE",
                    "READER_VERSION_MATCHES_PROFILE",
                    "BROWSER_VERSION_MATCHES_PROFILE",
                    "DESKTOP_SESSION_OWNED",
                    "NO_STALE_INPUT_SOURCE",
                )
            ):
                profiles.append(profile.digest)
        build = _build(payload.get("buildArtifactReceipt"), source, context)
        if build is not None and checks["BUILD_IDENTITY_MATCHES_MANIFEST"] == "TRUE":
            builds.append(build)
            source_tree = _source_lineage(payload["buildArtifactReceipt"], build)
            if source_tree is not None:
                sources.append(source_tree)
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
    observed_source = None
    if observed_build is not None and len(sources) == len(intents) and len(set(sources)) == 1:
        observed_source = sources[0]
    else:
        reasons.append("captured source-to-build lineage does not cover every original action")
    runner_profile = None
    if len(profiles) == len(intents) and len(set(profiles)) == 1:
        runner_profile = profiles[0]
    else:
        reasons.append("matching observed runner profiles do not cover every original action")
    return RuntimeEvidence(passed, observed_build, tuple(reasons), observed_source, runner_profile)
