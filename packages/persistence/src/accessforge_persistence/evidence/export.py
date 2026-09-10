"""Assembling a bundle from authoritative stored records.

The rule the module prompt states plainly: bundles are generated "from authoritative stored records
rather than agent-supplied summary JSON". So every value in a bundle is read here, from the database
and the object store, and no parameter exists through which a caller could pass a pre-built one.

The second rule is the one that is easy to get wrong under deadline: authorization is rechecked at
generation, not just at request. Export is asynchronous — request, then work, then download — and
membership revoked in between must take effect. An authorization decision cached at request time is
an authorization decision that outlives the authority it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from accessforge_domain.evaluation.verdict import PASS_SCOPE_STATEMENT
from accessforge_evidence.bundle import (
    BUNDLE_SCHEMA_VERSION,
    CANONICALIZATION_VERSION,
    ArtifactEntry,
    Bundle,
    CompletenessDeclaration,
    EvidenceState,
    ProducerEntry,
    ReviewEntry,
)

from .objectstore import ArtifactStore, ArtifactStoreError


class ExportError(Exception):
    """An export was refused."""


class ExportNotAuthorized(ExportError):
    """The requester may not export this run, now.

    Distinct from a generic refusal because the timing matters: this is raised at generation and at
    download as well as at request, and a caller that treated all three alike would be caching the
    first decision.
    """


@dataclass(frozen=True, slots=True)
class ExportRequest:
    workspace_id: str
    run_id: str
    attempt_id: str
    requested_by: str
    include_artifact_bytes: bool = True
    """When false the bundle carries digests without bytes -- a limited-disclosure export. The
    verifier reports those artifacts as NOT_APPLICABLE rather than failed: a holder of the original
    can still confirm the match."""


def assert_may_export(
    conn: psycopg.Connection[dict[str, Any]], *, workspace_id: str, actor_id: str
) -> None:
    """Recheck membership and the export permission against the database, now.

    Called at request, at generation and at download. Export is asynchronous, and a decision made at
    request time and reused at download time outlives the membership it rested on.
    """
    from accessforge_domain.authorization.roles import Permission, Role, role_permits

    row = conn.execute(
        "SELECT role, revoked_at FROM workspace_membership "
        "WHERE workspace_id = %s AND user_id = %s",
        (workspace_id, actor_id),
    ).fetchone()
    if row is None:
        raise ExportNotAuthorized("no membership in this workspace")
    if row["revoked_at"] is not None:
        raise ExportNotAuthorized(
            "this membership has been revoked. Rechecked here rather than trusted from the "
            "request, because an export is asynchronous and a revocation between request and "
            "download must take effect."
        )
    if not role_permits(Role(str(row["role"])), Permission.EVIDENCE_EXPORT):
        raise ExportNotAuthorized(
            f"{row['role']} may read evidence but not export it. Exporting is separate from "
            "reading because an export leaves the system."
        )


def build_bundle(
    conn: psycopg.Connection[dict[str, Any]],
    store: ArtifactStore,
    request: ExportRequest,
) -> tuple[Bundle, dict[str, bytes]]:
    """Assemble a bundle and the artifact bytes that travel with it.

    Returns the bundle and a mapping of archive member name to bytes, so signing and archive writing
    stay separate concerns from reading the database.
    """
    assert_may_export(conn, workspace_id=request.workspace_id, actor_id=request.requested_by)

    run = conn.execute(
        "SELECT manifest_digest, status, outcome FROM run WHERE id = %s", (request.run_id,)
    ).fetchone()
    if run is None:
        raise ExportError("no such run in this workspace")

    events = [
        dict(r)
        for r in conn.execute(
            """
            SELECT sequence, event_id, event_type, source_time, received_time,
                   previous_event_hash, payload_digest
            FROM canonical_event WHERE run_id = %s AND attempt_id = %s ORDER BY sequence
            """,
            (request.run_id, request.attempt_id),
        ).fetchall()
    ]

    producers = tuple(
        ProducerEntry(
            producer_id=str(r["producer_id"]),
            admitted_through=int(r["admitted_through"]),
            closed_at_sequence=(
                None if r["closed_at_sequence"] is None else int(r["closed_at_sequence"])
            ),
        )
        for r in conn.execute(
            """
            SELECT producer_id, admitted_through, closed_at_sequence
            FROM producer_stream WHERE attempt_id = %s ORDER BY producer_id
            """,
            (request.attempt_id,),
        ).fetchall()
    )

    artifacts: list[ArtifactEntry] = []
    members: dict[str, bytes] = {}
    for row in conn.execute(
        """
        SELECT kind, producer_id, content_digest, size_bytes, state, retention, object_key,
               redacted_object_key, redacted_digest
        FROM evidence_artifact WHERE attempt_id = %s ORDER BY kind, content_digest
        """,
        (request.attempt_id,),
    ).fetchall():
        retention = str(row["retention"])
        state = (
            EvidenceState(retention) if retention in set(EvidenceState) else EvidenceState.RETAINED
        )

        included = False
        if request.include_artifact_bytes and retention != "DELETED":
            # A redacted artifact travels as its redacted view. Including the raw bytes of something
            # marked redacted would defeat the redaction; excluding it entirely would hide that
            # anything was there.
            key = str(row["redacted_object_key"] or row["object_key"])
            digest_for_member = str(row["redacted_digest"] or row["content_digest"])
            try:
                members[f"artifacts/{digest_for_member}"] = store.get(key=key)
                included = True
            except ArtifactStoreError:
                # Unavailable now, which is not the same as deleted. The bundle says so rather than
                # silently omitting it: an artifact that vanishes without a note cannot be told
                # apart from one that never existed.
                state = EvidenceState.UNAVAILABLE

        artifacts.append(
            ArtifactEntry(
                kind=str(row["kind"]),
                producer_id=str(row["producer_id"]),
                content_digest=str(row["content_digest"]),
                size_bytes=int(row["size_bytes"]),
                state=state,
                redacted_digest=(
                    None if row["redacted_digest"] is None else str(row["redacted_digest"])
                ),
                included=included,
            )
        )

    reviews = tuple(
        ReviewEntry(
            review_id=str(r["id"]),
            reviewer_id=str(r["reviewer_id"]),
            verdict=str(r["verdict"]),
            observations=str(r["observations"]),
            limitations=str(r["limitations"]),
            used_assistive_technology=bool(r["used_assistive_technology"]),
            submitted_at=str(r["submitted_at"]),
        )
        for r in conn.execute(
            """
            SELECT rv.id, rv.reviewer_id, rv.verdict, rv.observations, rv.limitations,
                   rv.used_assistive_technology, rv.submitted_at
            FROM review rv
            JOIN finding_transition ft ON ft.review_id = rv.id
            JOIN finding f ON f.id = ft.finding_id
            WHERE f.run_id = %s ORDER BY rv.submitted_at
            """,
            (request.run_id,),
        ).fetchall()
    )

    from . import finalization

    completeness_reasons = tuple(
        finalization.assess_completeness(
            conn,
            store,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            required_producers=frozenset(p.producer_id for p in producers),
        ).reasons
    )

    identities = {
        str(r["kind"]): str(r["value"])
        for r in conn.execute(
            "SELECT kind, value FROM run_identity WHERE run_id = %s", (request.run_id,)
        ).fetchall()
    }

    bundle = Bundle(
        schema_version=BUNDLE_SCHEMA_VERSION,
        canonicalization_version=CANONICALIZATION_VERSION,
        workspace_id=request.workspace_id,
        run_id=request.run_id,
        attempt_id=request.attempt_id,
        manifest_digest=str(run["manifest_digest"]),
        identities=identities,
        outcome=str(run["outcome"]),
        outcome_reasons=(),
        evaluator_version=identities.get("EVALUATOR", ""),
        scope_statement=PASS_SCOPE_STATEMENT,
        events=tuple(events),
        producers=producers,
        artifacts=tuple(artifacts),
        reviews=reviews,
        completeness=CompletenessDeclaration(
            canonical_chain_contiguous=not any("gaps" in r for r in completeness_reasons),
            all_required_producers_closed=not any("closed" in r for r in completeness_reasons),
            all_required_artifacts_present=not any("artifacts" in r for r in completeness_reasons),
            reasons=completeness_reasons,
        ),
        redaction_notes=tuple(
            f"{a.kind} was redacted; the bundle carries the redacted view and its own digest"
            for a in artifacts
            if a.state is EvidenceState.REDACTED
        ),
    )
    return bundle, members
