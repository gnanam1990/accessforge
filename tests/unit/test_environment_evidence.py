"""Synthetic verified-input interpreter tests, never real host/reader acceptance."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest

from accessforge_domain.canonical import digest
from accessforge_orchestrator.environment_evidence import observed_environment
from accessforge_orchestrator.execution_artifacts import Refused
from accessforge_orchestrator.runtime_evidence import RuntimeEvidence


def inputs() -> dict[str, Any]:
    configuration = {
        "name": "isolated-reference",
        "allowedOrigins": ["http://127.0.0.1:8081"],
        "fixtureResetStrategy": "FRESH_FIXTURE_NONCE",
        "observerCredentialRef": "observer-profile",
        "resetCredentialRef": "reset-profile",
        "permittedEffects": ["FORM_SUBMIT"],
    }
    identity = digest(configuration)
    return {
        "environment": {
            "name": configuration["name"],
            "allowed_origins": configuration["allowedOrigins"],
            "fixture_reset_strategy": configuration["fixtureResetStrategy"],
            "observer_credential_ref": configuration["observerCredentialRef"],
            "reset_credential_ref": configuration["resetCredentialRef"],
            "permitted_effects": configuration["permittedEffects"],
            "expires_at": datetime(2026, 9, 14, tzinfo=UTC),
        },
        "snapshots": {
            "FIXTURE_SETUP": {
                "context": {
                    "environmentConfigDigest": identity,
                    "origin": "http://127.0.0.1:8081",
                    "observerCredentialRef": "observer-profile",
                    "resetCredentialRef": "reset-profile",
                }
            },
            "ACTION_TRACE": {
                "records": [
                    {
                        "eventType": "ACTION_INTENT",
                        "payload": {
                            "sourceRecord": {"origin": "http://127.0.0.1:8081"},
                        },
                    }
                ]
            },
        },
        "final_source": {
            "environmentConfigurationDigest": identity,
            "finalSample": True,
            "measurement": "KNOWN",
        },
        "runtime": RuntimeEvidence(True, "a" * 64, ()),
        "fixture_identity": "b" * 64,
    }


def test_configuration_requires_joined_original_producers() -> None:
    data = inputs()
    assert observed_environment(**data) == data["final_source"]["environmentConfigurationDigest"]
    # Reconstruct instead of copying a claimed identity, even when both producers claim the same.
    data["final_source"]["environmentConfigurationDigest"] = "c" * 64
    data["snapshots"]["FIXTURE_SETUP"]["context"]["environmentConfigDigest"] = "c" * 64
    with pytest.raises(Refused):
        observed_environment(**data)


@pytest.mark.parametrize("missing", ["setup", "observer", "fixture", "preflight", "measurement"])
def test_incomplete_evidence_never_acquires_an_environment(missing: str) -> None:
    data = inputs()
    if missing == "setup":
        data["snapshots"].pop("FIXTURE_SETUP")
    elif missing == "observer":
        data["final_source"].pop("environmentConfigurationDigest")
    elif missing == "fixture":
        data["fixture_identity"] = None
    elif missing == "preflight":
        data["runtime"] = RuntimeEvidence(False, None, ("missing",))
    else:
        data["final_source"]["measurement"] = "UNKNOWN"
    assert observed_environment(**data) is None


@pytest.mark.parametrize("field", ["name", "observer_credential_ref", "reset_credential_ref"])
def test_protected_configuration_drift_is_not_relabelled(field: str) -> None:
    data = inputs()
    data["environment"][field] = "changed"
    with pytest.raises(Refused):
        observed_environment(**data)


def test_every_action_must_use_the_original_prepared_origin() -> None:
    data = inputs()
    second = deepcopy(data["snapshots"]["ACTION_TRACE"]["records"][0])
    second["payload"]["sourceRecord"]["origin"] = "http://127.0.0.1:8082"
    data["snapshots"]["ACTION_TRACE"]["records"].append(second)
    with pytest.raises(Refused):
        observed_environment(**data)
