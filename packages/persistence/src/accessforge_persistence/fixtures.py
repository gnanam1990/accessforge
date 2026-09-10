"""Fixture instances: fresh per run, with the oracle kept private.

Two rules shape this module.

The table is ``run_fixture_instance`` rather than ``fixture_instance`` because the reference
application under test owns the latter, and in local development both live in the same database.
That
collision would have been silent — ``CREATE TABLE IF NOT EXISTS`` does nothing when the name is
taken,
and the product would have been reading the application's rows.

**Every run gets a new instance.** A reused instance carries a prior run's receipt, and a journey
whose completion assertion could be satisfied by an earlier run's row is not testing anything. The
instance nonce is what makes "exactly one request for *this* run" a decidable question.

**The oracle stays on the trusted side.** ``navigator_values`` is what a person would type;
``observer_config`` is what the independent observer checks. They are returned by different
functions,
and the navigator-facing one has no parameter through which oracle material could be requested.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain.canonical import digest


class FixtureError(Exception):
    """A fixture instance could not be created or read."""


@dataclass(frozen=True, slots=True)
class FixtureInstance:
    """A fresh fixture instance for exactly one run."""

    instance_id: str
    nonce: str
    template_id: str
    template_digest: str
    """Digest of the template, recorded separately from the per-run instance identity so a template
    change is distinguishable from a new run of an unchanged template."""


def create_instance(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    run_id: str,
    template_id: str,
    template_digest: str,
    navigator_values: dict[str, str],
    observer_config: dict[str, str],
    now: datetime | None = None,
) -> FixtureInstance:
    """Create a fresh instance bound to one run.

    The unique constraint on ``run_id`` is what enforces freshness: a second instance for the same
    run
    is refused by the database rather than by a convention someone has to remember.
    """
    instance_id = str(uuid.uuid4())
    nonce = secrets.token_urlsafe(16)
    conn.execute(
        """
        INSERT INTO run_fixture_instance
            (id, workspace_id, run_id, template_id, template_digest, nonce,
             navigator_values, observer_config, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            instance_id,
            workspace_id,
            run_id,
            template_id,
            template_digest,
            nonce,
            Jsonb(navigator_values),
            Jsonb(observer_config),
            now or datetime.now(UTC),
        ),
    )
    return FixtureInstance(
        instance_id=instance_id,
        nonce=nonce,
        template_id=template_id,
        template_digest=template_digest,
    )


def navigator_values(
    conn: psycopg.Connection[dict[str, Any]], *, instance_id: str
) -> dict[str, str]:
    """Exactly what the navigator may see.

    Deliberately a separate function from ``observer_config`` with no flag to switch between them: a
    single function with a ``include_oracle=True`` parameter is one careless call away from handing
    the navigator the answer key.
    """
    row = conn.execute(
        "SELECT navigator_values FROM run_fixture_instance WHERE id = %s", (instance_id,)
    ).fetchone()
    if row is None:
        raise FixtureError("no such fixture instance in this workspace")
    return dict(row["navigator_values"])


def observer_config(
    conn: psycopg.Connection[dict[str, Any]], *, instance_id: str
) -> dict[str, str]:
    """What the independent observer checks. Trusted callers only."""
    row = conn.execute(
        "SELECT observer_config FROM run_fixture_instance WHERE id = %s", (instance_id,)
    ).fetchone()
    if row is None:
        raise FixtureError("no such fixture instance in this workspace")
    return dict(row["observer_config"])


def instance_digest(instance: FixtureInstance) -> str:
    """Digest binding the template to this specific instance.

    Includes the nonce, so two runs of the same template have different fixture identities — which
    is
    what stops a prior run's receipt satisfying this run's completion assertion.
    """
    return digest(
        {
            "templateId": instance.template_id,
            "templateDigest": instance.template_digest,
            "nonce": instance.nonce,
        }
    )
