"""Real PostgreSQL tenant selection and real Git source binding; no build/reader attestation."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from accessforge_build_worker.artifacts import read_retained_candidate, retire_expired_candidate
from accessforge_build_worker.coordinator import ClaimedCandidate, execute_claim, prepare_and_claim
from accessforge_build_worker.process import CommandResult, CommandStopped
from accessforge_build_worker.reference_regressions import ReferenceRegressions
from accessforge_build_worker.sandbox import (
    DockerSandbox,
    SandboxPolicy,
    SandboxRefused,
    discover_daemon,
)
from accessforge_build_worker.snapshot import SnapshotRefused, SourceFile, SourceSnapshot
from accessforge_build_worker.source_broker import read_persisted_source
from accessforge_build_worker.toolchain import REFERENCE_BUILD_COMMAND
from accessforge_domain.origins import normalize_origin
from accessforge_domain.patch_policy import ProposedChange
from accessforge_domain.states import FindingStatus, Outcome
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import (
    assert_row_level_security_enforced,
    connect,
    evidence,
    migrate,
    patches,
    projects,
    restore,
    retention,
    reviews,
    runs,
    unscoped_connection,
    workspace_connection,
)
from accessforge_persistence import (
    candidate_builds as builds,
)
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
def binding(
    test_database_url: str, tmp_path: Path, request: pytest.FixtureRequest
) -> Iterator[BoundFixture]:
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
    if getattr(request, "param", None) == "reference":
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
                fixture_reset_strategy="RESET_ENDPOINT",
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
        seal = projects.seal_run(
            conn,
            workspace_id=binding.workspace,
            project_id=binding.project,
            source_snapshot_id=binding.snapshot,
            build_artifact_id=artifact,
            environment_manifest_id=env,
            inputs=projects.SealInputs(
                journey_digest="a" * 64,
                assertion_set_digest="a" * 64,
                fixture_digest="a" * 64,
                runner_profile_digest="a" * 64,
                navigator_policy_digest="a" * 64,
                evaluator_version="synthetic-test",
                model_config_digest="b" * 64,
            ),
        )
        run = runs.create_run(
            conn,
            workspace_id=binding.workspace,
            project_id=binding.project,
            manifest_digest=seal.manifest_digest,
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
    ("sabotage", "failure"),
    [
        ("", None),
        (
            "\nfrom . import validation as _validation\n"
            "_validation.validate_service_request = lambda **values: []\n",
            "reject_invalid_email",
        ),
        (
            "\nimport secrets\nsecrets.compare_digest = lambda *args: True\n",
            "fixture_creation_authorization",
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
        ),
    ],
    ids=["healthy", "validation-removed", "authorization-bypassed", "writes-despite-error"],
)
def test_actual_reference_app_wheel_is_built_retained_and_imported_in_isolation(
    binding: BoundFixture,
    isolated_archives: tuple[IsolatedArchiveStore, IsolatedArchiveStore],
    sabotage: str,
    failure: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = os.environ.get("ACCESSFORGE_REFERENCE_TOOLCHAIN")
    if not image:
        if os.environ.get("CI"):
            pytest.fail("the real reference toolchain must be provisioned in CI")
        pytest.skip("real reference toolchain not provisioned")
    path = "src/reference_app/templates.py"
    original = next(file.content for file in binding.source.files if file.path == path)
    # Deliberately a build-only approved edit, not an accessibility repair/reader attestation.
    content = original.decode() + "\n# Owned candidate packaging probe.\n" + sabotage
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
            "pathlib.Path('/work/out/import-ok.txt').write_text('imported captured wheel')",
        ),
    )
    assert smoke.artifact.files[0].content == b"imported captured wheel"
    runner = ReferenceRegressions(image=image, daemon=sandbox.daemon)
    if failure is not None:
        with pytest.raises(SandboxRefused, match=failure):
            runner.run(retained)
        return
    regression = runner.run(retained)
    assert regression.artifact_digest == retained.archive_digest
    assert "exact_independent_database_receipt" in regression.checks
    assert len(regression.containers) == 4
    assert "durable_receipt_after_stop" in regression.checks
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
