"""Freezing and reading journey versions.

Module 06 built the DSL, the validation and the compiler; module 18 exposed a read route. Nothing
wrote a journey version, so every journey in this system so far was inserted by a test. This module
is the writer, and it is deliberately thin: it compiles through module 06's own `compile_journey`
and stores the result. There is no second definition of what a journey means here.

Three properties the schema already enforces and this module must not undermine.

**Versions are immutable.** A trigger refuses every UPDATE. Editing produces a new row with a new
digest and a `supersedes` link, because success criteria that could change mid-run would make every
outcome provisional (INV-05, INV-16).

**Lineage is within one project and one workspace.** A successor that pointed at another project's
version would make the history of a journey depend on a project the reader may not be able to see.

**The digests come from the compiler, never from here.** A digest computed in this module would be a
second implementation of the seal, and the second one is always the one that drifts.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import psycopg

from accessforge_domain.journeys import CompiledJourney, JourneyDraft, compile_journey


class JourneyPersistenceError(RuntimeError):
    """A journey version could not be stored."""


def freeze_version(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    project_id: str,
    draft: JourneyDraft,
    supersedes: str | None = None,
) -> CompiledJourney:
    """Compile a draft and store the resulting immutable version.

    Returns the compiled journey rather than just its id, so the caller can show the reviewer
    summary and the digests that were actually sealed — not a re-derivation of them.

    Validation failures propagate as `CapabilityError` or `JourneyError` from module 06. They are
    not wrapped: the route maps them to a 422 and a 400 respectively, and swallowing the distinction
    here would make a well-formed-but-unsupported journey indistinguishable from a malformed one.
    """
    project = conn.execute("SELECT id FROM project WHERE id = %s", (project_id,)).fetchone()
    if project is None:
        # Row-level security means a project in another workspace is simply absent, so this one
        # message covers "does not exist" and "not yours" without the caller learning which.
        raise JourneyPersistenceError("no such project is available in this workspace")

    if supersedes is not None:
        predecessor = conn.execute(
            "SELECT project_id FROM journey_version WHERE id = %s", (supersedes,)
        ).fetchone()
        if predecessor is None:
            raise JourneyPersistenceError("the version this supersedes is not available here")
        if str(predecessor["project_id"]) != project_id:
            # The composite foreign key already prevents crossing a workspace. This prevents
            # crossing a project inside one, which would make a journey's history depend on a
            # project the reader may not be able to see.
            raise JourneyPersistenceError(
                "a successor must belong to the same project as the version it supersedes"
            )

    version_id = str(uuid.uuid4())
    compiled = compile_journey(draft, version_id=version_id)

    conn.execute(
        """
        INSERT INTO journey_version
            (id, workspace_id, project_id, name, platform, journey_digest, assertion_set_digest,
             fixture_digest, navigator_policy_digest, navigator_policy, reviewer_summary,
             supersedes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            compiled.version.version_id,
            workspace_id,
            project_id,
            draft.name,
            draft.platform,
            compiled.version.journey_digest,
            compiled.version.assertion_set_digest,
            compiled.version.fixture_digest,
            compiled.version.navigator_policy_digest,
            json.dumps(compiled.navigator_policy),
            json.dumps(compiled.reviewer_summary),
            supersedes,
        ),
    )
    return compiled


def list_versions(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    project_id: str,
    after: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Versions of one project's journeys, oldest identity first.

    Keyset on `id` like every other listing in this codebase: an `OFFSET` skips and repeats rows as
    the table grows underneath a reader, and a journey list grows every time somebody edits.
    """
    return conn.execute(
        """
        SELECT id, name, platform, journey_digest, assertion_set_digest, fixture_digest,
               navigator_policy_digest, reviewer_summary, supersedes, created_at
        FROM journey_version
        WHERE project_id = %s AND (%s::uuid IS NULL OR id > %s::uuid)
        ORDER BY id
        LIMIT %s
        """,
        (project_id, after, after, limit),
    ).fetchall()


def superseded_by(conn: psycopg.Connection[dict[str, Any]], *, project_id: str) -> dict[str, str]:
    """Map each version to the version that replaced it, where one exists.

    Read separately rather than joined into the listing, because a version's successor may be on a
    later page and a row that reported "no successor" for that reason would be wrong in the one
    direction that matters: a reader deciding a frozen version is still current.
    """
    rows = conn.execute(
        "SELECT id, supersedes FROM journey_version "
        "WHERE project_id = %s AND supersedes IS NOT NULL",
        (project_id,),
    ).fetchall()
    return {str(r["supersedes"]): str(r["id"]) for r in rows}
