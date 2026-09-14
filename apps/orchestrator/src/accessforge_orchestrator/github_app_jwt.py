"""Offline GitHub App signing for an isolated trusted credential service.

No filesystem/environment discovery, HTTP, printing, persistence or automatic key generation.
The caller supplies an explicitly provisioned PEM and the configured numeric App ID. This
module must not run in a navigator, repository build process, or untrusted request evaluator.
"""

import base64
import json
import time
from dataclasses import dataclass, field

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class Refused(ValueError):
    """Static signing failure, without private bytes or underlying parser exceptions."""


@dataclass(frozen=True, slots=True)
class AppJWT:
    app_id: int
    expires_at: int
    bearer: str = field(repr=False)


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_app_jwt(*, app_id: int, private_pem: bytes) -> AppJWT:
    """Mint RS256 with a fixed five-minute future expiry and 60-second iat backdating.

    Numeric App ID is an officially supported issuer; the access probe separately verifies
    the installation's App ID. These bounded defaults are not caller-selected JWT claims.
    Local signing alone does not verify that this key is registered with that GitHub App.
    The returned bearer is sensitive despite its redacted repr. Do not serialize/log it.
    """
    if type(app_id) is not int or not 1 <= app_id <= 2**63 - 1:
        raise Refused("configured GitHub App identity malformed")
    if type(private_pem) is not bytes or not 1 <= len(private_pem) <= 16_384:
        raise Refused("configured GitHub App signing key unavailable")
    try:
        key = serialization.load_pem_private_key(private_pem, password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or not 2048 <= key.key_size <= 8192:
            raise ValueError
        now = int(time.time())
        if not 60 <= now <= 253402299000:
            raise ValueError
        header = _encode(b'{"alg":"RS256","typ":"JWT"}')
        payload = _encode(
            json.dumps(
                {"iss": str(app_id), "iat": now - 60, "exp": now + 300},
                separators=(",", ":"),
            ).encode("ascii")
        )
        message = f"{header}.{payload}"
        signature = key.sign(message.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    except (ValueError, TypeError, OverflowError, UnsupportedAlgorithm):
        raise Refused("configured GitHub App signing key or clock unavailable") from None
    return AppJWT(app_id, now + 300, f"{message}.{_encode(signature)}")
