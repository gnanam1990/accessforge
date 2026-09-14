"""Deterministic local check preview, not admission, publication or an evidence verifier.

Inputs must be reconstructed from authorized stored run/manifest/evaluation/binding records by
the integration service. This renderer is intentionally not a public JSON-to-GitHub endpoint.
No HTTP, credentials, timestamps, remote IDs or automatic publication exist here.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from accessforge_domain.canonical import digest
from accessforge_domain.evaluation.verdict import PASS_SCOPE_STATEMENT
from accessforge_domain.states import Outcome, RunStatus, is_admissible_pair

from .github_access import RepositoryScope


class Refused(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CheckFacts:
    workspace_id: str
    binding_id: str
    run_id: str
    source_sha: str
    manifest_digest: str
    journey_digest: str
    profile_digest: str
    status: RunStatus
    outcome: Outcome
    execution_began: bool
    evaluation_digest: str | None = None

    def __post_init__(self) -> None:
        try:
            for value in (self.workspace_id, self.binding_id, self.run_id):
                if not isinstance(value, str) or str(UUID(value)) != value:
                    raise ValueError
            for value, sizes in (
                (self.source_sha, {40, 64}),
                (self.manifest_digest, {64}),
                (self.journey_digest, {64}),
                (self.profile_digest, {64}),
            ):
                if not isinstance(value, str) or len(value) not in sizes:
                    raise ValueError
                if any(c not in "0123456789abcdef" for c in value):
                    raise ValueError
            if (
                not isinstance(self.status, RunStatus)
                or not isinstance(self.outcome, Outcome)
                or type(self.execution_began) is not bool
                or not is_admissible_pair(
                    self.status, self.outcome, execution_began=self.execution_began
                )
            ):
                raise ValueError
            if self.status is RunStatus.COMPLETED:
                if (
                    not self.execution_began
                    or not isinstance(self.evaluation_digest, str)
                    or len(self.evaluation_digest) != 64
                    or any(c not in "0123456789abcdef" for c in self.evaluation_digest)
                ):
                    raise ValueError
            elif self.evaluation_digest is not None:
                raise ValueError
        except ValueError:
            raise Refused("check preview identities or outcome unavailable") from None


def preview_check(scope: RepositoryScope, facts: CheckFacts) -> dict[str, Any]:
    """Render exact local request bytes semantically; no approval or remote validity inferred.

    Digest binds repository scope, source/journey/profile/manifest, run lifecycle, original
    evaluation reference and the whole proposed request. The stable external ID is a discovery
    hint only, not an idempotency guarantee: GitHub may accept multiple creates with that ID.
    """
    if not isinstance(scope, RepositoryScope) or not isinstance(facts, CheckFacts):
        raise Refused("check preview input unavailable")
    identity = {
        "workspaceId": facts.workspace_id,
        "bindingId": facts.binding_id,
        "runId": facts.run_id,
        "appId": str(scope.app_id),
        "installationId": str(scope.installation_id),
        "accountId": str(scope.account_id),
        "repositoryId": str(scope.repository_id),
        "owner": scope.owner,
        "repository": scope.name,
        "sourceSha": facts.source_sha,
        "manifestDigest": facts.manifest_digest,
        "journeyDigest": facts.journey_digest,
        "profileDigest": facts.profile_digest,
    }
    status = "in_progress"
    conclusion: str | None = None
    if facts.status is RunStatus.QUEUED:
        status = "queued"
    elif facts.status is RunStatus.COMPLETED:
        status = "completed"
        conclusion = {
            Outcome.PASS: "success",
            Outcome.FAIL: "failure",
            Outcome.INCONCLUSIVE: "action_required",
        }[facts.outcome]
    elif facts.status is RunStatus.INTERRUPTED:
        status, conclusion = "completed", "action_required"
    elif facts.status is RunStatus.CANCELLED:
        status, conclusion = "completed", "cancelled"
    body: dict[str, Any] = {
        "name": "AccessForge journey evidence",
        "head_sha": facts.source_sha,
        "external_id": "accessforge:" + digest(identity),
        "status": status,
        "output": {
            "title": f"AccessForge: {facts.status.value} / {facts.outcome.value}",
            "summary": (
                PASS_SCOPE_STATEMENT
                + " Human review remains separate."
                + " This check does not authorize merge or deployment."
                + " Pending, interrupted or inconclusive evidence is not an accessibility PASS."
                + f"\nJourney: {facts.journey_digest}\nProfile: {facts.profile_digest}"
                + f"\nManifest: {facts.manifest_digest}"
            ),
        },
    }
    if conclusion is not None:
        body["conclusion"] = conclusion
    preview = {
        "schemaVersion": 1,
        "kind": "GITHUB_CHECK_CREATE_PREVIEW",
        "identity": identity,
        "runStatus": facts.status.value,
        "outcome": facts.outcome.value,
        "executionBegan": facts.execution_began,
        "evaluationDigest": facts.evaluation_digest,
        "request": {
            "method": "POST",
            "url": f"https://api.github.com/repos/{scope.owner}/{scope.name}/check-runs",
            "body": body,
        },
        "requiredApproval": "GITHUB_PUBLISH",
        "meaning": "LOCAL_PREVIEW_NOT_PUBLICATION_AUTHORITY",
    }
    return {**preview, "previewDigest": digest(preview)}
