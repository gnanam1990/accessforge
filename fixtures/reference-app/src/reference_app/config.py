"""Configuration for the reference application.

Fails closed: invalid or missing configuration raises at import/startup rather than allowing the
application to run in an undefined state. Secrets are never echoed back in diagnostics.
"""

from __future__ import annotations

import ipaddress
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The reference application must only ever accept traffic from the local machine. It exists to be
# driven by a screen reader on this desktop; binding it anywhere else would turn a test fixture
# into an exposed service.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

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


class ReferenceAppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REFAPP_", env_file=".env.refapp", extra="forbid")

    database_url: str = Field(min_length=1)
    host: str = "127.0.0.1"
    port: int = Field(default=8081, ge=1024, le=65535)

    # Test-only identities. These gate the receipt and reset interfaces, which the navigator must
    # never reach: it would turn the observer's answer key into navigator input.
    observer_token: str = Field(min_length=16)
    setup_token: str = Field(min_length=16)

    environment: Literal["local", "test"] = "local"

    @field_validator("host")
    @classmethod
    def _reject_non_loopback(cls, value: str) -> str:
        if value in _LOOPBACK_HOSTS:
            return value
        try:
            if ipaddress.ip_address(value).is_loopback:
                return value
        except ValueError:
            pass
        raise ValueError(
            f"refusing to bind the reference application to non-loopback host {value!r}; "
            "it is a local test fixture, not a deployable service"
        )

    @field_validator("database_url")
    @classmethod
    def _require_local_database(cls, value: str) -> str:
        if not value.startswith(("postgresql://", "postgres://")):
            raise ValueError("database_url must be a PostgreSQL URL")
        lowered = value.lower()
        if not any(h in lowered for h in ("@localhost", "@127.0.0.1", "@/", "@[::1]")):
            raise ValueError(
                "refusing a non-local database; the reference application must never write to a "
                "shared or production database"
            )
        return value

    @field_validator("observer_token", "setup_token")
    @classmethod
    def _reject_placeholder_tokens(cls, value: str) -> str:
        if _is_placeholder(value):
            raise ValueError("refusing a placeholder token value")
        return value

    def redacted(self) -> dict[str, str | int]:
        """Diagnostic view. Never includes token values or database credentials."""
        scheme, _, rest = self.database_url.partition("://")
        _, _, host_part = rest.rpartition("@")
        return {
            "environment": self.environment,
            "host": self.host,
            "port": self.port,
            "database": f"{scheme}://<redacted>@{host_part}",
            "observer_token": "<redacted>",
            "setup_token": "<redacted>",
        }
