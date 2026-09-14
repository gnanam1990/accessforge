"""Bounded GitHub webhook authentication, not installation authority or dispatch.

An integration ingress must authenticate the original bytes before decoding. Headers are not
covered by GitHub's body HMAC: neither delivery ID nor event name can authorize work or provide
the sole replay key. The caller must persist body-digest deduplication, resolve current scoped
installation/repository authority and validate the specific event before any side effect.
No HTTP route, secret discovery, logging, publication or model invocation lives here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

MAX_WEBHOOK_BYTES = 1_048_576


class Refused(ValueError):
    """Static ingress refusal; do not expose parser exceptions or private payloads."""


@dataclass(frozen=True, slots=True)
class AuthenticatedWebhook:
    """Only authenticated body identity; deliberately excludes raw repository/page text.

    GitHub numeric identities are claims signed by the configured secret, not proof that an
    installation remains active or is authorized for any AccessForge workspace.
    """

    body_digest: str
    installation_id: int
    repository_id: int | None
    delivery_id: str  # Unauthenticated transport metadata, never an authority/replay key alone.


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Refused("webhook JSON keys are ambiguous")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise Refused("webhook JSON constants are unsupported")


def _identifier(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise Refused("webhook numeric identity unavailable")
    return value


def authenticate(
    raw_body: bytes,
    *,
    secret: bytes,
    signature: str,
    delivery_id: str,
) -> AuthenticatedWebhook:
    """Authenticate one installation-scoped body without treating it as permission to act.

    The 1 MiB receiver bound is an AccessForge policy, not a claim about GitHub's maximum.
    Signature failure is checked before any JSON parsing. This is intentionally not a general
    event processor: ping and events without an installation identity are refused.
    """
    if (
        type(raw_body) is not bytes
        or not 0 < len(raw_body) <= MAX_WEBHOOK_BYTES
        or type(secret) is not bytes
        or not 32 <= len(secret) <= 4096
        or not isinstance(signature, str)
        or len(signature) != 71
        or not signature.startswith("sha256=")
        or any(c not in "0123456789abcdef" for c in signature[7:])
    ):
        raise Refused("webhook authentication unavailable")
    expected = "sha256=" + hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise Refused("webhook signature differs")
    try:
        if not isinstance(delivery_id, str) or str(UUID(delivery_id)) != delivery_id:
            raise ValueError("delivery")
        payload = json.loads(
            raw_body.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant
        )
    except (ValueError, UnicodeError, RecursionError):
        raise Refused("webhook encoding or delivery metadata unavailable") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("installation"), dict):
        raise Refused("webhook installation identity unavailable")
    installation = _identifier(payload["installation"].get("id"))
    repository = None
    if "repository" in payload:
        if not isinstance(payload["repository"], dict):
            raise Refused("webhook repository identity unavailable")
        repository = _identifier(payload["repository"].get("id"))
    return AuthenticatedWebhook(
        hashlib.sha256(raw_body).hexdigest(), installation, repository, delivery_id
    )
