"""Real isolated PostgreSQL trigger/ACL proof; no desktop, model or live app mutation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from accessforge_persistence.evidence import effect_audit
from accessforge_persistence.evidence.effect_audit import (
    AuditUnavailable,
    install_reference_effect_audit,
    read_creation_history,
)
from reference_app.db import SCHEMA

pytestmark = pytest.mark.integration
NONCE = "effect-audit-synthetic-fixture"


@contextmanager
def connection(url: str, role: str | None = None) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(url, row_factory=dict_row, autocommit=True) as conn:
        if role is not None:
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        conn.autocommit = False
        yield conn


@pytest.fixture
def audit_db(backup_database_url: str) -> Iterator[tuple[str, str, str, str]]:
    suffix = uuid4().hex
    database = f"af_effect_audit_{suffix}"
    app, observer = f"af_effect_app_{suffix}", f"af_effect_observer_{suffix}"
    created_roles: list[str] = []
    created_database = False
    url = make_conninfo(backup_database_url, dbname=database)
    # Only generated disposable targets are ever dropped. No shared fixture DB or live schema.
    with psycopg.connect(backup_database_url, autocommit=True) as admin:
        try:
            for role in (app, observer):
                admin.execute(
                    sql.SQL(
                        "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                    ).format(sql.Identifier(role))
                )
                created_roles.append(role)
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            created_database = True
            with connection(url) as conn:
                conn.execute(SCHEMA)
                installation = install_reference_effect_audit(
                    conn, application_role=app, observer_role=observer
                )
            yield url, app, observer, installation
        finally:
            if created_database:
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
            for role in reversed(created_roles):
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def history(audit_db: tuple[str, str, str, str]) -> int:
    url, app, observer, installation = audit_db
    with connection(url, observer) as conn:
        return read_creation_history(
            conn, application_role=app, installation_id=installation, fixture_nonce=NONCE
        ).committed_insertions


def insert(conn: psycopg.Connection[Any]) -> None:
    conn.execute(
        "INSERT INTO public.fixture_instance(nonce,template_digest,variant) "
        "VALUES(%s,%s,'accessible') ON CONFLICT DO NOTHING",
        (NONCE, "a" * 64),
    )
    conn.execute(
        "INSERT INTO public.service_request "
        "(id,fixture_nonce,full_name,email,category,description) "
        "VALUES(%s,%s,'Synthetic Person','synthetic@example.test','access','test')",
        (str(uuid4()), NONCE),
    )


def test_committed_history_survives_deletion_but_not_transaction_rollback(
    audit_db: tuple[str, str, str, str],
) -> None:
    url, app, _, _ = audit_db
    assert history(audit_db) == 0  # A history count, never a continuous-absence assertion.
    with connection(url, app) as conn:
        insert(conn)
        conn.execute("DELETE FROM public.fixture_instance WHERE nonce=%s", (NONCE,))
    assert history(audit_db) == 1
    with connection(url, app) as conn:
        count = conn.execute("SELECT count(*) AS n FROM public.service_request").fetchone()
        assert count is not None and count["n"] == 0
        insert(conn)
        conn.rollback()
    assert history(audit_db) == 1


def test_application_cannot_read_rewrite_or_disable_independent_history(
    audit_db: tuple[str, str, str, str],
) -> None:
    url, app, observer, _ = audit_db
    for statement in (
        "SELECT * FROM accessforge_effect_audit.creation",
        "DELETE FROM accessforge_effect_audit.creation",
        "TRUNCATE public.service_request",
        "UPDATE public.service_request SET fixture_nonce='different'",
        "ALTER TABLE public.service_request DISABLE TRIGGER accessforge_record_creation",
        "DROP TABLE public.service_request",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection(url, app) as conn:
            conn.execute(statement)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), connection(url, observer) as conn:
        conn.execute("DELETE FROM accessforge_effect_audit.creation")
    with connection(url, app) as conn:
        insert(conn)
    assert history(audit_db) == 1


@pytest.mark.parametrize(
    "damage", ["trigger", "grant", "identity", "observer-grant", "rls", "column-grant"]
)
def test_unavailable_or_weakened_boundary_is_not_an_empty_history(
    audit_db: tuple[str, str, str, str], damage: str
) -> None:
    url, app, observer, installation = audit_db
    with connection(url) as conn:
        if damage == "trigger":
            conn.execute(
                "ALTER TABLE public.service_request DISABLE TRIGGER accessforge_record_creation"
            )
        elif damage == "grant":
            conn.execute(
                sql.SQL("GRANT UPDATE ON public.service_request TO {}").format(sql.Identifier(app))
            )
        elif damage == "observer-grant":
            conn.execute(
                sql.SQL("GRANT DELETE ON accessforge_effect_audit.creation TO {}").format(
                    sql.Identifier(observer)
                )
            )
        elif damage == "rls":
            conn.execute("ALTER TABLE accessforge_effect_audit.creation ENABLE ROW LEVEL SECURITY")
        elif damage == "column-grant":
            conn.execute(
                sql.SQL("GRANT UPDATE(fixture_nonce) ON public.service_request TO {}").format(
                    sql.Identifier(app)
                )
            )
    with pytest.raises(AuditUnavailable), connection(url, observer) as conn:
        read_creation_history(
            conn,
            application_role=app,
            installation_id=str(uuid4()) if damage == "identity" else installation,
            fixture_nonce=NONCE,
        )


def test_installation_is_not_replaced_or_replayed(audit_db: tuple[str, str, str, str]) -> None:
    url, app, observer, _ = audit_db
    with pytest.raises(psycopg.errors.DuplicateSchema), connection(url) as conn:
        install_reference_effect_audit(conn, application_role=app, observer_role=observer)
    assert history(audit_db) == 0


def test_installation_refuses_nonempty_requests_without_reset(
    audit_db: tuple[str, str, str, str],
) -> None:
    url, app, observer, _ = audit_db
    with connection(url, app) as conn:
        insert(conn)
    with pytest.raises(AuditUnavailable, match="empty request table"), connection(url) as conn:
        install_reference_effect_audit(conn, application_role=app, observer_role=observer)
    assert history(audit_db) == 1


def test_caught_install_failure_cannot_commit_partial_installation(
    audit_db: tuple[str, str, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, app, observer, _ = audit_db
    # Remove only this disposable fixture's initial installation to exercise fresh provisioning.
    with connection(url) as conn:
        conn.execute("DROP SCHEMA accessforge_effect_audit CASCADE")

    def reject(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AuditUnavailable("injected final validation failure")

    monkeypatch.setattr(effect_audit, "_check", reject)
    with connection(url) as conn:
        with pytest.raises(AuditUnavailable, match="injected"):
            install_reference_effect_audit(conn, application_role=app, observer_role=observer)
        # Intentionally commit the surrounding transaction after catching the failure.
        conn.commit()
        row = conn.execute(
            "SELECT to_regnamespace('accessforge_effect_audit') AS schema"
        ).fetchone()
        assert row is not None and row["schema"] is None


def test_administrator_is_not_an_independent_observer(audit_db: tuple[str, str, str, str]) -> None:
    url, app, _, installation = audit_db
    with connection(url) as conn:
        with pytest.raises(AuditUnavailable, match="independent observer role"):
            read_creation_history(
                conn, application_role=app, installation_id=installation, fixture_nonce=NONCE
            )
    assert history(audit_db) == 0


def test_missing_history_is_unavailable_not_zero(audit_db: tuple[str, str, str, str]) -> None:
    url, _, _, _ = audit_db
    with connection(url) as conn:
        conn.execute("DROP TABLE accessforge_effect_audit.creation")
    with pytest.raises(AuditUnavailable) as failure:
        history(audit_db)
    assert isinstance(failure.value.__cause__, psycopg.Error)
