"""Read only setup identity metadata for a caller that has checked live run authority."""

from __future__ import annotations

from typing import Any

import psycopg

from accessforge_contracts.reference_fixture import REFERENCE_FIXTURE_DIGEST
from accessforge_domain.canonical import digest
from accessforge_domain.origins import normalize_origin
from accessforge_domain.reference_destination import reference_destination


def load_destination(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    run_id: str,
    manifest: dict[str, Any],
    fixture: dict[str, Any],
    sealed_url: str,
) -> str | None:
    """No observer configuration, counts, receipts or assertion expectations enter this read.

    Confirmed setup is a point-in-time identity boundary, not actual desktop evidence.
    Its retained observation contributes only its binding to the original context hash.
    """
    environment = conn.execute(
        "SELECT e.fixture_reset_strategy,e.allowed_origins,e.reset_credential_ref,"
        "e.observer_credential_ref FROM environment_manifest e JOIN sealed_manifest s "
        "ON s.environment_manifest_id=e.id AND s.workspace_id=e.workspace_id "
        "WHERE s.run_id=%s AND s.workspace_id=%s",
        (run_id, workspace_id),
    ).fetchone()
    reservation = conn.execute(
        "SELECT context,context_digest,observation->>'contextDigest' AS confirmed_context "
        "FROM fixture_setup_reservation WHERE run_id=%s AND workspace_id=%s",
        (run_id, workspace_id),
    ).fetchone()
    if environment is None:
        raise ValueError("original fixture environment unavailable")
    if reservation is None:
        if (
            environment["fixture_reset_strategy"] == "FRESH_FIXTURE_NONCE"
            or "/form/FIXTURE" in sealed_url
        ):
            raise ValueError("confirmed fixture destination unavailable")
        return None
    context = reservation["context"]
    expected_keys = {
        "workspaceId",
        "runId",
        "manifestDigest",
        "fixtureId",
        "nonce",
        "fixtureDigest",
        "templateDigest",
        "variant",
        "origin",
        "environmentConfigDigest",
        "resetCredentialRef",
        "observerCredentialRef",
    }
    if (
        not isinstance(context, dict)
        or set(context) != expected_keys
        or digest(context) != reservation["context_digest"]
        or reservation["confirmed_context"] != reservation["context_digest"]
        or environment["fixture_reset_strategy"] != "FRESH_FIXTURE_NONCE"
        or context["workspaceId"] != workspace_id
        or context["runId"] != run_id
        or context["manifestDigest"] != digest(manifest)
        or context["fixtureId"] != str(fixture["id"])
        or context["nonce"] != fixture["nonce"]
        or context["fixtureDigest"] != manifest["fixtureDigest"]
        or context["templateDigest"] != REFERENCE_FIXTURE_DIGEST
        or fixture["template_digest"] != REFERENCE_FIXTURE_DIGEST
        or context["environmentConfigDigest"] != manifest["environmentConfigDigest"]
        or context["resetCredentialRef"] != environment["reset_credential_ref"]
        or context["observerCredentialRef"] != environment["observer_credential_ref"]
        or normalize_origin(context["origin"])
        not in {normalize_origin(o) for o in environment["allowed_origins"]}
    ):
        raise ValueError("reserved destination differs from original execution identity")
    return reference_destination(
        sealed_url=sealed_url, origin=context["origin"], nonce=context["nonce"]
    )
