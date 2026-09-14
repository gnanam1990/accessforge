"""Join already-verified setup, observer and per-action runtime environment evidence.

This establishes configured environment identity, not uninterrupted physical host state.
Call only after original retained artifact/stream verification and fixture/runtime interpretation.
"""

from __future__ import annotations

from typing import Any

from accessforge_domain.origins import normalize_origin
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence.projects import EnvironmentSpec

from .execution_artifacts import Refused
from .runtime_evidence import RuntimeEvidence


def observed_environment(
    *,
    environment: dict[str, Any] | None,
    snapshots: dict[str, Any],
    final_source: dict[str, Any],
    runtime: RuntimeEvidence,
    fixture_identity: str | None,
) -> str | None:
    setup = snapshots.get("FIXTURE_SETUP")
    witness = final_source.get("environmentConfigurationDigest")
    if (
        setup is None
        or witness is None
        or fixture_identity is None
        or not runtime.preflight_passed
        or final_source.get("measurement") != "KNOWN"
    ):
        return None  # Missing original coverage is not an inferred configured identity.
    if environment is None:
        raise Refused("original environment configuration unavailable")
    spec = EnvironmentSpec(
        name=environment["name"],
        allowed_origins=frozenset(normalize_origin(o) for o in environment["allowed_origins"]),
        fixture_reset_strategy=environment["fixture_reset_strategy"],
        observer_credential_ref=environment["observer_credential_ref"],
        reset_credential_ref=environment["reset_credential_ref"],
        permitted_effects=frozenset(environment["permitted_effects"]),
        expires_at=to_rfc3339_utc(environment["expires_at"]),
    )
    configured = spec.config_digest()
    context = setup["context"]
    if (
        spec.fixture_reset_strategy != "FRESH_FIXTURE_NONCE"
        or spec.observer_credential_ref == spec.reset_credential_ref
        or context.get("environmentConfigDigest") != configured
        or witness != configured
        or context.get("observerCredentialRef") != spec.observer_credential_ref
        or context.get("resetCredentialRef") != spec.reset_credential_ref
        or final_source.get("finalSample") is not True
    ):
        raise Refused("original setup and observer environment configurations differ")
    try:
        origin = normalize_origin(context["origin"])
    except (KeyError, ValueError, TypeError) as exc:
        raise Refused("original setup origin unavailable") from exc
    if origin not in spec.allowed_origins:
        raise Refused("original setup origin is outside configured environment")
    # Runtime interpretation has already required one complete preflight per original action.
    # The authenticated intent carries the supervisor's independently sampled native origin;
    # dispatch commits require it to remain identical. Never infer it from the requested URL.
    intents = [
        event["payload"]["sourceRecord"]
        for event in snapshots["ACTION_TRACE"]["records"]
        if event["eventType"] == "ACTION_INTENT"
    ]
    if not intents:
        return None
    for intent in intents:
        try:
            measured = normalize_origin(intent["origin"])
        except (KeyError, ValueError, TypeError) as exc:
            raise Refused("runtime action origin unavailable") from exc
        if measured != origin:
            raise Refused("runtime actions do not use the originally prepared fixture origin")
    # Reconstructed configuration, never the manifest's expected environmentConfigDigest.
    # Expiry is enforced at live setup/observer/action admission; it is not a configuration field.
    return configured
