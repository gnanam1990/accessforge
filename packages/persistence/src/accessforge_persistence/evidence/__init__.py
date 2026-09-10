"""Provenance-bound evidence: artifact intake, integrity and finalization prerequisites.

Module 04 built the canonical chain — one trusted sequencer per attempt, producer source records,
closing watermarks. This package adds the half that turns an ordered chain into an *evidence set*:
the files the records refer to, the checks that they are the files they claim to be, and the
assembly that decides whether an attempt is complete enough to evaluate.

Two boundaries run through everything here.

**Uploading is not trusting.** An artifact arrives quarantined, is hashed by the server, is re-read
and re-hashed at promotion, and is bound to the sealed manifest before it becomes evidence. Nothing
here reads an artifact's contents for a verdict: this layer establishes provenance and integrity,
and module 11 decides what the contents mean.

**Completeness is not an outcome.** `assess_completeness` returns reasons and cannot express PASS.
A checker that guessed at outcomes would be a second place where a verdict could be decided, and the
whole architecture depends on there being exactly one.
"""

from .artifacts import (
    ArtifactError,
    ArtifactRejected,
    StoredArtifact,
    attach_redacted_view,
    declare_required_artifacts,
    delete_artifact_bytes,
    missing_required_artifacts,
    promote,
    upload_to_quarantine,
    verify_stored_integrity,
)
from .export import ExportError, ExportNotAuthorized, ExportRequest, assert_may_export, build_bundle
from .finalization import (
    MAX_STAGED_RECORDS_PER_ATTEMPT,
    Completeness,
    FinalizationError,
    assert_run_accepts_evidence,
    assert_staging_capacity,
    assess_completeness,
    evidence_summary,
    record_rejected_arrival,
    replay,
)
from .objectstore import (
    ALLOWED_CONTENT_TYPES,
    MAX_ARTIFACT_BYTES,
    ArtifactStore,
    ArtifactStoreError,
    ObjectStoreUnavailable,
    S3ArtifactStore,
    S3Settings,
    artifact_key,
    assert_uploadable,
    compute_digest,
)
from .observer import ApplicationObserver

__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "ApplicationObserver",
    "ExportError",
    "ExportNotAuthorized",
    "ExportRequest",
    "assert_may_export",
    "build_bundle",
    "MAX_ARTIFACT_BYTES",
    "MAX_STAGED_RECORDS_PER_ATTEMPT",
    "ArtifactError",
    "ArtifactRejected",
    "ArtifactStore",
    "ArtifactStoreError",
    "Completeness",
    "FinalizationError",
    "ObjectStoreUnavailable",
    "S3ArtifactStore",
    "S3Settings",
    "StoredArtifact",
    "artifact_key",
    "assert_run_accepts_evidence",
    "assert_staging_capacity",
    "assert_uploadable",
    "assess_completeness",
    "attach_redacted_view",
    "compute_digest",
    "declare_required_artifacts",
    "delete_artifact_bytes",
    "evidence_summary",
    "missing_required_artifacts",
    "promote",
    "record_rejected_arrival",
    "replay",
    "upload_to_quarantine",
    "verify_stored_integrity",
]
