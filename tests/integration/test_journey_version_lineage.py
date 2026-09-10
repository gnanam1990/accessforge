"""Journey version lineage cannot cross a tenant boundary.

Raised by the module 06 independent review as an IDOR (CWE-639). `journey_version.supersedes`
referenced `journey_version (id)` alone. Foreign keys are checked by the system, which is exempt
from row-level security, so the constraint would have accepted another tenant's journey version as a
predecessor -- linking two tenants' lineage through a row the citing workspace can never read.

Requirements: FR-003. Invariants: INV-05, INV-07.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from accessforge_domain.canonical import digest
from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    projects,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x60))
WS_OTHER = str(uuid.UUID(int=0x61))

D = digest({"journey": "e0"})


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS, "A"), (WS_OTHER, "B")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
    yield test_database_url


def _project(url: str, workspace: str) -> str:
    with workspace_connection(url, workspace) as conn:
        return projects.create_project(conn, workspace_id=workspace, name="p")


def _insert_version(
    conn: psycopg.Connection[dict],
    workspace: str,
    project_id: str,
    *,
    supersedes: str | None = None,
) -> str:
    version_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO journey_version
            (id, workspace_id, project_id, name, platform, journey_digest, assertion_set_digest,
             fixture_digest, navigator_policy_digest, navigator_policy, reviewer_summary,
             supersedes)
        VALUES (%s, %s, %s, 'j', 'darwin', %s, %s, %s, %s, '{}', '{}', %s)
        """,
        (version_id, workspace, project_id, D, D, D, D, supersedes),
    )
    return version_id


def test_a_version_may_supersede_one_in_its_own_workspace(db: str) -> None:
    """Allowed-path control: lineage within a tenant is the ordinary case and must still work."""
    project_id = _project(db, WS)
    with workspace_connection(db, WS) as conn:
        first = _insert_version(conn, WS, project_id)
        second = _insert_version(conn, WS, project_id, supersedes=first)
        row = conn.execute(
            "SELECT supersedes FROM journey_version WHERE id = %s", (second,)
        ).fetchone()
    assert row is not None and str(row["supersedes"]) == first


def test_a_version_cannot_supersede_another_tenants_version(db: str) -> None:
    """The composite key is what refuses this.

    Row-level security does not, and cannot: the foreign key is checked by the system, which every
    policy exempts. Without the workspace column in the key this INSERT succeeds and workspace B's
    journey lineage points at a row in workspace A.
    """
    project_a = _project(db, WS)
    project_b = _project(db, WS_OTHER)

    with workspace_connection(db, WS) as conn:
        theirs = _insert_version(conn, WS, project_a)

    with workspace_connection(db, WS_OTHER) as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            _insert_version(conn, WS_OTHER, project_b, supersedes=theirs)


def test_the_predecessor_is_invisible_to_the_other_tenant(db: str) -> None:
    """Why the foreign key had to carry the tenant: the row cannot even be read from here.

    A reference to a row a workspace can never see is a reference nobody in that workspace can
    audit, which is the shape of the vulnerability rather than an inconvenience.
    """
    project_a = _project(db, WS)
    with workspace_connection(db, WS) as conn:
        theirs = _insert_version(conn, WS, project_a)

    with workspace_connection(db, WS_OTHER) as conn:
        assert (
            conn.execute("SELECT 1 FROM journey_version WHERE id = %s", (theirs,)).fetchall() == []
        )


def test_the_composite_key_is_actually_the_one_in_force(db: str) -> None:
    """Structural, against the schema rather than against behaviour.

    A single-column key would still refuse an entirely unknown id, so a test that only inserted a
    random uuid would pass against the vulnerable schema too.
    """
    with unscoped_connection(db) as conn:
        # Joining key_column_usage to constraint_column_usage fans out -- each key column pairs with
        # each referenced column -- so the pair is read from key_column_usage alone and the target
        # table is established by the separate query below.
        columns = [
            str(r["column_name"])
            for r in conn.execute(
                """
                SELECT DISTINCT kcu.column_name, kcu.ordinal_position
                  FROM information_schema.table_constraints tc
                  JOIN information_schema.key_column_usage kcu
                    ON kcu.constraint_name = tc.constraint_name
                 WHERE tc.constraint_type = 'FOREIGN KEY'
                   AND tc.table_name = 'journey_version'
                   AND tc.constraint_name LIKE '%%supersedes%%'
                 ORDER BY kcu.ordinal_position
                """
            ).fetchall()
        ]
    assert columns == ["supersedes", "workspace_id"], columns

    with unscoped_connection(db) as conn:
        referenced = {
            str(r["table_name"])
            for r in conn.execute(
                """
                SELECT DISTINCT ccu.table_name
                  FROM information_schema.table_constraints tc
                  JOIN information_schema.constraint_column_usage ccu
                    ON ccu.constraint_name = tc.constraint_name
                 WHERE tc.constraint_type = 'FOREIGN KEY'
                   AND tc.table_name = 'journey_version'
                   AND tc.constraint_name LIKE '%%supersedes%%'
                """
            ).fetchall()
        }
    assert referenced == {"journey_version"}, referenced
