"""Ephemeral local keys only; never registered App credentials or network evidence."""

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from accessforge_orchestrator.github_app_jwt import Refused, sign_app_jwt


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def pem(key: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def test_rs256_signature_exact_claims_and_no_repr_credential(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("accessforge_orchestrator.github_app_jwt.time.time", lambda: 2_000_000_000)
    result = sign_app_jwt(app_id=7, private_pem=pem(key))
    header, payload, signature = result.bearer.split(".")
    assert json.loads(base64.urlsafe_b64decode(header + "==")) == {"alg": "RS256", "typ": "JWT"}
    assert json.loads(base64.urlsafe_b64decode(payload + "==")) == {
        "iss": "7",
        "iat": 1_999_999_940,
        "exp": 2_000_000_300,
    }
    key.public_key().verify(
        base64.urlsafe_b64decode(signature + "=="),
        f"{header}.{payload}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    assert result.expires_at == 2_000_000_300
    assert result.bearer not in repr(result) and "PRIVATE KEY" not in repr(result)


@pytest.mark.parametrize("app_id", [0, -1, True, 2**63])
def test_invalid_app_identity_is_refused(app_id: int, key: rsa.RSAPrivateKey) -> None:
    with pytest.raises(Refused):
        sign_app_jwt(app_id=app_id, private_pem=pem(key))


@pytest.mark.parametrize("value", [b"", b"private-sentinel-invalid-key", b"x" * 16385])
def test_malformed_or_unbounded_key_has_static_error(value: bytes) -> None:
    with pytest.raises(Refused) as error:
        sign_app_jwt(app_id=7, private_pem=value)
    assert "sentinel" not in str(error.value) and error.value.__cause__ is None


def test_non_rsa_and_weak_rsa_are_refused() -> None:
    for key in (
        ec.generate_private_key(ec.SECP256R1()),
        rsa.generate_private_key(public_exponent=65537, key_size=1024),  # noqa: S505 - rejection case
    ):
        with pytest.raises(Refused):
            sign_app_jwt(app_id=7, private_pem=pem(key))


def test_encrypted_key_without_operator_decryption_is_refused(key: rsa.RSAPrivateKey) -> None:
    encrypted = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"synthetic-test-passphrase"),
    )
    with pytest.raises(Refused):
        sign_app_jwt(app_id=7, private_pem=encrypted)


@pytest.mark.parametrize("now", [0, float("nan"), float("inf"), 253402300000])
def test_invalid_host_clock_cannot_mint_claims(
    key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch, now: float
) -> None:
    monkeypatch.setattr("accessforge_orchestrator.github_app_jwt.time.time", lambda: now)
    with pytest.raises(Refused):
        sign_app_jwt(app_id=7, private_pem=pem(key))
