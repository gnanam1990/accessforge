"""Non-member tester-desk participants: session discovery, own-row visibility, denied surfaces.

The real-browser run found a non-member tester could not enter their own desk: the session's
workspace list only read `workspace_membership`, and the route guard treats an unlisted workspace
as not-found. Migration 0081 adds SELECT-only `own_row_select` policies so the session can name
workspaces where the caller holds a tester enrollment or client-viewer grant — without letting a
user-scoped connection touch anyone else's rows or write anything.

These tests pin the three halves of that fix: the session payload, the read scope, and the
write/denial surface that must stay exactly as tight as before.
"""

from __future__ import annotations

import psycopg
import pytest

from accessforge_persistence import user_connection, workspace_connection  # noqa: E402

from .conftest import Desk  # noqa: E402


def _grant_viewer(desk: Desk, user_id: str) -> None:
    owner = desk.client_for(desk.owner)
    granted = owner.post(desk.path(f"/clients/{desk.client_id}/viewers"), json={"userId": user_id})
    assert granted.status_code == 201, granted.text


def test_session_names_tester_and_client_workspaces(desk: Desk) -> None:
    """A non-member's own tester/client rows put the workspace in their session."""
    _grant_viewer(desk, desk.client_user)

    tester = desk.client_for(desk.tester_user)
    session = tester.get("/v1/session")
    assert session.status_code == 200
    mine = [w for w in session.json()["workspaces"] if w["workspaceId"] == desk.workspace_id]
    assert [w["role"] for w in mine] == ["TESTER"]

    client = desk.client_for(desk.client_user)
    session = client.get("/v1/session")
    assert session.status_code == 200
    mine = [w for w in session.json()["workspaces"] if w["workspaceId"] == desk.workspace_id]
    assert [w["role"] for w in mine] == ["CLIENT"]

    # An outsider holds nothing in this workspace: the session does not name it.
    outsider = desk.client_for(desk.outsider_user)
    session = outsider.get("/v1/session")
    assert session.status_code == 200
    assert all(w["workspaceId"] != desk.workspace_id for w in session.json()["workspaces"])


def test_session_prefers_membership_over_tester_access(desk: Desk, test_database_url: str) -> None:
    """A user who is both a member and a tester keeps the member role."""
    with workspace_connection(test_database_url, desk.workspace_id) as conn:
        conn.execute(
            "INSERT INTO td_tester(id, workspace_id, user_id, display_name, browser_name, "
            "browser_version, assistive_technology, enrolled_by) "
            "VALUES (gen_random_uuid(), %s, %s, 'OwnerTester', 'Safari', '18', 'VoiceOver', %s)",
            (desk.workspace_id, desk.owner, desk.owner),
        )
    owner = desk.client_for(desk.owner)
    mine = [
        w
        for w in owner.get("/v1/session").json()["workspaces"]
        if w["workspaceId"] == desk.workspace_id
    ]
    assert [w["role"] for w in mine] == ["OWNER"]


def test_own_row_policy_reads_only_own_rows(desk: Desk, test_database_url: str) -> None:
    """The SELECT policy widens to exactly the caller's rows — never a neighbour's."""
    _grant_viewer(desk, desk.client_user)

    with user_connection(test_database_url, desk.tester_user) as conn:
        seen = conn.execute("SELECT user_id FROM td_tester").fetchall()
        assert {str(r["user_id"]) for r in seen} == {desk.tester_user}

        seen = conn.execute("SELECT user_id FROM td_client_viewer").fetchall()
        assert {str(r["user_id"]) for r in seen} == set()

    with user_connection(test_database_url, desk.client_user) as conn:
        seen = conn.execute("SELECT user_id FROM td_client_viewer").fetchall()
        assert {str(r["user_id"]) for r in seen} == {desk.client_user}

        seen = conn.execute("SELECT user_id FROM td_tester").fetchall()
        assert seen == []


def test_own_row_policy_never_writes(desk: Desk, test_database_url: str) -> None:
    """SELECT is the only thing the new policy permits: non-member writes still fail."""
    with user_connection(test_database_url, desk.tester_user) as conn:
        # DELETE consults USING only; the workspace-scoped policy sees zero rows for this
        # connection, so the delete is a no-op rather than an error. Either way nothing is removed.
        deleted = conn.execute("DELETE FROM td_tester WHERE user_id=%s", (desk.tester_user,))
        assert deleted.rowcount == 0
        updated = conn.execute(
            "UPDATE td_tester SET display_name='x' WHERE user_id=%s", (desk.tester_user,)
        )
        assert updated.rowcount == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO td_tester(id, workspace_id, user_id, display_name, browser_name, "
                "browser_version, assistive_technology, enrolled_by) "
                "VALUES (gen_random_uuid(), %s, %s, 'Self', 'Safari', '18', 'VoiceOver', %s)",
                (desk.workspace_id, desk.tester_user, desk.tester_user),
            )

    # The rows the non-member could not touch are still there, untouched.
    with workspace_connection(test_database_url, desk.workspace_id) as conn:
        rows = conn.execute(
            "SELECT id FROM td_tester WHERE user_id=%s", (desk.tester_user,)
        ).fetchall()
        assert len(rows) == 1


def test_tester_and_client_are_refused_on_agency_surfaces(desk: Desk) -> None:
    """Naming the workspace in the session is not authority: agency routes still refuse."""
    for user in (desk.tester_user, desk.client_user):
        actor = desk.client_for(user)
        assert actor.get(desk.path("/commissions")).status_code in (403, 404)
        assert actor.get(desk.path("/review/queue")).status_code in (403, 404)
        assert actor.get(desk.path("/finance/payables")).status_code in (403, 404)
        assert actor.get(desk.path("/testers")).status_code in (403, 404)
        refused = actor.post(
            desk.path("/commissions"),
            json={"clientId": desk.client_id, "reference": "AF-X", "brief": {}},
        )
        assert refused.status_code in (403, 404)
