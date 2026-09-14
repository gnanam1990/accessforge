"""Read only setup identity metadata for a caller that has checked live run authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg

from accessforge_contracts.reference_fixture import REFERENCE_FIXTURE_DIGEST
from accessforge_domain.canonical import digest
from accessforge_domain.origins import normalize_origin
from accessforge_domain.reference_destination import (
    candidate_reference_destination,
    reference_destination,
)
from accessforge_persistence import candidate_runs


@dataclass(frozen=True)
class RuntimeDestination:
    url: str
    authorized_candidate_origin: str | None = None


def load_destination(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    run_id: str,
    manifest: dict[str, Any],
    fixture: dict[str, Any],
    sealed_url: str,
) -> RuntimeDestination | None:
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
    try:
        candidate = candidate_runs.assert_live(conn, run_id=run_id)
    except candidate_runs.Refused as exc:
        raise ValueError("candidate destination no longer has live authority") from exc
    if candidate is None:
        return RuntimeDestination(
            reference_destination(
                sealed_url=sealed_url, origin=context["origin"], nonce=context["nonce"]
            )
        )
    original = conn.execute(
        "SELECT s.canonical_manifest,s.manifest_digest,e.allowed_origins FROM sealed_manifest s "
        "JOIN environment_manifest e ON e.id=s.environment_manifest_id "
        "AND e.workspace_id=s.workspace_id WHERE s.run_id=%s AND s.workspace_id=%s",
        (candidate["baseline_run_id"], workspace_id),
    ).fetchone()
    if original is None or not isinstance(original["canonical_manifest"], dict):
        raise ValueError("original baseline destination identity unavailable")
    baseline = original["canonical_manifest"]
    baseline_origin, marker, placeholder = sealed_url.rpartition("/form/")
    if (
        digest(baseline) != original["manifest_digest"]
        or not marker
        or placeholder != "FIXTURE"
        or baseline_origin not in original["allowed_origins"]
        or any(
            baseline[key] != manifest[key]
            for key in (
                "journeyVersionId",
                "navigatorPolicyDigest",
                "fixtureDigest",
                "assertionSetDigest",
            )
        )
        or candidate["fixture_nonce"] != context["nonce"]
        or candidate["permitted_differences"]
        != [
            {
                "field": "environment_config_digest",
                "baseline": baseline["environmentConfigDigest"],
                "candidate": manifest["environmentConfigDigest"],
                "reason": "exact authorized isolated candidate origin",
            }
        ]
    ):
        raise ValueError("candidate destination differs from its frozen baseline or allowed change")
    return RuntimeDestination(
        candidate_reference_destination(
            sealed_url=sealed_url,
            baseline_origin=baseline_origin,
            candidate_origin=context["origin"],
            nonce=context["nonce"],
        ),
        context["origin"],
    )
