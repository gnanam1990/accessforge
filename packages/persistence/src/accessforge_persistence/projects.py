"""Projects, environments and sealing a run's exact inputs.

Sealing is the moment a run stops being "against staging on main" and becomes a fixed set of bytes.
Everything here exists to make that transition honest:

* Inputs are captured **separately** — source tree, build artifact, environment configuration,
  journey, assertions, fixtures, runner profile, navigator policy, evaluator, model configuration.
  Conflating any two lets one change hide behind another.
* A seal is **immutable**. A changed input requires a new seal, never an amendment, which is why
  there is no update function here and a trigger refuses one.
* Identity is **revalidated immediately before dispatch**. An approval describes the inputs as they
  were when someone looked at them; between then and dispatch a deployment can change, and that must
  stop the run rather than produce a warning.
* Credentials are **referenced, never stored**. A manifest travels into every export that cites it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg

from accessforge_domain.canonical import digest
from accessforge_domain.origins import Origin, normalize_origin
from accessforge_domain.timestamps import is_expired, to_rfc3339_utc

from .source_intake import SourceIdentity


class ProjectError(Exception):
    """A project or environment operation was refused."""


class SealError(Exception):
    """A run could not be sealed, or a seal no longer describes reality."""


class IdentityChanged(SealError):
    """A sealed input has changed since the seal was created.

    Raised at dispatch. A changed deployment requires a new sealed run and a new exact
    authorization, not a warning and not a mutation of the existing approval (INV-03, INV-08).
    """


# --- projects and environments -------------------------------------------------------------------


def create_project(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    name: str,
    repository_url: str | None = None,
    repository_authorized_by: str | None = None,
) -> str:
    """Register a project.

    A repository URL without an authorizing user is refused. Public reachability of a repository is
    not consent to automate against it, and recording the authorizing person is what makes the
    difference auditable later.
    """
    if repository_url and not repository_authorized_by:
        raise ProjectError(
            "a repository requires an explicit authorizing user; reachability is not consent"
        )
    if repository_authorized_by is not None:
        # Checked here rather than left to the column's type, because an unparseable value reached
        # the database as a raw string and came back as an unhandled driver error -- a 500 with a
        # stack trace in the log and nothing useful for the caller. The authorizing user must also
        # be a live member of this workspace: "somebody authorized it" is only meaningful if the
        # somebody is a person this workspace can actually name.
        try:
            uuid.UUID(repository_authorized_by)
        except ValueError as exc:
            raise ProjectError(
                "the authorizing user must be identified by their user id, not by their name. "
                "A name is not a record of who granted permission."
            ) from exc
        member = conn.execute(
            "SELECT 1 FROM workspace_membership "
            "WHERE workspace_id = %s AND user_id = %s AND revoked_at IS NULL",
            (workspace_id, repository_authorized_by),
        ).fetchone()
        if member is None:
            raise ProjectError(
                "the authorizing user is not an active member of this workspace; an authorization "
                "recorded against someone who cannot be named here is not auditable"
            )
    project_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO project (id, workspace_id, name, repository_url, repository_authorized_by)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (project_id, workspace_id, name, repository_url, repository_authorized_by),
    )
    return project_id


@dataclass(frozen=True, slots=True)
class EnvironmentSpec:
    """What an environment is, before it is given an identity."""

    name: str
    allowed_origins: frozenset[Origin]
    fixture_reset_strategy: str
    observer_credential_ref: str
    reset_credential_ref: str
    permitted_effects: frozenset[str]
    expires_at: str

    def config_digest(self) -> str:
        """Digest of the environment's configuration.

        Credential *references* are included because swapping which credential profile is used is a
        configuration change that should invalidate a seal. Their values are not here at all, so no
        secret reaches the digest or anything derived from it.
        """
        return digest(
            {
                "name": self.name,
                "allowedOrigins": sorted(str(o) for o in self.allowed_origins),
                "fixtureResetStrategy": self.fixture_reset_strategy,
                "observerCredentialRef": self.observer_credential_ref,
                "resetCredentialRef": self.reset_credential_ref,
                "permittedEffects": sorted(self.permitted_effects),
            }
        )


def register_environment(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    project_id: str,
    spec: EnvironmentSpec,
    authorized_by: str,
) -> str:
    """Register an environment manifest.

    The observer and reset credential references must differ. The independent observer reads
    application state and the reset path rewrites it; one credential doing both would let whoever
    holds it both set up the answer and attest to it.
    """
    if spec.observer_credential_ref == spec.reset_credential_ref:
        raise ProjectError(
            "the observer and reset credentials must be different references; one identity "
            "that can both reset state and attest to it cannot be an independent observer"
        )
    if not spec.allowed_origins:
        raise ProjectError("an environment must declare at least one allowed origin")

    environment_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO environment_manifest
            (id, workspace_id, project_id, name, allowed_origins, fixture_reset_strategy,
             observer_credential_ref, reset_credential_ref, permitted_effects, authorized_by,
             config_digest, expires_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            environment_id,
            workspace_id,
            project_id,
            spec.name,
            sorted(str(o) for o in spec.allowed_origins),
            spec.fixture_reset_strategy,
            spec.observer_credential_ref,
            spec.reset_credential_ref,
            sorted(spec.permitted_effects),
            authorized_by,
            spec.config_digest(),
            spec.expires_at,
        ),
    )
    return environment_id


def supersede_environment(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    old_environment_id: str,
    spec: EnvironmentSpec,
    authorized_by: str,
) -> str:
    """Replace an environment with a new manifest, keeping the old one intact.

    Append-only. Evidence citing the old identity keeps meaning what it meant, and the link records
    that a successor exists — which is how a reader of old evidence learns the configuration has
    since
    moved on.
    """
    row = conn.execute(
        "SELECT project_id FROM environment_manifest WHERE id = %s",
        (old_environment_id,),
    ).fetchone()
    if row is None:
        raise ProjectError("no such environment in this workspace")

    new_id = register_environment(
        conn,
        workspace_id=workspace_id,
        project_id=str(row["project_id"]),
        spec=spec,
        authorized_by=authorized_by,
    )
    conn.execute(
        "UPDATE environment_manifest SET superseded_by = %s, revoked_at = now() WHERE id = %s",
        (new_id, old_environment_id),
    )
    return new_id


def assert_environment_usable(
    conn: psycopg.Connection[dict[str, Any]], *, environment_id: str, now: str
) -> None:
    """Raise unless this environment may be acted on right now."""
    row = conn.execute(
        "SELECT revoked_at, expires_at, superseded_by FROM environment_manifest WHERE id = %s",
        (environment_id,),
    ).fetchone()
    if row is None:
        raise ProjectError("no such environment in this workspace")
    # Superseded is checked first: superseding also sets revoked_at, and "use the current manifest"
    # tells the caller what to do next where "revoked" only says no.
    if row["superseded_by"] is not None:
        raise ProjectError("this environment has been superseded; use the current manifest")
    if row["revoked_at"] is not None:
        raise ProjectError("this environment has been revoked")
    if is_expired(now=now, expires_at=to_rfc3339_utc(row["expires_at"])):
        raise ProjectError("this environment's authorization has expired")


# --- source and build identity --------------------------------------------------------------------


def record_source_snapshot(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    project_id: str,
    identity: SourceIdentity,
    requested_revision: str,
) -> str:
    """Record a resolved source identity.

    ``dirty`` is stored as observed. A dirty tree is usable for local work, and the point of storing
    the flag rather than refusing outright is that nothing downstream can later describe it as
    clean.
    """
    snapshot_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO source_snapshot
            (id, workspace_id, project_id, commit_sha, tree_digest, dirty, dirty_path_count,
             requested_revision)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            snapshot_id,
            workspace_id,
            project_id,
            identity.commit_sha,
            identity.tree_digest,
            identity.dirty,
            len(identity.dirty_paths),
            requested_revision,
        ),
    )
    return snapshot_id


def record_build_artifact(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    project_id: str,
    source_snapshot_id: str,
    artifact_digest: str,
    identity_observable: bool,
) -> str:
    """Record a build artifact identity.

    ``identity_observable`` is False for a deployment that cannot prove which artifact it serves.
    Such a run may still execute; it simply cannot make a fully verified provenance claim, and that
    limitation stays visible rather than being papered over with a plausible digest.
    """
    artifact_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO build_artifact
            (id, workspace_id, project_id, source_snapshot_id, artifact_digest,
             identity_observable)
        VALUES (%s,%s,%s,%s,%s,%s)
        """,
        (
            artifact_id,
            workspace_id,
            project_id,
            source_snapshot_id,
            artifact_digest,
            identity_observable,
        ),
    )
    return artifact_id


# --- sealing -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SealInputs:
    """Everything a run is sealed against, each captured separately."""

    journey_digest: str
    assertion_set_digest: str
    fixture_digest: str
    runner_profile_digest: str
    navigator_policy_digest: str
    evaluator_version: str
    model_config_digest: str


@dataclass(frozen=True, slots=True)
class Seal:
    sealed_manifest_id: str
    manifest_digest: str


def seal_run(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    project_id: str,
    source_snapshot_id: str,
    build_artifact_id: str,
    environment_manifest_id: str,
    inputs: SealInputs,
    run_id: str | None = None,
    authorization_id: str | None = None,
    now: str | None = None,
) -> Seal:
    """Seal a run against its exact inputs.

    The environment is checked for usability first: sealing against a revoked, expired or superseded
    environment would produce a manifest that looked authoritative and was never authorized.
    """
    moment = now or to_rfc3339_utc(datetime.now(UTC))
    assert_environment_usable(conn, environment_id=environment_manifest_id, now=moment)

    env = conn.execute(
        "SELECT config_digest FROM environment_manifest WHERE id = %s",
        (environment_manifest_id,),
    ).fetchone()
    source = conn.execute(
        "SELECT commit_sha, tree_digest FROM source_snapshot WHERE id = %s",
        (source_snapshot_id,),
    ).fetchone()
    artifact = conn.execute(
        "SELECT artifact_digest FROM build_artifact WHERE id = %s", (build_artifact_id,)
    ).fetchone()

    if env is None or source is None or artifact is None:
        raise SealError("a sealed input is missing or not visible in this workspace")

    manifest_digest = digest(
        {
            "schemaVersion": 1,
            "workspaceId": workspace_id,
            "projectId": project_id,
            "sourceCommitSha": str(source["commit_sha"]),
            "sourceTreeDigest": str(source["tree_digest"]),
            "buildArtifactDigest": str(artifact["artifact_digest"]),
            "environmentConfigDigest": str(env["config_digest"]),
            "journeyDigest": inputs.journey_digest,
            "assertionSetDigest": inputs.assertion_set_digest,
            "fixtureDigest": inputs.fixture_digest,
            "runnerProfileDigest": inputs.runner_profile_digest,
            "navigatorPolicyDigest": inputs.navigator_policy_digest,
            "evaluatorVersion": inputs.evaluator_version,
            "modelConfigDigest": inputs.model_config_digest,
        }
    )

    sealed_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO sealed_manifest
            (id, workspace_id, project_id, run_id, source_snapshot_id, build_artifact_id,
             environment_manifest_id, environment_config_digest, journey_digest,
             assertion_set_digest, fixture_digest, runner_profile_digest, navigator_policy_digest,
             evaluator_version, model_config_digest, manifest_digest, authorization_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            sealed_id,
            workspace_id,
            project_id,
            run_id,
            source_snapshot_id,
            build_artifact_id,
            environment_manifest_id,
            str(env["config_digest"]),
            inputs.journey_digest,
            inputs.assertion_set_digest,
            inputs.fixture_digest,
            inputs.runner_profile_digest,
            inputs.navigator_policy_digest,
            inputs.evaluator_version,
            inputs.model_config_digest,
            manifest_digest,
            authorization_id,
        ),
    )
    return Seal(sealed_manifest_id=sealed_id, manifest_digest=manifest_digest)


def revalidate_before_dispatch(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    sealed_manifest_id: str,
    observed_source: SourceIdentity,
    observed_artifact_digest: str,
    now: str | None = None,
) -> None:
    """Re-check, at dispatch, that the sealed inputs still describe reality.

    An approval describes inputs as they were when someone looked at them. Between then and dispatch
    a deployment can be replaced, a branch force-pushed, or a working tree edited. Any of those
    means
    nobody authorized what is about to run, so this raises rather than warns — and the remedy is
    a new
    seal with a new exact authorization, never an amendment to this one.
    """
    moment = now or to_rfc3339_utc(datetime.now(UTC))
    row = conn.execute(
        """
        SELECT s.commit_sha, s.tree_digest, b.artifact_digest, m.environment_manifest_id
        FROM sealed_manifest m
        JOIN source_snapshot s ON s.id = m.source_snapshot_id
        JOIN build_artifact  b ON b.id = m.build_artifact_id
        WHERE m.id = %s
        """,
        (sealed_manifest_id,),
    ).fetchone()
    if row is None:
        raise SealError("no such sealed manifest in this workspace")

    # The environment must still be usable, independently of whether the bytes match.
    assert_environment_usable(conn, environment_id=str(row["environment_manifest_id"]), now=moment)

    if observed_source.commit_sha != str(row["commit_sha"]):
        raise IdentityChanged(
            f"source commit changed since sealing: sealed {str(row['commit_sha'])[:12]}, "
            f"observed {observed_source.commit_sha[:12]}. A force-pushed ref keeps its name and "
            "changes its bytes, so this needs a new seal."
        )
    if observed_source.tree_digest != str(row["tree_digest"]):
        raise IdentityChanged(
            "source tree changed since sealing even though the commit matches; the working "
            "tree has been edited"
        )
    if observed_artifact_digest != str(row["artifact_digest"]):
        raise IdentityChanged(
            "build artifact changed since sealing; the same URL is serving different bytes"
        )


def capability_summary(
    conn: psycopg.Connection[dict[str, Any]], *, environment_id: str
) -> dict[str, Any]:
    """A safe summary for journey authoring and patch preparation.

    Deliberately excludes credential references as well as values: a journey author needs to know
    *that* a reset strategy exists and which origins are in scope, not which credential profile
    performs it.
    """
    row = conn.execute(
        """
        SELECT name, allowed_origins, fixture_reset_strategy, permitted_effects, revoked_at,
               expires_at, superseded_by
        FROM environment_manifest WHERE id = %s
        """,
        (environment_id,),
    ).fetchone()
    if row is None:
        raise ProjectError("no such environment in this workspace")

    artifact = conn.execute(
        """
        SELECT bool_and(identity_observable) AS observable
        FROM build_artifact b
        JOIN sealed_manifest m ON m.build_artifact_id = b.id
        WHERE m.environment_manifest_id = %s
        """,
        (environment_id,),
    ).fetchone()

    return {
        "name": str(row["name"]),
        "allowedOrigins": [str(normalize_origin(o)) for o in row["allowed_origins"]],
        "fixtureResetStrategy": str(row["fixture_reset_strategy"]),
        "permittedEffects": sorted(row["permitted_effects"]),
        "usable": row["revoked_at"] is None and row["superseded_by"] is None,
        # None when nothing has been sealed yet: "unknown" rather than an optimistic True.
        "buildIdentityObservable": (
            None
            if artifact is None or artifact["observable"] is None
            else bool(artifact["observable"])
        ),
    }
