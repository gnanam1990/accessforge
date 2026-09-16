"""Reconstruct configured fixture identity only with matching initial/final DB measurements.

Inputs must already pass original retained-artifact and authenticated observer verification.
This is logical fixture configuration plus incarnation agreement, not environment/AT identity.
"""

from __future__ import annotations

from typing import Any

from accessforge_contracts.reference_fixture import REFERENCE_FIXTURE_DIGEST
from accessforge_domain.canonical import digest
from accessforge_domain.timestamps import parse_rfc3339_utc
from accessforge_persistence.fixtures import contract_digest

from .execution_artifacts import Refused


def observed_fixture(
    *,
    setup: dict[str, Any] | None,
    final_source: dict[str, Any],
    context: dict[str, Any],
    fixture: dict[str, Any] | None,
) -> str | None:
    if setup is None or final_source.get("measurement") != "KNOWN":
        return None  # Legacy or unobserved state cannot acquire a manufactured identity.
    if final_source.get("fixtureIdentityDigest") is None:
        return None
    if (
        fixture is None
        or setup.get("format") != "accessforge.fixture-setup.v1"
        or setup.get("runId") != str(context["run_id"])
        or setup.get("attemptId") != str(context["attempt_id"])
        or setup.get("manifestDigest") != context["manifest_digest"]
        or final_source.get("fixtureInstanceId") != str(fixture["id"])
        or final_source.get("finalSample") is not True
        or final_source.get("effect") != "CREATE_TEST_REQUEST"
        or type(final_source.get("count")) is not int
        or final_source["count"] < 0
    ):
        raise Refused("fixture identity does not describe this final observer boundary")
    application = setup["observation"]["application"]
    if (
        application["nonce"] != fixture["nonce"]
        or application["templateDigest"] != REFERENCE_FIXTURE_DIGEST
        or fixture["template_digest"] != REFERENCE_FIXTURE_DIGEST
        or fixture["template_id"] != "service-request"
        or application["variant"]
        not in {
            "accessible",
            "inaccessible",
            "missing-label-v1",
            "broken-focus-v1",
            "keyboard-trap-v1",
        }
        or type(application["effectCount"]) is not int
        or application["effectCount"] != 0
        or final_source["fixtureIdentityDigest"]
        != digest(
            {key: application[key] for key in ("nonce", "templateDigest", "variant", "createdAt")}
        )
        or parse_rfc3339_utc(final_source["observedAt"])
        < parse_rfc3339_utc(application["observedAt"])
    ):
        raise Refused("initial and final fixture incarnation measurements differ")
    if fixture["observer_config"] != {"effect": "CREATE_TEST_REQUEST"}:
        return None  # Do not claim observation of additional, unsupported observer settings.
    if fixture["captured_contract_digest"] != contract_digest(
        template_id=fixture["template_id"],
        template_digest=fixture["template_digest"],
        navigator_values=fixture["navigator_values"],
        observer_config=fixture["observer_config"],
    ):
        raise Refused("original configured fixture values changed")
    # Reconstruct from protected configured inputs and independently measured reset variant.
    # Never copy context.fixtureDigest or the seal's expected identity into observed identities.
    return digest(
        {
            "schemaVersion": 2,
            "templateId": fixture["template_id"],
            "navigatorValues": fixture["navigator_values"],
            "resetValuesDigest": digest({"variant": application["variant"]}),
            "observerConfigDigest": digest(fixture["observer_config"]),
        }
    )
