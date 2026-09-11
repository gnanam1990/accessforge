from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    for name in (".env", ".env.refapp", ".env.test", ".env.objectstore"):
        _load_file(ROOT / name)


def _load_file(env: Path) -> None:
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not configured; integration proof unavailable")
    return url


@pytest.fixture(scope="session")
def backup_database_url() -> str:
    """A connection that can actually take a backup.

    `FORCE ROW LEVEL SECURITY` applies to the table owner, so `pg_dump` run as the application role
    **fails** — it cannot read the rows it owns. A backup therefore requires a role with
    `BYPASSRLS` or a superuser, and that is an operational fact rather than a test detail: a backup
    script running as the application role produces no backup, and finds out when it is restored.

    Fails rather than skips. A restore drill that quietly did not run is worse than no drill,
    because the handoff would still say the suite was green.
    """
    url = os.environ.get("BACKUP_DATABASE_URL") or os.environ.get("SUPERUSER_URL")
    if not url:
        pytest.fail(
            "neither BACKUP_DATABASE_URL nor SUPERUSER_URL is set, so no role here can read the "
            "rows a backup needs. See .env.test.example: FORCE ROW LEVEL SECURITY means the "
            "application role cannot dump its own tables."
        )
    return url


@pytest.fixture(scope="session")
def seal_manifest() -> Callable[..., str]:
    """Seal a manifest the way the product requires, for tests that need to request a run.

    A fixture rather than an importable helper: `tests/` is not a package, so
    `from tests.sealing import ...` fails at collection — and making it one to share five lines
    would change how every module in this suite is imported.

    `POST /runs` refuses a manifest digest nothing ever sealed. That check closed a real hole (the
    digest was taken on the caller's word, so a run could be queued with an identity matching
    nothing) and it broke twenty-two tests that had been passing a bare `digest({...})`. Those tests
    were not wrong to exist; they were relying on the gap. The repair is to make them do what a
    caller must do, and one helper is what keeps that from becoming twenty-two copies of a fiddly
    five-row setup.

    A manifest covers the source commit, the built artifact, the environment configuration, the
    journey, its assertions, its fixture, the runner profile, the evaluator version and the model
    configuration — all of it, together. The values here are placeholders, which is what a test
    helper is for; the *structure* is real, so a test that requests a run exercises the path a
    caller does.
    """
    import uuid as _uuid
    from datetime import UTC, datetime, timedelta

    from accessforge_domain.canonical import digest
    from accessforge_domain.origins import normalize_origin
    from accessforge_persistence import projects, workspace_connection
    from accessforge_persistence.source_intake import SourceIdentity

    def _digest(seed: str) -> str:
        # 64 hex characters, because every digest column checks that shape and a test that slipped a
        # short string past one would be testing the absence of a constraint.
        return str(digest({"seed": seed}))

    def seal(
        database_url: str,
        *,
        workspace_id: str,
        project_id: str,
        authorized_by: str,
        journey_digest: str | None = None,
        base_url: str = "https://sealed.example.test",
    ) -> str:
        with workspace_connection(database_url, workspace_id) as conn:
            environment_id = projects.register_environment(
                conn,
                workspace_id=workspace_id,
                project_id=project_id,
                spec=projects.EnvironmentSpec(
                    name=f"sealed-{_uuid.uuid4().hex[:8]}",
                    allowed_origins=frozenset({normalize_origin(base_url)}),
                    fixture_reset_strategy="RESET_ENDPOINT",
                    # Two different references. One credential that both reads application state and
                    # rewrites it would let whoever holds it set up the answer and attest to it, and
                    # `register_environment` refuses that outright.
                    observer_credential_ref="observer-profile",
                    reset_credential_ref="reset-profile",
                    permitted_effects=frozenset({"FORM_SUBMIT"}),
                    expires_at=(datetime.now(UTC) + timedelta(days=30))
                    .isoformat()
                    .replace("+00:00", "Z"),
                ),
                authorized_by=authorized_by,
            )
            snapshot_id = projects.record_source_snapshot(
                conn,
                workspace_id=workspace_id,
                project_id=project_id,
                identity=SourceIdentity(
                    commit_sha="a" * 40, tree_digest=_digest("tree"), dirty=False
                ),
                requested_revision="HEAD",
            )
            artifact_id = projects.record_build_artifact(
                conn,
                workspace_id=workspace_id,
                project_id=project_id,
                source_snapshot_id=snapshot_id,
                artifact_digest=_digest("artifact"),
                identity_observable=True,
            )
            sealed = projects.seal_run(
                conn,
                workspace_id=workspace_id,
                project_id=project_id,
                source_snapshot_id=snapshot_id,
                build_artifact_id=artifact_id,
                environment_manifest_id=environment_id,
                inputs=projects.SealInputs(
                    journey_digest=journey_digest or _digest("journey"),
                    assertion_set_digest=_digest("assertions"),
                    fixture_digest=_digest("fixture"),
                    runner_profile_digest=_digest("profile"),
                    navigator_policy_digest=_digest("policy"),
                    evaluator_version="1.0.0",
                    model_config_digest=_digest("model"),
                ),
            )
        return str(sealed.manifest_digest)

    return seal
