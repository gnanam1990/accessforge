"""Read a local check preview from authorized original records, without remote calls.

Only trusted session middleware supplies the principal. This is an owner-only service function,
not a public endpoint, an execution grant or publication approval. No evidence is reconstructed
or rewritten on reads. Historical snapshots do not assert present object-store retention.
"""

from typing import Any

from accessforge_contracts import validate
from accessforge_domain.authorization import HumanPrincipal
from accessforge_domain.canonical import digest
from accessforge_domain.states import Outcome, RunStatus
from accessforge_persistence import evaluations, github_bindings, workspace_connection

from .github_access import RepositoryScope
from .github_check_preview import CheckFacts, Refused, preview_check
from .github_connections import _authorize


def prepare_check_preview(
    database_url: str, *, principal: HumanPrincipal, binding_id: str, run_id: str
) -> dict[str, Any]:
    """Snapshot local authority and immutable source/evaluation under short DB locks.

    Exact stored HTTPS repository URL matching is a local association check, not proof that a
    commit belongs to the current remote repository. Publication still requires fresh remote
    identity/commit checks and one exact current GITHUB_PUBLISH approval. No network occurs here.
    """
    with workspace_connection(database_url, principal.workspace_id) as conn:
        conn.execute("SET LOCAL statement_timeout='5s'")
        _authorize(conn, principal)
        binding = github_bindings.require_live(
            conn, workspace_id=principal.workspace_id, binding_id=binding_id
        )
        row = conn.execute(
            "SELECT r.*,m.canonical_manifest,p.repository_url,p.repository_authorized_by,"
            "p.revision AS project_revision,p.revoked_at AS project_revoked,"
            "s.commit_sha,s.tree_digest,s.dirty,b.artifact_digest,"
            "m.manifest_digest AS sealed_digest,m.workspace_id AS sealed_workspace,"
            "m.project_id AS sealed_project,m.authorization_id AS sealed_authorization "
            "FROM run r JOIN sealed_manifest m ON m.run_id=r.id "
            "AND m.workspace_id=r.workspace_id JOIN project p ON p.id=r.project_id "
            "AND p.workspace_id=r.workspace_id "
            "JOIN source_snapshot s ON s.id=m.source_snapshot_id AND s.project_id=p.id "
            "AND s.workspace_id=r.workspace_id "
            "JOIN build_artifact b ON b.id=m.build_artifact_id AND b.project_id=p.id "
            "AND b.source_snapshot_id=s.id AND b.workspace_id=r.workspace_id "
            "WHERE r.id=%s AND r.workspace_id=%s FOR SHARE OF r,m,p,s,b",
            (run_id, principal.workspace_id),
        ).fetchone()
        if row is None or row["project_revoked"] is not None or row["dirty"]:
            raise Refused("original clean source-bound run unavailable")
        scope = RepositoryScope(
            binding["app_id"],
            binding["installation_id"],
            binding["account_id"],
            binding["repository_id"],
            binding["owner_name"],
            binding["repository_name"],
        )
        url = f"https://github.com/{scope.owner}/{scope.name}"
        if (
            row["repository_url"] not in {url, url + ".git"}
            or row["repository_authorized_by"] is None
        ):
            raise Refused("project repository does not match the exact authorized binding")
        manifest = row["canonical_manifest"]
        validate("run-manifest.schema.json", manifest)
        if (
            digest(manifest) != row["manifest_digest"]
            or row["manifest_digest"] != row["sealed_digest"]
            or any(
                manifest[key] != str(row[column])
                for key, column in (
                    ("workspaceId", "workspace_id"),
                    ("workspaceId", "sealed_workspace"),
                    ("runId", "id"),
                    ("projectId", "project_id"),
                    ("projectId", "sealed_project"),
                    ("authorizationId", "authorization_id"),
                    ("authorizationId", "sealed_authorization"),
                    ("sourceCommitSha", "commit_sha"),
                    ("sourceTreeDigest", "tree_digest"),
                    ("buildArtifactDigest", "artifact_digest"),
                )
            )
        ):
            raise Refused("original run and sealed source identity differ")
        evaluation = evaluations.read(conn, run_id=run_id)
        if evaluation is not None:
            snapshot = evaluation["snapshot"]
            if (
                snapshot["manifestDigest"] != row["manifest_digest"]
                or snapshot["outcome"] != row["outcome"]
                or row["status"] != "COMPLETED"
                or snapshot.get("sealedIdentities", {}).get("JOURNEY_VERSION")
                != manifest["journeyDigest"]
                or snapshot.get("sealedIdentities", {}).get("RUNNER_PROFILE")
                != manifest["runnerProfileDigest"]
                or snapshot.get("sealedIdentities", {}).get("SOURCE")
                != manifest["sourceTreeDigest"]
            ):
                raise Refused("original evaluation does not bind this run identity")
        facts = CheckFacts(
            principal.workspace_id,
            binding_id,
            run_id,
            manifest["sourceCommitSha"],
            row["manifest_digest"],
            manifest["journeyDigest"],
            manifest["runnerProfileDigest"],
            RunStatus(row["status"]),
            Outcome(row["outcome"]),
            row["execution_began"],
            evaluation["snapshotDigest"] if evaluation else None,
        )
        preview = preview_check(scope, facts)
        preview.pop("previewDigest")
        preview["localAdmission"] = {
            "projectId": str(row["project_id"]),
            "projectRevision": row["project_revision"],
            "runRevision": row["revision"],
            "repositoryAssociation": "EXACT_STORED_HTTPS_URL_NOT_REMOTE_PROOF",
            "retentionMeaning": "ORIGINAL_SNAPSHOT_NOT_CURRENT_OBJECT_STORE_VERIFICATION",
        }
        return {**preview, "previewDigest": digest(preview)}
