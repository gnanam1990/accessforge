"""Export a real bundle, sign it, and try to break it.

The attack cases the module prompt lists: modified bytes, gapped events, a missing observer
watermark, an altered identity, a missing review, an unknown schema, malicious archive paths and
deleted required evidence. Plus the two that decide whether a signature means anything — a forged
signature, and a key that travelled inside the bundle it is supposed to authenticate.

The verifier runs against the archive with no database and no network. That is the property being
tested: a bundle checkable only by the service that made it is not independently inspectable.

Requirements: FR-013, FR-014, FR-015, FR-020. Invariants: INV-03, INV-06, INV-11, INV-15.
"""

from __future__ import annotations

import json
import os
import uuid
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from accessforge_domain.canonical import digest
from accessforge_evidence import (
    CheckOutcome,
    KeyProvenance,
    SigningKey,
    TrustLevel,
    to_json,
    verify_archive,
    write_archive,
)
from accessforge_persistence import (
    assert_row_level_security_enforced,
    evidence,
    migrate,
    runs,
    sequencer,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0xE0))
EXPORTER = str(uuid.UUID(int=0xE1))
VIEWER = str(uuid.UUID(int=0xE2))
MANIFEST = digest({"m": "17"})
SUPERVISOR = "supervisor:mac-01"
OBSERVER = "observer:receipts"
TRANSCRIPT = b'{"phrases": ["Email, invalid entry"]}'

IDENTITIES = {
    "SOURCE": "source-aaa",
    "BUILD": "build-bbb",
    "ENVIRONMENT": "env-ccc",
    "RUNNER_PROFILE": "profile-ddd",
    "EVALUATOR": "evaluator@1.4.0",
    "JOURNEY_VERSION": "journey-eee",
    "ASSERTION_SET": "assertions-fff",
    "FIXTURE_INSTANCE": "fixture-ggg",
}


@pytest.fixture(scope="session")
def store() -> evidence.S3ArtifactStore:
    endpoint = os.environ.get("OBJECT_STORE_ENDPOINT")
    if not endpoint:
        pytest.fail("OBJECT_STORE_ENDPOINT is not configured; there is no filesystem fallback")
    s3 = evidence.S3ArtifactStore(
        evidence.S3Settings(
            endpoint_url=endpoint,
            access_key=os.environ["OBJECT_STORE_ACCESS_KEY"],
            secret_key=os.environ["OBJECT_STORE_SECRET_KEY"],
            bucket=os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
        )
    )
    s3.ensure_bucket()
    return s3


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
        conn.execute("TRUNCATE app_user CASCADE")
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (WS, "A"))
        for user in (EXPORTER, VIEWER):
            conn.execute(
                "INSERT INTO app_user (id, email) VALUES (%s, %s)",
                (user, f"{user}@example.test"),
            )
    # Membership is workspace-scoped, so writing it requires the matching scope. An unscoped
    # connection is refused by the row-level security policy, which is the policy working.
    with workspace_connection(test_database_url, WS) as conn:
        for user, role in ((EXPORTER, "MAINTAINER"), (VIEWER, "VIEWER")):
            conn.execute(
                "INSERT INTO workspace_membership (workspace_id, user_id, role) "
                "VALUES (%s, %s, %s)",
                (WS, user, role),
            )
    yield test_database_url


@pytest.fixture()
def signing_key() -> SigningKey:
    return SigningKey.generate(key_id="af-test-2026-09", issuer="accessforge-service")


def _complete_run(url: str, s3: evidence.S3ArtifactStore) -> tuple[str, str]:
    """A run with a contiguous chain, both producers closed, one promoted artifact, identities."""
    with workspace_connection(url, WS) as conn:
        run_id = runs.create_run(conn, workspace_id=WS, manifest_digest=MANIFEST)
        attempt_id = runs.start_attempt(conn, run_id=run_id, workspace_id=WS, lease_epoch=1)
        for kind, value in IDENTITIES.items():
            conn.execute(
                "INSERT INTO run_identity (workspace_id, run_id, kind, value) "
                "VALUES (%s, %s, %s, %s)",
                (WS, run_id, kind, value),
            )
        evidence.declare_required_artifacts(
            conn, workspace_id=WS, run_id=run_id, requirements={"SPEECH_TRANSCRIPT": SUPERVISOR}
        )

    for producer, seq, kind in (
        (SUPERVISOR, 1, "RUN_STARTED"),
        (SUPERVISOR, 2, "READER_OBSERVATION"),
        (SUPERVISOR, 3, "RUN_FINISHED"),
        (OBSERVER, 1, "EFFECT_RECEIPT"),
    ):
        with workspace_connection(url, WS) as conn:
            sequencer.admit_record(
                conn,
                workspace_id=WS,
                run_id=run_id,
                attempt_id=attempt_id,
                lease_epoch=1,
                producer_id=producer,
                source_record_id=f"{producer}:{seq}",
                producer_sequence=seq,
                event_type=kind,
                manifest_digest=MANIFEST,
                payload={"n": seq},
                source_time=datetime(2026, 9, 10, 12, seq, tzinfo=UTC),
            )
    for producer, final in ((SUPERVISOR, 3), (OBSERVER, 1)):
        with workspace_connection(url, WS) as conn:
            sequencer.close_producer_stream(
                conn,
                workspace_id=WS,
                run_id=run_id,
                attempt_id=attempt_id,
                producer_id=producer,
                final_producer_sequence=final,
            )

    with workspace_connection(url, WS) as conn:
        artifact = evidence.upload_to_quarantine(
            conn,
            s3,
            workspace_id=WS,
            run_id=run_id,
            attempt_id=attempt_id,
            kind="SPEECH_TRANSCRIPT",
            producer_id=SUPERVISOR,
            lease_epoch=1,
            manifest_digest=MANIFEST,
            content_type="application/json",
            payload=TRANSCRIPT,
        )
        evidence.promote(
            conn, s3, artifact_id=artifact.artifact_id, expected_manifest_digest=MANIFEST
        )
    return run_id, attempt_id


def _export(
    url: str,
    s3: evidence.S3ArtifactStore,
    key: SigningKey,
    path: Path,
    *,
    run_id: str,
    attempt_id: str,
    include_bytes: bool = True,
    requested_by: str = EXPORTER,
) -> None:
    with workspace_connection(url, WS) as conn:
        bundle, members = evidence.build_bundle(
            conn,
            s3,
            evidence.ExportRequest(
                workspace_id=WS,
                run_id=run_id,
                attempt_id=attempt_id,
                requested_by=requested_by,
                include_artifact_bytes=include_bytes,
            ),
        )
    manifest_bytes = bundle.manifest_bytes()
    attestation = key.sign(manifest_bytes)
    members["bundle.json"] = to_json(bundle).encode("utf-8")
    members["manifest.canonical.json"] = manifest_bytes
    members["attestation.json"] = json.dumps(
        {
            "keyId": attestation.key_id,
            "issuer": attestation.issuer,
            "algorithm": attestation.algorithm,
            "signature": attestation.signature_base64,
        }
    ).encode("utf-8")
    write_archive(str(path), members)


def _outcomes(report) -> dict[str, CheckOutcome]:
    return {f.check: f.outcome for f in report.findings}


# --- a real bundle, verified offline -------------------------------------------------------------


def test_a_complete_bundle_verifies_offline(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    """The control, and the property that matters: no database, no network, no account."""
    run_id, attempt_id = _complete_run(db, store)
    archive = tmp_path / "bundle.zip"
    _export(db, store, signing_key, archive, run_id=run_id, attempt_id=attempt_id)

    report = verify_archive(
        str(archive),
        trust_root=signing_key.trust_root(provenance=KeyProvenance.EXTERNALLY_SUPPLIED),
    )
    assert report.integrity_intact, [f.detail for f in report.failed]
    assert report.exit_code == 0
    assert report.trust_level is TrustLevel.FULLY_VERIFIABLE
    assert report.attributed_to == "accessforge-service"
    assert report.independently_trusted


def test_the_report_says_what_it_does_not_establish(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    """A verification report that omits its limits is the artifact most likely to be quoted."""
    run_id, attempt_id = _complete_run(db, store)
    archive = tmp_path / "bundle.zip"
    _export(db, store, signing_key, archive, run_id=run_id, attempt_id=attempt_id)

    text = verify_archive(str(archive)).human_report()
    assert "does NOT establish" in text
    assert "usable by people with disabilities in general" in text
    assert "not physical truth" in text


# --- tampering -----------------------------------------------------------------------------------


def _repack(archive: Path, tmp_path: Path, mutate) -> Path:
    """Rewrite one member of an archive. Used to construct every tampering case below."""
    with zipfile.ZipFile(archive) as source:
        members = {name: source.read(name) for name in source.namelist()}
    mutate(members)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(tampered, "w") as out:
        for name, data in members.items():
            out.writestr(name, data)
    return tampered


def test_modified_artifact_bytes_are_caught(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    run_id, attempt_id = _complete_run(db, store)
    archive = tmp_path / "bundle.zip"
    _export(db, store, signing_key, archive, run_id=run_id, attempt_id=attempt_id)

    def swap(members: dict[str, bytes]) -> None:
        name = next(n for n in members if n.startswith("artifacts/"))
        members[name] = b'{"phrases": []}'

    report = verify_archive(str(_repack(archive, tmp_path, swap)))
    assert report.exit_code == 1
    assert any(f.check.startswith("artifacts.digests") for f in report.failed)


def test_a_gapped_event_chain_is_caught(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    run_id, attempt_id = _complete_run(db, store)
    archive = tmp_path / "bundle.zip"
    _export(db, store, signing_key, archive, run_id=run_id, attempt_id=attempt_id)

    def drop_event(members: dict[str, bytes]) -> None:
        document = json.loads(members["bundle.json"])
        document["events"] = [e for e in document["events"] if e["sequence"] != 2]
        members["bundle.json"] = json.dumps(document).encode("utf-8")

    report = verify_archive(str(_repack(archive, tmp_path, drop_event)))
    assert report.exit_code == 1
    assert _outcomes(report)["events.chain"] is CheckOutcome.FAILED


def test_an_altered_identity_breaks_the_signature(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    """Two checks catch this and both should: the readable manifest stops matching the signed bytes,
    and the signature stops matching the manifest. Either alone would be enough; both is the design
    working as intended."""
    run_id, attempt_id = _complete_run(db, store)
    archive = tmp_path / "bundle.zip"
    _export(db, store, signing_key, archive, run_id=run_id, attempt_id=attempt_id)

    def alter(members: dict[str, bytes]) -> None:
        document = json.loads(members["bundle.json"])
        document["manifest"]["identities"]["BUILD"] = "a-different-build"
        members["bundle.json"] = json.dumps(document).encode("utf-8")

    report = verify_archive(
        str(_repack(archive, tmp_path, alter)),
        trust_root=signing_key.trust_root(provenance=KeyProvenance.EXTERNALLY_SUPPLIED),
    )
    assert report.exit_code == 1
    assert _outcomes(report)["manifest.signedBytes"] is CheckOutcome.FAILED


def test_a_forged_signature_is_caught(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    """A forger re-signs with their own key. Against the issuer's key it does not verify."""
    run_id, attempt_id = _complete_run(db, store)
    archive = tmp_path / "bundle.zip"
    _export(db, store, signing_key, archive, run_id=run_id, attempt_id=attempt_id)

    forger = SigningKey.generate(key_id=signing_key.key_id, issuer=signing_key.issuer)

    def resign(members: dict[str, bytes]) -> None:
        attestation = forger.sign(members["manifest.canonical.json"])
        members["attestation.json"] = json.dumps(
            {
                "keyId": attestation.key_id,
                "issuer": attestation.issuer,
                "algorithm": attestation.algorithm,
                "signature": attestation.signature_base64,
            }
        ).encode("utf-8")

    report = verify_archive(
        str(_repack(archive, tmp_path, resign)),
        trust_root=signing_key.trust_root(provenance=KeyProvenance.EXTERNALLY_SUPPLIED),
    )
    assert report.exit_code == 1
    assert _outcomes(report)["attestation.signature"] is CheckOutcome.FAILED


def test_a_key_from_inside_the_bundle_attributes_nothing(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    """The check that decides whether a signature means anything.

    A forger signs with their own key and embeds it, and every digest still matches because they
    recomputed them. The signature passes and attributes the bundle to nobody, and the report says
    so rather than printing a green checkmark.
    """
    run_id, attempt_id = _complete_run(db, store)
    archive = tmp_path / "bundle.zip"
    _export(db, store, signing_key, archive, run_id=run_id, attempt_id=attempt_id)

    report = verify_archive(
        str(archive), trust_root=signing_key.trust_root(provenance=KeyProvenance.EMBEDDED_IN_BUNDLE)
    )
    assert report.integrity_intact, "the bytes are intact; that is not the question"
    assert report.independently_trusted is False
    signature = next(f for f in report.findings if f.check == "attestation.signature")
    assert "attributes the bundle to nobody" in signature.detail


def test_an_unchecked_signature_is_reported_rather_than_skipped(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    """A verifier silent about an unchecked signature prints a clean report for unsigned bytes."""
    run_id, attempt_id = _complete_run(db, store)
    archive = tmp_path / "bundle.zip"
    _export(db, store, signing_key, archive, run_id=run_id, attempt_id=attempt_id)

    report = verify_archive(str(archive))
    assert _outcomes(report)["attestation.signature"] is CheckOutcome.UNSUPPORTED
    assert "UNSUPPORTED" in report.human_report()


def test_an_unknown_schema_version_is_unsupported_not_failed(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    """A verifier guessing at a format it does not know would report absences as tampering."""
    run_id, attempt_id = _complete_run(db, store)
    archive = tmp_path / "bundle.zip"
    _export(db, store, signing_key, archive, run_id=run_id, attempt_id=attempt_id)

    def bump(members: dict[str, bytes]) -> None:
        document = json.loads(members["bundle.json"])
        document["manifest"]["schemaVersion"] = "99.0.0"
        members["bundle.json"] = json.dumps(document).encode("utf-8")

    report = verify_archive(str(_repack(archive, tmp_path, bump)))
    assert _outcomes(report)["bundle.schemaVersion"] is CheckOutcome.UNSUPPORTED


# --- incompleteness ------------------------------------------------------------------------------


def test_a_missing_observer_watermark_makes_the_bundle_incomplete(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    """A canonical chain missing an observer tail is incomplete, and the verifier says which
    producer. The chain itself is perfectly contiguous."""
    run_id, attempt_id = _complete_run(db, store)
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "UPDATE producer_stream SET closed_at_sequence = NULL, closed_at = NULL "
            "WHERE attempt_id = %s AND producer_id = %s",
            (attempt_id, OBSERVER),
        )
    archive = tmp_path / "bundle.zip"
    _export(db, store, signing_key, archive, run_id=run_id, attempt_id=attempt_id)

    report = verify_archive(str(archive))
    assert report.exit_code == 1
    assert _outcomes(report)["events.chain"] is CheckOutcome.PASSED, "the chain has no gaps"

    watermark = next(f for f in report.findings if f.check == f"producers.watermarks[{OBSERVER}]")
    assert watermark.outcome is CheckOutcome.FAILED
    # The message is asserted, not just the outcome. Removing the null check leaves the next branch
    # failing too -- "closed at None but 1 admitted" -- so an outcome-only assertion could not tell
    # the two apart, and the explanation an operator reads is the whole difference between them.
    assert "never closed its stream" in watermark.detail
    assert "perfectly contiguous" in watermark.detail

    assert report.trust_level is TrustLevel.INCOMPLETE


def test_a_limited_disclosure_bundle_does_not_claim_full_verifiability(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    """Redaction may permit a limited-disclosure bundle and cannot retain a full-verification claim.

    The artifacts are reported NOT_APPLICABLE rather than failed: a digest without bytes is still a
    claim a holder of the original can check.
    """
    run_id, attempt_id = _complete_run(db, store)
    archive = tmp_path / "bundle.zip"
    _export(
        db, store, signing_key, archive, run_id=run_id, attempt_id=attempt_id, include_bytes=False
    )

    report = verify_archive(str(archive))
    assert report.trust_level is TrustLevel.LIMITED_DISCLOSURE
    assert report.integrity_intact, "withholding bytes is a disclosure decision, not a failure"
    assert any(
        f.check.startswith("artifacts.digests") and f.outcome is CheckOutcome.NOT_APPLICABLE
        for f in report.findings
    )


def test_deleted_evidence_is_reported_as_deleted_rather_than_absent(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    """INV-15 reaching the export. An artifact that vanished with no note would be indistinguishable
    from one that never existed."""
    run_id, attempt_id = _complete_run(db, store)
    with workspace_connection(db, WS) as conn:
        artifact_id = str(
            conn.execute(
                "SELECT id FROM evidence_artifact WHERE attempt_id = %s", (attempt_id,)
            ).fetchone()["id"]
        )
        evidence.delete_artifact_bytes(
            conn, store, artifact_id=artifact_id, reason="retention policy"
        )
    archive = tmp_path / "bundle.zip"
    _export(db, store, signing_key, archive, run_id=run_id, attempt_id=attempt_id)

    report = verify_archive(str(archive))
    assert report.trust_level is TrustLevel.INCOMPLETE
    deleted = next(f for f in report.findings if f.check.startswith("artifacts.digests"))
    assert deleted.outcome is CheckOutcome.NOT_APPLICABLE
    assert "deleted" in deleted.detail.lower()


def test_an_overclaimed_trust_level_is_caught(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    """The issuer writes the trust level and the verifier recomputes it. A bundle claiming
    FULLY_VERIFIABLE while withholding artifacts asks a reader to conclude what it lacks."""
    run_id, attempt_id = _complete_run(db, store)
    archive = tmp_path / "bundle.zip"
    _export(
        db, store, signing_key, archive, run_id=run_id, attempt_id=attempt_id, include_bytes=False
    )

    def overclaim(members: dict[str, bytes]) -> None:
        document = json.loads(members["bundle.json"])
        document["manifest"]["trustLevel"] = "FULLY_VERIFIABLE"
        members["bundle.json"] = json.dumps(document).encode("utf-8")

    report = verify_archive(str(_repack(archive, tmp_path, overclaim)))
    assert _outcomes(report)["bundle.trustLevel"] is CheckOutcome.FAILED
    assert report.exit_code == 1


# --- authorization -------------------------------------------------------------------------------


def test_a_viewer_may_read_evidence_but_not_export_it(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    """Exporting is separate from reading because an export leaves the system."""
    run_id, attempt_id = _complete_run(db, store)
    with pytest.raises(evidence.ExportNotAuthorized, match="not export"):
        _export(
            db,
            store,
            signing_key,
            tmp_path / "b.zip",
            run_id=run_id,
            attempt_id=attempt_id,
            requested_by=VIEWER,
        )


def test_a_revoked_membership_stops_an_export_at_generation(
    db: str, store: evidence.S3ArtifactStore, signing_key: SigningKey, tmp_path: Path
) -> None:
    """The reason authorization is rechecked at generation rather than cached from the request. An
    export is asynchronous, and a revocation in between must take effect."""
    run_id, attempt_id = _complete_run(db, store)
    with workspace_connection(db, WS) as conn:
        evidence.assert_may_export(conn, workspace_id=WS, actor_id=EXPORTER)
        conn.execute(
            "UPDATE workspace_membership SET revoked_at = now() "
            "WHERE workspace_id = %s AND user_id = %s",
            (WS, EXPORTER),
        )
    with pytest.raises(evidence.ExportNotAuthorized, match="revoked"):
        _export(db, store, signing_key, tmp_path / "b.zip", run_id=run_id, attempt_id=attempt_id)


# --- hostile archives ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "member",
    [
        "../../etc/passwd",
        "/absolute/path",
        "artifacts/../../escape",
        "artifacts/nested/deeper",
        "run.sh",
        "artifacts/.hidden",
    ],
)
def test_a_malicious_member_name_is_refused(tmp_path: Path, member: str) -> None:
    """Nothing is extracted to disk at all, so there is no directory to escape -- but the name is
    still refused, because a member the format does not define could be anything."""
    archive = tmp_path / "hostile.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("bundle.json", b"{}")
        out.writestr("manifest.canonical.json", b"{}")
        out.writestr("attestation.json", b"{}")
        out.writestr(member, b"payload")

    report = verify_archive(str(archive))
    assert report.exit_code == 1
    assert _outcomes(report)["archive.safety"] is CheckOutcome.FAILED


def test_a_decompression_bomb_is_refused(tmp_path: Path) -> None:
    """A few kilobytes of zeros inflate to gigabytes. Refused on the declared ratio before any of it
    is decompressed."""
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
        out.writestr("bundle.json", b"\0" * (4 * 1024 * 1024))
        out.writestr("manifest.canonical.json", b"{}")
        out.writestr("attestation.json", b"{}")

    report = verify_archive(str(archive))
    assert report.exit_code == 1
    assert "zeros" in report.failed[0].detail


def test_an_archive_missing_a_required_member_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "partial.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("bundle.json", b"{}")

    report = verify_archive(str(archive))
    assert report.exit_code == 1
    assert "manifest.canonical.json" in report.failed[0].detail
