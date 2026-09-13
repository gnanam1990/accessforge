"""Forward migration from the previous schema, and what an abrupt kill leaves behind.

Two questions module 27 has to answer with a real database rather than an argument.

**Can this release migrate the schema the previous release wrote?** Answered by building a database
at the previous migration, applying the tree, and checking that data written under the old rules
survives and that the new rules are in force. A migration test that started from an empty database
would prove that the SQL parses, which is not the question.

**What does an abrupt kill leave?** A worker killed mid-transaction leaves a claimed job and an
unpublished outbox row, because that is what a rollback leaves. Both are recoverable *by state*
rather than by remembering what the dead process was doing, and both survive a backup taken while
they are in that condition. The test kills the backend for real -- `pg_terminate_backend` -- rather
than closing a connection politely, because a polite close rolls back cleanly and an SPI-level kill
is what a machine losing power actually does.

Requirements: FR-015, FR-020, FR-022. Invariants: INV-09, INV-10, INV-11.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from accessforge_persistence import (
    MIGRATIONS_DIR,
    connect,
    expected_migrations,
    migrate,
    outbox,
    restore,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x2B0))

#: The migration this release adds on top of the previous one. Named rather than computed, so that
#: adding a migration without extending this test is a failure rather than a silent widening.
NEWEST = "0047_candidate_effect_delivery.sql"

#: Every unique constraint on `evidence_artifact` covering exactly (id, workspace_id). Read from
#: the catalog rather than by name: a migration adding a second one under a different name is
#: exactly the regression this is here to catch, and a name-based check would not see it.
_COMPOSITE_ARTIFACT_KEYS = (
    "SELECT conname FROM pg_constraint "
    " WHERE conrelid = 'evidence_artifact'::regclass AND contype = 'u' "
    "   AND pg_get_constraintdef(oid) = 'UNIQUE (id, workspace_id)' "
    " ORDER BY conname"
)


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


@pytest.fixture()
def disposable(backup_database_url: str) -> Iterator[str]:
    name = f"accessforge_fwd_{uuid.uuid4().hex[:8]}"
    with connect(_with_database(backup_database_url, "postgres")) as conn:
        conn.autocommit = True
        conn.execute(f'CREATE DATABASE "{name}"')  # noqa: S608 - generated name, not user input
    try:
        yield _with_database(backup_database_url, name)
    finally:
        with connect(_with_database(backup_database_url, "postgres")) as conn:
            conn.autocommit = True
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')  # noqa: S608


def _apply_through(database_url: str, last: str) -> None:
    """Build a database at exactly one migration, the way the previous release left it.

    The migrator itself always applies everything, so this deliberately bypasses it and writes the
    ledger by hand -- which is also the only way to produce the "previous release" state without
    checking out the previous release.
    """
    with connect(database_url) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration "
            "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migration (name) VALUES (%s)", (path.name,))
            if path.name == last:
                break
        conn.commit()


def _previous() -> str:
    names = expected_migrations()
    assert names[-1] == NEWEST, (
        f"the newest migration is {names[-1]}, not {NEWEST}. This test names the boundary "
        "explicitly so that adding a migration without extending the forward-migration drill "
        "fails here rather than shipping untested."
    )
    return names[-2]


def test_a_database_at_the_previous_schema_migrates_forward(disposable: str) -> None:
    previous = _previous()
    _apply_through(disposable, previous)

    with connect(disposable) as conn:
        before = restore.schema_state(conn)
    assert before.latest == previous
    assert NEWEST not in before.applied

    applied = migrate(disposable)
    assert applied == [NEWEST]

    with connect(disposable) as conn:
        after = restore.schema_state(conn)
    assert after.latest == NEWEST


def test_data_written_under_the_previous_rules_survives_the_migration(disposable: str) -> None:
    """The half of a migration test that an empty-database run cannot reach.

    A released lease with an old-vocabulary reason is written before the migration and read after
    it. Widening a CHECK constraint keeps old rows valid by construction, and "by construction" is
    exactly the kind of claim this project does not accept without a row to point at.
    """
    _apply_through(disposable, _previous())
    lease = _seed_released_lease(disposable, reason="OPERATOR_RESET")

    migrate(disposable)

    with connect(disposable) as conn:
        row = conn.execute(
            "SELECT release_reason FROM desktop_lease WHERE id = %s", (lease,)
        ).fetchone()
    assert row is not None and row["release_reason"] == "OPERATOR_RESET"


def test_dispatch_migration_does_not_invent_historical_machine_credentials(disposable: str) -> None:
    _apply_through(disposable, "0034_manual_execution_approval.sql")
    lease = _seed_released_lease(disposable, reason="OPERATOR_RESET")
    with connect(disposable) as conn:
        assert conn.execute(
            "SELECT to_regclass('supervisor_dispatch_ticket') AS name"
        ).fetchone() == {"name": None}
    assert migrate(disposable) == [
        "0035_supervisor_dispatch_ticket.sql",
        "0036_supervisor_execution_session.sql",
        "0037_run_evaluation.sql",
        "0038_reader_startup_consent.sql",
        "0039_finding_diagnosis.sql",
        "0040_diagnosis_request.sql",
        "0041_diagnosis_request_recovery.sql",
        "0042_patch_source_comparison.sql",
        "0043_repair_request.sql",
        "0044_repair_delivery.sql",
        "0045_candidate_artifact_observation.sql",
        "0046_candidate_action_effect_permit.sql",
        NEWEST,
    ]
    with connect(disposable) as conn:
        assert conn.execute("SELECT count(*) AS n FROM supervisor_dispatch_ticket").fetchone() == {
            "n": 0
        }
        assert conn.execute(
            "SELECT release_reason FROM desktop_lease WHERE id=%s", (lease,)
        ).fetchone() == {"release_reason": "OPERATOR_RESET"}


def test_session_migration_does_not_mint_historical_execution_authority(disposable: str) -> None:
    _apply_through(disposable, "0035_supervisor_dispatch_ticket.sql")
    lease = _seed_released_lease(disposable, reason="OPERATOR_RESET")
    assert migrate(disposable) == [
        "0036_supervisor_execution_session.sql",
        "0037_run_evaluation.sql",
        "0038_reader_startup_consent.sql",
        "0039_finding_diagnosis.sql",
        "0040_diagnosis_request.sql",
        "0041_diagnosis_request_recovery.sql",
        "0042_patch_source_comparison.sql",
        "0043_repair_request.sql",
        "0044_repair_delivery.sql",
        "0045_candidate_artifact_observation.sql",
        "0046_candidate_action_effect_permit.sql",
        NEWEST,
    ]
    with connect(disposable) as conn:
        assert conn.execute(
            "SELECT count(*) AS n FROM supervisor_execution_session"
        ).fetchone() == {"n": 0}
        assert conn.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE oid='supervisor_execution_session'::regclass"
        ).fetchone() == {
            "relrowsecurity": True,
            "relforcerowsecurity": True,
        }
        assert conn.execute(
            "SELECT release_reason FROM desktop_lease WHERE id=%s", (lease,)
        ).fetchone() == {
            "release_reason": "OPERATOR_RESET",
        }


def test_manual_approval_migration_preserves_old_decisions_without_creating_consent(
    disposable: str,
) -> None:
    _apply_through(disposable, "0033_canonical_execution_manifest.sql")
    actor, approval, target = (str(uuid.uuid4()) for _ in range(3))
    with connect(disposable) as conn:
        conn.execute("INSERT INTO workspace(id,name) VALUES(%s,'upgrade')", (WS,))
        conn.execute("INSERT INTO app_user(id,email) VALUES(%s,'upgrade@example.test')", (actor,))
        conn.execute(
            "INSERT INTO approval(id,workspace_id,scope,actor_user,target_id,target_digest,"
            "expected_revision,expires_at) VALUES(%s,%s,'PATCH_APPLY',%s,%s,repeat('a',64),"
            "4,now()+interval '1 hour')",
            (approval, WS, actor, target),
        )
        before = conn.execute("SELECT * FROM approval").fetchall()
    assert migrate(disposable) == [
        "0034_manual_execution_approval.sql",
        "0035_supervisor_dispatch_ticket.sql",
        "0036_supervisor_execution_session.sql",
        "0037_run_evaluation.sql",
        "0038_reader_startup_consent.sql",
        "0039_finding_diagnosis.sql",
        "0040_diagnosis_request.sql",
        "0041_diagnosis_request_recovery.sql",
        "0042_patch_source_comparison.sql",
        "0043_repair_request.sql",
        "0044_repair_delivery.sql",
        "0045_candidate_artifact_observation.sql",
        "0046_candidate_action_effect_permit.sql",
        NEWEST,
    ]
    with connect(disposable) as conn:
        assert conn.execute("SELECT * FROM approval").fetchall() == before
        assert conn.execute(
            "SELECT count(*) AS n FROM approval WHERE scope='RUN_EFFECTS'"
        ).fetchone() == {"n": 0}
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute("UPDATE approval SET expected_revision=5")
        conn.execute("UPDATE approval SET revoked_at=now()")
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute("UPDATE approval SET revoked_at=NULL")


def test_regression_migrations_effect_is_absent_before_and_present_after(
    disposable: str,
) -> None:
    _apply_through(disposable, "0028_candidate_archive_location.sql")
    with connect(disposable) as conn:
        assert conn.execute(
            "SELECT to_regclass('candidate_regression_attempt') AS name"
        ).fetchone() == {"name": None}
    assert migrate(disposable) == [
        "0029_candidate_regressions.sql",
        "0030_candidate_endpoint.sql",
        "0031_candidate_materialization.sql",
        "0032_candidate_run_binding.sql",
        "0033_canonical_execution_manifest.sql",
        "0034_manual_execution_approval.sql",
        "0035_supervisor_dispatch_ticket.sql",
        "0036_supervisor_execution_session.sql",
        "0037_run_evaluation.sql",
        "0038_reader_startup_consent.sql",
        "0039_finding_diagnosis.sql",
        "0040_diagnosis_request.sql",
        "0041_diagnosis_request_recovery.sql",
        "0042_patch_source_comparison.sql",
        "0043_repair_request.sql",
        "0044_repair_delivery.sql",
        "0045_candidate_artifact_observation.sql",
        "0046_candidate_action_effect_permit.sql",
        NEWEST,
    ]
    with connect(disposable) as conn:
        for table in ("candidate_regression_attempt", "candidate_regression_process"):
            assert conn.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=%s", (table,)
            ).fetchone() == {
                "relrowsecurity": True,
                "relforcerowsecurity": True,
            }
        assert conn.execute("SELECT * FROM candidate_regression_attempt").fetchall() == []


def test_materialization_upgrade_does_not_fabricate_historical_source(disposable: str) -> None:
    _apply_through(disposable, "0030_candidate_endpoint.sql")
    historical = _seed_legacy_candidate(disposable, "BUILT")
    with connect(disposable) as conn:
        assert conn.execute(
            "SELECT to_regclass('candidate_materialization') AS name"
        ).fetchone() == {"name": None}
    assert migrate(disposable) == [
        "0031_candidate_materialization.sql",
        "0032_candidate_run_binding.sql",
        "0033_canonical_execution_manifest.sql",
        "0034_manual_execution_approval.sql",
        "0035_supervisor_dispatch_ticket.sql",
        "0036_supervisor_execution_session.sql",
        "0037_run_evaluation.sql",
        "0038_reader_startup_consent.sql",
        "0039_finding_diagnosis.sql",
        "0040_diagnosis_request.sql",
        "0041_diagnosis_request_recovery.sql",
        "0042_patch_source_comparison.sql",
        "0043_repair_request.sql",
        "0044_repair_delivery.sql",
        "0045_candidate_artifact_observation.sql",
        "0046_candidate_action_effect_permit.sql",
        NEWEST,
    ]
    with connect(disposable) as conn:
        assert conn.execute("SELECT * FROM candidate_materialization").fetchall() == []
        assert conn.execute(
            "SELECT state FROM candidate_build_attempt WHERE id=%s", (historical,)
        ).fetchone() == {"state": "BUILT"}
        assert conn.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relname='candidate_materialization'"
        ).fetchone() == {"relrowsecurity": True, "relforcerowsecurity": True}


def test_canonical_manifest_upgrade_preserves_legacy_fingerprint_without_authority(
    disposable: str,
) -> None:
    _apply_through(disposable, "0032_candidate_run_binding.sql")
    build = _seed_legacy_candidate(disposable, "BUILT")
    with connect(disposable) as conn:
        row = conn.execute(
            "SELECT b.workspace_id AS ws,b.project_id AS project,b.source_snapshot_id AS source,"
            "v.baseline_run_id AS run,a.actor_user AS actor FROM candidate_build_attempt b "
            "JOIN patch_verification v ON v.id=b.verification_id "
            "JOIN approval a ON a.id=b.approval_id "
            "WHERE b.id=%s",
            (build,),
        ).fetchone()
        assert row is not None
        ids = {**row, **{key: str(uuid.uuid4()) for key in ("env", "artifact", "seal")}}
        conn.execute(
            "INSERT INTO environment_manifest(id,workspace_id,project_id,name,allowed_origins,"
            "fixture_reset_strategy,observer_credential_ref,reset_credential_ref,permitted_effects,"
            "authorized_by,config_digest,expires_at) VALUES (%(env)s,%(ws)s,%(project)s,'legacy',"
            "ARRAY['http://127.0.0.1:1'],'reset','observer','reset',ARRAY[]::text[],%(actor)s,"
            "repeat('a',64),now()+interval '1 hour')",
            ids,
        )
        conn.execute(
            "INSERT INTO build_artifact(id,workspace_id,project_id,source_snapshot_id,"
            "artifact_digest,identity_observable) VALUES (%(artifact)s,%(ws)s,%(project)s,"
            "%(source)s,repeat('a',64),true)",
            ids,
        )
        conn.execute(
            "INSERT INTO sealed_manifest(id,workspace_id,project_id,run_id,source_snapshot_id,"
            "build_artifact_id,environment_manifest_id,environment_config_digest,journey_digest,"
            "assertion_set_digest,fixture_digest,runner_profile_digest,navigator_policy_digest,"
            "evaluator_version,model_config_digest,manifest_digest) VALUES (%(seal)s,%(ws)s,"
            "%(project)s,%(run)s,%(source)s,%(artifact)s,%(env)s,repeat('a',64),repeat('a',64),"
            "repeat('a',64),repeat('a',64),repeat('a',64),repeat('a',64),'legacy',repeat('a',64),"
            "repeat('f',64))",
            ids,
        )
    assert migrate(disposable) == [
        "0033_canonical_execution_manifest.sql",
        "0034_manual_execution_approval.sql",
        "0035_supervisor_dispatch_ticket.sql",
        "0036_supervisor_execution_session.sql",
        "0037_run_evaluation.sql",
        "0038_reader_startup_consent.sql",
        "0039_finding_diagnosis.sql",
        "0040_diagnosis_request.sql",
        "0041_diagnosis_request_recovery.sql",
        "0042_patch_source_comparison.sql",
        "0043_repair_request.sql",
        "0044_repair_delivery.sql",
        "0045_candidate_artifact_observation.sql",
        "0046_candidate_action_effect_permit.sql",
        NEWEST,
    ]
    with connect(disposable) as conn:
        assert conn.execute(
            "SELECT canonical_manifest,manifest_digest,authorization_id FROM sealed_manifest"
        ).fetchall() == [
            {"canonical_manifest": None, "manifest_digest": "f" * 64, "authorization_id": None}
        ]
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute("UPDATE sealed_manifest SET canonical_manifest='{}'::jsonb")


def test_candidate_run_upgrade_adds_no_invented_run_or_lease(disposable: str) -> None:
    _apply_through(disposable, "0031_candidate_materialization.sql")
    legacy_lease = _seed_released_lease(disposable, reason="OPERATOR_RESET")
    with connect(disposable) as conn:
        conn.execute(
            "INSERT INTO run_fixture_instance(id,workspace_id,run_id,template_id,template_digest,"
            "nonce,navigator_values,observer_config) SELECT %s,workspace_id,run_id,'legacy',"
            "repeat('a',64),'legacy-nonce','{}'::jsonb,'{}'::jsonb FROM desktop_lease WHERE id=%s",
            (str(uuid.uuid4()), legacy_lease),
        )
        assert conn.execute("SELECT to_regclass('candidate_run_binding') AS name").fetchone() == {
            "name": None
        }
    assert migrate(disposable) == [
        "0032_candidate_run_binding.sql",
        "0033_canonical_execution_manifest.sql",
        "0034_manual_execution_approval.sql",
        "0035_supervisor_dispatch_ticket.sql",
        "0036_supervisor_execution_session.sql",
        "0037_run_evaluation.sql",
        "0038_reader_startup_consent.sql",
        "0039_finding_diagnosis.sql",
        "0040_diagnosis_request.sql",
        "0041_diagnosis_request_recovery.sql",
        "0042_patch_source_comparison.sql",
        "0043_repair_request.sql",
        "0044_repair_delivery.sql",
        "0045_candidate_artifact_observation.sql",
        "0046_candidate_action_effect_permit.sql",
        NEWEST,
    ]
    with connect(disposable) as conn:
        assert conn.execute(
            "SELECT captured_contract_digest FROM run_fixture_instance"
        ).fetchall() == [{"captured_contract_digest": None}]
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute("UPDATE run_fixture_instance SET captured_contract_digest=repeat('b',64)")
        for table in ("candidate_run_binding", "candidate_reader_lease"):
            assert (
                conn.execute(
                    psycopg.sql.SQL("SELECT * FROM {}").format(psycopg.sql.Identifier(table))
                ).fetchall()
                == []
            )
            assert conn.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=%s", (table,)
            ).fetchone() == {"relrowsecurity": True, "relforcerowsecurity": True}


def test_endpoint_migration_adds_no_invented_binding(disposable: str) -> None:
    _apply_through(disposable, "0029_candidate_regressions.sql")
    historical = _seed_legacy_candidate(disposable, "BUILT")
    attempt = str(uuid.uuid4())
    with connect(disposable) as conn:
        conn.execute(
            "INSERT INTO candidate_regression_attempt "
            "(id,workspace_id,build_id,worker_token,artifact_digest,policy_digest,image_id,"
            "daemon_endpoint,daemon_id,state,lease_expires_at,dispatched_at,finished_at,"
            "cleanup_confirmed,checks) VALUES (%s,%s,%s,%s,repeat('a',64),repeat('b',64),"
            "'sha256:' || repeat('c',64),'unix:///tmp/synthetic.sock','synthetic',"
            "'PASSED',now()+interval '180 seconds',now(),now(),true,ARRAY['historical'])",
            (attempt, WS, historical, str(uuid.uuid4())),
        )
        assert conn.execute("SELECT to_regclass('candidate_endpoint') AS name").fetchone() == {
            "name": None
        }
    assert migrate(disposable) == [
        "0030_candidate_endpoint.sql",
        "0031_candidate_materialization.sql",
        "0032_candidate_run_binding.sql",
        "0033_canonical_execution_manifest.sql",
        "0034_manual_execution_approval.sql",
        "0035_supervisor_dispatch_ticket.sql",
        "0036_supervisor_execution_session.sql",
        "0037_run_evaluation.sql",
        "0038_reader_startup_consent.sql",
        "0039_finding_diagnosis.sql",
        "0040_diagnosis_request.sql",
        "0041_diagnosis_request_recovery.sql",
        "0042_patch_source_comparison.sql",
        "0043_repair_request.sql",
        "0044_repair_delivery.sql",
        "0045_candidate_artifact_observation.sql",
        "0046_candidate_action_effect_permit.sql",
        NEWEST,
    ]
    with connect(disposable) as conn:
        assert conn.execute("SELECT * FROM candidate_endpoint").fetchall() == []
        assert conn.execute(
            "SELECT state,endpoint_required FROM candidate_regression_attempt WHERE id=%s",
            (attempt,),
        ).fetchone() == {"state": "PASSED", "endpoint_required": False}
        assert conn.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relname='candidate_endpoint'"
        ).fetchone() == {"relrowsecurity": True, "relforcerowsecurity": True}


def test_archive_location_upgrade_keeps_unknown_historical_locations_unbound(
    disposable: str,
) -> None:
    _apply_through(disposable, "0027_candidate_archive_retirement.sql")
    with connect(disposable) as conn:
        assert conn.execute(
            "SELECT to_regclass('candidate_archive_restore_location') AS name"
        ).fetchone() == {"name": None}
    assert migrate(disposable) == [
        "0028_candidate_archive_location.sql",
        "0029_candidate_regressions.sql",
        "0030_candidate_endpoint.sql",
        "0031_candidate_materialization.sql",
        "0032_candidate_run_binding.sql",
        "0033_canonical_execution_manifest.sql",
        "0034_manual_execution_approval.sql",
        "0035_supervisor_dispatch_ticket.sql",
        "0036_supervisor_execution_session.sql",
        "0037_run_evaluation.sql",
        "0038_reader_startup_consent.sql",
        "0039_finding_diagnosis.sql",
        "0040_diagnosis_request.sql",
        "0041_diagnosis_request_recovery.sql",
        "0042_patch_source_comparison.sql",
        "0043_repair_request.sql",
        "0044_repair_delivery.sql",
        "0045_candidate_artifact_observation.sql",
        "0046_candidate_action_effect_permit.sql",
        NEWEST,
    ]
    with connect(disposable) as conn:
        assert conn.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relname = 'candidate_archive_restore_location'"
        ).fetchone() == {"relrowsecurity": True, "relforcerowsecurity": True}
        columns = conn.execute(
            "SELECT column_name,is_nullable FROM information_schema.columns "
            "WHERE table_name = 'candidate_archive' "
            "AND column_name IN ('store_endpoint','store_bucket') ORDER BY column_name"
        ).fetchall()
        assert columns == [
            {"column_name": "store_bucket", "is_nullable": "YES"},
            {"column_name": "store_endpoint", "is_nullable": "YES"},
        ]


def test_retirement_migration_preserves_legacy_upload_protocol(disposable: str) -> None:
    _apply_through(disposable, "0026_nonterminal_run_delete.sql")
    build = _seed_legacy_candidate(disposable, "BUILT")
    with connect(disposable) as conn:
        conn.execute(
            "INSERT INTO candidate_process_receipt "
            "(build_id,workspace_id,container_id,image_id,platform) "
            "VALUES (%s,%s,repeat('a',64),'sha256:' || repeat('b',64),'linux/arm64')",
            (build, WS),
        )
        conn.execute(
            "INSERT INTO candidate_archive (build_id,workspace_id,content_digest,size_bytes,"
            "object_key,stdout_digest,stderr_digest,state) "
            "VALUES (%s,%s,repeat('a',64),10240,'synthetic',"
            "repeat('b',64),repeat('c',64),'QUARANTINED')",
            (build, WS),
        )
        assert conn.execute(
            "SELECT to_regclass('candidate_archive_retirement') AS name"
        ).fetchone() == {"name": None}
    assert migrate(disposable) == [
        "0027_candidate_archive_retirement.sql",
        "0028_candidate_archive_location.sql",
        "0029_candidate_regressions.sql",
        "0030_candidate_endpoint.sql",
        "0031_candidate_materialization.sql",
        "0032_candidate_run_binding.sql",
        "0033_canonical_execution_manifest.sql",
        "0034_manual_execution_approval.sql",
        "0035_supervisor_dispatch_ticket.sql",
        "0036_supervisor_execution_session.sql",
        "0037_run_evaluation.sql",
        "0038_reader_startup_consent.sql",
        "0039_finding_diagnosis.sql",
        "0040_diagnosis_request.sql",
        "0041_diagnosis_request_recovery.sql",
        "0042_patch_source_comparison.sql",
        "0043_repair_request.sql",
        "0044_repair_delivery.sql",
        "0045_candidate_artifact_observation.sql",
        "0046_candidate_action_effect_permit.sql",
        NEWEST,
    ]
    with connect(disposable) as conn:
        assert conn.execute(
            "SELECT storage_protocol FROM candidate_archive WHERE build_id = %s", (build,)
        ).fetchone() == {"storage_protocol": None}
        assert conn.execute(
            "SELECT store_endpoint,store_bucket FROM candidate_archive WHERE build_id = %s",
            (build,),
        ).fetchone() == {"store_endpoint": None, "store_bucket": None}
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute(
                "UPDATE candidate_archive SET store_endpoint = 'http://localhost:9000', "
                "store_bucket = 'invented' WHERE build_id = %s",
                (build,),
            )
        assert conn.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
            "WHERE relname = 'candidate_archive_retirement'"
        ).fetchone() == {"relrowsecurity": True, "relforcerowsecurity": True}
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute(
                "UPDATE candidate_archive SET storage_protocol = 'CREATE_ONLY_V1' "
                "WHERE build_id = %s",
                (build,),
            )
        conn.execute(
            "INSERT INTO candidate_archive_retirement "
            "(build_id,workspace_id,policy_revision,retain_days) VALUES (%s,%s,0,90)",
            (build, WS),
        )
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute(
                "UPDATE candidate_archive_retirement SET retain_days = 0 WHERE build_id = %s",
                (build,),
            )


def test_nonterminal_delete_migration_prevents_orphans(disposable: str) -> None:
    _apply_through(disposable, "0025_candidate_artifact_receipts.sql")
    run_id = str(uuid.uuid4())
    with connect(disposable) as conn:
        conn.execute("INSERT INTO workspace (id,name) VALUES (%s,'delete-regression')", (WS,))
        conn.execute(
            "INSERT INTO run (id,workspace_id,manifest_digest,status,outcome) "
            "VALUES (%s,%s,repeat('a',64),'QUEUED','NOT_EVALUATED')",
            (run_id, WS),
        )
        # Demonstrate the old trigger, but roll back instead of leaving corrupt data behind.
        with conn.transaction(force_rollback=True):
            assert conn.execute("DELETE FROM workspace WHERE id = %s", (WS,)).rowcount == 1
            assert conn.execute("SELECT id FROM run WHERE id = %s", (run_id,)).fetchone()
            with pytest.raises(restore.RestoreError, match="missing workspaces"):
                restore.assert_backup_run_integrity(conn)
    assert migrate(disposable) == [
        "0026_nonterminal_run_delete.sql",
        "0027_candidate_archive_retirement.sql",
        "0028_candidate_archive_location.sql",
        "0029_candidate_regressions.sql",
        "0030_candidate_endpoint.sql",
        "0031_candidate_materialization.sql",
        "0032_candidate_run_binding.sql",
        "0033_canonical_execution_manifest.sql",
        "0034_manual_execution_approval.sql",
        "0035_supervisor_dispatch_ticket.sql",
        "0036_supervisor_execution_session.sql",
        "0037_run_evaluation.sql",
        "0038_reader_startup_consent.sql",
        "0039_finding_diagnosis.sql",
        "0040_diagnosis_request.sql",
        "0041_diagnosis_request_recovery.sql",
        "0042_patch_source_comparison.sql",
        "0043_repair_request.sql",
        "0044_repair_delivery.sql",
        "0045_candidate_artifact_observation.sql",
        "0046_candidate_action_effect_permit.sql",
        NEWEST,
    ]
    with connect(disposable) as conn:
        assert conn.execute("DELETE FROM workspace WHERE id = %s", (WS,)).rowcount == 1
        assert conn.execute("SELECT id FROM run WHERE id = %s", (run_id,)).fetchone() is None
        restore.assert_backup_run_integrity(conn)


@pytest.mark.parametrize("state", ["COMPLETED", "INTERRUPTED", "CANCELLED"])
def test_run_delete_fix_preserves_terminal_immutability(disposable: str, state: str) -> None:
    migrate(disposable)
    run_id = str(uuid.uuid4())
    with connect(disposable) as conn:
        conn.execute("INSERT INTO workspace (id,name) VALUES (%s,'terminal-delete')", (WS,))
        conn.execute(
            "INSERT INTO run (id,workspace_id,manifest_digest,status,outcome,ambiguity_reason) "
            "VALUES (%s,%s,repeat('a',64),%s,'INCONCLUSIVE','synthetic terminal fixture')",
            (run_id, WS, state),
        )
        for statement, identity in (
            ("DELETE FROM run WHERE id = %s", run_id),
            ("DELETE FROM workspace WHERE id = %s", WS),
            ("UPDATE run SET revision = revision + 1 WHERE id = %s", run_id),
        ):
            with pytest.raises(psycopg.IntegrityError), conn.transaction():
                conn.execute(statement, (identity,))
        assert conn.execute("SELECT id FROM workspace WHERE id = %s", (WS,)).fetchone()
        assert conn.execute("SELECT id FROM run WHERE id = %s", (run_id,)).fetchone()


def test_candidate_artifact_migration_preserves_its_constraints(disposable: str) -> None:
    _apply_through(disposable, "0024_candidate_daemon_binding.sql")
    historical = _seed_legacy_candidate(disposable, "BUILT")
    with connect(disposable) as conn:
        assert conn.execute("SELECT to_regclass('candidate_archive') AS name").fetchone() == {
            "name": None,
        }
    assert migrate(disposable) == [
        "0025_candidate_artifact_receipts.sql",
        "0026_nonterminal_run_delete.sql",
        "0027_candidate_archive_retirement.sql",
        "0028_candidate_archive_location.sql",
        "0029_candidate_regressions.sql",
        "0030_candidate_endpoint.sql",
        "0031_candidate_materialization.sql",
        "0032_candidate_run_binding.sql",
        "0033_canonical_execution_manifest.sql",
        "0034_manual_execution_approval.sql",
        "0035_supervisor_dispatch_ticket.sql",
        "0036_supervisor_execution_session.sql",
        "0037_run_evaluation.sql",
        "0038_reader_startup_consent.sql",
        "0039_finding_diagnosis.sql",
        "0040_diagnosis_request.sql",
        "0041_diagnosis_request_recovery.sql",
        "0042_patch_source_comparison.sql",
        "0043_repair_request.sql",
        "0044_repair_delivery.sql",
        "0045_candidate_artifact_observation.sql",
        "0046_candidate_action_effect_permit.sql",
        NEWEST,
    ]
    with connect(disposable) as conn:
        # Migration cannot invent process provenance or available bytes for an old digest.
        assert conn.execute("SELECT * FROM candidate_process_receipt").fetchall() == []
        assert conn.execute("SELECT * FROM candidate_archive").fetchall() == []
        assert conn.execute(
            "SELECT state FROM candidate_build_attempt WHERE id = %s", (historical,)
        ).fetchone() == {"state": "BUILT"}
        for table in ("candidate_process_receipt", "candidate_archive"):
            assert conn.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname = %s",
                (table,),
            ).fetchone() == {"relrowsecurity": True, "relforcerowsecurity": True}
            policy = conn.execute(
                "SELECT qual,with_check FROM pg_policies WHERE tablename = %s",
                (table,),
            ).fetchone()
            assert policy is not None
            assert all("current_workspace_id()" in str(value) for value in policy.values())
        # Real inserts through the new FK and byte-limit checks, explicitly synthetic provenance.
        conn.execute(
            "INSERT INTO candidate_process_receipt "
            "(build_id,workspace_id,container_id,image_id,platform) "
            "VALUES (%s,%s,repeat('a',64),'sha256:' || repeat('b',64),'linux/arm64')",
            (historical, WS),
        )
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute(
                "UPDATE candidate_process_receipt SET platform = 'linux/amd64' WHERE build_id = %s",
                (historical,),
            )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO candidate_archive (build_id,workspace_id,content_digest,size_bytes,"
                "object_key,stdout_digest,stderr_digest,state) "
                "VALUES (%s,%s,repeat('a',64),41943041,'synthetic',"
                "repeat('b',64),repeat('c',64),'QUARANTINED')",
                (historical, WS),
            )


def test_daemon_binding_migration_fences_legacy_attempts(
    disposable: str,
) -> None:
    """Legacy in-flight builds are fenced; no historical endpoint is invented."""
    _apply_through(disposable, "0023_candidate_build_attempt.sql")
    ids = {
        state: _seed_legacy_candidate(disposable, state)
        for state in ("CLAIMED", "DISPATCHED", "BUILT")
    }
    with connect(disposable) as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM information_schema.columns WHERE "
                "table_name = 'candidate_build_attempt' AND column_name = 'daemon_id'"
            ).fetchone()
            is None
        )
    migrate(disposable)
    with connect(disposable) as conn:
        for old_state, build_id in ids.items():
            row = conn.execute(
                "SELECT state, epoch, daemon_endpoint, daemon_id, artifact_digest, "
                "failure_code FROM candidate_build_attempt WHERE id = %s",
                (build_id,),
            ).fetchone()
            assert row is not None
            assert row["daemon_endpoint"] is None and row["daemon_id"] is None
            if old_state == "BUILT":
                assert row["state"] == "BUILT" and row["epoch"] == 1
                assert row["artifact_digest"] == "a" * 64
            else:
                assert row["state"] == "UNKNOWN" and row["epoch"] == 2
                assert row["failure_code"] == "MISSING_DAEMON_BINDING"
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE candidate_build_attempt SET state = 'CLAIMED', finished_at = NULL "
                "WHERE id = %s",
                (ids["CLAIMED"],),
            )


def _seed_legacy_candidate(database_url: str, state: str) -> str:
    """Synthetic pre-0024 rows through real constraints, not historical execution proof."""
    lease = _seed_released_lease(database_url, reason="OPERATOR_RESET")
    ids = {
        key: str(uuid.uuid4())
        for key in (
            "user",
            "project",
            "source",
            "finding",
            "patch",
            "approval",
            "verification",
            "build",
        )
    }
    ids["ws"] = WS
    ids["email"] = ids["user"] + "@example.test"
    ids["name"] = "legacy-" + ids["project"]
    with connect(database_url) as conn:
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
        run = conn.execute("SELECT run_id FROM desktop_lease WHERE id = %s", (lease,)).fetchone()
        assert run is not None
        ids["run"] = str(run["run_id"])
        conn.execute("INSERT INTO app_user (id,email) VALUES (%(user)s,%(email)s)", ids)
        conn.execute(
            "INSERT INTO project (id,workspace_id,name) VALUES (%(project)s,%(ws)s,%(name)s)",
            ids,
        )
        conn.execute(
            "INSERT INTO source_snapshot (id,workspace_id,project_id,commit_sha,tree_digest,"
            "dirty,requested_revision) "
            "VALUES (%(source)s,%(ws)s,%(project)s,repeat('a',40),repeat('a',64),false,'HEAD')",
            ids,
        )
        conn.execute(
            "INSERT INTO finding (id,workspace_id,run_id,assertion_id,status,summary) "
            "VALUES (%(finding)s,%(ws)s,%(run)s,'synthetic','REPRODUCED','synthetic')",
            ids,
        )
        conn.execute(
            "INSERT INTO patch_proposal (id,workspace_id,finding_id,base_manifest_digest,"
            "base_source_digest,patch_digest,status,proposed_by,rationale) "
            "VALUES (%(patch)s,%(ws)s,%(finding)s,repeat('a',64),repeat('a',64),"
            "repeat('a',64),'BUILDING',%(user)s,'synthetic')",
            ids,
        )
        conn.execute(
            "INSERT INTO approval (id,workspace_id,scope,actor_user,target_id,target_digest,"
            "expected_revision,expires_at) VALUES (%(approval)s,%(ws)s,'PATCH_APPLY',%(user)s,"
            "%(patch)s,repeat('a',64),1,now()+interval '1 hour')",
            ids,
        )
        conn.execute(
            "INSERT INTO patch_verification (id,workspace_id,patch_id,baseline_run_id,"
            "baseline_identity,state) "
            "VALUES (%(verification)s,%(ws)s,%(patch)s,%(run)s,'{}','BUILDING')",
            ids,
        )
        conn.execute(
            "INSERT INTO candidate_build_attempt (id,workspace_id,patch_id,verification_id,"
            "project_id,source_snapshot_id,approval_id,approved_revision,building_revision,"
            "source_commit,source_tree_digest,base_archive_digest,candidate_archive_digest,"
            "patch_digest,policy_digest,surface_digest,worker_token,state,lease_expires_at,"
            "created_at,dispatched_at,finished_at,artifact_digest,cleanup_confirmed) "
            "VALUES (%(build)s,%(ws)s,%(patch)s,%(verification)s,"
            "%(project)s,%(source)s,%(approval)s,"
            "1,2,repeat('a',40),repeat('a',64),repeat('a',64),repeat('a',64),repeat('a',64),"
            "repeat('a',64),repeat('a',64),%(user)s,%(state)s,now()+interval '1 hour',now(),"
            "CASE WHEN %(state)s <> 'CLAIMED' THEN now() END,"
            "CASE WHEN %(state)s = 'BUILT' THEN now() END,"
            "CASE WHEN %(state)s = 'BUILT' THEN repeat('a',64) END,%(state)s = 'BUILT')",
            {**ids, "state": state},
        )
    return ids["build"]


def test_the_candidate_attempt_migrations_effect_remains_correct(disposable: str) -> None:
    """Keep every 0023 constraint/trigger/isolation assertion after it ceases to be the tip."""
    _apply_through(disposable, "0022_rate_limit_buckets.sql")
    with connect(disposable) as conn:
        assert conn.execute("SELECT to_regclass('candidate_build_attempt') AS name").fetchone() == {
            "name": None,
        }
    migrate(disposable)
    with connect(disposable) as conn:
        forced = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'candidate_build_attempt'",
        ).fetchone()
        policy = conn.execute(
            "SELECT qual, with_check FROM pg_policies WHERE tablename = 'candidate_build_attempt'",
        ).fetchone()
        constraints = [
            str(row["definition"])
            for row in conn.execute(
                "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
                "WHERE conrelid = 'candidate_build_attempt'::regclass",
            ).fetchall()
        ]
        trigger = conn.execute(
            "SELECT tgenabled FROM pg_trigger WHERE tgrelid = 'candidate_build_attempt'::regclass "
            "AND tgname = 'candidate_build_identity_is_immutable'",
        ).fetchone()
    assert forced == {"relrowsecurity": True, "relforcerowsecurity": True}
    assert policy is not None
    for clause in (str(policy["qual"]), str(policy["with_check"])):
        assert "current_workspace_id()" in clause
        assert "IS NULL" not in clause
    assert "UNIQUE (patch_id)" in constraints
    for column, parent in (
        ("patch_id", "patch_proposal"),
        ("verification_id", "patch_verification"),
        ("project_id", "project"),
        ("source_snapshot_id", "source_snapshot"),
        ("approval_id", "approval"),
    ):
        assert any(
            f"FOREIGN KEY ({column}, workspace_id) REFERENCES {parent}(id, workspace_id)"
            in definition
            for definition in constraints
        )
    assert any("building_revision = (approved_revision + 1)" in item for item in constraints)
    assert any(
        "cleanup_confirmed" in item and "dispatched_at IS NOT NULL" in item for item in constraints
    )
    assert trigger == {"tgenabled": "O"}


def test_the_rate_limit_migrations_effect_remains_correct(disposable: str) -> None:
    """Preserve every former tip assertion for 0022, including its pre-migration absence."""
    _apply_through(disposable, "0021_patches_and_verification.sql")
    with connect(disposable) as conn:
        before = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            " WHERE table_schema = 'public' AND table_name = 'rate_limit_bucket'"
        ).fetchone()
    assert before is None, "the bucket table already existed, so this proves nothing"

    migrate(disposable)

    with connect(disposable) as conn:
        after = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            " WHERE table_schema = 'public' AND table_name = 'rate_limit_bucket'"
        ).fetchone()
        checks = {
            str(row["conname"]): str(row["definition"])
            for row in conn.execute(
                "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint "
                " WHERE conrelid = 'rate_limit_bucket'::regclass AND contype = 'c'"
            ).fetchall()
        }
        policy = conn.execute(
            "SELECT qual, with_check FROM pg_policies "
            " WHERE tablename = 'rate_limit_bucket' AND policyname = 'workspace_isolation'"
        ).fetchone()
        forced = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            " WHERE relname = 'rate_limit_bucket'"
        ).fetchone()
        # Fractional tokens. Integer tokens would round every partial refill down to nothing, so a
        # caller arriving steadily just under the refill interval would be refused for ever.
        token_type = conn.execute(
            "SELECT data_type FROM information_schema.columns "
            " WHERE table_name = 'rate_limit_bucket' AND column_name = 'tokens'"
        ).fetchone()

    assert after is not None
    # The pairing of scope and workspace is exact, so a workspace bucket cannot be written as a
    # global one -- which is what would let a tenant's allowance escape its own row.
    assert "workspace_scope_is_exact" in checks, (
        "a WORKSPACE bucket could be stored with no workspace, escaping tenant scoping"
    )
    assert "PRINCIPAL" in checks["workspace_scope_is_exact"]
    # Matched on the operator rather than the whole rendering: PostgreSQL prints the literal as
    # `(0)::double precision`, and an assertion on the exact text would fail for the wrong reason.
    assert any("tokens >= " in definition for definition in checks.values()), (
        "tokens could go negative, which is a bucket that can never be refilled to a usable state"
    )
    assert token_type is not None and token_type["data_type"] == "double precision"
    assert forced is not None
    # FORCE, not merely ENABLE: the application role owns this table and ENABLE does nothing for an
    # owner.
    assert bool(forced["relrowsecurity"]) and bool(forced["relforcerowsecurity"])
    # The deliberate exception, asserted so it cannot widen by accident: global principal rows are
    # admitted, and nothing else is.
    assert policy is not None
    for clause in (str(policy["qual"]), str(policy["with_check"])):
        assert "current_workspace_id()" in clause, (
            "the policy no longer scopes workspace buckets to their tenant"
        )
        assert "workspace_id IS NULL" in clause, (
            "global principal buckets are no longer admitted, so a principal's allowance would "
            "become per-workspace and multiply with every invitation"
        )


def test_the_patch_tables_from_an_earlier_migration_are_still_correct(
    disposable: str,
) -> None:
    """Migration 0021's effect, kept as its own case now that it is no longer the newest.

    Every assertion the tip test made when 0021 *was* the newest, not a weaker set. Weakening moved
    coverage to a name check is how a guard quietly stops being checked: the test keeps passing
    while the thing it named turns into something else.
    """
    migrate(disposable)
    with connect(disposable) as conn:
        verification_guards = {
            str(row["conname"]): str(row["definition"])
            for row in conn.execute(
                "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint "
                " WHERE conrelid = 'patch_verification'::regclass AND contype = 'c'"
            ).fetchall()
        }
        change_guards = {
            str(row["conname"]): str(row["definition"])
            for row in conn.execute(
                "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint "
                " WHERE conrelid = 'patch_change'::regclass AND contype IN ('c', 'u')"
            ).fetchall()
        }
        surface_guard = conn.execute(
            "SELECT 1 FROM pg_constraint "
            " WHERE conrelid = 'project_repair_surface'::regclass AND contype = 'c' "
            "   AND pg_get_constraintdef(oid) LIKE '%cardinality(paths)%'"
        ).fetchone()
        forced = conn.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            " WHERE relname IN ('patch_proposal', 'patch_change', 'patch_verification', "
            "                   'patch_proposal_transition', 'project_repair_surface')"
        ).fetchall()
        finding_ref = conn.execute(
            "SELECT confdeltype FROM pg_constraint "
            " WHERE conrelid = 'patch_proposal'::regclass AND contype = 'f' "
            "   AND confrelid = 'finding'::regclass"
        ).fetchone()

    # A verification could otherwise be VERIFIED with no candidate, carry no reasons, or hold a
    # verdict while claiming it was never concluded.
    assert "verified_requires_a_candidate" in verification_guards
    assert "conclusion_is_explained" in verification_guards
    assert "conclusion_is_complete" in verification_guards

    # Not just the name: the constraint has to still distinguish a deletion from a lost record.
    assert "content_matches_operation" in change_guards
    assert "DELETE" in change_guards["content_matches_operation"]

    # Both uniqueness constraints. A repeated path makes a reloaded patch ambiguous and its digest
    # unreproducible -- and that digest is what an approval binds to. A repeated ordinal loses the
    # order a reviewer reads the diff in.
    uniques = [d for d in change_guards.values() if d.startswith("UNIQUE")]
    assert any("patch_id, path" in d for d in uniques), uniques
    assert any("patch_id, ordinal" in d for d in uniques), uniques

    # A configured surface of nothing cannot be told from no surface at all, and one of those two
    # has to refuse every proposal -- so the database refuses the ambiguity.
    assert surface_guard is not None, "a project could record a repair surface of no paths"

    # All five, FORCE and not merely ENABLE: the application role owns these tables and ENABLE does
    # nothing for an owner. A proposal names source paths in a customer's repository.
    assert len(forced) == 5, sorted(str(row["relname"]) for row in forced)
    for row in forced:
        assert bool(row["relrowsecurity"]) and bool(row["relforcerowsecurity"]), row["relname"]

    # 'r' is RESTRICT. A patch proposal outliving the finding it repairs would be a change to
    # somebody's application that nothing explains.
    assert finding_ref is not None and finding_ref["confdeltype"] == "r"


def test_the_purge_queue_artifact_key_from_an_earlier_migration_is_still_correct(
    disposable: str,
) -> None:
    """Migration 0020's effect, kept as its own case now that it is no longer the newest."""
    migrate(disposable)
    with connect(disposable) as conn:
        key = conn.execute(
            "SELECT confdeltype, convalidated, pg_get_constraintdef(oid) AS definition "
            "  FROM pg_constraint "
            " WHERE conrelid = 'evidence_object_purge'::regclass AND contype = 'f' "
            "   AND confrelid = 'evidence_artifact'::regclass"
        ).fetchone()
        keys = [str(row["conname"]) for row in conn.execute(_COMPOSITE_ARTIFACT_KEYS).fetchall()]
    assert key is not None
    assert "(artifact_id, workspace_id)" in str(key["definition"])
    assert key["confdeltype"] == "r"
    assert bool(key["convalidated"])
    # Still exactly one composite key, and still 0009's. A duplicate costs an index write on every
    # insert of evidence_artifact and buys nothing.
    assert keys == ["evidence_artifact_id_workspace_id_key"], keys


def test_the_navigator_checkpoints_from_an_earlier_migration_are_still_correct(
    disposable: str,
) -> None:
    """Migration 0019's effect, kept as its own case now that it is no longer the newest.

    A migration test that only ever covered the tip would stop exercising every earlier change the
    moment another one landed -- which is precisely when a regression in one of them would ship.
    """
    migrate(disposable)
    with connect(disposable) as conn:
        table = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            " WHERE table_schema = 'public' AND table_name = 'navigator_planning_checkpoint'"
        ).fetchone()
        ordered_index = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'navigator_planning_checkpoint' "
            "AND indexname = 'navigator_planning_checkpoint_attempt_order'"
        ).fetchone()
        shape = conn.execute(
            "SELECT 1 FROM pg_constraint "
            "WHERE conrelid = 'navigator_planning_checkpoint'::regclass "
            "AND conname = 'navigator_checkpoint_shape'"
        ).fetchone()
        forced = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            " WHERE relname = 'navigator_planning_checkpoint'"
        ).fetchone()
    assert table is not None
    assert ordered_index is not None
    assert shape is not None
    assert forced is not None
    assert bool(forced["relrowsecurity"]) and bool(forced["relforcerowsecurity"])


def test_the_object_purge_queue_from_the_previous_migration_remains_correct(
    disposable: str,
) -> None:
    migrate(disposable)
    with connect(disposable) as conn:
        pending_index = conn.execute(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'evidence_object_purge' "
            "AND indexname = 'evidence_object_purge_pending'"
        ).fetchone()
        unique_key = conn.execute(
            "SELECT 1 FROM pg_constraint WHERE conrelid = 'evidence_object_purge'::regclass "
            "AND contype = 'u' AND pg_get_constraintdef(oid) "
            "LIKE '%(deletion_id, object_key)%'"
        ).fetchone()
        forced = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'evidence_object_purge'"
        ).fetchone()
        restricted = conn.execute(
            "SELECT confdeltype, convalidated FROM pg_constraint "
            "WHERE conrelid = 'evidence_deletion'::regclass AND contype = 'f' "
            "AND confrelid = 'run'::regclass"
        ).fetchone()
    assert pending_index is not None
    assert "purged_at IS NULL" in str(pending_index["indexdef"])
    assert unique_key is not None
    assert forced is not None
    assert bool(forced["relrowsecurity"]) and bool(forced["relforcerowsecurity"])
    assert restricted is not None and restricted["confdeltype"] == "r"
    assert bool(restricted["convalidated"])


def test_the_deletion_record_from_an_earlier_migration_is_still_correct(
    disposable: str,
) -> None:
    """Migration 0017's effect, kept as its own case now that it is no longer the newest.

    A migration test that only ever covered the tip would stop exercising every earlier change the
    moment another one landed -- which is precisely when a regression in one of them would ship.
    """
    migrate(disposable)
    with connect(disposable) as conn:
        table = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            " WHERE table_schema = 'public' AND table_name = 'evidence_deletion'"
        ).fetchone()
        reason_guard = conn.execute(
            "SELECT 1 FROM pg_constraint WHERE conrelid = 'evidence_deletion'::regclass "
            "   AND pg_get_constraintdef(oid) LIKE '%btrim(reason)%'"
        ).fetchone()
        forced = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            " WHERE relname = 'evidence_deletion'"
        ).fetchone()
    assert table is not None
    assert reason_guard is not None, "a deletion could be recorded with no stated reason"
    assert forced is not None
    # A deletion record readable across tenants would disclose what another workspace removed, why,
    # and who asked for it.
    assert bool(forced["relrowsecurity"]) and bool(forced["relforcerowsecurity"])


def test_the_schedule_reapproval_columns_from_an_earlier_migration_are_still_correct(
    disposable: str,
) -> None:
    """Migration 0016's effect, kept as its own case now that it is no longer the newest.

    A migration test that only ever covered the tip would stop exercising every earlier change the
    moment another one landed -- which is precisely when a regression in one of them would ship.
    """
    migrate(disposable)
    with connect(disposable) as conn:
        columns = conn.execute(
            "SELECT count(*) AS n FROM information_schema.columns "
            " WHERE table_name = 'schedule' AND column_name IN ('reapproved_at','reapproved_by')"
        ).fetchone()
        constraint = conn.execute(
            "SELECT 1 FROM pg_constraint WHERE conname = 'reapproval_is_attributable'"
        ).fetchone()
    assert columns is not None and int(columns["n"]) == 2
    assert constraint is not None


def test_the_grant_revalidation_column_from_an_earlier_migration_is_still_correct(
    disposable: str,
) -> None:
    """Migration 0015's effect, kept as its own case now that it is no longer the newest.

    NOT NULL DEFAULT false: an existing grant keeps working, because "a restore brought you back and
    nobody has confirmed you" is not true of a grant that has been live all along.
    """
    migrate(disposable)
    with connect(disposable) as conn:
        column = conn.execute(
            "SELECT column_default, is_nullable FROM information_schema.columns "
            " WHERE table_name = 'execution_grant' AND column_name = 'revalidation_required'"
        ).fetchone()
    assert column is not None
    assert column["is_nullable"] == "NO"
    assert "false" in str(column["column_default"])


def test_an_older_release_reason_survives_and_a_nonsense_one_is_still_refused(
    disposable: str,
) -> None:
    """Migration 0014's widening, kept as its own case now that it is no longer the newest.

    A migration test that only ever covered the tip would stop exercising every earlier change the
    moment another one landed, which is precisely when a regression in one of them would ship.
    """
    migrate(disposable)
    assert _seed_released_lease(disposable, reason="RESTORED_DATABASE")
    assert _seed_released_lease(disposable, reason="OPERATOR_RESET")

    # Widened to a named set, not to anything.
    with pytest.raises(psycopg.errors.CheckViolation):
        _seed_released_lease(disposable, reason="TIDIED_UP")


def test_a_schema_ahead_of_this_build_is_refused_rather_than_downgraded(disposable: str) -> None:
    migrate(disposable)
    with connect(disposable) as conn:
        conn.execute("INSERT INTO schema_migration (name) VALUES ('0099_from_the_future.sql')")
        conn.commit()
        ok, detail = restore.restore_is_forward_compatible(conn, expected=expected_migrations())
    assert ok is False
    assert "newer release" in detail
    assert "code rollback does not reverse a data migration" in detail


def _seed_released_lease(database_url: str, *, reason: str) -> str:
    """A workspace, a runner, a run, an attempt and a released lease. Enough to exercise the check.

    Written through an elevated connection because the disposable database has no application role
    and this is testing schema rather than isolation.
    """
    from accessforge_persistence import runs

    lease_id = str(uuid.uuid4())
    with connect(database_url) as conn:
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
        existing = conn.execute("SELECT 1 FROM workspace WHERE id = %s", (WS,)).fetchone()
        if existing is None:
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Forward')", (WS,))
        runner_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO runner (id, workspace_id, name, status, session_key, platform, device_id,
                                interactive_session_id, console, profile_digest, profile,
                                lease_epoch)
            VALUES (%s, %s, %s, 'OFFLINE', %s, 'darwin', 'device', 'session', true, %s,
                    '{"readerName": "VoiceOver"}', 1)
            """,
            (runner_id, WS, f"desk-{runner_id[:8]}", uuid.uuid4().hex * 2, "e" * 64),
        )
        run_id = runs.create_run(conn, workspace_id=WS, manifest_digest="f" * 64)
        attempt = runs.start_attempt(conn, run_id=run_id, workspace_id=WS, lease_epoch=1)
        conn.execute(
            """
            INSERT INTO desktop_lease
                (id, workspace_id, runner_id, session_key, run_id, attempt_id, epoch, deadline_at,
                 released_at, release_reason)
            VALUES (%s, %s, %s, %s, %s, %s, 1, now() + interval '1 hour', now(), %s)
            """,
            (lease_id, WS, runner_id, uuid.uuid4().hex * 2, run_id, attempt, reason),
        )
        conn.commit()
    return lease_id


def _seed_queue(disposable: str) -> None:
    migrate(disposable)
    with connect(disposable) as conn:
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Interrupt')", (WS,))
        outbox.enqueue_job(
            conn,
            workspace_id=WS,
            operation_id=str(uuid.uuid4()),
            kind="finalize",
            reference={"runId": "x"},
        )
        outbox.enqueue_message(
            conn,
            workspace_id=WS,
            operation_id=str(uuid.uuid4()),
            topic="run.finished",
            reference={"runId": "x"},
        )
        conn.commit()


def _kill(conn: psycopg.Connection[dict[str, object]], killer_url: str) -> None:
    """Terminate a backend for real, rather than closing its connection politely.

    A polite close rolls back through the driver, which is the case that already works.
    `pg_terminate_backend` is what a machine losing power does to a session.
    """
    backend = conn.execute("SELECT pg_backend_pid() AS pid").fetchone()
    assert backend is not None
    with connect(killer_url) as killer:
        killer.autocommit = True
        killer.execute("SELECT pg_terminate_backend(%s)", (backend["pid"],))


def test_a_worker_killed_before_committing_its_claim_loses_nothing(disposable: str) -> None:
    """The claim is not durable until it commits, so an abrupt kill returns the job to PENDING.

    This is the outcome, not the assumption: the first version of this test asserted the job stayed
    CLAIMED and failed, because `claim_jobs` leaves the transaction open for the caller to commit
    alongside whatever else the job does. That is the right design -- a claim that committed
    separately from the work would be a claim that can outlive a rollback of the work -- and it
    means a kill in this window costs nothing at all.
    """
    _seed_queue(disposable)

    victim = psycopg.connect(disposable, row_factory=psycopg.rows.dict_row, autocommit=False)
    victim.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
    assert len(outbox.claim_jobs(victim, claimed_by="doomed-worker", limit=5)) == 1
    _kill(victim, disposable)

    with connect(disposable) as conn:
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
        job = conn.execute("SELECT status, claimed_by, attempts FROM job").fetchone()
        assert job is not None
        assert job["status"] == "PENDING"
        assert job["claimed_by"] is None
        # Even the attempt counter rolled back. Nothing observed the dead worker at all.
        assert int(job["attempts"]) == 0
        assert outbox.unpublished_count(conn) == 1


def test_a_worker_killed_after_committing_its_claim_leaves_a_stale_claim(disposable: str) -> None:
    """The window that does cost something, and what makes it recoverable.

    A worker that commits its claim and then dies leaves a row saying a process that no longer
    exists owns this work. It is recoverable *by state* -- the claim carries an expiry, and nothing
    has to remember what the dead process was doing -- which is the property that lets a restore
    reason about it at all.
    """
    _seed_queue(disposable)

    victim = psycopg.connect(disposable, row_factory=psycopg.rows.dict_row, autocommit=False)
    victim.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
    assert len(outbox.claim_jobs(victim, claimed_by="doomed-worker", limit=5)) == 1
    victim.commit()
    _kill(victim, disposable)

    with connect(disposable) as conn:
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
        job = conn.execute("SELECT status, claimed_by, claim_expires_at FROM job").fetchone()
        assert job is not None
        assert job["status"] == "CLAIMED"
        assert job["claimed_by"] == "doomed-worker"
        assert job["claim_expires_at"] is not None
        assert outbox.unpublished_count(conn) == 1


def test_reconciliation_releases_a_claim_left_by_a_killed_worker(disposable: str) -> None:
    """The recovery, end to end: the state a kill leaves is exactly what reconciliation fixes.

    Released rather than deleted. A job is a database operation and re-reading state makes a repeat
    harmless; the claim is what is stale, not the work. Deleting it would lose the work with no
    record that anything was dropped.
    """
    test_a_worker_killed_after_committing_its_claim_leaves_a_stale_claim(disposable)

    with connect(disposable) as conn:
        report = restore.reconcile(conn, operator="interruption-drill")
        conn.commit()
    assert report.jobs_released == 1
    assert report.outbox_messages_suppressed == 1

    with connect(disposable) as conn:
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", (WS,))
        job = conn.execute("SELECT status, claimed_by FROM job").fetchone()
        assert job is not None
        assert job["status"] == "PENDING"
        assert job["claimed_by"] is None
        assert outbox.unpublished_count(conn) == 0
