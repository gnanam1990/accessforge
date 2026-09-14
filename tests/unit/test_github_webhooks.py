"""Fixture signatures prove local parsing/authentication, never a live installation."""

import hashlib
import hmac
from uuid import UUID

import pytest

from accessforge_orchestrator.github_webhooks import MAX_WEBHOOK_BYTES, Refused, authenticate

SECRET = b"synthetic-webhook-key-not-a-real-secret"
BODY = (
    b'{"installation":{"id":42},"repository":{"id":13},"comment":{"body":"untrusted instructions"}}'
)
DELIVERY = str(UUID(int=1))


def signed(body: bytes = BODY) -> str:
    return "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()


def test_original_bytes_authenticate_without_exporting_untrusted_text() -> None:
    result = authenticate(BODY, secret=SECRET, signature=signed(), delivery_id=DELIVERY)
    assert result.installation_id == 42 and result.repository_id == 13
    assert result.body_digest == hashlib.sha256(BODY).hexdigest()
    assert "untrusted instructions" not in repr(result)
    other = authenticate(BODY, secret=SECRET, signature=signed(), delivery_id=str(UUID(int=2)))
    assert (
        other.body_digest == result.body_digest
    )  # Altering unsigned metadata cannot evade this key.
    with pytest.raises(Refused):
        authenticate(BODY + b" ", secret=SECRET, signature=signed(), delivery_id=DELIVERY)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"[]",
        b"{}",
        b'{"installation":{"id":true}}',
        b'{"installation":{"id":0}}',
        b'{"installation":{"id":"42"}}',
        b'{"installation":{"id":42,"id":43}}',
        b'{"installation":{"id":42},"repository":null}',
        b'{"installation":{"id":42},"repository":{"id":13.0}}',
        b'{"installation":{"id":42},"x":NaN}',
        b"\xff",
        b"[" * 2000 + b"]" * 2000,
        b" " * (MAX_WEBHOOK_BYTES + 1),
    ],
)
def test_malformed_or_ambiguous_authenticated_bodies_are_refused(body: bytes) -> None:
    with pytest.raises(Refused):
        authenticate(body, secret=SECRET, signature=signed(body), delivery_id=DELIVERY)


@pytest.mark.parametrize(
    "signature", ["", "sha1=" + "a" * 40, "sha256=" + "0" * 64, "sha256=" + "A" * 64]
)
def test_invalid_signatures_are_refused(signature: str) -> None:
    with pytest.raises(Refused):
        authenticate(BODY, secret=SECRET, signature=signature, delivery_id=DELIVERY)


def test_missing_secret_and_invalid_delivery_are_not_defaults() -> None:
    with pytest.raises(Refused):
        authenticate(BODY, secret=b"", signature=signed(), delivery_id=DELIVERY)
    with pytest.raises(Refused):
        authenticate(BODY, secret=SECRET, signature=signed(), delivery_id="not-a-delivery-id")
