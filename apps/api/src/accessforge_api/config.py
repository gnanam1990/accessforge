"""Control-plane configuration. Fails closed on invalid or missing values."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_TOKENS = ("changeme", "placeholder", "replace_me", "replaceme", "your_", "xxxx")


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

    environment: Literal["local", "test", "staging", "production"] = "local"
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

    @field_validator("environment")
    @classmethod
    def _production_defaults(cls, value: str) -> str:
        return value

    def redacted(self) -> dict[str, str | int]:
        """Diagnostics view. Credentials are never included, in any environment."""
        scheme, _, rest = self.database_url.partition("://")
        _, _, host_part = rest.rpartition("@")
        return {
            "environment": self.environment,
            "host": self.host,
            "port": self.port,
            "database": f"{scheme}://<redacted>@{host_part}",
            "evidence_endpoint_url": self.evidence_endpoint_url,
            "evidence_bucket": self.evidence_bucket,
            "evidence_access_key": "<redacted>",
            "evidence_secret_key": "<redacted>",
        }
