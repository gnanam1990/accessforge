"""Liveness and readiness.

These are deliberately different questions, because conflating them is how a product ends up
reporting itself healthy while being unable to do anything:

  liveness   this process is running and can serve a response
  readiness  every dependency this service needs is actually reachable right now

A missing database or evidence store must make readiness fail visibly. It must never be smoothed
over into a 200, and it must never fall back to ephemeral storage.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import psycopg


@dataclass(frozen=True)
class DependencyResult:
    name: str
    ok: bool
    detail: str


def check_database(database_url: str) -> DependencyResult:
    try:
        with psycopg.connect(database_url, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error as exc:
        # Class name only; driver messages can embed the connection string.
        return DependencyResult("postgresql", False, type(exc).__name__)
    return DependencyResult("postgresql", True, "reachable")


def check_evidence_store(endpoint_url: str) -> DependencyResult:
    """Confirm the evidence endpoint accepts a TCP connection.

    This proves reachability, not bucket existence or credential validity. Module 10 owns the
    real object-store contract; overstating what this check establishes would be exactly the kind
    of false green this product exists to prevent.
    """
    parsed = urlparse(endpoint_url)
    if not parsed.hostname:
        return DependencyResult("evidence-store", False, "unparseable endpoint url")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=3):
            pass
    except OSError as exc:
        return DependencyResult("evidence-store", False, type(exc).__name__)
    return DependencyResult("evidence-store", True, "tcp-reachable")
