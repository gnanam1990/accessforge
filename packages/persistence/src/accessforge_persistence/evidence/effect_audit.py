"""Protected reference-app creation history; not continuous-absence or canonical evidence.

Explicit administrative provisioning only, never called by application startup or a navigator.
The application role cannot own/disable the trigger, rewrite the history, or move a request to
another fixture. Committed insertions survive subsequent request/fixture deletion. Rollbacks
leave no committed effect. Installation and reads do not mint EffectCoverage or a verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus

from accessforge_domain.timestamps import to_rfc3339_utc


class AuditUnavailable(Exception):
    """History cannot be trusted or provisioned; never equivalent to zero effects."""


# Database-local barrier. The trigger holds a shared transaction lock through COMMIT;
# an independent collector holds the exclusive session lock only at its two boundaries.
CREATION_BARRIER_KEY = 4703804837898241

_BODY = """
BEGIN
  PERFORM pg_advisory_xact_lock_shared(4703804837898241::bigint);
  INSERT INTO accessforge_effect_audit.creation(request_id, fixture_nonce)
    VALUES (NEW.id, NEW.fixture_nonce);
  RETURN NEW;
END
"""


def _role(conn: psycopg.Connection[Any], name: str, owner: str) -> int:
    row = conn.execute(
        "SELECT oid FROM pg_roles WHERE rolname=%s AND NOT rolsuper AND NOT rolcreaterole "
        "AND NOT rolcreatedb AND NOT rolreplication AND NOT rolbypassrls",
        (name,),
    ).fetchone()
    if row is None:
        raise AuditUnavailable("dedicated unprivileged role required")
    dangerous = conn.execute(
        "SELECT 1 FROM pg_roles WHERE pg_has_role(%s,oid,'MEMBER') AND "
        "(rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls "
        "OR rolname IN (%s,'pg_read_server_files','pg_write_server_files',"
        "'pg_execute_server_program') "
        "OR oid=(SELECT datdba FROM pg_database WHERE datname=current_database()) "
        "OR oid=(SELECT nspowner FROM pg_namespace WHERE nspname='public')) LIMIT 1",
        (name, owner),
    ).fetchone()
    if dangerous is not None:
        raise AuditUnavailable("application/observer role has administrative authority")
    return int(row["oid"])


def install_reference_effect_audit(
    conn: psycopg.Connection[Any], *, application_role: str, observer_role: str
) -> str:
    """Install once in an empty reference database, within the caller's admin transaction.

    Caller must commit successfully before using the returned installation ID. Never retry an
    uncertain commit: inspect the original installation. Existing schemas are not overwritten;
    existing requests are refused, not erased. No roles or login credentials are created here.
    """
    if conn.autocommit or application_role == observer_role:
        raise AuditUnavailable("separate roles and an explicit admin transaction required")
    # Ensure there is an outer caller-owned transaction, then contain installation in a
    # savepoint. Even a caller that catches AuditUnavailable cannot commit a partial install.
    conn.execute("SELECT 1")
    with conn.transaction():
        return _install(conn, application_role=application_role, observer_role=observer_role)


def _install(conn: psycopg.Connection[Any], *, application_role: str, observer_role: str) -> str:
    identity = conn.execute("SELECT current_user AS name").fetchone()
    assert identity is not None
    owner = str(identity["name"])
    app_id = _role(conn, application_role, owner)
    observer_id = _role(conn, observer_role, owner)
    linked = conn.execute(
        "SELECT pg_has_role(%s,%s,'MEMBER') OR pg_has_role(%s,%s,'MEMBER') AS linked",
        (application_role, observer_role, observer_role, application_role),
    ).fetchone()
    if linked is None or linked["linked"]:
        raise AuditUnavailable("application and observer roles must be independent")
    conn.execute("SET LOCAL lock_timeout='2s'")
    conn.execute(
        "LOCK TABLE public.service_request, public.fixture_instance IN ACCESS EXCLUSIVE MODE"
    )
    if conn.execute("SELECT 1 FROM public.service_request LIMIT 1").fetchone() is not None:
        raise AuditUnavailable("install requires an empty request table; no implicit reset")
    for table in ("fixture_instance", "service_request"):
        conn.execute(
            sql.SQL("ALTER TABLE public.{} OWNER TO {}").format(
                sql.Identifier(table), sql.Identifier(owner)
            )
        )
    conn.execute("CREATE SCHEMA accessforge_effect_audit")
    conn.execute("REVOKE ALL ON SCHEMA accessforge_effect_audit FROM PUBLIC")
    conn.execute("""
        CREATE TABLE accessforge_effect_audit.installation (
          singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
          id uuid NOT NULL, installed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          application_role oid NOT NULL, observer_role oid NOT NULL,
          request_table oid NOT NULL, history_table oid NOT NULL, owner_name text NOT NULL
        );
        CREATE TABLE accessforge_effect_audit.creation (
          sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          request_id uuid NOT NULL, fixture_nonce text NOT NULL,
          recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX creation_fixture ON accessforge_effect_audit.creation(fixture_nonce);
        REVOKE ALL ON ALL TABLES IN SCHEMA accessforge_effect_audit FROM PUBLIC;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA accessforge_effect_audit FROM PUBLIC;
    """)
    conn.execute(
        sql.SQL(
            "CREATE FUNCTION accessforge_effect_audit.record_creation() RETURNS trigger "
            "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS {}"
        ).format(sql.Literal(_BODY))
    )
    conn.execute("REVOKE ALL ON FUNCTION accessforge_effect_audit.record_creation() FROM PUBLIC")
    conn.execute("""
        CREATE TRIGGER accessforge_record_creation AFTER INSERT ON public.service_request
        FOR EACH ROW EXECUTE FUNCTION accessforge_effect_audit.record_creation();
        ALTER TABLE public.service_request ENABLE ALWAYS TRIGGER accessforge_record_creation;
    """)
    for role in (application_role, observer_role):
        conn.execute(
            sql.SQL("REVOKE ALL ON public.fixture_instance, public.service_request FROM {}").format(
                sql.Identifier(role)
            )
        )
    conn.execute("REVOKE ALL ON public.fixture_instance, public.service_request FROM PUBLIC")
    conn.execute(
        sql.SQL(
            "GRANT SELECT, INSERT, DELETE ON public.fixture_instance, public.service_request TO {}"
        ).format(sql.Identifier(application_role))
    )
    conn.execute(
        sql.SQL("GRANT USAGE ON SCHEMA accessforge_effect_audit TO {}").format(
            sql.Identifier(observer_role)
        )
    )
    conn.execute(
        sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA accessforge_effect_audit TO {}").format(
            sql.Identifier(observer_role)
        )
    )
    installation_id = str(uuid4())
    conn.execute(
        "INSERT INTO accessforge_effect_audit.installation(id,application_role,observer_role,"
        "request_table,history_table,owner_name) VALUES(%s,%s,%s,"
        "'public.service_request'::regclass::oid,"
        "'accessforge_effect_audit.creation'::regclass::oid,%s)",
        (installation_id, app_id, observer_id, owner),
    )
    _check(conn, application_role=application_role, installation_id=installation_id)
    return installation_id


def _check(
    conn: psycopg.Connection[Any], *, application_role: str, installation_id: str
) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM accessforge_effect_audit.installation").fetchone()
    if row is None or str(row["id"]) != installation_id:
        raise AuditUnavailable("original audit installation unavailable")
    if _role(conn, application_role, row["owner_name"]) != row["application_role"]:
        raise AuditUnavailable("application identity changed")
    observer = conn.execute(
        "SELECT rolname FROM pg_roles WHERE oid=%s", (row["observer_role"],)
    ).fetchone()
    if observer is None:
        raise AuditUnavailable("observer role disappeared")
    observer_name = str(observer["rolname"])
    _role(conn, observer_name, row["owner_name"])
    schema_access = conn.execute(
        "SELECT has_schema_privilege(%s,'accessforge_effect_audit','USAGE,CREATE') "
        "OR has_schema_privilege(%s,'accessforge_effect_audit','CREATE') AS unsafe",
        (application_role, observer_name),
    ).fetchone()
    if schema_access is None or schema_access["unsafe"]:
        raise AuditUnavailable("audit schema access exceeds the independent read boundary")
    history_table = conn.execute(
        "SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner "
        "WHERE c.oid=%s AND c.oid='accessforge_effect_audit.creation'::regclass "
        "AND c.relpersistence='p' AND NOT c.relrowsecurity AND r.rolname=%s",
        (row["history_table"], row["owner_name"]),
    ).fetchone()
    if history_table is None:
        raise AuditUnavailable("history table was replaced, filtered or made non-durable")
    guarded = conn.execute(
        "SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid "
        "JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_roles r ON r.oid=c.relowner "
        "WHERE c.oid=%s AND c.oid='public.service_request'::regclass "
        "AND r.rolname=%s AND c.relpersistence='p' AND NOT c.relrowsecurity "
        "AND t.tgname='accessforge_record_creation' AND t.tgenabled='A' "
        "AND t.tgtype=5 AND t.tgqual IS NULL AND t.tgnargs=0 "
        "AND p.oid='accessforge_effect_audit.record_creation()'::regprocedure "
        "AND p.proowner=c.relowner AND p.prosecdef AND p.prosrc=%s "
        "AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[]",
        (row["request_table"], row["owner_name"], _BODY),
    ).fetchone()
    if guarded is None:
        raise AuditUnavailable("creation trigger or source identity changed")
    for table in (
        "public.service_request",
        "accessforge_effect_audit.creation",
        "accessforge_effect_audit.installation",
    ):
        privileges = (
            "UPDATE,TRUNCATE,TRIGGER"
            if table == "public.service_request"
            else "SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER"
        )
        allowed = conn.execute(
            "SELECT has_table_privilege(%s,%s,%s) AS allowed", (application_role, table, privileges)
        ).fetchone()
        if allowed is None or allowed["allowed"]:
            raise AuditUnavailable("application can bypass or rewrite the audit boundary")
        observer_write = conn.execute(
            "SELECT has_table_privilege(%s,%s,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') AS allowed",
            (observer_name, table),
        ).fetchone()
        if observer_write is None or observer_write["allowed"]:
            raise AuditUnavailable("observer has source or audit write authority")
        column_access = conn.execute(
            "SELECT has_any_column_privilege(%s,%s,%s) "
            "OR has_any_column_privilege(%s,%s,'INSERT,UPDATE') AS unsafe",
            (
                application_role,
                table,
                "UPDATE" if table == "public.service_request" else "SELECT,INSERT,UPDATE",
                observer_name,
                table,
            ),
        ).fetchone()
        if column_access is None or column_access["unsafe"]:
            raise AuditUnavailable("column-level grant bypasses the audit boundary")
    for role in (application_role, observer_name):
        indirect = conn.execute(
            "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog','information_schema') "
            "AND has_schema_privilege(%s,n.oid,'USAGE') "
            "AND has_function_privilege(%s,p.oid,'EXECUTE') LIMIT 1",
            (role, role),
        ).fetchone()
        if indirect is not None:
            raise AuditUnavailable("unreviewed security-definer capability available to an actor")
    return dict(row)


@dataclass(frozen=True, slots=True)
class CreationHistory:
    installation_id: str
    fixture_nonce: str
    committed_insertions: int
    installed_at: str


def read_creation_history(
    conn: psycopg.Connection[Any],
    *,
    application_role: str,
    installation_id: str,
    fixture_nonce: str,
) -> CreationHistory:
    """Use a new observer read-only transaction. Zero is NOT continuous-absence evidence.

    Database/source failures raise rather than returning zero. A deployment's independent
    observer must still bind installation, run/attempt, complete execution window and retained
    source bytes before using history in a canonical assertion. No such assertion is emitted here.
    """
    if conn.autocommit or conn.info.transaction_status != TransactionStatus.IDLE:
        raise AuditUnavailable("history requires a fresh explicit observer transaction")
    try:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.execute("SET LOCAL statement_timeout='5s'")
        row = _check(conn, application_role=application_role, installation_id=installation_id)
        identity = conn.execute("SELECT oid FROM pg_roles WHERE rolname=current_user").fetchone()
        if identity is None or identity["oid"] != row["observer_role"]:
            raise AuditUnavailable("history requires the installed independent observer role")
        count = conn.execute(
            "SELECT count(*) AS n FROM accessforge_effect_audit.creation WHERE fixture_nonce=%s",
            (fixture_nonce,),
        ).fetchone()
    except psycopg.Error as error:
        raise AuditUnavailable("protected creation history unavailable") from error
    assert count is not None
    return CreationHistory(
        installation_id, fixture_nonce, int(count["n"]), to_rfc3339_utc(row["installed_at"])
    )
