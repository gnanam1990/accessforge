"""Control-plane configuration. Fails closed on invalid or missing values."""

from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_TOKENS = ("changeme", "placeholder", "replace_me", "replaceme", "your_", "xxxx")


def _is_loopback(database_url: str) -> bool:
    host = urlsplit(database_url).hostname
    if not host:
        return True  # local unix socket
    if host.lower() in {"localhost"}:
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _is_placeholder(value: str) -> bool:
    """Reject values that are obviously copied from an example file.

    Substring matching, not equality: the shipped examples use REPLACE_ME_WITH_A_RANDOM_VALUE,
    which an equality check would happily accept.
    """
    lowered = value.strip().lower()
    if lowered in {"", "secret", "token", "password"}:
        return True
    return any(marker in lowered for marker in _PLACEHOLDER_TOKENS)


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACCESSFORGE_", env_file=".env", extra="forbid")

    database_url: str = Field(min_length=1)

    # Private S3-compatible evidence storage. Configured here; the evidence object model itself is
    # module 10's, and nothing in this module writes objects.
    evidence_endpoint_url: str = Field(min_length=1)
    evidence_bucket: str = Field(min_length=1)
    evidence_access_key: str = Field(min_length=1)
    evidence_secret_key: str = Field(min_length=1)

    # The key id an export will be signed with. The key itself is never here: a verifier needs only
    # the public half, and the private half belongs in a key management service. Recording the id on
    # the export row is what tells a reader which key to obtain from the issuer.
    signing_key_id: str = Field(default="af-unsigned-local", min_length=1)

    environment: Literal["local", "test", "staging", "production"] = "local"

    # Who establishes that a person is who they say they are. "none" is the default and means this
    # deployment cannot sign anyone in: POST /v1/sessions refuses with a missing dependency rather
    # than inventing a credential store. "local-development" accepts an email with no secret, which
    # is an authentication bypass by construction -- see the validator below and the docstring of
    # `routes/session.py`.
    identity_provider: Literal["none", "local-development"] = "none"
    # Write-rate limits, one bucket per principal and one per workspace, both enforced. Environment
    # configuration rather than a request field or a per-workspace row: the value has to be trusted,
    # and the two things a caller controls are exactly the two that must not set it.
    #
    # The defaults are generous enough that a person driving the UI never meets them and tight
    # enough that a loop does within a second or two. A deployment under real load tunes them;
    # a deployment that wants them off has to say so by raising them, because there is no way to
    # express "unlimited" here.
    rate_limit_principal_per_minute: int = Field(default=120, ge=1, le=100_000)
    rate_limit_workspace_per_minute: int = Field(default=600, ge=1, le=1_000_000)

    # The burst a caller may spend at once, as a multiple of one minute's allowance. A burst of
    # exactly one minute's worth is what a token bucket gives by default and is what makes a page
    # that fires six requests on load work at a limit of 120/minute.
    rate_limit_burst_multiplier: float = Field(default=1.0, ge=1.0, le=10.0)

    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1024, le=65535)

    @field_validator("database_url")
    @classmethod
    def _require_postgres(cls, value: str) -> str:
        if not value.startswith(("postgresql://", "postgres://")):
            raise ValueError("database_url must be a PostgreSQL URL")
        return value

    @field_validator("evidence_secret_key", "evidence_access_key")
    @classmethod
    def _reject_placeholders(cls, value: str) -> str:
        if _is_placeholder(value):
            raise ValueError("refusing a placeholder credential value")
        return value

    @model_validator(mode="after")
    def _enforce_environment_appropriate_defaults(self) -> ApiSettings:
        """Rules that depend on which environment this is.

        This replaces an earlier validator that was named for this purpose but only returned its
        input unchanged — a function that looked like enforcement while enforcing nothing. In a
        product whose thesis is that claims must not outrun evidence, dead code shaped like a
        guard is worse than no guard at all.
        """
        if self.environment in ("staging", "production"):
            if urlsplit(self.evidence_endpoint_url).scheme != "https":
                raise ValueError(
                    f"environment {self.environment!r} requires an https evidence endpoint; "
                    "object-store credentials must not cross the network in plaintext"
                )

        if self.identity_provider == "local-development" and self.environment != "local":
            raise ValueError(
                "identity_provider 'local-development' accepts an email with no secret and is "
                f"refused in environment {self.environment!r}. Setting the variable is not enough: "
                "a deployment that is not local cannot start with an authentication bypass enabled."
            )

        if self.environment == "production" and _is_loopback(self.database_url):
            raise ValueError(
                "refusing a loopback database in production; a production deployment pointing at "
                "its own localhost is a misconfiguration, not a deliberate choice"
            )

        return self

    def redacted(self) -> dict[str, str | int]:
        """Diagnostics view. Credentials are never included, in any environment."""
        scheme, _, rest = self.database_url.partition("://")
        _, _, host_part = rest.rpartition("@")
        return {
            "environment": self.environment,
            "identity_provider": self.identity_provider,
            "host": self.host,
            "port": self.port,
            "database": f"{scheme}://<redacted>@{host_part}",
            "evidence_endpoint_url": self.evidence_endpoint_url,
            "evidence_bucket": self.evidence_bucket,
            "signing_key_id": self.signing_key_id,
            "evidence_access_key": "<redacted>",
            "evidence_secret_key": "<redacted>",
        }
