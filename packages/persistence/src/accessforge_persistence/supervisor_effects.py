"""Route effect permission by the authenticated original run, never a caller-supplied mode."""

from typing import Any

import psycopg

from . import (
    baseline_effect_delivery,
    baseline_effects,
    candidate_effect_delivery,
    candidate_effects,
    supervisor_sessions,
)


def _baseline(
    conn: psycopg.Connection[dict[str, Any]], workspace_id: str, session_id: str, token: str
) -> bool:
    session, _ = supervisor_sessions._live(conn, workspace_id, session_id, token)
    row = conn.execute(
        "SELECT EXISTS(SELECT 1 FROM baseline_session_binding WHERE run_id=%s) AS baseline,"
        "EXISTS(SELECT 1 FROM candidate_run_binding WHERE run_id=%s) AS candidate",
        (session["run_id"], session["run_id"]),
    ).fetchone()
    if row is None or row["baseline"] == row["candidate"]:
        raise supervisor_sessions.Refused("one original baseline or candidate binding required")
    return bool(row["baseline"])


def authorize_form(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    session_id: str,
    action_id: str,
    token: str,
) -> dict[str, Any]:
    issuer = (
        baseline_effects.authorize_form
        if _baseline(conn, workspace_id, session_id, token)
        else candidate_effects.authorize_form
    )
    return issuer(
        conn, workspace_id=workspace_id, session_id=session_id, action_id=action_id, token=token
    )


def status(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    session_id: str,
    action_id: str,
    token: str,
) -> dict[str, Any]:
    delivery = (
        baseline_effect_delivery.status
        if _baseline(conn, workspace_id, session_id, token)
        else candidate_effect_delivery.status
    )
    return delivery(
        conn, workspace_id=workspace_id, session_id=session_id, action_id=action_id, token=token
    )
