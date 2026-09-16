"""Offline signed synthetic archives; no reader, model or application acceptance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from accessforge_domain.canonical import canonicalize, digest
from accessforge_domain.evaluation.identity import ALWAYS_REQUIRED
from accessforge_evidence import (
    CheckOutcome,
    KeyProvenance,
    SigningKey,
    verify_archive,
    write_archive,
)


def fixture() -> tuple[dict[str, Any], dict[str, bytes], SigningKey]:
    events = [{"sequence": 1, "synthetic": True}]
    manifest = {
        "schemaVersion": "1.0.0",
        "canonicalizationVersion": "RFC8785+SHA256/1",
        "outcome": "FAIL",
        "evaluatorVersion": "test-evaluator-v1",
        "eventChainDigest": digest({"events": events}),
        "identities": {str(kind): "synthetic-identity" for kind in ALWAYS_REQUIRED},
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
    document = {
        "manifest": manifest,
        "events": events,
        "reviews": [],
        "machineOutcome": {
            "outcome": "FAIL",
            "evaluatorVersion": "test-evaluator-v1",
            "reasons": ["synthetic-failure"],
        },
    }
    key = SigningKey.generate(key_id="synthetic-test", issuer="synthetic-test")
    manifest_bytes = canonicalize(manifest).encode()
    attestation = key.sign(manifest_bytes)
    return (
        document,
        {
            "manifest.canonical.json": manifest_bytes,
            "attestation.json": json.dumps(
                {
                    "keyId": attestation.key_id,
                    "issuer": attestation.issuer,
                    "algorithm": attestation.algorithm,
                    "signature": attestation.signature_base64,
                }
            ).encode(),
        },
        key,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"outcome": "PASS"},
        {"outcome": "INCONCLUSIVE"},
        {"outcome": "invented"},
        {"outcome": []},
        {"evaluatorVersion": "different-evaluator"},
        {"evaluatorVersion": 3},
        {"evaluatorVersion": ""},
        {"reasons": None},
        {"reasons": [7]},
        None,
    ],
)
def test_readable_outcome_cannot_disagree_with_signed_manifest(
    tmp_path: Path,
    mutation: dict[str, Any] | None,
) -> None:
    document, members, key = fixture()
    root = key.trust_root(provenance=KeyProvenance.EXTERNALLY_SUPPLIED)
    members["bundle.json"] = json.dumps(document).encode()
    original = tmp_path / "original.zip"
    write_archive(str(original), members)
    control = verify_archive(str(original), trust_root=root)
    assert control.integrity_intact
    assert control.independently_trusted

    if mutation is None:
        document["machineOutcome"] = None
    else:
        document["machineOutcome"].update(mutation)
    members["bundle.json"] = json.dumps(document).encode()
    changed = tmp_path / "changed.zip"
    write_archive(str(changed), members)
    checked = verify_archive(str(changed), trust_root=root)
    assert checked.exit_code == 1
    assert not checked.integrity_intact
    assert checked.independently_trusted  # The unchanged manifest signature still verifies.
    assert [(finding.check, finding.outcome) for finding in checked.failed] == [
        ("outcome.frozen", CheckOutcome.FAILED),
    ]


def test_reason_text_is_not_misrepresented_as_signed(tmp_path: Path) -> None:
    document, members, key = fixture()
    document["machineOutcome"]["reasons"] = ["changed unsigned explanation"]
    members["bundle.json"] = json.dumps(document).encode()
    archive = tmp_path / "reasons.zip"
    write_archive(str(archive), members)
    checked = verify_archive(
        str(archive),
        trust_root=key.trust_root(provenance=KeyProvenance.EXTERNALLY_SUPPLIED),
    )
    assert checked.integrity_intact
    finding = next(f for f in checked.findings if f.check == "outcome.frozen")
    assert "not covered" in finding.detail
