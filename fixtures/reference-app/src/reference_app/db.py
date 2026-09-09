"""Durable storage for the reference application.

PostgreSQL is authoritative. A submission that is not committed here did not happen, regardless of
what the browser was shown — that distinction is the whole point of the receipt interface.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

SCHEMA = """
CREATE TABLE IF NOT EXISTS fixture_instance (
    nonce           TEXT PRIMARY KEY,
    template_digest TEXT        NOT NULL,
    variant         TEXT        NOT NULL CHECK (variant IN ('accessible', 'inaccessible')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS service_request (
    id            UUID PRIMARY KEY,
    fixture_nonce TEXT        NOT NULL REFERENCES fixture_instance (nonce) ON DELETE CASCADE,
    full_name     TEXT        NOT NULL,
    email         TEXT        NOT NULL,
    category      TEXT        NOT NULL,
    description   TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Exactly one request per fixture instance. The journey's success condition is "created exactly
-- one test request"; enforcing it in the database means a double submission is a visible error
-- rather than a silently duplicated receipt.
CREATE UNIQUE INDEX IF NOT EXISTS service_request_one_per_fixture
    ON service_request (fixture_nonce);
"""


def connect(database_url: str) -> psycopg.Connection[dict[str, Any]]:
    return psycopg.connect(database_url, row_factory=dict_row, autocommit=False)


@contextmanager
def transaction(database_url: str) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    conn = connect(database_url)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def initialize(database_url: str) -> None:
    with transaction(database_url) as conn:
        conn.execute(SCHEMA)


def ping(database_url: str) -> None:
    """Raise if the database is not actually reachable. Used by readiness, which must fail
    visibly rather than report a process that happens to be listening."""
    with transaction(database_url) as conn:
        conn.execute("SELECT 1")
