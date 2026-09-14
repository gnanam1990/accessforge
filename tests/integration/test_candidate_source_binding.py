"""Real PostgreSQL tenant selection and real Git source binding; no build/reader attestation."""

from __future__ import annotations

import http.client
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from accessforge_build_worker.artifacts import read_retained_candidate, retire_expired_candidate
from accessforge_build_worker.candidate_gateway import (
    CSP,
    CandidateEndpointBinding,
    CandidateGateway,
)
from accessforge_build_worker.coordinator import (
    ClaimedCandidate,
    execute_claim,
    prepare_and_claim,
    publish_retained_materialization,
)
from accessforge_build_worker.process import CommandResult, CommandStopped
from accessforge_build_worker.reference_regressions import ReferenceRegressions
from accessforge_build_worker.regression_coordinator import CandidateSession, execute_regressions
from accessforge_build_worker.sandbox import (
    CleanupUnconfirmed,
    DockerSandbox,
    SandboxPolicy,
    SandboxRefused,
    discover_daemon,
)
from accessforge_build_worker.snapshot import SnapshotRefused, SourceFile, SourceSnapshot
from accessforge_build_worker.source_broker import read_persisted_source
from accessforge_build_worker.toolchain import REFERENCE_BUILD_COMMAND
from accessforge_contracts import validate
from accessforge_contracts.reference_fixture import REFERENCE_FIXTURE_DIGEST
from accessforge_domain.authority import AuthorityError
from accessforge_domain.canonical import digest
from accessforge_domain.functional_validation import VALIDATION_SUITE_DIGEST
from accessforge_domain.journeys.assertions import (
    Assertion,
    AssertionKind,
    AssertionSet,
    EvaluationRule,
    UnknownReason,
)
from accessforge_domain.origins import normalize_origin
from accessforge_domain.patch_policy import ProposedChange
from accessforge_domain.states import FindingStatus, Outcome
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import (
    assert_row_level_security_enforced,
    candidate_runs,
    connect,
    evidence,
    fixtures,
    migrate,
    patches,
    projects,
    restore,
    retention,
    reviews,
    runners,
    runs,
    unscoped_connection,
    workspace_connection,
)
from accessforge_persistence import (
    candidate_builds as builds,
)
from accessforge_persistence import candidate_endpoints as endpoints
from accessforge_persistence import candidate_materializations as materializations
from accessforge_persistence import candidate_regressions as regressions
from accessforge_persistence.source_intake import SourceIdentity

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class BoundFixture:
    database: str
    workspace: str
    project: str
    snapshot: str
    repository: Path
    source: SourceSnapshot
    owner: str


@pytest.fixture()
def candidate_store() -> Iterator[evidence.S3ArtifactStore]:
    store = evidence.S3ArtifactStore(
        evidence.S3Settings(
            endpoint_url=os.environ["OBJECT_STORE_ENDPOINT"],
            access_key=os.environ["OBJECT_STORE_ACCESS_KEY"],
            secret_key=os.environ["OBJECT_STORE_SECRET_KEY"],
            bucket=os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
        )
    )
    store.ensure_bucket()
    yield store


@dataclass
class FaultStore:
    """Fault injection around real S3 writes, not a storage substitute."""

    store: evidence.S3ArtifactStore
    binding: BoundFixture
    fault: str
    keys: list[str]

    @property
    def storage_identity(self) -> tuple[str, str]:
        return self.store.storage_identity

    def put_create_only(self, *, key: str, payload: bytes, content_type: str) -> str:
        self.keys.append(key)
        if self.fault == "upload_failure":
            raise evidence.ObjectStoreUnavailable("injected upload outage")
        self.store.put_create_only(key=key, payload=payload, content_type=content_type)
        if self.fault == "tamper":
            self.store.put(key=key, payload=b"x" * len(payload), content_type=content_type)
        if self.fault == "fenced":
            with workspace_connection(self.binding.database, self.binding.workspace) as conn:
                assert builds.fence_expired(conn, now=datetime.now(UTC) + timedelta(hours=1)) == 1
        return key

    def get_bounded(self, *, key: str, max_bytes: int) -> bytes:
        return self.store.get_bounded(key=key, max_bytes=max_bytes)


@pytest.fixture()
def candidate_run_database(test_database_url: str, backup_database_url: str) -> Iterator[str]:
    """Baselines and seed receipts are immutable; dispose the exact owned test database."""
    name = "accessforge_candidate_run_" + uuid.uuid4().hex[:12]
    owner = urlsplit(test_database_url).username
    assert owner is not None
    with connect(_database_at(backup_database_url, "postgres")) as conn:
        conn.autocommit = True
        conn.execute(
            psycopg.sql.SQL("CREATE DATABASE {} OWNER {}").format(
                psycopg.sql.Identifier(name), psycopg.sql.Identifier(owner)
            )
        )
    try:
        yield _database_at(test_database_url, name)
    finally:
        with connect(_database_at(backup_database_url, "postgres")) as conn:
            conn.autocommit = True
            conn.execute(
                psycopg.sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    psycopg.sql.Identifier(name)
                )
            )


@pytest.fixture()
def binding(
    test_database_url: str, tmp_path: Path, request: pytest.FixtureRequest
) -> Iterator[BoundFixture]:
    isolated_terminal = getattr(request, "param", None) in {"reference", "reference-session"}
    if isolated_terminal:
        test_database_url = str(request.getfixturevalue("candidate_run_database"))
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    executable = shutil.which("git")
    assert executable is not None
    source = SourceSnapshot(
        (
            SourceFile("app.py", b"print('owned')\n"),
            SourceFile(
                "build.js",
                b"const fs = require('fs'); "
                b"fs.writeFileSync('/work/out/candidate.txt', fs.readFileSync('app.py'));",
            ),
        )
    )
    if getattr(request, "param", None) in ("reference", "reference-session"):
        # Read exact committed bytes, never a dirty working copy or repository build script.
        root = Path(__file__).resolve().parents[2]

        def committed(*args: str) -> bytes:
            return subprocess.run(  # noqa: S603 - fixed read-only Git operations on owned fixture.
                [executable, "--no-replace-objects", "-C", str(root), *args],
                capture_output=True,
                check=True,
                timeout=10,
                env={
                    "PATH": os.defpath,
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_NO_LAZY_FETCH": "1",
                },
            ).stdout

        revision = committed("rev-parse", "HEAD").decode().strip()
        prefix = "fixtures/reference-app/"
        names = (
            committed("ls-tree", "-r", "--name-only", revision, "--", prefix).decode().splitlines()
        )
        source = SourceSnapshot(
            tuple(
                SourceFile(name.removeprefix(prefix), committed("show", f"{revision}:{name}"))
                for name in names
                if name == prefix + "pyproject.toml" or name.startswith(prefix + "src/")
            )
            + (SourceFile("SOURCE_PROVENANCE.txt", f"{revision}:{prefix}\n".encode()),)
        )
    for file in source.files:
        (tmp_path / file.path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / file.path).write_bytes(file.content)

    def git(*args: str) -> str:
        result = subprocess.run(  # noqa: S603 - owned synthetic repository setup only.
            [executable, "-c", "core.hooksPath=/dev/null", "-C", str(tmp_path), *args],
            capture_output=True,
            check=True,
            timeout=10,
            env={"PATH": os.defpath, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
        )
        return result.stdout.decode().strip()

    git("init", "--object-format=sha1")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    git("add", ".")
    git("commit", "--no-gpg-sign", "-m", "owned source")
    identity = SourceIdentity(git("rev-parse", "HEAD"), source.tree_digest, False)
    workspace, user = str(uuid.uuid4()), str(uuid.uuid4())
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'source-binding')", (workspace,))
        conn.execute(
            "INSERT INTO app_user (id, email) VALUES (%s, %s)", (user, f"{user}@example.test")
        )
    with workspace_connection(test_database_url, workspace) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) VALUES (%s,%s,'OWNER')",
            (workspace, user),
        )
        project = projects.create_project(
            conn,
            workspace_id=workspace,
            name="owned source",
            repository_url="https://example.test/owned.git",
            repository_authorized_by=user,
        )
        snapshot = projects.record_source_snapshot(
            conn,
            workspace_id=workspace,
            project_id=project,
            identity=identity,
            requested_revision="HEAD",
        )
    yield BoundFixture(test_database_url, workspace, project, snapshot, tmp_path, source, user)
    if isolated_terminal:
        return  # The owning fixture drops only this generated disposable database.
    with workspace_connection(test_database_url, workspace) as conn:
        # Exact synthetic test workspace, in FK dependency order. Other tests/data are untouched.
        conn.execute("DELETE FROM candidate_build_attempt WHERE workspace_id = %s", (workspace,))
        conn.execute("DELETE FROM patch_verification WHERE workspace_id = %s", (workspace,))
        conn.execute("DELETE FROM patch_proposal WHERE workspace_id = %s", (workspace,))
    with unscoped_connection(test_database_url) as conn:
        conn.execute("DELETE FROM workspace WHERE id = %s", (workspace,))
        conn.execute("DELETE FROM app_user WHERE id = %s", (user,))


def test_identity_is_loaded_from_the_persisted_workspace_record(binding: BoundFixture) -> None:
    (binding.repository / "app.py").write_text("mutable checkout is not the source record")
    with workspace_connection(binding.database, binding.workspace) as conn:
        result = read_persisted_source(
            conn,
            workspace_id=binding.workspace,
            source_snapshot_id=binding.snapshot,
            repositories={binding.project: binding.repository},
        )
    assert result.source_snapshot_id == binding.snapshot
    assert result.project_id == binding.project
    assert result.workspace_id == binding.workspace
    assert result.bound.archive_digest == binding.source.archive_digest


def test_foreign_workspace_record_is_not_visible_even_with_its_exact_id(
    binding: BoundFixture,
) -> None:
    foreign = str(uuid.uuid4())
    with workspace_connection(binding.database, foreign) as conn:
        with pytest.raises(SnapshotRefused, match="unavailable"):
            read_persisted_source(
                conn,
                workspace_id=binding.workspace,
                source_snapshot_id=binding.snapshot,
                repositories={binding.project: binding.repository},
            )


def test_repository_path_cannot_be_selected_without_operator_mapping(binding: BoundFixture) -> None:
    with workspace_connection(binding.database, binding.workspace) as conn:
        with pytest.raises(SnapshotRefused, match="operator-provisioned"):
            read_persisted_source(
                conn,
                workspace_id=binding.workspace,
                source_snapshot_id=binding.snapshot,
                repositories={},
            )


def test_revoked_project_cannot_load_source(binding: BoundFixture) -> None:
    with workspace_connection(binding.database, binding.workspace) as conn:
        conn.execute("UPDATE project SET revoked_at = now() WHERE id = %s", (binding.project,))
        with pytest.raises(SnapshotRefused, match="revoked"):
            read_persisted_source(
                conn,
                workspace_id=binding.workspace,
                source_snapshot_id=binding.snapshot,
                repositories={binding.project: binding.repository},
            )


def test_dirty_database_record_is_not_relabelled_as_a_clean_commit(binding: BoundFixture) -> None:
    with workspace_connection(binding.database, binding.workspace) as conn:
        conn.execute(
            "UPDATE source_snapshot SET dirty = true, dirty_path_count = 1 WHERE id = %s",
            (binding.snapshot,),
        )
        with pytest.raises(SnapshotRefused, match="dirty source"):
            read_persisted_source(
                conn,
                workspace_id=binding.workspace,
                source_snapshot_id=binding.snapshot,
                repositories={binding.project: binding.repository},
            )


def _prepare_owned_build(
    binding: BoundFixture,
    *,
    exit_failure: bool = False,
    image: str | None = None,
    command: tuple[str, ...] = ("/usr/local/bin/node", "build.js"),
    change: ProposedChange | None = None,
    canonical_execution: bool = False,
    fresh_fixture: bool = False,
    functional_contract: bool = False,
) -> tuple[ClaimedCandidate, DockerSandbox, tuple[str, ...]]:
    """Actual build pipeline over owned synthetic source, not reference-app or reader proof."""
    image = image or os.environ.get("ACCESSFORGE_SANDBOX_IMAGE")
    change = change or ProposedChange("app.py", "print('repaired')\n")
    if not image:
        pytest.skip("real Docker toolchain not provisioned")
    with workspace_connection(binding.database, binding.workspace) as conn:
        expiry = to_rfc3339_utc(datetime.now(UTC) + timedelta(hours=1))
        env = projects.register_environment(
            conn,
            workspace_id=binding.workspace,
            project_id=binding.project,
            spec=projects.EnvironmentSpec(
                name="synthetic baseline",
                allowed_origins=frozenset({normalize_origin("http://localhost:8000")}),
                fixture_reset_strategy="FRESH_FIXTURE_NONCE" if fresh_fixture else "RESET_ENDPOINT",
                observer_credential_ref="observer",
                reset_credential_ref="reset",
                permitted_effects=frozenset(),
                expires_at=expiry,
            ),
            authorized_by=binding.owner,
        )
        artifact = projects.record_build_artifact(
            conn,
            workspace_id=binding.workspace,
            project_id=binding.project,
            source_snapshot_id=binding.snapshot,
            artifact_digest="a" * 64,
            identity_observable=True,
        )
        reserved_run = str(uuid.uuid4()) if canonical_execution else None
        reserved_approval = str(uuid.uuid4()) if canonical_execution else None
        execution = None
        fixture_digest = policy_digest = "a" * 64
        assertion_digest = "a" * 64
        if canonical_execution:
            journey = str(uuid.uuid4())
            policy: dict[str, Any] = {}
            summary: dict[str, Any] = {}
            if functional_contract:
                reasons = frozenset({UnknownReason.OBSERVATION_MISSING})
                contract = AssertionSet(
                    (
                        Assertion(
                            "completion",
                            AssertionKind.TASK_COMPLETION,
                            "Request persisted",
                            unknown_reasons=reasons,
                        ),
                        Assertion(
                            "validation",
                            AssertionKind.FUNCTIONAL_VALIDATION,
                            "Validation preserved",
                            unknown_reasons=reasons,
                            evaluation_rule=EvaluationRule(
                                "PROTECTED_REFERENCE_VALIDATION",
                                suite_digest=VALIDATION_SUITE_DIGEST,
                            ),
                        ),
                        Assertion(
                            "legacy-validation",
                            AssertionKind.FUNCTIONAL_VALIDATION,
                            "Prose is not a predicate",
                            unknown_reasons=reasons,
                        ),
                    )
                )
                summary["assertionContract"] = contract.canonical_form()
                assertion_digest = digest(contract.canonical_form())
            if fresh_fixture:
                policy = {
                    "fixtureValues": {"email": "fixture@example.test"},
                    "startUrl": "http://localhost:8000/form/FIXTURE",
                    "taskSummary": "Inspect the isolated reference form.",
                    "successCondition": "Stop after inspection.",
                    "allowedActions": ["STOP"],
                    "allowedKeyChords": [],
                    "maxActions": 10,
                    "wallTimeSeconds": 20,
                    "forbiddenObservations": [
                        "DOM",
                        "SELECTORS",
                        "SCREENSHOTS",
                        "SOURCE",
                        "OBSERVER_RECEIPTS",
                        "ASSERTION_EXPECTATIONS",
                    ],
                }
                summary["fixtureContract"] = {
                    "schemaVersion": 2,
                    "templateId": "service-request",
                    "navigatorValues": policy["fixtureValues"],
                    "resetValuesDigest": digest({"variant": "inaccessible"}),
                    "observerConfigDigest": digest({"effect": "CREATE_TEST_REQUEST"}),
                }
                fixture_digest, policy_digest = digest(summary["fixtureContract"]), digest(policy)
            conn.execute(
                "INSERT INTO journey_version(id,workspace_id,project_id,name,platform,"
                "journey_digest,"
                "assertion_set_digest,fixture_digest,navigator_policy_digest,navigator_policy,"
                "reviewer_summary) VALUES (%s,%s,%s,'synthetic metadata','web',repeat('a',64),"
                "%s,%s,%s,%s::jsonb,%s::jsonb)",
                (
                    journey,
                    binding.workspace,
                    binding.project,
                    assertion_digest,
                    fixture_digest,
                    policy_digest,
                    json.dumps(policy),
                    json.dumps(summary),
                ),
            )
            execution = projects.ExecutionInputs(journey, expiry, 10, 20, frozenset())
        seal = projects.seal_run(
            conn,
            workspace_id=binding.workspace,
            project_id=binding.project,
            source_snapshot_id=binding.snapshot,
            build_artifact_id=artifact,
            environment_manifest_id=env,
            run_id=reserved_run,
            authorization_id=reserved_approval,
            execution=execution,
            inputs=projects.SealInputs(
                journey_digest="a" * 64,
                assertion_set_digest=assertion_digest,
                fixture_digest=fixture_digest,
                runner_profile_digest="a" * 64,
                navigator_policy_digest=policy_digest,
                evaluator_version="synthetic-test",
                model_config_digest="b" * 64,
            ),
        )
        run = runs.create_run(
            conn,
            workspace_id=binding.workspace,
            project_id=binding.project,
            manifest_digest=seal.manifest_digest,
            run_id=reserved_run,
            authorization_id=reserved_approval,
        )
        finding = reviews.create_finding(
            conn,
            workspace_id=binding.workspace,
            run_id=run,
            assertion_id="synthetic",
            summary="synthetic failure for build dispatch only",
            status=FindingStatus.REPRODUCED,
            run_outcome=Outcome.FAIL,
            actor_id=binding.owner,
        )
        patches.configure_repair_surface(
            conn,
            workspace_id=binding.workspace,
            project_id=binding.project,
            paths=(change.path,),
            configured_by=binding.owner,
        )
        proposal = patches.propose_patch(
            conn,
            workspace_id=binding.workspace,
            finding_id=finding,
            base_manifest_digest=seal.manifest_digest,
            base_source_digest=binding.source.tree_digest,
            changes=(change,),
            rationale="exercise an owned candidate build",
            proposed_by=binding.owner,
        )
        patches.approve_patch(
            conn,
            workspace_id=binding.workspace,
            patch_id=proposal.patch_id,
            actor_id=binding.owner,
            expires_at=expiry,
        )
    endpoint = os.environ.get("ACCESSFORGE_SANDBOX_ENDPOINT")
    assert endpoint is not None, "explicit local Docker endpoint required"
    sandbox = DockerSandbox(
        SandboxPolicy(image=image, wall_seconds=60), daemon=discover_daemon(endpoint)
    )
    if exit_failure:
        command = ("/usr/local/bin/node", "-e", "console.log('PASS'); process.exit(23)")
    claimed = prepare_and_claim(
        binding.database,
        workspace_id=binding.workspace,
        patch_id=proposal.patch_id,
        repositories={binding.project: binding.repository},
        sandbox=sandbox,
        command=command,
    )
    return claimed, sandbox, command


@pytest.mark.sandbox
@pytest.mark.parametrize("binding", ["reference"], indirect=True)
@pytest.mark.parametrize(
    ("sabotage", "failure", "presentation_repair"),
    [
        ("", None, False),
        ("", None, True),
        ("", "cancelled", False),
        ("", "fenced", False),
        ("", "endpoint-fenced", False),
        ("", "deployed-artifact-changed", False),
        ("", "endpoint-bind-failed", False),
        ("", "endpoint-before-admission-fenced", False),
        (
            "\nfrom . import fixture_definition as _fixture\n"
            "_fixture.REFERENCE_FIXTURE_DIGEST = '0' * 64\n",
            "fixture_definition_identity",
            False,
        ),
        (
            "\nfrom . import validation as _validation\n"
            "_validation.validate_service_request = lambda **values: []\n",
            "reject_invalid_email",
            False,
        ),
        (
            "\nimport secrets\nsecrets.compare_digest = lambda *args: True\n",
            "fixture_creation_authorization",
            False,
        ),
        (
            "\nfrom . import validation as _validation\nimport os, psycopg\n"
            "_original_validate = _validation.validate_service_request\n"
            "def _write_and_reject(**values):\n"
            "    with psycopg.connect(os.environ['REFAPP_DATABASE_URL']) as conn:\n"
            '        conn.execute("INSERT INTO service_request '
            "(id,fixture_nonce,full_name,email,category,description) "
            "SELECT '11111111-1111-4111-8111-111111111111'::uuid,nonce,%s,%s,%s,%s "
            'FROM fixture_instance", tuple(values[key] for key in '
            "('full_name','email','category','description')))\n"
            "    return _original_validate(**values)\n"
            "_validation.validate_service_request = _write_and_reject\n",
            "no_invalid_write_email",
            False,
        ),
    ],
    ids=[
        "healthy",
        "presentation-repaired",
        "cancelled",
        "fenced",
        "endpoint-fenced",
        "deployed-artifact-changed",
        "endpoint-bind-failed",
        "endpoint-before-admission-fenced",
        "fixture-declaration-tampered",
        "validation-removed",
        "authorization-bypassed",
        "writes-despite-error",
    ],
)
def test_actual_reference_app_wheel_is_built_retained_and_imported_in_isolation(
    binding: BoundFixture,
    isolated_archives: tuple[IsolatedArchiveStore, IsolatedArchiveStore],
    sabotage: str,
    failure: str | None,
    presentation_repair: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = os.environ.get("ACCESSFORGE_REFERENCE_TOOLCHAIN")
    if not image:
        if os.environ.get("CI"):
            pytest.fail("the real reference toolchain must be provisioned in CI")
        pytest.skip("real reference toolchain not provisioned")
    path = "src/reference_app/templates.py"
    original = next(file.content for file in binding.source.files if file.path == path)
    # Synthetic approved changes exercise packaging and backend regression, not actual AT proof.
    content = original.decode() + "\n# Owned candidate packaging probe.\n" + sabotage
    if presentation_repair:
        assert 'accessible = variant == "accessible"' in content
        content = content.replace('accessible = variant == "accessible"', "accessible = True")
    claimed, sandbox, command = _prepare_owned_build(
        binding,
        image=image,
        command=REFERENCE_BUILD_COMMAND,
        change=ProposedChange(path, content),
    )
    store = isolated_archives[0].store
    built = execute_claim(
        binding.database,
        workspace_id=binding.workspace,
        claimed=claimed,
        sandbox=sandbox,
        command=command,
        store=store,
    )
    retained = read_retained_candidate(
        binding.database,
        workspace_id=binding.workspace,
        build_id=claimed.claim.build_id,
        store=store,
    )
    assert retained.archive() == built.artifact.archive()
    assert built.cleanup_confirmed and built.image_id == image
    assert len(built.artifact.files) == 1
    wheel = built.artifact.files[0]
    assert wheel.path == "out/accessforge_reference_app-0.0.0-py3-none-any.whl"
    expected = {
        file.path.removeprefix("src/"): file.content
        for file in binding.source.files
        if file.path.startswith("src/")
    }
    expected["reference_app/templates.py"] = content.encode()
    # Bounded inspection only: never extract/import target code into this host process.
    with zipfile.ZipFile(io.BytesIO(wheel.content)) as archive:
        entries = archive.infolist()
        assert len(entries) == len(expected) + 3
        assert len({entry.filename for entry in entries}) == len(entries)
        assert sum(entry.file_size for entry in entries) < 1024 * 1024
        metadata = "accessforge_reference_app-0.0.0.dist-info/"
        assert set(archive.namelist()) == set(expected) | {
            metadata + name for name in ("METADATA", "WHEEL", "RECORD")
        }
        for name, payload in expected.items():
            assert archive.read(name) == payload
        assert b"Name: accessforge-reference-app\n" in archive.read(metadata + "METADATA")
    # A fresh contained process uses only the captured wheel and preinstalled runtime.
    # This import smoke is NOT protected HTTP/database regression or actual reader proof.
    smoke = sandbox.build(
        built.artifact,
        command=(
            "/usr/local/bin/python",
            "-I",
            "-c",
            "import sys,pathlib; sys.path.insert(0, '/work/src/" + wheel.path + "'); "
            "import reference_app.app, reference_app.validation; "
            "assert reference_app.app.__file__.startswith('/work/src/'); "
            + (
                "from reference_app.templates import render_form, template_digest; "
                "assert render_form(nonce='probe', variant='inaccessible') == "
                "render_form(nonce='probe', variant='accessible'); "
                "assert template_digest('inaccessible') == template_digest('accessible'); "
                if presentation_repair
                else ""
            )
            + "pathlib.Path('/work/out/import-ok.txt').write_text('imported captured wheel')",
        ),
    )
    assert smoke.artifact.files[0].content == b"imported captured wheel"
    runner = ReferenceRegressions(image=image, daemon=sandbox.daemon)
    if failure == "fenced":
        record_created = regressions.created

        def fence_after_creation(
            conn: psycopg.Connection[dict[str, Any]],
            *,
            claim: regressions.RegressionClaim,
            role: str,
            container_id: str,
            image_id: str,
        ) -> None:
            record_created(
                conn, claim=claim, role=role, container_id=container_id, image_id=image_id
            )
            assert regressions.fence_expired(conn, now=datetime.now(UTC) + timedelta(hours=1)) == 1

        monkeypatch.setattr(regressions, "created", fence_after_creation)
        with pytest.raises(builds.BuildClaimRefused, match="fenced"):
            execute_regressions(
                binding.database,
                workspace_id=binding.workspace,
                build_id=claimed.claim.build_id,
                runner=runner,
                store=store,
            )
        with workspace_connection(binding.database, binding.workspace) as conn:
            assert conn.execute(
                "SELECT state,epoch FROM candidate_regression_attempt WHERE build_id=%s",
                (claimed.claim.build_id,),
            ).fetchone() == {"state": "UNKNOWN", "epoch": 2}
            assert conn.execute(
                "SELECT state FROM candidate_regression_process WHERE attempt_id="
                "(SELECT id FROM candidate_regression_attempt WHERE build_id=%s)",
                (claimed.claim.build_id,),
            ).fetchall() == [{"state": "REMOVED"}]
        return
    if failure == "cancelled":
        checked = runner.sandbox._checked
        stop_requested = False

        def cancel_after_pg(*args: str, **kwargs: Any) -> CommandResult:
            nonlocal stop_requested
            result = checked(*args, **kwargs)
            if any(arg.endswith("/pg_ctl") for arg in args):
                stop_requested = True
            return result

        monkeypatch.setattr(runner.sandbox, "_checked", cancel_after_pg)
        with pytest.raises(CommandStopped, match="cancelled"):
            execute_regressions(
                binding.database,
                workspace_id=binding.workspace,
                build_id=claimed.claim.build_id,
                runner=runner,
                store=store,
                cancelled=lambda: stop_requested,
            )
        with workspace_connection(binding.database, binding.workspace) as conn:
            assert conn.execute(
                "SELECT state,cleanup_confirmed,failure_code FROM candidate_regression_attempt "
                "WHERE build_id=%s",
                (claimed.claim.build_id,),
            ).fetchone() == {
                "state": "FAILED",
                "cleanup_confirmed": True,
                "failure_code": "CANCELLED_CONFIRMED",
            }
            assert conn.execute(
                "SELECT state FROM candidate_regression_process WHERE attempt_id="
                "(SELECT id FROM candidate_regression_attempt WHERE build_id=%s)",
                (claimed.claim.build_id,),
            ).fetchall() == [{"state": "REMOVED"}]
        return
    if failure in {"endpoint-bind-failed", "endpoint-before-admission-fenced"}:
        original_bound = endpoints.bound
        bound_observations: list[dict[str, Any]] = []

        def interrupted_binding(conn: Any, *, claim: Any, receipt: dict[str, Any]) -> None:
            bound_observations.append(receipt)
            if failure == "endpoint-bind-failed":
                raise RuntimeError("injected endpoint binding persistence failure")
            original_bound(conn, claim=claim, receipt=receipt)
            assert regressions.fence_expired(conn, now=datetime.now(UTC) + timedelta(hours=1)) == 1

        monkeypatch.setattr(endpoints, "bound", interrupted_binding)
        expected_error = (
            RuntimeError if failure == "endpoint-bind-failed" else builds.BuildClaimRefused
        )
        with pytest.raises(expected_error):
            execute_regressions(
                binding.database,
                workspace_id=binding.workspace,
                build_id=claimed.claim.build_id,
                runner=runner,
                store=store,
                on_candidate_endpoint=lambda gateway: pytest.fail("uncommitted/fenced admission"),
            )
        assert len(bound_observations) == 1
        with pytest.raises(OSError):
            socket.create_connection(
                ("127.0.0.1", int(bound_observations[0]["origin"].rsplit(":", 1)[1])), timeout=1
            )
        with workspace_connection(binding.database, binding.workspace) as conn:
            assert conn.execute(
                "SELECT state,receipt,cleanup_confirmed FROM candidate_endpoint "
                "WHERE attempt_id=%s",
                (bound_observations[0]["taskId"],),
            ).fetchone() == {
                "state": "CLOSED" if failure == "endpoint-bind-failed" else "UNKNOWN",
                "receipt": None if failure == "endpoint-bind-failed" else bound_observations[0],
                "cleanup_confirmed": True,
            }
            assert conn.execute(
                "SELECT state FROM candidate_regression_attempt WHERE id=%s",
                (bound_observations[0]["taskId"],),
            ).fetchone() == {"state": "FAILED" if failure == "endpoint-bind-failed" else "UNKNOWN"}
        return
    if failure is not None and failure not in {"endpoint-fenced", "deployed-artifact-changed"}:
        with pytest.raises(SandboxRefused, match=failure):
            execute_regressions(
                binding.database,
                workspace_id=binding.workspace,
                build_id=claimed.claim.build_id,
                runner=runner,
                store=store,
            )
        with workspace_connection(binding.database, binding.workspace) as conn:
            record = conn.execute(
                "SELECT state,cleanup_confirmed FROM candidate_regression_attempt "
                "WHERE build_id=%s",
                (claimed.claim.build_id,),
            ).fetchone()
            assert record == {"state": "FAILED", "cleanup_confirmed": True}
        return
    original_endpoint_bound = endpoints.bound

    def verify_binding_guards(conn: Any, *, claim: Any, receipt: dict[str, Any]) -> None:
        # The real listener is reserved, but no request handler can run until this returns.
        for key, value in {
            "artifactDigest": "0" * 64,
            "runtimePolicyDigest": "0" * 64,
            "candidateId": "0" * 64,
            "driverId": "0" * 64,
            "imageId": "sha256:" + "0" * 64,
            "daemonId": "another-daemon",
            "origin": "http://example.test:80",
            "bindingDigest": "0" * 64,
        }.items():
            with pytest.raises((builds.BuildClaimRefused, ValueError)), conn.transaction():
                original_endpoint_bound(conn, claim=claim, receipt={**receipt, key: value})
        original_endpoint_bound(conn, claim=claim, receipt=receipt)
        with pytest.raises(builds.BuildClaimRefused), conn.transaction():
            original_endpoint_bound(conn, claim=claim, receipt=receipt)

    monkeypatch.setattr(endpoints, "bound", verify_binding_guards)
    from accessforge_persistence import candidate_fixture_setups

    original_confirm_fixture = candidate_fixture_setups.confirm

    def verify_initial_fixture(
        conn: Any, *, claim: Any, context_digest: str, application: dict[str, Any]
    ) -> dict[str, Any]:
        intent = candidate_fixture_setups.read(conn, attempt_id=claim.attempt_id)
        assert intent is not None and intent["observation"] is None
        assert intent["contextDigest"] == context_digest
        assert conn.execute("SELECT 1 FROM candidate_endpoint").fetchone() is None
        for key, value in {
            "nonce": "different-reserved-nonce",
            "templateDigest": "0" * 64,
            "effectCount": 1,
            "createdAt": "2000-01-01T00:00:00Z",
        }.items():
            with pytest.raises(builds.BuildClaimRefused), conn.transaction():
                original_confirm_fixture(
                    conn,
                    claim=claim,
                    context_digest=context_digest,
                    application={**application, key: value},
                )
        with pytest.raises(builds.BuildClaimRefused), conn.transaction():
            candidate_fixture_setups.reserve(conn, claim=claim, nonce=application["nonce"])
        return original_confirm_fixture(
            conn, claim=claim, context_digest=context_digest, application=application
        )

    monkeypatch.setattr(candidate_fixture_setups, "confirm", verify_initial_fixture)
    endpoint_receipts: list[dict[str, Any]] = []

    def browser_endpoint_probe(gateway: CandidateGateway) -> None:
        from accessforge_persistence import candidate_observations

        receipt = gateway.receipt()
        observation = gateway.observe_artifact()
        with workspace_connection(binding.database, binding.workspace) as conn:
            initial_fixture = candidate_fixture_setups.read(
                conn, attempt_id=gateway.binding.task_id
            )
            assert initial_fixture is not None and initial_fixture["confirmedAt"] is not None
            assert "/form/" + initial_fixture["context"]["nonce"] == receipt["path"]
            assert initial_fixture["context"]["artifactDigest"] == receipt["artifactDigest"]
            assert initial_fixture["context"]["processes"]["candidate"] == receipt["candidateId"]
            assert initial_fixture["observation"]["application"]["effectCount"] == 0
            assert initial_fixture["observationDigest"] == digest(initial_fixture["observation"])
            for statement in (
                "UPDATE candidate_fixture_reservation SET observation=NULL,"
                "observation_digest=NULL,observed_at=NULL",
                "DELETE FROM candidate_fixture_reservation",
            ):
                with pytest.raises(psycopg.IntegrityError), conn.transaction():
                    conn.execute(statement)
            retained_observations = candidate_observations.list_for_attempt(
                conn, attempt_id=gateway.binding.task_id
            )
            assert len(retained_observations) == 1
            measured = retained_observations[0]
            assert measured["receipt"]["observation"] == observation
            assert measured["receipt"]["endpointBindingDigest"] == receipt["bindingDigest"]
            assert measured["receipt"]["runtimePolicyDigest"] == runner.policy_digest()
            assert measured["receipt"]["runId"] is None
            assert measured["receipt"]["leaseId"] is None
            assert measured["receiptDigest"] == digest(measured["receipt"])
            lineage = measured["receipt"]["sourceLineage"]
            assert lineage["sourceTreeDigest"] == claimed.candidate.source.tree_digest
            assert lineage["sourceArchiveDigest"] == claimed.candidate.source.archive_digest
            assert lineage["artifactDigest"] == retained.archive_digest
            assert lineage["buildId"] == claimed.claim.build_id
            assert lineage["imageId"] == image
            assert lineage["meaning"] == "CAPTURED_BUILD_INPUT_LINEAGE_NOT_RUNTIME_SOURCE_READ"
            for statement in (
                "UPDATE candidate_artifact_observation SET ordinal=99",
                "DELETE FROM candidate_artifact_observation",
            ):
                with pytest.raises(psycopg.IntegrityError), conn.transaction():
                    conn.execute(statement)
        with workspace_connection(binding.database, str(uuid.uuid4())) as conn:
            assert candidate_fixture_setups.read(conn, attempt_id=gateway.binding.task_id) is None
            assert (
                candidate_observations.list_for_attempt(conn, attempt_id=gateway.binding.task_id)
                == []
            )
        assert observation["artifactDigest"] == retained.archive_digest
        assert observation["artifactTreeDigest"] == retained.tree_digest
        assert observation["candidateId"] == gateway.binding.candidate_id
        assert observation["meaning"] == "DEPLOYED_FILESYSTEM_MEASUREMENT_NOT_EXECUTION_ATTESTATION"
        if failure == "deployed-artifact-changed":
            # CI-owned container only: change the deployed file mode, not the retained archive.
            runner.sandbox._checked(
                "exec",
                gateway.binding.candidate_id,
                "/bin/chmod",
                "755",
                "/work/src/out/accessforge_reference_app-0.0.0-py3-none-any.whl",
                deadline=time.monotonic() + 5,
            )
            gateway.observe_artifact()
            pytest.fail("changed deployed artifact was accepted")
        assert receipt["artifactDigest"] == retained.archive_digest
        assert receipt["imageId"] == image
        assert receipt["daemonId"] == sandbox.daemon.daemon_id
        assert receipt["runtimePolicyDigest"] == runner.policy_digest()
        endpoint_receipts.append(receipt)
        with workspace_connection(binding.database, binding.workspace) as conn:
            assert conn.execute(
                "SELECT state,receipt,cleanup_confirmed FROM candidate_endpoint "
                "WHERE attempt_id=%s",
                (receipt["taskId"],),
            ).fetchone() == {"state": "BOUND", "receipt": receipt, "cleanup_confirmed": False}
            assert conn.execute(
                "SELECT endpoint_required FROM candidate_regression_attempt WHERE id=%s",
                (receipt["taskId"],),
            ).fetchone() == {"endpoint_required": True}
            with pytest.raises(psycopg.IntegrityError), conn.transaction():
                conn.execute(
                    "UPDATE candidate_regression_attempt SET endpoint_required=false WHERE id=%s",
                    (receipt["taskId"],),
                )
            with pytest.raises(psycopg.IntegrityError), conn.transaction():
                conn.execute(
                    "UPDATE candidate_endpoint SET origin='http://127.0.0.1:1' WHERE attempt_id=%s",
                    (receipt["taskId"],),
                )
        with workspace_connection(binding.database, str(uuid.uuid4())) as conn:
            assert conn.execute("SELECT * FROM candidate_endpoint").fetchall() == []
        connection = http.client.HTTPConnection(gateway.origin.removeprefix("http://"), timeout=10)
        try:
            connection.request("GET", gateway.path)
            response = connection.getresponse()
            assert response.status == 200
            assert response.getheader("Content-Security-Policy") == CSP
            assert b"Service request" in response.read()
            connection.request(
                "POST",
                gateway.path,
                body="email=not-an-email",
                headers={
                    "Origin": gateway.origin,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            response = connection.getresponse()
            assert response.status == 422
            page = response.read()
            assert (b'aria-invalid="true"' in page) is presentation_repair
            connection.request("POST", "/api/_test/reset", headers={"Origin": gateway.origin})
            response = connection.getresponse()
            assert response.status == 403
            response.read()
            if failure == "endpoint-fenced":
                with workspace_connection(binding.database, binding.workspace) as conn:
                    assert (
                        regressions.fence_expired(conn, now=datetime.now(UTC) + timedelta(hours=1))
                        == 1
                    )
                original_checked = runner.sandbox._checked

                def no_dispatch_after_fence(*args: str, **kwargs: Any) -> CommandResult:
                    assert args[0] != "exec", "fenced browser request reached a container"
                    return original_checked(*args, **kwargs)

                with monkeypatch.context() as context:
                    context.setattr(runner.sandbox, "_checked", no_dispatch_after_fence)
                    connection.request("GET", gateway.path)
                    response = connection.getresponse()
                    assert response.status == 502
                    response.read()
        finally:
            connection.close()

    if failure == "deployed-artifact-changed":
        with pytest.raises(SandboxRefused, match="artifact measurement unavailable"):
            execute_regressions(
                binding.database,
                workspace_id=binding.workspace,
                build_id=claimed.claim.build_id,
                runner=runner,
                store=store,
                on_candidate_endpoint=browser_endpoint_probe,
            )
        with workspace_connection(binding.database, binding.workspace) as conn:
            assert conn.execute(
                "SELECT state,cleanup_confirmed FROM candidate_regression_attempt "
                "WHERE build_id=%s",
                (claimed.claim.build_id,),
            ).fetchone() == {"state": "FAILED", "cleanup_confirmed": True}
        return
    if failure == "endpoint-fenced":
        with pytest.raises(builds.BuildClaimRefused):
            execute_regressions(
                binding.database,
                workspace_id=binding.workspace,
                build_id=claimed.claim.build_id,
                runner=runner,
                store=store,
                on_candidate_endpoint=browser_endpoint_probe,
            )
        with workspace_connection(binding.database, binding.workspace) as conn:
            assert conn.execute(
                "SELECT state,epoch FROM candidate_regression_attempt WHERE build_id=%s",
                (claimed.claim.build_id,),
            ).fetchone() == {"state": "UNKNOWN", "epoch": 2}
            assert conn.execute(
                "SELECT state,receipt,cleanup_confirmed,closed_at IS NOT NULL AS closed "
                "FROM candidate_endpoint WHERE attempt_id=%s",
                (endpoint_receipts[0]["taskId"],),
            ).fetchone() == {
                "state": "UNKNOWN",
                "receipt": endpoint_receipts[0],
                "cleanup_confirmed": True,
                "closed": True,
            }
            assert (
                conn.execute(
                    "SELECT state FROM candidate_regression_process WHERE attempt_id="
                    "(SELECT id FROM candidate_regression_attempt WHERE build_id=%s)",
                    (claimed.claim.build_id,),
                ).fetchall()
                == [{"state": "REMOVED"}] * 3
            )
        return

    regression = execute_regressions(
        binding.database,
        workspace_id=binding.workspace,
        build_id=claimed.claim.build_id,
        runner=runner,
        store=store,
        on_candidate_endpoint=browser_endpoint_probe,
    )
    assert len(endpoint_receipts) == 1
    from accessforge_persistence import candidate_observations

    with workspace_connection(binding.database, binding.workspace) as conn:
        observations = candidate_observations.list_for_attempt(conn, attempt_id=regression.task_id)
        assert len(observations) >= 5
        assert [row["ordinal"] for row in observations] == list(range(1, len(observations) + 1))
    assert endpoint_receipts[0]["taskId"] == regression.task_id
    assert regression.artifact_digest == retained.archive_digest
    assert "exact_independent_database_receipt" in regression.checks
    assert "fixture_definition_identity" in regression.checks
    assert "independent_fixture_definition" in regression.checks
    assert len(regression.containers) == 4
    assert "durable_receipt_after_stop" in regression.checks
    with workspace_connection(binding.database, binding.workspace) as conn:
        record = conn.execute(
            "SELECT state,artifact_digest,checks FROM candidate_regression_attempt WHERE id=%s",
            (regression.task_id,),
        ).fetchone()
        assert record is not None and record["state"] == "PASSED"
        assert record["artifact_digest"] == retained.archive_digest
        assert tuple(record["checks"]) == regression.checks
        assert conn.execute(
            "SELECT state,receipt,cleanup_confirmed FROM candidate_endpoint WHERE attempt_id=%s",
            (regression.task_id,),
        ).fetchone() == {
            "state": "CLOSED",
            "receipt": endpoint_receipts[0],
            "cleanup_confirmed": True,
        }
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute(
                "UPDATE candidate_endpoint SET state='BOUND' WHERE attempt_id=%s",
                (regression.task_id,),
            )
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute(
                "UPDATE candidate_regression_attempt SET state='UNKNOWN' WHERE id=%s",
                (regression.task_id,),
            )
    with pytest.raises(builds.BuildClaimRefused, match="already exists"):
        execute_regressions(
            binding.database,
            workspace_id=binding.workspace,
            build_id=claimed.claim.build_id,
            runner=runner,
            store=store,
        )
    # Cancel only after an actual PostgreSQL process was started, then verify exact cleanup.
    original_checked = runner.sandbox._checked
    stop = False
    created: list[str] = []

    def observed(*args: str, **kwargs: Any) -> CommandResult:
        nonlocal stop
        result = original_checked(*args, **kwargs)
        if args[:2] == ("container", "create"):
            created.append(result.stdout.decode().strip())
        if any(arg.endswith("/pg_ctl") for arg in args):
            stop = True
        return result

    with monkeypatch.context() as context:
        context.setattr(runner.sandbox, "_checked", observed)
        with pytest.raises(CommandStopped, match="cancelled"):
            runner.run(retained, cancelled=lambda: stop)
    assert len(created) == 1
    for container in created:
        remaining = original_checked(
            "container",
            "ls",
            "-a",
            "--no-trunc",
            "--filter",
            f"id={container}",
            "--format",
            "{{.ID}}",
            deadline=time.monotonic() + 10,
        )
        assert not remaining.stdout.strip()
    with workspace_connection(binding.database, binding.workspace) as conn:
        row = conn.execute(
            "SELECT state FROM candidate_build_attempt WHERE id=%s", (claimed.claim.build_id,)
        ).fetchone()
        assert row is not None and row["state"] == "BUILT"


@dataclass(frozen=True)
class IsolatedArchiveStore:
    settings: evidence.S3Settings
    store: evidence.S3ArtifactStore


@pytest.mark.sandbox
@pytest.mark.parametrize("binding", ["reference-session"], indirect=True)
@pytest.mark.parametrize(
    "reader_exit",
    ["released", "returned-active", "raised-active", "fresh-released", "fresh-preview"],
)
def test_live_candidate_session_binds_exact_seal_fresh_fixture_and_first_lease(
    binding: BoundFixture,
    isolated_archives: tuple[IsolatedArchiveStore, IsolatedArchiveStore],
    reader_exit: str,
) -> None:
    """Actual candidate/runtime, synthetic baseline/desktop metadata; NOT actual-reader proof."""
    image = os.environ.get("ACCESSFORGE_REFERENCE_TOOLCHAIN")
    assert image is not None
    fresh = reader_exit.startswith("fresh-")
    path = "src/reference_app/templates.py"
    original = next(file.content for file in binding.source.files if file.path == path)
    claimed, sandbox, command = _prepare_owned_build(
        binding,
        image=image,
        command=REFERENCE_BUILD_COMMAND,
        change=ProposedChange(path, original.decode() + "\n# Candidate session binding probe.\n"),
        canonical_execution=True,
        fresh_fixture=fresh,
        functional_contract=reader_exit == "fresh-released",
    )
    store = isolated_archives[0].store
    execute_claim(
        binding.database,
        workspace_id=binding.workspace,
        claimed=claimed,
        sandbox=sandbox,
        command=command,
        store=store,
    )
    with workspace_connection(binding.database, binding.workspace) as conn:
        base = builds._baseline(conn, claimed.claim.patch_id, binding.workspace)
        baseline_id = str(base["baseline_run_id"])
        baseline_fixture = fixtures.create_instance(
            conn,
            workspace_id=binding.workspace,
            run_id=baseline_id,
            template_id="service-request" if fresh else "reference-service-request",
            template_digest=REFERENCE_FIXTURE_DIGEST,
            navigator_values={"email": "fixture@example.test"},
            observer_config={"effect": "CREATE_TEST_REQUEST"},
        )
    prepared: list[dict[str, Any]] = []

    def controller(session: CandidateSession) -> None:
        gateway = session.gateway
        if reader_exit == "fresh-preview":
            gateway.observe_artifact()  # Actual unbound preview history, not fabricated DB rows.
        with workspace_connection(binding.database, binding.workspace) as conn:
            endpoint = conn.execute(
                "SELECT * FROM candidate_endpoint WHERE attempt_id=%s", (gateway.binding.task_id,)
            ).fetchone()
            assert endpoint is not None
            base_env = conn.execute(
                "SELECT * FROM environment_manifest WHERE id=%s", (base["environment_manifest_id"],)
            ).fetchone()
            assert base_env is not None
            spec = projects.EnvironmentSpec(
                name=base_env["name"],
                allowed_origins=frozenset({normalize_origin(gateway.origin)}),
                fixture_reset_strategy=base_env["fixture_reset_strategy"],
                observer_credential_ref=base_env["observer_credential_ref"],
                reset_credential_ref=base_env["reset_credential_ref"],
                permitted_effects=frozenset(base_env["permitted_effects"]),
                expires_at=to_rfc3339_utc(endpoint["expires_at"] - timedelta(seconds=1)),
            )
            environment_id = projects.register_environment(
                conn,
                workspace_id=binding.workspace,
                project_id=binding.project,
                spec=spec,
                authorized_by=binding.owner,
            )
        with pytest.raises(builds.BuildClaimRefused, match="baseline"):
            session.prepare_run(environment_id)
        with workspace_connection(binding.database, binding.workspace) as conn:
            # Synthetic control-plane baseline facts, never labelled as actual AT observations.
            conn.execute(
                "UPDATE run SET status='COMPLETED',outcome='FAIL',execution_began=true WHERE id=%s",
                (baseline_id,),
            )
            attempt = runs.start_attempt(
                conn, workspace_id=binding.workspace, run_id=baseline_id, lease_epoch=1
            )
            conn.execute(
                "INSERT INTO producer_stream(workspace_id,run_id,attempt_id,producer_id,"
                "admitted_through,closed_at_sequence,closed_at) "
                "VALUES (%s,%s,%s,'synthetic-baseline',1,1,clock_timestamp())",
                (binding.workspace, baseline_id, attempt),
            )
            wrong = projects.register_environment(
                conn,
                workspace_id=binding.workspace,
                project_id=binding.project,
                spec=replace(
                    spec, allowed_origins=frozenset({normalize_origin("http://127.0.0.1:1")})
                ),
                authorized_by=binding.owner,
            )
        with pytest.raises(builds.BuildClaimRefused, match="endpoint"):
            session.prepare_run(wrong)
        result = session.prepare_run(environment_id)
        prepared.append(result)
        run_id = str(result["run_id"])
        assert result["fixture_nonce"] != baseline_fixture.nonce
        assert result["fixture_nonce"] == gateway.path.removeprefix("/form/")
        with pytest.raises(builds.BuildClaimRefused, match="already"):
            session.prepare_run(environment_id)
        with workspace_connection(binding.database, str(uuid.uuid4())) as conn:
            assert conn.execute("SELECT * FROM candidate_run_binding").fetchall() == []
        with workspace_connection(binding.database, binding.workspace) as conn:
            assert candidate_runs.assert_live(conn, run_id=run_id) is not None
            sealed = conn.execute(
                "SELECT * FROM sealed_manifest WHERE id=%s", (result["sealed_manifest_id"],)
            ).fetchone()
            assert sealed is not None
            for field in projects.SealInputs.__dataclass_fields__:
                assert sealed[field] == base[field]
            material = conn.execute(
                "SELECT * FROM candidate_materialization WHERE build_id=%s",
                (claimed.claim.build_id,),
            ).fetchone()
            assert material is not None
            assert sealed["source_snapshot_id"] == material["source_snapshot_id"]
            assert sealed["build_artifact_id"] == material["build_artifact_id"]
            manifest = sealed["canonical_manifest"]
            validate("run-manifest.schema.json", manifest)
            assert digest(manifest) == sealed["manifest_digest"]
            assert manifest["runId"] == run_id
            assert manifest["authorizationId"] == str(sealed["authorization_id"])
            assert manifest["authorizationId"] != base["canonical_manifest"]["authorizationId"]
            assert manifest["journeyVersionId"] == base["canonical_manifest"]["journeyVersionId"]
            assert manifest["actionBudget"] == 10
            assert manifest["wallTimeBudgetSeconds"] == 20
            assert conn.execute(
                "SELECT status,authorization_id FROM run WHERE id=%s", (run_id,)
            ).fetchone() == {"status": "QUEUED", "authorization_id": sealed["authorization_id"]}
            assert (
                conn.execute(
                    "SELECT 1 FROM approval WHERE id=%s", (sealed["authorization_id"],)
                ).fetchone()
                is None
            )
            for statement, value in (
                ("UPDATE run SET authorization_id=%s WHERE id=%s", str(uuid.uuid4())),
                ("UPDATE run SET manifest_digest=%s WHERE id=%s", "f" * 64),
            ):
                with pytest.raises(psycopg.IntegrityError), conn.transaction():
                    conn.execute(statement, (value, run_id))
            with pytest.raises(psycopg.IntegrityError), conn.transaction():
                runs.create_run(
                    conn,
                    workspace_id=binding.workspace,
                    project_id=binding.project,
                    manifest_digest=sealed["manifest_digest"],
                    authorization_id=str(sealed["authorization_id"]),
                )
            with pytest.raises(psycopg.IntegrityError), conn.transaction():
                conn.execute(
                    "UPDATE run_fixture_instance SET observer_config='{}'::jsonb WHERE run_id=%s",
                    (run_id,),
                )
            with pytest.raises(psycopg.IntegrityError), conn.transaction():
                conn.execute(
                    "UPDATE patch_verification SET candidate_run_id=%s WHERE id=%s",
                    (baseline_id, claimed.claim.verification_id),
                )
            with pytest.raises(patches.VerificationError), conn.transaction():
                patches.conclude_verification(
                    conn,
                    workspace_id=binding.workspace,
                    verification_id=claimed.claim.verification_id,
                    candidate_run_id=baseline_id,
                )
            with pytest.raises(patches.VerificationError), conn.transaction():
                patches.conclude_verification(
                    conn,
                    workspace_id=binding.workspace,
                    verification_id=claimed.claim.verification_id,
                    permitted_differences=(
                        {"field": "runner_profile_digest", "candidate": "0" * 64},
                    ),
                )
            runner_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO runner(id,workspace_id,name,status,session_key,platform,device_id,"
                "interactive_session_id,console,profile_digest,profile) "
                "VALUES (%s,%s,'synthetic-metadata-only','READY',repeat('1',64),'darwin',"
                "'synthetic','synthetic',true,%s,'{}'::jsonb)",
                (runner_id, binding.workspace, base["runner_profile_digest"]),
            )
            candidate_attempt = runs.start_attempt(
                conn, workspace_id=binding.workspace, run_id=run_id, lease_epoch=1
            )
        if fresh:
            from accessforge_orchestrator.candidate_fixture_setup import prepare as attach_setup
            from accessforge_orchestrator.reference_fixture_setup import Refused as SetupRefused
            from accessforge_persistence import execution_approvals, fixture_setup_evidence

            setup_arguments: dict[str, Any] = {
                "workspace_id": binding.workspace,
                "run_id": run_id,
                "origin": gateway.origin,
                "reset_credential_ref": "reset",
                "observer_credential_ref": "observer",
                "reset_values": {"variant": "inaccessible"},
                "observer_config": {"effect": "CREATE_TEST_REQUEST"},
            }
            with pytest.raises(execution_approvals.Refused):
                attach_setup(binding.database, **setup_arguments)
            with workspace_connection(binding.database, binding.workspace) as conn:
                assert conn.execute("SELECT 1 FROM fixture_setup_reservation").fetchone() is None
                with pytest.raises(runners.RunnerError, match="fixture"), conn.transaction():
                    runners.admit_lease(
                        conn,
                        workspace_id=binding.workspace,
                        runner_id=runner_id,
                        run_id=run_id,
                        attempt_id=candidate_attempt,
                        ttl_seconds=5,
                    )
                execution_approvals.issue(
                    conn,
                    sealed_manifest_id=str(sealed["id"]),
                    actor_id=binding.owner,
                    target_digest=sealed["manifest_digest"],
                    expected_revision=0,
                    expires_at=manifest["expiresAt"],
                )
            with pytest.raises(SetupRefused):
                attach_setup(
                    binding.database,
                    **{**setup_arguments, "reset_values": {"variant": "accessible"}},
                )
            if reader_exit == "fresh-preview":
                with pytest.raises(builds.BuildClaimRefused, match="preview-observed"):
                    attach_setup(binding.database, **setup_arguments)
                with workspace_connection(binding.database, binding.workspace) as conn:
                    assert (
                        conn.execute("SELECT 1 FROM fixture_setup_reservation").fetchone() is None
                    )
                return
            setup = attach_setup(binding.database, **setup_arguments)
            assert attach_setup(binding.database, **setup_arguments) == setup
            with workspace_connection(binding.database, binding.workspace) as conn:
                readiness = conn.execute(
                    "SELECT fixture_setup_unresolved(%s) AS unresolved", (run_id,)
                ).fetchone()
                assert readiness is not None and not readiness["unresolved"]
                retained_setup = fixture_setup_evidence.snapshot(
                    conn,
                    {
                        "run_id": run_id,
                        "workspace_id": binding.workspace,
                        "attempt_id": candidate_attempt,
                        "manifest_digest": sealed["manifest_digest"],
                    },
                )
                assert retained_setup["observation"] == setup
                from accessforge_navigation_tools import NavigatorProjection
                from accessforge_orchestrator.navigator.destination import load_destination

                fixture_row = conn.execute(
                    "SELECT * FROM run_fixture_instance WHERE run_id=%s", (run_id,)
                ).fetchone()
                assert fixture_row is not None
                destination_arguments = {
                    "workspace_id": binding.workspace,
                    "run_id": run_id,
                    "manifest": manifest,
                    "fixture": fixture_row,
                    "sealed_url": "http://localhost:8000/form/FIXTURE",
                }
                destination = load_destination(conn, **destination_arguments)
                assert destination is not None and destination.url == gateway.start_url
                original_policy = conn.execute(
                    "SELECT navigator_policy FROM journey_version WHERE id=%s",
                    (manifest["journeyVersionId"],),
                ).fetchone()
                assert original_policy is not None
                view = NavigatorProjection.from_policy(
                    run_ref=run_id,
                    policy=original_policy["navigator_policy"],
                    reader_observations=[],
                    runtime_start_url=destination.url,
                    authorized_candidate_origin=destination.authorized_candidate_origin,
                )
                assert view.model_payload()["policy"] == original_policy["navigator_policy"]
                assert view.model_payload()["runtimeStartUrl"] == gateway.start_url
                assert set(view.model_payload()) == {
                    "runRef",
                    "policy",
                    "readerObservations",
                    "runtimeStartUrl",
                }
                with pytest.raises(ValueError):
                    load_destination(
                        conn,
                        **{
                            **destination_arguments,
                            "sealed_url": "http://127.0.0.1:8000/form/FIXTURE",
                        },
                    )
                with pytest.raises(ValueError), conn.transaction():
                    conn.execute(
                        "UPDATE environment_manifest SET revoked_at=clock_timestamp() WHERE id=%s",
                        (environment_id,),
                    )
                    load_destination(conn, **destination_arguments)
                assert (
                    setup["candidateSeed"]["context"]["regressionAttemptId"]
                    == gateway.binding.task_id
                )
        with workspace_connection(binding.database, binding.workspace) as conn:
            with pytest.raises(runners.RunnerError, match="lifetime"), conn.transaction():
                runners.admit_lease(
                    conn,
                    workspace_id=binding.workspace,
                    runner_id=runner_id,
                    run_id=run_id,
                    attempt_id=candidate_attempt,
                    ttl_seconds=60,
                )
            lease = runners.admit_lease(
                conn,
                workspace_id=binding.workspace,
                runner_id=runner_id,
                run_id=run_id,
                attempt_id=candidate_attempt,
                ttl_seconds=5,
            )
            candidate_runs.assert_lease(
                conn, run_id=run_id, lease_id=lease.lease_id, epoch=lease.epoch
            )
            # Each fault rolls back its own savepoint, preserving the actual endpoint for the
            # next probe. Neither an environment nor a lease is continuing authority by itself.
            for statement, identifier in (
                (
                    "UPDATE environment_manifest SET revoked_at=clock_timestamp() WHERE id=%s",
                    environment_id,
                ),
                ("UPDATE runner SET profile_digest=repeat('0',64) WHERE id=%s", runner_id),
                (
                    "UPDATE desktop_lease SET deadline_at=clock_timestamp()+interval '1 hour' "
                    "WHERE id=%s",
                    lease.lease_id,
                ),
                (
                    "UPDATE desktop_lease SET cancel_requested_at=clock_timestamp(),"
                    "cancellation_revision=0 WHERE id=%s",
                    lease.lease_id,
                ),
                (
                    "UPDATE run SET cancel_requested_at=clock_timestamp(),"
                    "cancellation_revision=0 WHERE id=%s",
                    run_id,
                ),
            ):
                with pytest.raises(builds.BuildClaimRefused), conn.transaction():
                    conn.execute(statement, (identifier,))
                    candidate_runs.assert_request(
                        conn, attempt_id=gateway.binding.task_id, method="GET"
                    )
            for table in ("candidate_run_binding", "candidate_reader_lease"):
                with pytest.raises(psycopg.IntegrityError), conn.transaction():
                    conn.execute(
                        psycopg.sql.SQL(
                            "UPDATE {} SET created_at=clock_timestamp() WHERE run_id=%s"
                        ).format(psycopg.sql.Identifier(table)),
                        (run_id,),
                    )
            with pytest.raises(builds.BuildClaimRefused), conn.transaction():
                candidate_runs.assert_lease(
                    conn, run_id=run_id, lease_id=lease.lease_id, epoch=lease.epoch + 1
                )
            with pytest.raises(builds.BuildClaimRefused, match="unresolved"), conn.transaction():
                candidate_runs.assert_reader_released(conn, attempt_id=gateway.binding.task_id)
            with pytest.raises(builds.BuildClaimRefused, match="RUN_EFFECTS"), conn.transaction():
                candidate_runs.assert_request(
                    conn, attempt_id=gateway.binding.task_id, method="POST"
                )
        target = urlsplit(gateway.origin)
        assert target.hostname is not None
        http_client = http.client.HTTPConnection(target.hostname, target.port, timeout=5)
        http_client.request("GET", gateway.path)
        response = http_client.getresponse()
        assert response.status == 200
        response.read()
        http_client.close()
        if reader_exit == "returned-active":
            return
        if reader_exit == "raised-active":
            raise RuntimeError("injected controller failure with unresolved reader lease")
        with workspace_connection(binding.database, binding.workspace) as conn:
            # No OS dispatch occurs. Release synthetic admission before leaving the live endpoint.
            conn.execute(
                "UPDATE desktop_lease SET released_at=clock_timestamp(),"
                "release_reason='OPERATOR_RESET' WHERE id=%s",
                (lease.lease_id,),
            )
            with pytest.raises(builds.BuildClaimRefused, match="released"), conn.transaction():
                candidate_runs.assert_lease(
                    conn, run_id=run_id, lease_id=lease.lease_id, epoch=lease.epoch
                )
        http_client = http.client.HTTPConnection(target.hostname, target.port, timeout=5)
        http_client.request("GET", gateway.path)
        response = http_client.getresponse()
        assert response.status == 502
        response.read()
        http_client.close()

    def execute() -> Any:
        return execute_regressions(
            binding.database,
            workspace_id=binding.workspace,
            build_id=claimed.claim.build_id,
            runner=ReferenceRegressions(image=image, daemon=sandbox.daemon),
            store=store,
            on_candidate_session=controller,
        )

    if reader_exit not in {"released", "fresh-released", "fresh-preview"}:
        with pytest.raises(CleanupUnconfirmed, match="reader cleanup"):
            execute()
        with workspace_connection(binding.database, binding.workspace) as conn:
            assert conn.execute(
                "SELECT state,cleanup_confirmed FROM candidate_regression_attempt"
            ).fetchall() == [{"state": "UNKNOWN", "cleanup_confirmed": False}]
            assert conn.execute(
                "SELECT count(*) AS n FROM desktop_lease WHERE released_at IS NULL"
            ).fetchone() == {"n": 1}
        return
    regression = execute()
    assert len(prepared) == 1
    assert prepared[0]["regression_attempt_id"] == uuid.UUID(regression.task_id)
    with workspace_connection(binding.database, binding.workspace) as conn:
        with pytest.raises(builds.BuildClaimRefused), conn.transaction():
            candidate_runs.assert_live(conn, run_id=str(prepared[0]["run_id"]))
        verification = conn.execute(
            "SELECT verification_id FROM candidate_run_binding WHERE run_id=%s",
            (prepared[0]["run_id"],),
        ).fetchone()
        assert verification is not None
        # Even actual successful protected regressions are not an original evaluated AT pair.
        assert (
            patches._regression_attestation(
                conn,
                str(prepared[0]["run_id"]),
                verification_id=str(verification["verification_id"]),
                baseline_run_id=baseline_id,
            )[0]
            is False
        )
        if fresh:
            from accessforge_persistence import candidate_fixture_setups

            if reader_exit == "fresh-preview":
                with pytest.raises(builds.BuildClaimRefused, match="preview-observed"):
                    candidate_fixture_setups.for_run(conn, run_id=str(prepared[0]["run_id"]))
                return
            history = candidate_fixture_setups.for_run(conn, run_id=str(prepared[0]["run_id"]))
            assert history is not None and history["observation"]["application"]["effectCount"] == 0
            from accessforge_persistence import functional_regression_evidence

            functional = functional_regression_evidence.for_run(
                conn, run_id=str(prepared[0]["run_id"])
            )
            assert functional is not None
            assert functional["regressionAttemptId"] == regression.task_id
            assert functional["artifactDigest"] == regression.artifact_digest
            producer = functional["producerReceipt"]
            assert producer["validation"] == regression.validation.canonical_form()
            assert regression.validation.passed
            authored = producer["runEvidence"]
            assert authored["runId"] == functional["runId"]
            assert authored["workspaceId"] == binding.workspace
            assert authored["leaseId"] == functional["leaseId"]
            assert authored["leaseEpoch"] == functional["leaseEpoch"]
            assert authored["assertionObservations"] == [
                {
                    "assertionId": "validation",
                    "kind": "FUNCTIONAL_VALIDATION",
                    "condition": "TRUE",
                    "provenance": "OBSERVER_AUTHORED",
                },
                {
                    "assertionId": "legacy-validation",
                    "kind": "FUNCTIONAL_VALIDATION",
                    "condition": "UNKNOWN",
                    "provenance": "OBSERVER_AUTHORED",
                    "unknownReason": "matching frozen functional predicate unavailable",
                },
            ]
            for replacement in (None, {"forged": True}):
                with pytest.raises(psycopg.IntegrityError), conn.transaction():
                    conn.execute(
                        "UPDATE candidate_regression_attempt SET functional_receipt=%s::jsonb "
                        "WHERE id=%s",
                        (
                            json.dumps(replacement) if replacement is not None else None,
                            regression.task_id,
                        ),
                    )
            assert functional["originalSeedDigest"] == digest(history)
            assert functional_regression_evidence.VALIDATION_CHECKS.issubset(functional["checks"])
            assert history["context"]["nonce"] not in json.dumps(functional)
            assert all(process["state"] == "REMOVED" for process in functional["processes"])
            from accessforge_domain.canonical import canonicalize
            from accessforge_persistence.evidence import artifacts

            session = conn.execute(
                "SELECT l.workspace_id,l.run_id,l.id AS lease_id,l.epoch,l.attempt_id,"
                "r.manifest_digest FROM desktop_lease l JOIN run r "
                "ON r.id=l.run_id AND r.workspace_id=l.workspace_id WHERE l.id=%s",
                (functional["leaseId"],),
            ).fetchone()
            assert session is not None
            bundle = functional_regression_evidence.snapshot(conn, session)
            assert bundle["receipt"] == functional
            assert bundle["receiptDigest"] == digest(functional)
            with pytest.raises(builds.BuildClaimRefused):
                functional_regression_evidence.snapshot(
                    conn, {**session, "epoch": session["epoch"] + 1}
                )
            payload = canonicalize(bundle).encode()
            artifact = artifacts.upload_to_quarantine(
                conn,
                store,
                workspace_id=binding.workspace,
                run_id=str(session["run_id"]),
                attempt_id=str(session["attempt_id"]),
                kind="FUNCTIONAL_REGRESSION",
                producer_id=bundle["producerId"],
                lease_epoch=session["epoch"],
                manifest_digest=session["manifest_digest"],
                content_type="application/json",
                payload=payload,
            )
            artifacts.promote(
                conn,
                store,
                artifact_id=artifact.artifact_id,
                expected_manifest_digest=session["manifest_digest"],
            )
            assert store.get(key=artifact.object_key) == payload
            assert conn.execute(
                "SELECT state FROM evidence_artifact WHERE id=%s", (artifact.artifact_id,)
            ).fetchone() == {"state": "PROMOTED"}
            from accessforge_orchestrator.execution_artifacts import Refused as EvidenceRefused
            from accessforge_orchestrator.functional_evidence import observed_assertions
            from accessforge_persistence import journeys

            original_manifest = conn.execute(
                "SELECT canonical_manifest FROM sealed_manifest WHERE run_id=%s",
                (session["run_id"],),
            ).fetchone()
            assert original_manifest is not None
            contract = journeys.load_assertion_contract(
                conn,
                version_id=original_manifest["canonical_manifest"]["journeyVersionId"],
                expected_digest=authored["assertionSetDigest"],
            )
            # Actual retained bytes and original DB receipt; runtime argument is a controlled
            # join input here, not proof of native reader/runtime interpretation.
            join = {
                "bundle": json.loads(store.get(key=artifact.object_key)),
                "context": session,
                "assertions": contract,
                "observed_build": regression.artifact_digest,
                "artifact_digest": digest(bundle),
            }
            consumed = observed_assertions(**join)
            assert consumed["validation"].condition.value == "TRUE"
            assert consumed["legacy-validation"].condition.value == "UNKNOWN"
            assert consumed["validation"].evidence_refs == (digest(bundle),)
            assert observed_assertions(**{**join, "observed_build": None}) == {}
            with pytest.raises(EvidenceRefused):
                observed_assertions(**{**join, "observed_build": "0" * 64})
            from accessforge_persistence.regression_attestation import _retained

            retained_snapshot = {
                "runId": str(session["run_id"]),
                "attemptId": str(session["attempt_id"]),
                "manifestDigest": session["manifest_digest"],
                "artifacts": [
                    {
                        "artifactId": artifact.artifact_id,
                        "kind": "FUNCTIONAL_REGRESSION",
                        "producerId": bundle["producerId"],
                        "digest": digest(bundle),
                    }
                ],
            }
            assert _retained(conn, retained_snapshot)
            assert not _retained(conn, {**retained_snapshot, "runId": str(uuid.uuid4())})
            assert not _retained(conn, {**retained_snapshot, "manifestDigest": "0" * 64})
        with workspace_connection(binding.database, str(uuid.uuid4())) as other:
            from accessforge_persistence import functional_regression_evidence

            assert (
                functional_regression_evidence.for_run(other, run_id=str(prepared[0]["run_id"]))
                is None
            )


@pytest.mark.sandbox
def test_candidate_source_capture_failure_rolls_back_claim_and_patch_transition(
    binding: BoundFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_source = materializations.capture_source

    def fail_after_capture(*args: Any, **kwargs: Any) -> None:
        capture_source(*args, **kwargs)
        raise RuntimeError("injected source capture commit failure")

    monkeypatch.setattr(materializations, "capture_source", fail_after_capture)
    with pytest.raises(RuntimeError, match="capture commit failure"):
        _prepare_owned_build(binding)
    with workspace_connection(binding.database, binding.workspace) as conn:
        for table in ("candidate_materialization", "candidate_build_attempt", "patch_verification"):
            assert conn.execute(
                psycopg.sql.SQL("SELECT count(*) AS n FROM {}").format(
                    psycopg.sql.Identifier(table)
                )
            ).fetchone() == {"n": 0}
        assert conn.execute("SELECT status FROM patch_proposal").fetchall() == [
            {"status": "APPROVED"}
        ]
        assert conn.execute("SELECT count(*) AS n FROM source_snapshot").fetchone() == {"n": 1}


@pytest.mark.sandbox
def test_candidate_materialization_uses_actual_patched_source_and_retained_output(
    binding: BoundFixture,
    isolated_archives: tuple[IsolatedArchiveStore, IsolatedArchiveStore],
) -> None:
    claimed, sandbox, command = _prepare_owned_build(binding)
    arguments = {"workspace_id": binding.workspace, "build_id": claimed.claim.build_id}
    store = isolated_archives[0].store
    with workspace_connection(binding.database, binding.workspace) as conn:
        capture = conn.execute(
            "SELECT * FROM candidate_materialization WHERE build_id=%s",
            (claimed.claim.build_id,),
        ).fetchone()
        assert capture is not None and capture["build_artifact_id"] is None
        assert capture["source_tree_digest"] == claimed.candidate.source.tree_digest
        assert tuple(capture["changed_paths"]) == claimed.candidate.changed_paths
        assert str(capture["source_snapshot_id"]) != claimed.inputs.source_snapshot_id
    with pytest.raises(builds.BuildClaimRefused):
        publish_retained_materialization(binding.database, **arguments, store=store)
    built = execute_claim(
        binding.database,
        workspace_id=binding.workspace,
        claimed=claimed,
        sandbox=sandbox,
        command=command,
        store=store,
    )
    source_id, artifact_id = publish_retained_materialization(
        binding.database, **arguments, store=store
    )
    assert publish_retained_materialization(binding.database, **arguments, store=store) == (
        source_id,
        artifact_id,
    )
    with workspace_connection(binding.database, str(uuid.uuid4())) as conn:
        assert conn.execute("SELECT * FROM candidate_materialization").fetchall() == []
    with pytest.raises(builds.BuildClaimRefused):
        publish_retained_materialization(
            binding.database,
            **{**arguments, "workspace_id": str(uuid.uuid4())},
            store=store,
        )
    with workspace_connection(binding.database, binding.workspace) as conn:
        source = conn.execute("SELECT * FROM source_snapshot WHERE id=%s", (source_id,)).fetchone()
        assert source is not None
        assert source["commit_sha"] == claimed.inputs.source_commit
        assert source["tree_digest"] == claimed.candidate.source.tree_digest
        assert source["dirty"] is True
        assert source["dirty_path_count"] == len(claimed.candidate.changed_paths)
        assert source["requested_revision"] == "approved-patch:" + claimed.inputs.patch_digest
        assert conn.execute(
            "SELECT artifact_digest,source_snapshot_id,identity_observable FROM build_artifact "
            "WHERE id=%s",
            (artifact_id,),
        ).fetchone() == {
            "artifact_digest": built.artifact.archive_digest,
            "source_snapshot_id": uuid.UUID(source_id),
            "identity_observable": True,
        }
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute(
                "UPDATE candidate_materialization SET source_tree_digest=repeat('0',64) "
                "WHERE build_id=%s",
                (claimed.claim.build_id,),
            )
    # Readers recheck source/artifact records even though the materialization itself is immutable.
    with workspace_connection(binding.database, binding.workspace) as conn:
        for sql, identifier in (
            ("UPDATE source_snapshot SET tree_digest=repeat('0',64) WHERE id=%s", source_id),
            (
                "UPDATE source_snapshot SET dirty_path_count=dirty_path_count+1 WHERE id=%s",
                source_id,
            ),
            ("UPDATE source_snapshot SET requested_revision='HEAD' WHERE id=%s", source_id),
            ("UPDATE build_artifact SET artifact_digest=repeat('0',64) WHERE id=%s", artifact_id),
            ("UPDATE build_artifact SET identity_observable=false WHERE id=%s", artifact_id),
            ("DELETE FROM candidate_materialization WHERE build_id=%s", claimed.claim.build_id),
        ):
            with conn.transaction(force_rollback=True):
                conn.execute(sql, (identifier,))
                with pytest.raises(builds.BuildClaimRefused):
                    materializations.publish(
                        conn,
                        **arguments,
                        observed_artifact_digest=built.artifact.archive_digest,
                    )
                original_build = conn.execute(
                    "SELECT * FROM candidate_build_attempt WHERE id=%s",
                    (claimed.claim.build_id,),
                ).fetchone()
                assert original_build is not None
                if sql.startswith("DELETE FROM candidate_materialization"):
                    assert materializations.source_lineage(conn, build=original_build) is None
                else:
                    with pytest.raises(builds.BuildClaimRefused):
                        materializations.source_lineage(conn, build=original_build)
        with conn.transaction(force_rollback=True):
            conn.execute(
                "UPDATE approval SET revoked_at=clock_timestamp() WHERE id="
                "(SELECT approval_id FROM candidate_build_attempt WHERE id=%s)",
                (claimed.claim.build_id,),
            )
            with pytest.raises((builds.BuildClaimRefused, AuthorityError)):
                materializations.publish(
                    conn, **arguments, observed_artifact_digest=built.artifact.archive_digest
                )


@pytest.mark.sandbox
@pytest.mark.parametrize(
    "invalid", ["artifact", "image", "daemon", "workspace", "revision", "revoked"]
)
def test_regression_claim_rechecks_exact_authority_and_fences_late_receipts(
    binding: BoundFixture,
    isolated_archives: tuple[IsolatedArchiveStore, IsolatedArchiveStore],
    invalid: str,
) -> None:
    claimed, sandbox, command = _prepare_owned_build(binding)
    built = execute_claim(
        binding.database,
        workspace_id=binding.workspace,
        claimed=claimed,
        sandbox=sandbox,
        command=command,
        store=isolated_archives[0].store,
    )
    arguments: dict[str, Any] = {
        "workspace_id": binding.workspace,
        "build_id": claimed.claim.build_id,
        "artifact_digest": built.artifact.archive_digest,
        "policy_digest": "e" * 64,
        "image_id": built.image_id,
        "daemon_endpoint": sandbox.daemon.endpoint,
        "daemon_id": sandbox.daemon.daemon_id,
    }
    with workspace_connection(binding.database, binding.workspace) as conn:
        with conn.transaction(force_rollback=True):
            bad = dict(arguments)
            if invalid == "artifact":
                bad["artifact_digest"] = "0" * 64
            elif invalid == "image":
                bad["image_id"] = "sha256:" + "0" * 64
            elif invalid == "daemon":
                bad["daemon_id"] = "different"
            elif invalid == "workspace":
                bad["workspace_id"] = str(uuid.uuid4())
            elif invalid == "revision":
                conn.execute(
                    "UPDATE patch_proposal SET revision=revision+1 WHERE id=%s",
                    (claimed.claim.patch_id,),
                )
            elif invalid == "revoked":
                conn.execute(
                    "UPDATE approval SET revoked_at=clock_timestamp() WHERE id="
                    "(SELECT approval_id FROM candidate_build_attempt WHERE id=%s)",
                    (claimed.claim.build_id,),
                )
            with pytest.raises((builds.BuildClaimRefused, AuthorityError)):
                # Domain approval refusals are also expected; none may insert a claim.
                regressions.claim(conn, **bad)
        assert (
            conn.execute(
                "SELECT id FROM candidate_regression_attempt WHERE build_id=%s",
                (claimed.claim.build_id,),
            ).fetchone()
            is None
        )
        intent = regressions.claim(conn, **arguments)
    with workspace_connection(binding.database, str(uuid.uuid4())) as conn:
        assert (
            conn.execute(
                "SELECT * FROM candidate_regression_attempt WHERE id=%s", (intent.attempt_id,)
            ).fetchone()
            is None
        )
        with pytest.raises(builds.BuildClaimRefused):
            regressions.dispatch(
                conn,
                claim=intent,
                policy_digest="e" * 64,
                artifact_digest=built.artifact.archive_digest,
            )
    with workspace_connection(binding.database, binding.workspace) as conn:
        with pytest.raises(builds.BuildClaimRefused), conn.transaction():
            regressions.dispatch(
                conn,
                claim=intent,
                policy_digest="f" * 64,
                artifact_digest=built.artifact.archive_digest,
            )
        regressions.dispatch(
            conn,
            claim=intent,
            policy_digest="e" * 64,
            artifact_digest=built.artifact.archive_digest,
        )
        with pytest.raises(builds.BuildClaimRefused), conn.transaction():
            regressions.finish(
                conn,
                claim=intent,
                policy_digest="e" * 64,
                artifact_digest=built.artifact.archive_digest,
                checks=("forged_pass",),
                containers=(),
            )
        assert regressions.fence_expired(conn, now=datetime.now(UTC) + timedelta(hours=1)) == 1
        with pytest.raises(builds.BuildClaimRefused), conn.transaction():
            regressions.dispatch(
                conn,
                claim=intent,
                policy_digest="e" * 64,
                artifact_digest=built.artifact.archive_digest,
            )
        assert conn.execute(
            "SELECT state,epoch FROM candidate_regression_attempt WHERE id=%s", (intent.attempt_id,)
        ).fetchone() == {"state": "UNKNOWN", "epoch": 2}


@pytest.fixture()
def isolated_archives() -> Iterator[tuple[IsolatedArchiveStore, IsolatedArchiveStore]]:
    buckets: list[IsolatedArchiveStore] = []
    try:
        for _ in range(2):
            settings = evidence.S3Settings(
                endpoint_url=os.environ["OBJECT_STORE_ENDPOINT"],
                access_key=os.environ["OBJECT_STORE_ACCESS_KEY"],
                secret_key=os.environ["OBJECT_STORE_SECRET_KEY"],
                bucket=f"accessforge-candidate-drill-{uuid.uuid4().hex}",
            )
            store = evidence.S3ArtifactStore(settings)
            store.ensure_bucket()
            buckets.append(IsolatedArchiveStore(settings, store))
        yield buckets[0], buckets[1]
    finally:
        # Only freshly generated, isolated drill buckets; never the configured evidence bucket.
        for bucket in buckets:
            for page in bucket.store._client.get_paginator("list_object_versions").paginate(
                Bucket=bucket.settings.bucket
            ):
                for version in [*page.get("Versions", []), *page.get("DeleteMarkers", [])]:
                    bucket.store._client.delete_object(
                        Bucket=bucket.settings.bucket,
                        Key=version["Key"],
                        VersionId=version["VersionId"],
                    )
            bucket.store._client.delete_bucket(Bucket=bucket.settings.bucket)


def _database_at(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


@pytest.mark.parametrize("configuration", ["versioning", "lifecycle", "denied"])
def test_retirement_refuses_unprovable_store_configuration_without_changing_bytes(
    isolated_archives: tuple[IsolatedArchiveStore, IsolatedArchiveStore],
    monkeypatch: pytest.MonkeyPatch,
    configuration: str,
) -> None:
    from botocore.exceptions import ClientError

    bucket = isolated_archives[0]
    store = bucket.store
    key = "synthetic-config-guard"
    store.put_create_only(key=key, payload=b"original", content_type="application/octet-stream")
    if configuration == "versioning":
        store._client.put_bucket_versioning(
            Bucket=bucket.settings.bucket, VersioningConfiguration={"Status": "Enabled"}
        )
    elif configuration == "lifecycle":
        store._client.put_bucket_lifecycle_configuration(
            Bucket=bucket.settings.bucket,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "test-only",
                        "Status": "Enabled",
                        "Filter": {"Prefix": "unrelated/"},
                        "Expiration": {"Days": 1},
                    }
                ]
            },
        )
    else:

        def denied(*, Bucket: str) -> None:  # noqa: N803 - exact boto3 keyword.
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "injected configuration denial"}},
                "GetBucketVersioning",
            )

        monkeypatch.setattr(store._client, "get_bucket_versioning", denied)
    with pytest.raises(evidence.ArtifactStoreError):
        store.retire_create_only(key=key)
    assert store.get_bounded(key=key, max_bytes=8) == b"original"


def test_restore_cannot_overwrite_a_retirement_tombstone(
    isolated_archives: tuple[IsolatedArchiveStore, IsolatedArchiveStore],
) -> None:
    store = isolated_archives[0].store
    key = f"workspaces/{uuid.uuid4()}/candidate-builds/{uuid.uuid4()}/archives/" + "a" * 64
    store.retire_create_only(key=key)
    with pytest.raises(evidence.ObjectStoreUnavailable):
        restore.restore_object_bytes(store, key=key, payload=b"old backup bytes")
    assert store.get_bounded(key=key, max_bytes=1) == b""
    restore.restore_object_bytes(store, key=key, payload=b"")
    assert store.get_bounded(key=key, max_bytes=1) == b""


def _expire_candidate_policy(binding: BoundFixture) -> None:
    with workspace_connection(binding.database, binding.workspace) as conn:
        policy = retention.current_policy(conn, workspace_id=binding.workspace)
        retention.configure_policy(
            conn,
            workspace_id=binding.workspace,
            configured_by=binding.owner,
            expected_revision=policy.revision,
            entries=[
                {
                    "evidenceClass": entry.evidence_class,
                    "retainDays": 0
                    if entry.evidence_class == "SOURCE_SNAPSHOT"
                    else entry.retain_days,
                    "consentRequired": entry.consent_required,
                }
                for entry in policy.entries
            ],
        )


@pytest.mark.sandbox
def test_expired_candidate_retirement_is_fenced_durable_and_retryable(
    binding: BoundFixture,
    isolated_archives: tuple[IsolatedArchiveStore, IsolatedArchiveStore],
) -> None:
    source = isolated_archives[0].store
    claimed, sandbox, command = _prepare_owned_build(binding)
    built = execute_claim(
        binding.database,
        workspace_id=binding.workspace,
        claimed=claimed,
        sandbox=sandbox,
        command=command,
        store=source,
    )
    args = {"workspace_id": binding.workspace, "build_id": claimed.claim.build_id}
    assert not retire_expired_candidate(binding.database, **args, store=source)
    with pytest.raises(builds.BuildClaimRefused, match="no candidate"):
        retire_expired_candidate(
            binding.database,
            workspace_id=str(uuid.uuid4()),
            build_id=claimed.claim.build_id,
            store=source,
        )
    _expire_candidate_policy(binding)
    with pytest.raises(builds.BuildClaimRefused, match="storage location"):
        retire_expired_candidate(binding.database, **args, store=isolated_archives[1].store)
    assert list(isolated_archives[1].store.iter_keys()) == []
    with pytest.raises(builds.BuildClaimRefused, match="expired"):
        read_retained_candidate(binding.database, **args, store=source)

    class LostRetirementResponse:
        @property
        def storage_identity(self) -> tuple[str, str]:
            return source.storage_identity

        def retire_create_only(self, *, key: str) -> None:
            source.retire_create_only(key=key)
            raise evidence.ObjectStoreUnavailable("injected lost retirement response")

    with pytest.raises(evidence.ObjectStoreUnavailable):
        retire_expired_candidate(binding.database, **args, store=LostRetirementResponse())
    with workspace_connection(binding.database, binding.workspace) as conn:
        row = conn.execute(
            "SELECT a.state,a.object_key,r.completed_at FROM candidate_archive a "
            "JOIN candidate_archive_retirement r USING(build_id,workspace_id) "
            "WHERE a.build_id = %s",
            (claimed.claim.build_id,),
        ).fetchone()
        assert row is not None and row["state"] == "RETAINED" and row["completed_at"] is None
        key = str(row["object_key"])
    assert source.get_bounded(key=key, max_bytes=1) == b""
    with pytest.raises(builds.BuildClaimRefused, match="no retained"):
        read_retained_candidate(binding.database, **args, store=source)
    assert retire_expired_candidate(binding.database, **args, store=source)
    assert retire_expired_candidate(binding.database, **args, store=source)
    # A delayed uploader or SDK retry cannot recreate bytes after retirement.
    with pytest.raises(evidence.ObjectStoreUnavailable):
        source.put_create_only(
            key=key, payload=built.artifact.archive(), content_type="application/x-tar"
        )
    assert source.get_bounded(key=key, max_bytes=1) == b""
    with workspace_connection(binding.database, binding.workspace) as conn:
        row = conn.execute(
            "SELECT a.state,a.content_digest,b.state AS build_state,r.completed_at "
            "FROM candidate_archive a JOIN candidate_build_attempt b ON b.id = a.build_id "
            "JOIN candidate_archive_retirement r ON r.build_id = a.build_id "
            "WHERE a.build_id = %s",
            (claimed.claim.build_id,),
        ).fetchone()
        assert row is not None and row["state"] == "DELETED"
        assert row["build_state"] == "BUILT" and row["completed_at"] is not None
        assert row["content_digest"] == built.artifact.archive_digest
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute(
                "UPDATE candidate_archive_retirement SET completed_at = NULL WHERE build_id = %s",
                (claimed.claim.build_id,),
            )


@pytest.mark.sandbox
@pytest.mark.parametrize("before_upload", [True, False])
def test_quarantine_retirement_blocks_late_upload_and_late_promotion(
    binding: BoundFixture,
    isolated_archives: tuple[IsolatedArchiveStore, IsolatedArchiveStore],
    before_upload: bool,
) -> None:
    source = isolated_archives[0].store
    claimed, sandbox, command = _prepare_owned_build(binding)
    keys: list[str] = []

    class DelayedUploader:
        @property
        def storage_identity(self) -> tuple[str, str]:
            return source.storage_identity

        def put_create_only(self, *, key: str, payload: bytes, content_type: str) -> str:
            keys.append(key)
            if not before_upload:
                source.put_create_only(key=key, payload=payload, content_type=content_type)
            _expire_candidate_policy(binding)
            assert retire_expired_candidate(
                binding.database,
                workspace_id=binding.workspace,
                build_id=claimed.claim.build_id,
                store=source,
            )
            if before_upload:
                source.put_create_only(key=key, payload=payload, content_type=content_type)
            return key

        def get_bounded(self, *, key: str, max_bytes: int) -> bytes:
            return source.get_bounded(key=key, max_bytes=max_bytes)

    with pytest.raises((builds.BuildClaimRefused, evidence.ObjectStoreUnavailable)):
        execute_claim(
            binding.database,
            workspace_id=binding.workspace,
            claimed=claimed,
            sandbox=sandbox,
            command=command,
            store=DelayedUploader(),
        )
    assert source.get_bounded(key=keys[0], max_bytes=1) == b""
    with workspace_connection(binding.database, binding.workspace) as conn:
        assert conn.execute(
            "SELECT state,epoch,failure_code FROM candidate_build_attempt WHERE id = %s",
            (claimed.claim.build_id,),
        ).fetchone() == {"state": "UNKNOWN", "epoch": 2, "failure_code": "ARCHIVE_EXPIRED"}
        assert conn.execute(
            "SELECT state FROM candidate_archive WHERE build_id = %s",
            (claimed.claim.build_id,),
        ).fetchone() == {"state": "DELETED"}


@pytest.mark.parametrize("replacement", [b"wrong!!", b"short", b"oversized", None])
def test_restore_rejects_substituted_or_missing_bytes_after_actual_upload(
    isolated_archives: tuple[IsolatedArchiveStore, IsolatedArchiveStore],
    monkeypatch: pytest.MonkeyPatch,
    replacement: bytes | None,
) -> None:
    store = isolated_archives[1].store
    original_put = store.put
    key = "synthetic-restore-probe"

    def substituted_put(*, key: str, payload: bytes, content_type: str) -> str:
        original_put(key=key, payload=payload, content_type=content_type)
        if replacement is None:
            store.delete(key=key)
        else:
            original_put(key=key, payload=replacement, content_type=content_type)
        return key

    monkeypatch.setattr(store, "put", substituted_put)
    with pytest.raises((restore.RestoreError, evidence.ArtifactStoreError)):
        restore.restore_object_bytes(store, key=key, payload=b"trusted")


@pytest.fixture()
def candidate_restore_target(backup_database_url: str) -> Iterator[str]:
    name = f"accessforge_candidate_restore_{uuid.uuid4().hex[:12]}"
    with connect(_database_at(backup_database_url, "postgres")) as conn:
        conn.autocommit = True
        conn.execute(f'CREATE DATABASE "{name}"')  # noqa: S608 - generated disposable name.
    try:
        yield _database_at(backup_database_url, name)
    finally:
        with connect(_database_at(backup_database_url, "postgres")) as conn:
            conn.autocommit = True
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')  # noqa: S608


def _operator_script(
    script: str, args: tuple[str, ...], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).resolve().parents[2]
    return subprocess.run(  # noqa: S603 - owned scripts and explicit argv; no shell.
        [sys.executable, str(root / "scripts" / script), *args],
        cwd=root,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.sandbox
@pytest.mark.parametrize("archive_state", ["retained", "quarantined", "deleted"])
def test_actual_candidate_encrypted_backup_and_isolated_restore(
    binding: BoundFixture,
    backup_database_url: str,
    candidate_restore_target: str,
    isolated_archives: tuple[IsolatedArchiveStore, IsolatedArchiveStore],
    tmp_path_factory: pytest.TempPathFactory,
    archive_state: str,
) -> None:
    retained = archive_state != "quarantined"
    source, target = isolated_archives
    claimed, sandbox, command = _prepare_owned_build(binding)
    store = FaultStore(source.store, binding, "none" if retained else "tamper", [])
    if retained:
        execute_claim(
            binding.database,
            workspace_id=binding.workspace,
            claimed=claimed,
            sandbox=sandbox,
            command=command,
            store=store,
        )
    else:
        with pytest.raises(builds.BuildClaimRefused, match="substituted"):
            execute_claim(
                binding.database,
                workspace_id=binding.workspace,
                claimed=claimed,
                sandbox=sandbox,
                command=command,
                store=store,
            )
    if archive_state == "deleted":
        _expire_candidate_policy(binding)
        assert retire_expired_candidate(
            binding.database,
            workspace_id=binding.workspace,
            build_id=claimed.claim.build_id,
            store=source.store,
        )
    with workspace_connection(binding.database, binding.workspace) as conn:
        archive_row = conn.execute(
            "SELECT * FROM candidate_archive WHERE build_id = %s", (claimed.claim.build_id,)
        ).fetchone()
        process_row = conn.execute(
            "SELECT * FROM candidate_process_receipt WHERE build_id = %s",
            (claimed.claim.build_id,),
        ).fetchone()
        assert archive_row is not None and process_row is not None
        materialization_row = conn.execute(
            "SELECT * FROM candidate_materialization WHERE build_id=%s",
            (claimed.claim.build_id,),
        ).fetchone()
        assert materialization_row is not None
        assert (materialization_row["build_artifact_id"] is not None) is retained
        if archive_state == "retained":
            # Durable dispatch intent over actual retained bytes; no regression execution is
            # fabricated. A restored intent cannot establish what ran on the original daemon.
            regression_claim = regressions.claim(
                conn,
                workspace_id=binding.workspace,
                build_id=claimed.claim.build_id,
                artifact_digest=str(archive_row["content_digest"]),
                policy_digest="d" * 64,
                image_id=str(process_row["image_id"]),
                daemon_endpoint=sandbox.daemon.endpoint,
                daemon_id=sandbox.daemon.daemon_id,
                endpoint_required=True,
            )
            regressions.dispatch(
                conn,
                claim=regression_claim,
                policy_digest="d" * 64,
                artifact_digest=str(archive_row["content_digest"]),
            )
            # Synthetic crash-state seed, not observed endpoint/process execution evidence.
            # The backup drill must retain this intent but never resume it or invent cleanup.
            endpoint_plan = CandidateGateway(
                binding=CandidateEndpointBinding(
                    task_id=regression_claim.attempt_id,
                    artifact_digest=str(archive_row["content_digest"]),
                    runtime_policy_digest="d" * 64,
                    candidate_id="c" * 64,
                    driver_id="e" * 64,
                    image_id=str(process_row["image_id"]),
                    daemon=sandbox.daemon,
                ),
                nonce="synthetic-restore-fixture",
                transport=lambda *args: {},
            ).plan()
            conn.execute(
                "INSERT INTO candidate_endpoint(attempt_id,workspace_id,plan,state,expires_at) "
                "VALUES (%s,%s,%s::jsonb,'PLANNED',clock_timestamp()+interval '30 seconds')",
                (regression_claim.attempt_id, binding.workspace, json.dumps(endpoint_plan)),
            )
    key = str(archive_row["object_key"])
    captured = source.store.get_bounded(key=key, max_bytes=int(archive_row["size_bytes"]))
    directory = tmp_path_factory.mktemp("candidate-encrypted-backup")
    key_file, backup = directory / "backup.key", directory / "candidate.afbk"
    env = {
        "ACCESSFORGE_BACKUP_DATABASE_URL": backup_database_url,
        "ACCESSFORGE_EVIDENCE_ENDPOINT_URL": source.settings.endpoint_url,
        "ACCESSFORGE_EVIDENCE_ACCESS_KEY": source.settings.access_key,
        "ACCESSFORGE_EVIDENCE_SECRET_KEY": source.settings.secret_key,
        "ACCESSFORGE_EVIDENCE_BUCKET": source.settings.bucket,
    }
    generated = _operator_script(
        "backup.py",
        ("--key-file", str(key_file), "--write-new-key"),
        env,
    )
    assert generated.returncode == 0, generated.stderr
    backed_up = _operator_script(
        "backup.py",
        ("--key-file", str(key_file), "--output", str(backup)),
        env,
    )
    assert backed_up.returncode == 0, backed_up.stderr
    assert b"print('repaired')" not in backup.read_bytes()
    args = (
        "--archive",
        str(backup),
        "--key-file",
        str(key_file),
        "--target-database-url",
        candidate_restore_target,
        "--operator",
        "candidate-drill",
    )
    # Fail before touching PostgreSQL when a destination bucket is missing or is the source.
    for suffix in ((), ("--target-bucket", source.settings.bucket)):
        refused = _operator_script("restore.py", (*args, *suffix), env)
        assert refused.returncode == 2
        with connect(candidate_restore_target) as conn:
            assert conn.execute(
                "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchone() == {"n": 0}
    source.store.delete(key=key)
    restored = _operator_script(
        "restore.py",
        (*args, "--target-bucket", target.settings.bucket),
        env,
    )
    assert restored.returncode == 0, restored.stderr
    assert "reconciled:" in restored.stdout
    assert not source.store.exists(key=key)
    assert target.store.get_bounded(key=key, max_bytes=max(1, len(captured))) == captured
    restored_app_url = _database_at(
        binding.database, urlsplit(candidate_restore_target).path.lstrip("/")
    )
    with workspace_connection(restored_app_url, binding.workspace) as conn:
        assert (
            conn.execute(
                "SELECT * FROM candidate_materialization WHERE build_id=%s",
                (claimed.claim.build_id,),
            ).fetchone()
            == materialization_row
        )
        if archive_state == "retained":
            assert conn.execute(
                "SELECT state,epoch,failure_code FROM candidate_regression_attempt "
                "WHERE build_id=%s",
                (claimed.claim.build_id,),
            ).fetchone() == {"state": "UNKNOWN", "epoch": 2, "failure_code": "RESTORED_DATABASE"}
            assert conn.execute(
                "SELECT state,plan,receipt,cleanup_confirmed,closed_at FROM candidate_endpoint "
                "WHERE attempt_id=%s",
                (regression_claim.attempt_id,),
            ).fetchone() == {
                "state": "UNKNOWN",
                "plan": endpoint_plan,
                "receipt": None,
                "cleanup_confirmed": False,
                "closed_at": None,
            }
            with pytest.raises(builds.BuildClaimRefused), conn.transaction():
                endpoints.assert_live(conn, claim=regression_claim)
            with pytest.raises(builds.BuildClaimRefused), conn.transaction():
                regressions.dispatch(
                    conn,
                    claim=regression_claim,
                    policy_digest="d" * 64,
                    artifact_digest=str(archive_row["content_digest"]),
                )
        location = conn.execute(
            "SELECT store_endpoint,store_bucket FROM candidate_archive_restore_location "
            "WHERE build_id = %s ORDER BY revision DESC LIMIT 1",
            (claimed.claim.build_id,),
        ).fetchone()
        assert location == {
            "store_endpoint": target.store.storage_identity[0],
            "store_bucket": target.store.storage_identity[1],
        }
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute(
                "UPDATE candidate_archive_restore_location SET store_bucket = 'wrong' "
                "WHERE build_id = %s",
                (claimed.claim.build_id,),
            )
        assert (
            conn.execute(
                "SELECT * FROM candidate_archive WHERE build_id = %s", (claimed.claim.build_id,)
            ).fetchone()
            == archive_row
        )
        assert (
            conn.execute(
                "SELECT * FROM candidate_process_receipt WHERE build_id = %s",
                (claimed.claim.build_id,),
            ).fetchone()
            == process_row
        )
        state = conn.execute(
            "SELECT state,epoch,failure_code FROM candidate_build_attempt WHERE id = %s",
            (claimed.claim.build_id,),
        ).fetchone()
        assert state == {
            "state": "BUILT" if retained else "UNKNOWN",
            "epoch": 1 if retained else 2,
            "failure_code": None if retained else "RESTORED_DATABASE",
        }
        with pytest.raises(builds.BuildClaimRefused):
            builds.authorize_dispatch(
                conn, workspace_id=binding.workspace, claim=claimed.claim, inputs=claimed.inputs
            )
    if archive_state == "retained":
        with pytest.raises(builds.BuildClaimRefused, match="storage location"):
            read_retained_candidate(
                restored_app_url,
                workspace_id=binding.workspace,
                build_id=claimed.claim.build_id,
                store=source.store,
            )
        candidate = read_retained_candidate(
            restored_app_url,
            workspace_id=binding.workspace,
            build_id=claimed.claim.build_id,
            store=target.store,
        )
        assert candidate.files == (SourceFile("out/candidate.txt", b"print('repaired')\n"),)
    else:
        with pytest.raises(builds.BuildClaimRefused, match="no retained"):
            read_retained_candidate(
                restored_app_url,
                workspace_id=binding.workspace,
                build_id=claimed.claim.build_id,
                store=target.store,
            )


@pytest.mark.sandbox
@pytest.mark.parametrize("exit_failure", [False, True])
@pytest.mark.parametrize("fault", ["none", "upload_failure", "tamper", "fenced"])
def test_real_source_claim_docker_capture_and_durable_receipt(
    binding: BoundFixture,
    exit_failure: bool,
    fault: str,
    candidate_store: evidence.S3ArtifactStore,
) -> None:
    claimed, sandbox, command = _prepare_owned_build(binding, exit_failure=exit_failure)
    store = FaultStore(candidate_store, binding, fault, [])
    if exit_failure:
        with pytest.raises(SandboxRefused, match="code 23"):
            execute_claim(
                binding.database,
                workspace_id=binding.workspace,
                claimed=claimed,
                sandbox=sandbox,
                command=command,
                store=store,
            )
        with workspace_connection(binding.database, binding.workspace) as conn:
            receipt = conn.execute(
                "SELECT state, cleanup_confirmed, artifact_digest "
                "FROM candidate_build_attempt WHERE id = %s",
                (claimed.claim.build_id,),
            ).fetchone()
            assert receipt == {
                "state": "FAILED",
                "cleanup_confirmed": True,
                "artifact_digest": None,
            }
        return
    if fault != "none":
        expected = (
            evidence.ObjectStoreUnavailable
            if fault == "upload_failure"
            else builds.BuildClaimRefused
        )
        try:
            with pytest.raises(expected):
                execute_claim(
                    binding.database,
                    workspace_id=binding.workspace,
                    claimed=claimed,
                    sandbox=sandbox,
                    command=command,
                    store=store,
                )
            with workspace_connection(binding.database, binding.workspace) as conn:
                row = conn.execute(
                    "SELECT b.state, b.artifact_digest, a.state AS archive_state "
                    "FROM candidate_build_attempt b JOIN candidate_archive a ON a.build_id = b.id "
                    "WHERE b.id = %s",
                    (claimed.claim.build_id,),
                ).fetchone()
                assert row == {
                    "state": "UNKNOWN" if fault == "fenced" else "DISPATCHED",
                    "artifact_digest": None,
                    "archive_state": "QUARANTINED",
                }
            with pytest.raises(builds.BuildClaimRefused, match="no retained"):
                read_retained_candidate(
                    binding.database,
                    workspace_id=binding.workspace,
                    build_id=claimed.claim.build_id,
                    store=store,
                )
        finally:
            for key in store.keys:
                candidate_store.delete(key=key)
        return
    result = execute_claim(
        binding.database,
        workspace_id=binding.workspace,
        claimed=claimed,
        sandbox=sandbox,
        command=command,
        store=store,
    )
    assert result.task_id == claimed.claim.build_id
    assert result.artifact.files == (SourceFile("out/candidate.txt", b"print('repaired')\n"),)
    assert (binding.repository / "app.py").read_bytes() == b"print('owned')\n"
    with workspace_connection(binding.database, binding.workspace) as conn:
        receipt = conn.execute(
            "SELECT state, artifact_digest, cleanup_confirmed, daemon_endpoint, daemon_id "
            "FROM candidate_build_attempt WHERE id = %s",
            (claimed.claim.build_id,),
        ).fetchone()
        assert receipt == {
            "state": "BUILT",
            "artifact_digest": result.artifact.archive_digest,
            "cleanup_confirmed": True,
            "daemon_endpoint": result.daemon.endpoint,
            "daemon_id": result.daemon.daemon_id,
        }
        process = conn.execute(
            "SELECT container_id,image_id,platform FROM candidate_process_receipt "
            "WHERE build_id = %s",
            (claimed.claim.build_id,),
        ).fetchone()
        assert process == {
            "container_id": result.container_id,
            "image_id": result.image_id,
            "platform": result.platform,
        }
        verification = patches.load_verification(
            conn, verification_id=claimed.claim.verification_id
        )
        assert verification.state == "BUILDING" and verification.conclusion is None
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute(
                "UPDATE candidate_process_receipt SET platform = 'linux/other' WHERE build_id = %s",
                (claimed.claim.build_id,),
            )
        with pytest.raises(psycopg.IntegrityError), conn.transaction():
            conn.execute(
                "UPDATE candidate_archive SET size_bytes = 1 WHERE build_id = %s",
                (claimed.claim.build_id,),
            )
    try:
        assert (
            read_retained_candidate(
                binding.database,
                workspace_id=binding.workspace,
                build_id=claimed.claim.build_id,
                store=store,
            )
            == result.artifact
        )
        with pytest.raises(builds.BuildClaimRefused, match="no retained"):
            read_retained_candidate(
                binding.database,
                workspace_id=str(uuid.uuid4()),
                build_id=claimed.claim.build_id,
                store=store,
            )
        candidate_store.put(
            key=store.keys[0],
            payload=b"x" * len(result.artifact.archive()),
            content_type="application/x-tar",
        )
        with pytest.raises(builds.BuildClaimRefused, match="substituted"):
            read_retained_candidate(
                binding.database,
                workspace_id=binding.workspace,
                build_id=claimed.claim.build_id,
                store=store,
            )
    finally:
        for key in store.keys:
            candidate_store.delete(key=key)
