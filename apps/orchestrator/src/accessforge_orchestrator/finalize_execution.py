"""Finalize a stopped manual attempt from immutable retained inputs, never caller verdict JSON.

Runtime preflight and server-bound deployed artifact measurements are interpreted separately from
stored admission. Missing source/profile/environment/model identities remain INCONCLUSIVE.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain import reducers
from accessforge_domain.canonical import digest
from accessforge_domain.evaluation.assertions import (
    AssertionOutcome,
    Provenance,
    evaluate_assertions,
)
from accessforge_domain.evaluation.identity import IdentityKind, revalidate
from accessforge_domain.evaluation.observer import CompletionObservation
from accessforge_domain.evaluation.rules import derive_reader_assertion
from accessforge_domain.evaluation.verdict import decide
from accessforge_domain.journeys.assertions import AssertionKind
from accessforge_domain.states import Condition
from accessforge_orchestrator.execution_artifacts import (
    ExecutionArtifactStore,
    Refused,
    _BoundedStore,
    _bundle,
    _context,
)
from accessforge_orchestrator.runtime_evidence import interpret as interpret_runtime
from accessforge_orchestrator.runtime_evidence import reader_samples
from accessforge_persistence import evaluations, journeys, runs, workspace_connection
from accessforge_persistence.evidence import assess_completeness
from accessforge_persistence.evidence.objectstore import artifact_key, compute_digest
from accessforge_persistence.evidence.session import requirements

EVALUATOR_VERSION = "1.3.0"


def _retained(
    conn: psycopg.Connection[Any], store: ExecutionArtifactStore, row: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    artifacts = conn.execute(
        "SELECT * FROM evidence_artifact WHERE attempt_id=%s ORDER BY kind,id FOR UPDATE",
        (row["attempt_id"],),
    ).fetchall()
    if len(artifacts) != 5 or any(
        a["state"] != "PROMOTED" or a["retention"] != "RETAINED" for a in artifacts
    ):
        raise Refused("five retained promoted execution artifacts are required")
    for artifact in artifacts:
        expected_key = artifact_key(
            workspace_id=str(row["workspace_id"]),
            run_id=str(row["run_id"]),
            attempt_id=str(row["attempt_id"]),
            kind=artifact["kind"],
            content_digest=artifact["content_digest"],
        )
        if artifact["object_key"] != expected_key:
            raise Refused("artifact key is not bound to this workspace/run/attempt")
    journal = next((a for a in artifacts if a["kind"] == "RUNNER_JOURNAL"), None)
    if journal is None:
        raise Refused("original runner journal unavailable")
    bounded = _BoundedStore(store)
    expected = _bundle(conn, row, bounded.get(key=journal["object_key"]))
    snapshots: dict[str, Any] = {}
    identities = []
    for artifact in artifacts:
        kind = artifact["kind"]
        if kind not in expected:
            raise Refused("unexpected execution artifact")
        producer, content_type, payload = expected[kind]
        if (
            artifact["producer_id"] != producer
            or artifact["content_type"] != content_type
            or artifact["content_digest"] != compute_digest(payload)
            or artifact["size_bytes"] != len(payload)
            or artifact["manifest_digest"] != row["manifest_digest"]
            or artifact["lease_epoch"] != row["epoch"]
            or bounded.get(key=artifact["object_key"]) != payload
        ):
            raise Refused("retained artifact differs from original closed source evidence")
        identities.append(
            {
                "artifactId": str(artifact["id"]),
                "kind": kind,
                "producerId": producer,
                "digest": artifact["content_digest"],
            }
        )
        if kind != "RUNNER_JOURNAL":
            snapshots[kind] = json.loads(payload)
    required = requirements(conn, row)
    complete = assess_completeness(
        conn,
        bounded,
        run_id=str(row["run_id"]),
        attempt_id=str(row["attempt_id"]),
        required_producers=frozenset(p for k, p in required.items() if k != "RUNNER_JOURNAL"),
    )
    if not complete.complete:
        raise Refused("evidence cannot be finalized: " + "; ".join(complete.reasons))
    return snapshots, identities


def _decide(
    conn: psycopg.Connection[Any],
    row: dict[str, Any],
    snapshots: dict[str, Any],
    artifact_ids: list[dict[str, Any]],
) -> dict[str, Any]:
    sealed = conn.execute(
        "SELECT canonical_manifest FROM sealed_manifest WHERE run_id=%s", (row["run_id"],)
    ).fetchone()
    if sealed is None or not isinstance(sealed["canonical_manifest"], dict):
        raise Refused("canonical execution manifest unavailable")
    manifest = sealed["canonical_manifest"]
    if digest(manifest) != row["manifest_digest"]:
        raise Refused("sealed manifest content differs")
    assertions = journeys.load_assertion_contract(
        conn,
        version_id=manifest["journeyVersionId"],
        expected_digest=manifest["assertionSetDigest"],
    )
    values: dict[str, AssertionOutcome] = {}
    samples = reader_samples(snapshots)
    last = snapshots["EFFECT_RECEIPT"]["records"][-1]
    source = last["payload"]["sourceRecord"]
    if (
        last["eventType"] != "EFFECT_RECEIPT"
        or last["payload"]["serviceIdentity"] != "OBSERVER"
        or source.get("finalSample") is not True
        or source.get("assertionSetDigest") != manifest["assertionSetDigest"]
    ):
        raise Refused("final independent observer assertion binding unavailable")
    authored = source.get("assertionObservations", [])
    if not isinstance(authored, list):
        raise Refused("observer assertions malformed")
    ids = [a.get("assertionId") for a in authored if isinstance(a, dict)]
    if len(ids) != len(authored) or len(set(ids)) != len(ids):
        raise Refused("conflicting observer assertions")
    for item in authored:
        if item.get("kind") != "TASK_COMPLETION" or item.get("provenance") != "OBSERVER_AUTHORED":
            raise Refused("observer cannot supply another assertion family")
        values[item["assertionId"]] = AssertionOutcome(
            item["assertionId"],
            AssertionKind.TASK_COMPLETION,
            Condition(item["condition"]),
            Provenance.OBSERVER_AUTHORED,
            (last["eventId"],),
            item.get("unknownReason"),
        )
    for assertion in assertions.required:
        if assertion.kind in {AssertionKind.REQUIRED_ANNOUNCEMENT, AssertionKind.READING_ORDER}:
            values[assertion.assertion_id] = derive_reader_assertion(assertion, samples)
    evaluated = evaluate_assertions(assertions.required, values)
    task_values = [
        a.condition for a in evaluated.outcomes if a.kind is AssertionKind.TASK_COMPLETION
    ]
    completion = Condition.UNKNOWN
    if task_values and Condition.UNKNOWN not in task_values:
        completion = Condition.FALSE if Condition.FALSE in task_values else Condition.TRUE
    sealed_identities = {
        IdentityKind.SOURCE: manifest["sourceTreeDigest"],
        IdentityKind.BUILD: manifest["buildArtifactDigest"],
        IdentityKind.ENVIRONMENT: manifest["environmentConfigDigest"],
        IdentityKind.RUNNER_PROFILE: manifest["runnerProfileDigest"],
        IdentityKind.EVALUATOR: manifest["evaluatorVersion"],
        IdentityKind.JOURNEY_VERSION: manifest["journeyDigest"],
        IdentityKind.ASSERTION_SET: manifest["assertionSetDigest"],
        IdentityKind.FIXTURE_INSTANCE: manifest["fixtureDigest"],
        IdentityKind.MODEL: manifest["modelConfigDigest"],
    }
    # These two values are independently established by the executing evaluator and rehashed
    # original contract. The control plane's seal is NOT an observed build/profile/model identity.
    observed = {
        IdentityKind.EVALUATOR: EVALUATOR_VERSION,
        IdentityKind.ASSERTION_SET: digest(assertions.canonical_form()),
    }
    runtime = interpret_runtime(snapshots, row)
    if runtime.observed_build is not None:
        observed[IdentityKind.BUILD] = runtime.observed_build
    if runtime.observed_source is not None:
        observed[IdentityKind.SOURCE] = runtime.observed_source
    identity = revalidate(sealed_identities, observed)
    evidence_digest = digest({"manifestDigest": row["manifest_digest"], "artifacts": artifact_ids})
    verdict = decide(
        identity=identity,
        completeness_reasons=(),
        preflight_passed=runtime.preflight_passed,
        assertions=evaluated,
        completion=CompletionObservation(
            completion,
            "retained final independent observer assertion conditions",
            source.get("count"),
        ),
        evaluator_version=EVALUATOR_VERSION,
        evidence_set_digest=evidence_digest,
    )
    return {
        "schemaVersion": 1,
        "runId": str(row["run_id"]),
        "attemptId": str(row["attempt_id"]),
        "manifestDigest": row["manifest_digest"],
        "evidenceSetDigest": evidence_digest,
        "evaluatorVersion": EVALUATOR_VERSION,
        "outcome": verdict.outcome.value,
        "reasons": list(verdict.reasons) + list(runtime.reasons),
        "scope": verdict.scope,
        "sealedIdentities": dict(sealed_identities),
        "observedIdentities": dict(observed),
        "artifacts": artifact_ids,
        "assertions": [
            {
                "assertionId": a.assertion_id,
                "kind": a.kind.value,
                "condition": a.condition.value,
                "provenance": a.provenance.value,
                "evidenceRefs": list(a.evidence_refs),
                "unknownReason": a.unknown_reason,
            }
            for a in evaluated.outcomes
        ],
    }


def finalize(
    database_url: str, store: ExecutionArtifactStore, *, workspace_id: str, run_id: str
) -> dict[str, Any]:
    with workspace_connection(database_url, workspace_id) as conn:
        # Replay is the original immutable snapshot, not a fresh verdict over changed retention.
        existing = evaluations.read(conn, run_id=run_id)
        if existing is not None:
            return existing
        row = _context(conn, run_id)
        # A concurrent finalizer may have won while _context waited for the run lock; its terminal
        # state is refused, never finalized a second time. Retrying reads the committed snapshot.
        snapshots, artifacts = _retained(conn, store, row)
        snapshot = _decide(conn, row, snapshots, artifacts)
        evaluation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "accessforge:finalize:" + run_id))
        from accessforge_domain.states import Outcome

        runs.apply_transition(
            conn,
            run_id=run_id,
            reducer=lambda state: reducers.complete(state, outcome=Outcome(snapshot["outcome"])),
            operation_id=evaluation_id,
            topic="run.completed",
            actor_service="outcome-verifier",
            audit_action="RUN_EVALUATED",
            audit_context={
                "evaluationId": evaluation_id,
                "snapshotDigest": digest(snapshot),
                "evidenceSetDigest": snapshot["evidenceSetDigest"],
            },
        )
        conn.execute(
            "INSERT INTO run_evaluation(id,workspace_id,run_id,attempt_id,manifest_digest,"
            "evidence_set_digest,snapshot_digest,evaluator_version,outcome,snapshot) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                evaluation_id,
                workspace_id,
                run_id,
                row["attempt_id"],
                row["manifest_digest"],
                snapshot["evidenceSetDigest"],
                digest(snapshot),
                EVALUATOR_VERSION,
                snapshot["outcome"],
                Jsonb(snapshot),
            ),
        )
        for kind, value in snapshot["sealedIdentities"].items():
            current = conn.execute(
                "SELECT value FROM run_identity WHERE run_id=%s AND kind=%s", (run_id, kind)
            ).fetchone()
            if current is not None and current["value"] != value:
                raise Refused("an existing immutable outcome identity differs")
            if current is None:
                conn.execute(
                    "INSERT INTO run_identity(workspace_id,run_id,kind,value) VALUES(%s,%s,%s,%s)",
                    (workspace_id, run_id, kind, value),
                )
        conn.execute(
            "UPDATE run_attempt SET ended_at=now() WHERE id=%s AND ended_at IS NULL",
            (row["attempt_id"],),
        )
        retained = evaluations.read(conn, run_id=run_id)
        assert retained is not None
        return retained


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", type=uuid.UUID, required=True)
    parser.add_argument("--run-id", type=uuid.UUID, required=True)
    args = parser.parse_args()
    try:
        from accessforge_orchestrator.maintenance.purge_worker import _store_from_environment

        result = finalize(
            os.environ["ACCESSFORGE_DATABASE_URL"],
            _store_from_environment(),
            workspace_id=str(args.workspace_id),
            run_id=str(args.run_id),
        )
    except Exception:  # noqa: BLE001 - no credential-bearing SDK exceptions or raw evidence in logs
        # A lost commit acknowledgement can leave a committed snapshot. Reconcile by reading
        # the original evaluation; never promise rollback solely from a client exception.
        print("EVALUATION_UNCONFIRMED; read the retained evaluation before retrying")
        raise SystemExit(1) from None
    print("EVALUATION_RETAINED outcome=" + result["snapshot"]["outcome"])


if __name__ == "__main__":
    main()
