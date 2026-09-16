"""Retained synthetic archive inspection, never an actual benchmark run."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from accessforge_domain.canonical import canonicalize, digest
from accessforge_domain.evaluation.identity import ALWAYS_REQUIRED
from accessforge_evidence import KeyProvenance, SigningKey, TrustRoot, write_archive

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import benchmark_evidence  # noqa: E402
from benchmark_evidence import retained_report  # noqa: E402


def inputs(bundle_digest: str) -> tuple[dict[str, Any], dict[str, Any]]:
    identity = {
        "sourceCommit": "1" * 40,
        **{
            key: "2" * 64
            for key in (
                "buildDigest",
                "profileDigest",
                "evaluatorDigest",
                "lockfileDigest",
            )
        },
        "evidenceClass": "UNIT",
        "versions": {"reader": "not-run"},
    }
    return {
        "identity": identity,
        "corpusVersion": "synthetic-v1",
        "cases": [
            {"caseId": "clean", "category": "CLEAN", "repetitions": 2, "oracleDigest": "3" * 64}
        ],
    }, {
        "observations": [
            {
                "caseId": "clean",
                "repetition": 1,
                "identity": identity,
                "status": "COMPLETED",
                "outcome": "FAIL",
                "bundleDigest": bundle_digest,
                "durationMs": 1.0,
            }
        ]
    }


def retained_bundle(directory: Path, *, readable_outcome: str = "FAIL") -> tuple[str, TrustRoot]:
    events = [{"sequence": 1, "synthetic": True}]
    manifest = {
        "schemaVersion": "1.0.0",
        "canonicalizationVersion": "RFC8785+SHA256/1",
        "outcome": "FAIL",
        "evaluatorVersion": "synthetic-v1",
        "eventChainDigest": digest({"events": events}),
        "identities": {str(kind): "synthetic" for kind in ALWAYS_REQUIRED},
        "producers": [{"producerId": "synthetic", "closedAtSequence": 1, "admittedThrough": 1}],
        "artifacts": [],
        "reviewIds": [],
        "trustLevel": "FULLY_VERIFIABLE",
        "completeness": {
            "canonicalChainContiguous": True,
            "allRequiredProducersClosed": True,
            "allRequiredArtifactsPresent": True,
        },
    }
    key = SigningKey.generate(key_id="synthetic", issuer="synthetic")
    signed = canonicalize(manifest).encode()
    attestation = key.sign(signed)
    document = {
        "manifest": manifest,
        "events": events,
        "reviews": [],
        "machineOutcome": {
            "outcome": readable_outcome,
            "evaluatorVersion": "synthetic-v1",
            "reasons": [],
        },
    }
    archive = directory / "new.zip"
    write_archive(
        str(archive),
        {
            "bundle.json": json.dumps(document).encode(),
            "manifest.canonical.json": signed,
            "attestation.json": json.dumps(
                {
                    "keyId": attestation.key_id,
                    "issuer": attestation.issuer,
                    "algorithm": attestation.algorithm,
                    "signature": attestation.signature_base64,
                }
            ).encode(),
        },
    )
    hashed = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.rename(directory / f"{hashed}.zip")
    return hashed, key.trust_root(provenance=KeyProvenance.EXTERNALLY_SUPPLIED)


def test_retained_signature_findings_preserve_denominators(tmp_path: Path) -> None:
    hashed, root = retained_bundle(tmp_path)
    result = retained_report(*inputs(hashed), tmp_path, trust_root=root)
    row = result["rows"][0]["retainedEvidence"]
    assert row["status"] == "INSPECTED"
    assert row["archiveDigestMatches"] and row["integrityIntact"]
    assert row["signatureVerifiedWithIndependentKey"] and row["declaredOutcomeMatchesManifest"]
    assert not row["cohortIdentityVerified"] and not row["independentExecutionVerified"]
    assert result["plannedRepetitions"] == 2 and result["counts"]["NOT_RUN"] == 1
    assert result["declaredFalseDefects"] == 1 and not result["evidenceVerified"]
    assert result["rows"][1]["retainedEvidence"] == {"status": "NO_REFERENCE"}


def test_absent_and_wrong_trust_roots_never_become_trusted(tmp_path: Path) -> None:
    hashed, _ = retained_bundle(tmp_path)
    absent = retained_report(*inputs(hashed), tmp_path)["rows"][0]["retainedEvidence"]
    assert not absent["signatureVerifiedWithIndependentKey"]
    assert not absent["independentTrustRoot"]
    assert absent["findingCounts"]["UNSUPPORTED"] >= 1
    key = SigningKey.generate(key_id="synthetic", issuer="synthetic")
    wrong = retained_report(
        *inputs(hashed),
        tmp_path,
        trust_root=key.trust_root(
            provenance=KeyProvenance.EXTERNALLY_SUPPLIED,
        ),
    )["rows"][0]["retainedEvidence"]
    assert not wrong["signatureVerifiedWithIndependentKey"] and not wrong["integrityIntact"]
    assert wrong["independentTrustRoot"] and not wrong["signatureMatches"]


def test_signed_fail_and_readable_pass_cannot_match(tmp_path: Path) -> None:
    hashed, root = retained_bundle(tmp_path, readable_outcome="PASS")
    plan, results = inputs(hashed)
    results["observations"][0]["outcome"] = "PASS"
    row = retained_report(plan, results, tmp_path, trust_root=root)["rows"][0]["retainedEvidence"]
    assert row["signatureVerifiedWithIndependentKey"]
    assert not row["declaredOutcomeMatchesManifest"]


@pytest.mark.parametrize("case", ["missing", "changed", "symlink", "invalid", "shape"])
def test_unusable_references_still_count(tmp_path: Path, case: str) -> None:
    hashed = "a" * 64
    expected = {
        "missing": "MISSING",
        "changed": "DIGEST_MISMATCH",
        "symlink": "UNREADABLE",
        "invalid": "INVALID_BUNDLE",
        "shape": "INVALID_BUNDLE",
    }[case]
    if case == "changed":
        (tmp_path / f"{hashed}.zip").write_bytes(b"changed")
    elif case == "symlink":
        (tmp_path / f"{hashed}.zip").symlink_to(tmp_path / "private-target")
    elif case in {"invalid", "shape"}:
        path = tmp_path / "input.zip"
        if case == "shape":
            write_archive(
                str(path),
                {"bundle.json": b"[]", "manifest.canonical.json": b"{}", "attestation.json": b"{}"},
            )
        else:
            path.write_bytes(b"not a zip")
        hashed = hashlib.sha256(path.read_bytes()).hexdigest()
        path.rename(tmp_path / f"{hashed}.zip")
    result = retained_report(*inputs(hashed), tmp_path)
    assert result["rows"][0]["retainedEvidence"] == {"status": expected}
    assert result["counts"]["COMPLETED"] == 1 and result["plannedRepetitions"] == 2
    assert result["declaredFalseDefects"] == 1
    assert "private-target" not in json.dumps(result)


def test_verification_uses_the_hashed_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hashed, root = retained_bundle(tmp_path)
    original = benchmark_evidence._snapshot

    def replace_after_hash(source: Path, target: Path, expected: str) -> bool:
        matched = original(source, target, expected)
        source.write_bytes(b"replaced after hashing")
        return matched

    monkeypatch.setattr(benchmark_evidence, "_snapshot", replace_after_hash)
    first = retained_report(*inputs(hashed), tmp_path, trust_root=root)
    assert first["rows"][0]["retainedEvidence"]["signatureVerifiedWithIndependentKey"]
    second = retained_report(*inputs(hashed), tmp_path, trust_root=root)
    assert second["rows"][0]["retainedEvidence"] == {"status": "DIGEST_MISMATCH"}


def test_oversized_reference_is_not_read_as_a_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hashed, _ = retained_bundle(tmp_path)
    monkeypatch.setattr(benchmark_evidence, "MAX_ARCHIVE_BYTES", 1)
    result = retained_report(*inputs(hashed), tmp_path)
    assert result["rows"][0]["retainedEvidence"] == {"status": "INVALID_BUNDLE"}
