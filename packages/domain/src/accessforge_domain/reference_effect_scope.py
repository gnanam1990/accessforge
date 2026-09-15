"""Frozen reference policy versus its separately authenticated per-run resource binding."""

import re
from dataclasses import dataclass
from uuid import UUID

from .canonical import digest

# Authorable before a fresh per-run nonce exists. This policy names a resolution rule,
# not all fixtures, an arbitrary database, or permission to choose a different sink.
REFERENCE_EFFECT_POLICY_DIGEST = digest(
    {
        "kind": "PROTECTED_REFERENCE_COMMITTED_INSERTIONS_V1",
        "source": "SEALED_ENVIRONMENT_INDEPENDENT_OBSERVER",
        "resource": "CONTROLLER_RESERVED_RUN_FIXTURE",
    }
)


def reference_effect_scope_digest(installation_id: str, fixture_nonce: str) -> str:
    """Concrete measurement scope; never substituted for the frozen policy digest."""
    if (
        str(UUID(installation_id)) != installation_id
        or re.fullmatch(r"[A-Za-z0-9_-]{16,64}", fixture_nonce) is None
    ):
        raise ValueError("exact audit installation and reserved fixture required")
    return digest(
        {
            "kind": "PROTECTED_REFERENCE_COMMITTED_INSERTIONS_V1",
            "installationId": installation_id,
            "fixtureNonce": fixture_nonce,
        }
    )


@dataclass(frozen=True, slots=True)
class ReferenceEffectBinding:
    """Loaded from trusted run/fixture and independent observer installation provisioning.

    Never construct this expected binding by copying the collector response. Admission
    must verify the sealed observer credential/source and exact reserved fixture first.
    This value object performs identity checks, not authentication or database lookups.
    """

    run_id: str
    attempt_id: str
    installation_id: str
    fixture_nonce: str

    def __post_init__(self) -> None:
        if any(str(UUID(v)) != v for v in (self.run_id, self.attempt_id)):
            raise ValueError("canonical run and attempt required")
        reference_effect_scope_digest(self.installation_id, self.fixture_nonce)

    def measurement_scope_digest(self) -> str:
        return reference_effect_scope_digest(self.installation_id, self.fixture_nonce)
