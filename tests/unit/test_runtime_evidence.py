"""Synthetic retained records for CI; these are not physical reader execution proof."""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from accessforge_domain.canonical import digest
from accessforge_domain.runners.preflight import REQUIRED_PREFLIGHT_CHECKS
from accessforge_orchestrator.execution_artifacts import Refused
from accessforge_orchestrator.runtime_evidence import interpret

CONTEXT = {"workspace_id": "workspace", "run_id": "run", "lease_id": "lease", "epoch": 1}


@pytest.mark.parametrize("fault", [None, "candidate-namespace", "unknown-kind", "missing-kind"])
def test_baseline_receipt_requires_its_explicit_kind_and_namespace(fault: str | None) -> None:
    snapshots = fixture()
    for event in snapshots["PREFLIGHT_RECORD"]["records"]:
        receipt = event["payload"]["buildArtifactReceipt"]
        payload = receipt["receipt"]
        if fault != "missing-kind":
            payload["runtimeKind"] = "UNTRUSTED" if fault == "unknown-kind" else "BASELINE"
        fingerprint = digest(payload)
        namespace = (
            "accessforge:artifact-observation:"
            if fault == "candidate-namespace"
            else "accessforge:baseline-artifact-observation:"
        )
        receipt["receiptDigest"] = fingerprint
        receipt["receiptId"] = str(uuid5(NAMESPACE_URL, namespace + fingerprint))
    if fault is None:
        assert interpret(snapshots, CONTEXT).observed_build == "a" * 64
    else:
        with pytest.raises(Refused):
            interpret(snapshots, CONTEXT)


@pytest.mark.parametrize("case", ["complete", "missing", "changed", "unknown", "extra"])
def test_runner_profile_requires_complete_matching_observations(case: str) -> None:
    snapshots = fixture()
    profile = {
        "platform": "darwin",
        "readerName": "VoiceOver",
        "readerVersion": "bundled with macOS 26.6 (build 25G72)",
        "browserName": "Safari",
        "browserVersion": "26.6",
        "locale": "en-US",
        "keyboardLayout": "com.apple.keylayout.US",
    }
    reports = snapshots["PREFLIGHT_RECORD"]["records"]
    for report in reports:
        report["payload"]["sourceRecord"]["runnerProfile"] = dict(profile)
    first = reports[0]["payload"]["sourceRecord"]
    if case == "missing":
        del first["runnerProfile"]
    elif case == "changed":
        first["runnerProfile"]["locale"] = "en-GB"
    elif case == "unknown":
        first["checks"]["DESKTOP_SESSION_OWNED"] = "UNKNOWN"
    elif case == "extra":
        first["runnerProfile"]["privatePath"] = "/private"
    if case == "extra":
        with pytest.raises(Refused):
            interpret(snapshots, CONTEXT)
    else:
        result = interpret(snapshots, CONTEXT)
        assert result.observed_runner_profile == (digest(profile) if case == "complete" else None)


def fixture() -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    for ordinal in (1, 2):
        payload = {
            "workspaceId": "workspace",
            "runId": "run",
            "leaseId": "lease",
            "leaseEpoch": 1,
            "meaning": "BUILD_RECEIPT_CONTEXT_NOT_CANONICAL_EXECUTION_EVIDENCE",
            "observation": {
                "artifactDigest": "a" * 64,
                "observedAt": "2026-09-13T00:00:01Z",
                "meaning": "DEPLOYED_FILESYSTEM_MEASUREMENT_NOT_EXECUTION_ATTESTATION",
            },
        }
        fingerprint = digest(payload)
        receipt = {
            "receipt": payload,
            "receiptDigest": fingerprint,
            "receiptId": str(
                uuid5(NAMESPACE_URL, "accessforge:artifact-observation:" + fingerprint)
            ),
        }
        for kind, offset in (("ACTION_INTENT", 0), ("ACTION_RESULT", 2)):
            actions.append(
                {
                    "eventType": kind,
                    "sequence": ordinal * 3 + offset,
                    "payload": {
                        "serviceIdentity": "SUPERVISOR",
                        "sourceRecord": {"actionId": str(ordinal), "sequence": ordinal},
                    },
                }
            )
        reports.append(
            {
                "eventType": "PREFLIGHT_RESULT",
                "sequence": ordinal * 3 + 1,
                "payload": {
                    "serviceIdentity": "SUPERVISOR",
                    "provenance": "RUNTIME_PROBE_REPORT",
                    "buildArtifactReceipt": receipt,
                    "sourceRecord": {
                        "actionId": str(ordinal),
                        "actionSequence": ordinal,
                        "capturedAtUtc": "2026-09-13T00:00:02Z",
                        "checks": dict.fromkeys(REQUIRED_PREFLIGHT_CHECKS, "TRUE"),
                    },
                },
            }
        )
    return {"ACTION_TRACE": {"records": actions}, "PREFLIGHT_RECORD": {"records": reports}}


def test_complete_runtime_reports_use_measurement_not_sealed_build() -> None:
    result = interpret(fixture(), CONTEXT)
    assert result.preflight_passed and result.observed_build == "a" * 64
    assert result.observed_source is None
    assert result.reasons == (
        "captured source-to-build lineage does not cover every original action",
        "matching observed runner profiles do not cover every original action",
    )


def _rehash(receipt: dict[str, Any]) -> None:
    fingerprint = digest(receipt["receipt"])
    receipt["receiptDigest"] = fingerprint
    receipt["receiptId"] = str(
        uuid5(NAMESPACE_URL, "accessforge:artifact-observation:" + fingerprint)
    )


def _with_lineage() -> dict[str, Any]:
    snapshots = fixture()
    for report in snapshots["PREFLIGHT_RECORD"]["records"]:
        receipt = report["payload"]["buildArtifactReceipt"]
        payload = receipt["receipt"]
        payload["buildId"] = str(UUID(int=1))
        payload["observation"].update(imageId="sha256:" + "b" * 64, daemonId="daemon")
        payload["sourceLineage"] = {
            "meaning": "CAPTURED_BUILD_INPUT_LINEAGE_NOT_RUNTIME_SOURCE_READ",
            "workspaceId": "workspace",
            "buildId": str(UUID(int=1)),
            "sourceSnapshotId": str(UUID(int=2)),
            "buildArtifactId": str(UUID(int=3)),
            "sourceTreeDigest": "c" * 64,
            "sourceArchiveDigest": "d" * 64,
            "artifactDigest": "a" * 64,
            "buildContainerId": "e" * 64,
            "imageId": "sha256:" + "b" * 64,
            "daemonId": "daemon",
            "sourceCapturedAt": "2026-09-12T23:59:00Z",
            "buildDispatchedAt": "2026-09-12T23:59:01Z",
            "buildFinishedAt": "2026-09-13T00:00:00Z",
            "artifactPublishedAt": "2026-09-13T00:00:00.000001Z",
        }
        _rehash(receipt)
    return snapshots


def test_source_is_the_captured_input_linked_to_every_measured_build() -> None:
    result = interpret(_with_lineage(), CONTEXT)
    assert result.observed_source == "c" * 64  # Not the output archive digest or a sealed input.
    assert result.observed_build == "a" * 64
    assert result.observed_runner_profile is None
    assert result.reasons == (
        "matching observed runner profiles do not cover every original action",
    )


@pytest.mark.parametrize("fault", ["missing", "different-source", "build-unknown"])
def test_partial_or_conflicting_lineage_never_establishes_source(fault: str) -> None:
    snapshots = _with_lineage()
    report = snapshots["PREFLIGHT_RECORD"]["records"][0]
    receipt = report["payload"]["buildArtifactReceipt"]
    if fault == "missing":
        del receipt["receipt"]["sourceLineage"]
    elif fault == "different-source":
        receipt["receipt"]["sourceLineage"]["sourceTreeDigest"] = "f" * 64
    else:
        report["payload"]["sourceRecord"]["checks"]["BUILD_IDENTITY_MATCHES_MANIFEST"] = "UNKNOWN"
    _rehash(receipt)
    assert interpret(snapshots, CONTEXT).observed_source is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspaceId", "foreign"),
        ("buildId", str(UUID(int=4))),
        ("artifactDigest", "f" * 64),
        ("imageId", "other-image"),
        ("daemonId", "other-daemon"),
        ("sourceSnapshotId", "not-a-uuid"),
        ("sourceTreeDigest", "not-a-digest"),
        ("sourceCapturedAt", "2026-09-13T00:00:00Z"),
        ("artifactPublishedAt", "2026-09-13T00:00:01.000001Z"),
    ],
)
def test_lineage_must_match_its_original_measurement_and_causal_order(
    field: str, value: str
) -> None:
    snapshots = _with_lineage()
    receipt = snapshots["PREFLIGHT_RECORD"]["records"][0]["payload"]["buildArtifactReceipt"]
    receipt["receipt"]["sourceLineage"][field] = value
    _rehash(receipt)  # A consistent envelope hash does not establish matching provenance.
    with pytest.raises(Refused):
        interpret(snapshots, CONTEXT)


@pytest.mark.parametrize("missing", ["all", "one", "measurement", "unknown", "build-unknown"])
def test_missing_or_unknown_evidence_does_not_become_complete(missing: str) -> None:
    snapshots = fixture()
    reports = snapshots["PREFLIGHT_RECORD"]["records"]
    if missing == "all":
        for report in reports:
            report["payload"]["provenance"] = "CONTROL_PLANE_RECEIPT"
    elif missing == "one":
        reports.pop()
    elif missing == "measurement":
        del reports[0]["payload"]["buildArtifactReceipt"]
    else:
        check = (
            "BUILD_IDENTITY_MATCHES_MANIFEST"
            if missing == "build-unknown"
            else next(
                name
                for name in REQUIRED_PREFLIGHT_CHECKS
                if name != "BUILD_IDENTITY_MATCHES_MANIFEST"
            )
        )
        reports[0]["payload"]["sourceRecord"]["checks"][check] = "UNKNOWN"
    result = interpret(snapshots, CONTEXT)
    assert result.preflight_passed is (missing == "measurement")
    assert result.observed_build == ("a" * 64 if missing == "unknown" else None)
    assert result.reasons


@pytest.mark.parametrize(
    "fault", ["digest", "foreign", "stale", "future", "order", "bool", "duplicate"]
)
def test_untrusted_or_misordered_runtime_records_refused(fault: str) -> None:
    snapshots = fixture()
    reports = snapshots["PREFLIGHT_RECORD"]["records"]
    report = reports[0]
    receipt = report["payload"]["buildArtifactReceipt"]
    if fault in {"digest", "foreign"}:
        receipt["receipt"]["leaseId"] = "other-lease"
        if fault == "foreign":
            fingerprint = digest(receipt["receipt"])
            receipt["receiptDigest"] = fingerprint
            receipt["receiptId"] = str(
                uuid5(NAMESPACE_URL, "accessforge:artifact-observation:" + fingerprint)
            )
    elif fault in {"stale", "future"}:
        report["payload"]["sourceRecord"]["capturedAtUtc"] = (
            "2026-09-13T00:00:12Z" if fault == "stale" else "2026-09-13T00:00:00Z"
        )
    elif fault == "order":
        report["sequence"] = 5
    elif fault == "bool":
        report["payload"]["sourceRecord"]["actionSequence"] = True
    else:
        reports.append(report)
    with pytest.raises(Refused):
        interpret(snapshots, CONTEXT)
