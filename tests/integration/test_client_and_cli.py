"""The generated client and the operator CLI, against a real server.

Module 18's acceptance gate asks for a clean client that can create a project, request a run,
inspect its state and retrieve an export through the real API. This exercises that path — and the
parts of it that are still BLOCKED are asserted as blocked rather than skipped, because a suite that
quietly omitted them would leave the gate looking closer than it is.

Two properties matter more than coverage here:

* **A 202 never reads as a result.** The client returns `Requested`, the CLI prints "requested", and
  neither offers anything a script could mistake for an outcome.
* **The client cannot bypass server policy.** Every refusal below comes from the server. There is no
  client-side check that skips a round trip, because a client-side rule that disagreed with the
  server would be a second, wrong definition of the rules.

Requirements: FR-001–FR-007, FR-010–FR-016.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from accessforge_client import OPERATIONS, PATHS, AccessForgeClient, ApiProblem, Requested
from accessforge_persistence import (
    assert_row_level_security_enforced,
    budgets,
    migrate,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
WS = str(uuid.UUID(int=0x340))
OWNER = str(uuid.UUID(int=0x341))
EMAIL = "operator@example.test"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    """A real uvicorn process with local-development sign-in enabled.

    `local-development` accepts an email with no secret, which is an authentication bypass by
    construction — and the server refuses to start with it unless the environment is `local`.
    Setting the variable is not enough, which is why this fixture also sets the environment, and why
    that combination must never appear anywhere but a test or a developer's own machine.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("TEST_DATABASE_URL is not configured; this suite cannot run against nothing")

    port = _free_port()
    process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "accessforge_api"],
        cwd=ROOT,
        env={
            **os.environ,
            "ACCESSFORGE_DATABASE_URL": url,
            "ACCESSFORGE_EVIDENCE_ENDPOINT_URL": os.environ.get(
                "OBJECT_STORE_ENDPOINT", "http://127.0.0.1:9000"
            ),
            "ACCESSFORGE_EVIDENCE_BUCKET": os.environ.get(
                "OBJECT_STORE_BUCKET", "accessforge-evidence"
            ),
            "ACCESSFORGE_EVIDENCE_ACCESS_KEY": os.environ.get(
                "OBJECT_STORE_ACCESS_KEY", "accessforge"
            ),
            "ACCESSFORGE_EVIDENCE_SECRET_KEY": os.environ.get(
                "OBJECT_STORE_SECRET_KEY", "unset-for-this-test"
            ),
            "ACCESSFORGE_ENVIRONMENT": "local",
            "ACCESSFORGE_IDENTITY_PROVIDER": "local-development",
            "ACCESSFORGE_PORT": str(port),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise AssertionError(
                    f"the server exited: {process.stdout and process.stdout.read()}"
                )
            try:
                if httpx.get(f"{base}/health/live", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:  # pragma: no cover - only when the server never starts
            raise AssertionError("the server never became live")
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
        conn.execute("TRUNCATE app_user CASCADE")
    with unscoped_connection(test_database_url) as conn:
        conn.execute("INSERT INTO workspace (id, name) VALUES (%s, 'Client')", (WS,))
        conn.execute("INSERT INTO app_user (id, email) VALUES (%s, %s)", (OWNER, EMAIL))
    with workspace_connection(test_database_url, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) VALUES (%s,%s,'OWNER')",
            (WS, OWNER),
        )
        budgets.configure_entitlement(
            conn,
            workspace_id=WS,
            max_runs_per_day=100,
            max_actions_per_day=10_000,
            max_wall_seconds_per_day=86_400,
            max_model_tokens_per_day=1_000_000,
            max_concurrent_runs=10,
            configured_by="test-fixture",
            reason="generous, so these tests exercise the client rather than the limit",
        )
    yield test_database_url


@pytest.fixture()
def client(db: str, server: str) -> Iterator[AccessForgeClient]:
    with AccessForgeClient(server) as api:
        api.sign_in(EMAIL)
        yield api


# --- the generated operation table ---------------------------------------------------------------


def test_every_generated_operation_names_a_route_the_server_serves(server: str) -> None:
    """The table is generated from the contract, which is generated from the app.

    This closes the loop at runtime rather than trusting the chain: it compares the generated paths
    against what the running server publishes. A generator that silently produced yesterday's table
    would pass a file diff and fail here.
    """
    published = httpx.get(f"{server}/openapi.json", timeout=10).json()["paths"]
    assert set(PATHS) == set(published)
    assert OPERATIONS, "the generated table is empty"

    for identifier, operation in OPERATIONS.items():
        assert operation.path in published, identifier
        assert operation.method.lower() in published[operation.path], identifier


def test_rendering_a_path_without_its_parameters_is_refused() -> None:
    """An unsubstituted `{workspace_id}` is a 404 from a URL that looks almost right."""
    operation = OPERATIONS["list_projects"]
    with pytest.raises(KeyError, match="workspace_id"):
        operation.render()


def test_calling_an_operation_this_build_does_not_have_says_so(server: str) -> None:
    with AccessForgeClient(server) as api:
        with pytest.raises(KeyError, match="does not serve"):
            api.call("publish_to_github", workspace_id=WS)


# --- the client ----------------------------------------------------------------------------------


def test_a_mutation_without_a_session_is_refused_before_it_is_sent(server: str) -> None:
    """The CSRF header is paired with the session and cannot be supplied separately.

    Refused here rather than sent and rejected, because this is the one client-side check that does
    not duplicate a server rule: it is about what this client can construct, not about what the
    server permits.
    """
    with AccessForgeClient(server) as api:
        with pytest.raises(ApiProblem, match="no session"):
            api.call("create_project", workspace_id=WS, body={"name": "x"})


def test_a_project_is_created_and_read_back(client: AccessForgeClient) -> None:
    created = client.call("create_project", workspace_id=WS, body={"name": "Client project"})
    read = client.call("get_project", workspace_id=WS, project_id=created["projectId"])
    assert read["name"] == "Client project"


def test_a_problem_document_becomes_a_typed_exception_carrying_its_code(
    client: AccessForgeClient,
) -> None:
    """Callers branch on `code`. Nobody can usefully branch on prose, and the prose changes."""
    with pytest.raises(ApiProblem) as raised:
        client.call("get_project", workspace_id=WS, project_id=str(uuid.uuid4()))
    assert raised.value.code == "RESOURCE_NOT_FOUND"
    assert raised.value.status == 404
    assert raised.value.request_id


def test_requesting_a_run_returns_a_request_and_not_a_result(
    db: str, client: AccessForgeClient, manifest: Callable[[str], str]
) -> None:
    """The single confusion this product exists to prevent, at the client boundary.

    `Requested` has no field a caller could read as an outcome and no truthiness anybody would
    misinterpret. A client that returned the 202 body like any other success is how somebody writes
    `if client.request_run(...)` and believes a run happened.
    """
    project = client.call("create_project", workspace_id=WS, body={"name": "Runs"})
    manifest = manifest(project["projectId"])

    outcome = client.call(
        "request_run",
        workspace_id=WS,
        body={"projectId": project["projectId"], "manifestDigest": manifest},
    )
    assert isinstance(outcome, Requested)
    assert outcome.identifier
    assert outcome.status_location and outcome.status_location.endswith(outcome.identifier)
    assert "has not been performed" in outcome.means
    # No field a caller could read as a verdict. `Requested` is the whole answer.
    assert not hasattr(outcome, "outcome")
    # The body may carry an outcome field, and if it does it says NOT_EVALUATED — which is the
    # truth at this point and not a placeholder for one.
    assert outcome.body.get("outcome") in (None, "NOT_EVALUATED")

    # And the run itself reports a status, with the outcome held separately and not evaluated.
    runs = client.call("list_runs", workspace_id=WS)["items"]
    assert runs[0]["status"] == "QUEUED"
    assert runs[0]["outcome"] == "NOT_EVALUATED"


def test_the_same_idempotency_key_replays_rather_than_creating_twice(
    db: str, client: AccessForgeClient, manifest: Callable[[str], str]
) -> None:
    project = client.call("create_project", workspace_id=WS, body={"name": "Idempotent"})
    manifest = manifest(project["projectId"])
    key = str(uuid.uuid4())
    body = {"projectId": project["projectId"], "manifestDigest": manifest}

    first = client.call("request_run", workspace_id=WS, body=body, idempotency_key=key)
    second = client.call("request_run", workspace_id=WS, body=body, idempotency_key=key)
    assert isinstance(first, Requested)
    assert isinstance(second, Requested)
    assert first.identifier == second.identifier
    assert len(client.call("list_runs", workspace_id=WS)["items"]) == 1


def test_the_same_key_with_a_different_body_is_a_conflict(
    db: str, client: AccessForgeClient, manifest: Callable[[str], str]
) -> None:
    """Two different operations wearing one name; replaying the first would discard the second."""
    project = client.call("create_project", workspace_id=WS, body={"name": "Conflict"})
    manifest = manifest(project["projectId"])
    key = str(uuid.uuid4())

    client.call(
        "request_run",
        workspace_id=WS,
        body={"projectId": project["projectId"], "manifestDigest": manifest},
        idempotency_key=key,
    )
    with pytest.raises(ApiProblem) as raised:
        client.call(
            "request_run",
            workspace_id=WS,
            body={
                "projectId": project["projectId"],
                "manifestDigest": manifest,
                "environment": "x",
            },
            idempotency_key=key,
        )
    assert raised.value.code in {"IDEMPOTENCY_KEY_REUSED", "UNEXPECTED_FIELD"}


# --- the CLI -------------------------------------------------------------------------------------


def _cli(server: str, session_file: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "accessforge_client.cli", "--base-url", server, *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env={**os.environ, "ACCESSFORGE_CLI_SESSION": str(session_file)},
    )


@pytest.fixture()
def session_file(tmp_path: Path) -> Path:
    return tmp_path / "session.json"


def test_the_cli_signs_in_and_stores_a_session_that_only_the_owner_can_read(
    db: str, server: str, session_file: Path
) -> None:
    """A file at mode 600 rather than an environment variable.

    An environment variable holding a session token is inherited by every child process a shell
    starts; a file mode is a boundary the operating system enforces.
    """
    result = _cli(server, session_file, "sign-in", "--email", EMAIL)
    assert result.returncode == 0, result.stderr
    assert session_file.exists()
    assert oct(session_file.stat().st_mode)[-3:] == "600"
    assert oct(session_file.parent.stat().st_mode)[-3:] == "700"
    assert "sessionToken" in json.loads(session_file.read_text())


def test_the_session_file_is_never_readable_by_anyone_else_even_briefly(
    db: str, server: str, tmp_path: Path
) -> None:
    """The window a mode check after the fact cannot see.

    Writing the file and then chmodding it leaves it at the umask default — 644 on a normal
    configuration — for the interval between the two calls. A session token world-readable for a
    millisecond on a shared machine is a session token world-readable, and the previous version of
    the test above passed against exactly that code because it only looked at the end state.

    Forced here by running under a permissive umask: if the mode came from the umask rather than
    from the `open` call, this is where it shows.
    """
    session_file = tmp_path / "nested" / "session.json"
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            "-c",
            "import os,sys;os.umask(0o000);"
            "from accessforge_client.cli import main;sys.exit(main(sys.argv[1:]))",
            "--base-url",
            server,
            "sign-in",
            "--email",
            EMAIL,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env={**os.environ, "ACCESSFORGE_CLI_SESSION": str(session_file)},
    )
    assert result.returncode == 0, result.stderr
    assert oct(session_file.stat().st_mode)[-3:] == "600"
    assert oct(session_file.parent.stat().st_mode)[-3:] == "700"
    # And nothing was left behind by the atomic write.
    assert [p.name for p in session_file.parent.iterdir()] == ["session.json"]


def test_a_session_is_not_printed_by_its_own_repr(db: str, server: str) -> None:
    """A dataclass prints every field, and this one holds a live credential.

    Nothing in this package prints a `Session` today. That is a property of this month's code, not
    of the type, and the places it would surface — a traceback, a log line, a debugger watch, a bug
    report someone pastes — are exactly the places nobody is looking when it happens.
    """
    with AccessForgeClient(server) as api:
        session = api.sign_in(EMAIL)

    rendered = repr(session)
    assert session.session_token not in rendered
    assert session.csrf_token not in rendered
    assert "Session(" in rendered


def test_the_cli_refuses_a_mutation_when_nobody_is_signed_in(
    db: str, server: str, session_file: Path
) -> None:
    result = _cli(server, session_file, "project", "list", "--workspace", WS)
    assert result.returncode != 0
    assert "not signed in" in result.stdout + result.stderr


def test_the_cli_prints_a_run_request_as_requested_and_never_as_a_result(
    db: str, server: str, session_file: Path, manifest: Callable[[str], str]
) -> None:
    """`jq -r .outcome` on this output returns nothing useful, which is correct."""
    assert _cli(server, session_file, "sign-in", "--email", EMAIL).returncode == 0
    created = _cli(server, session_file, "project", "list", "--workspace", WS)
    assert created.returncode == 0, created.stderr

    with AccessForgeClient(server) as api:
        api.sign_in(EMAIL)
        project = api.call("create_project", workspace_id=WS, body={"name": "CLI"})
    manifest = manifest(project["projectId"])

    result = _cli(
        server,
        session_file,
        "run",
        "request",
        "--workspace",
        WS,
        "--project",
        project["projectId"],
        "--manifest",
        manifest,
    )
    assert result.returncode == 0, result.stderr
    printed = json.loads(result.stdout)
    assert printed["state"] == "requested"
    assert "outcome" not in printed
    assert "has not been performed" in printed["means"]


def test_the_cli_reports_a_refusal_with_its_code_and_request_id(
    db: str, server: str, session_file: Path
) -> None:
    assert _cli(server, session_file, "sign-in", "--email", EMAIL).returncode == 0
    result = _cli(
        server, session_file, "project", "show", "--workspace", WS, "--project", str(uuid.uuid4())
    )
    assert result.returncode == 1
    assert "RESOURCE_NOT_FOUND" in result.stderr
    assert "request id:" in result.stderr


def test_the_cli_lists_only_operations_this_build_can_call(
    db: str, server: str, session_file: Path
) -> None:
    result = _cli(server, session_file, "operations")
    assert result.returncode == 0
    assert "create_execution_grant" in result.stdout
    # Nothing from a module that does not exist.
    assert "publish" not in result.stdout
    assert "patch" not in result.stdout


@pytest.fixture()
def manifest(db: str, seal_manifest: Callable[..., str]) -> Callable[[str], str]:
    """Seal a manifest for a given project, so a run can be requested against it.

    This file previously had a `_manifest()` that returned a bare digest, with a docstring
    explaining that `POST /runs` never checked whether it had been sealed. That gap is closed, so
    the helper is replaced by the real thing rather than by the note about its absence.
    """

    def seal_for(project_id: str) -> str:
        return seal_manifest(db, workspace_id=WS, project_id=project_id, authorized_by=OWNER)

    return seal_for
