"""Owned E0 protected HTTP/database regressions, outside the candidate filesystem/processes.

Three disposable containers share ONLY a network-none loopback namespace. The candidate never
receives the PostgreSQL administrator identity or the driver's process/filesystem. Results are
supervisor observations, not candidate stdout, and do not attest an actual screen-reader run.
This bounded local primitive has no public endpoint or automatic retry after ambiguous cleanup.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from types import CodeType
from typing import Any
from urllib.parse import urlencode

from accessforge_contracts.reference_fixture import (
    REFERENCE_FIXTURE_DIGEST,
    REFERENCE_FIXTURE_VERSION,
)
from accessforge_domain.canonical import digest

from .candidate_gateway import CandidateEndpointBinding, CandidateGateway, gateway_policy
from .sandbox import (
    MEMORY,
    OWNER_LABEL,
    PIDS,
    SCRATCH,
    USER,
    CleanupUnconfirmed,
    DaemonBinding,
    DockerSandbox,
    SandboxPolicy,
    SandboxRefused,
)
from .snapshot import MAX_ARCHIVE_BYTES, SourceFile, SourceSnapshot, read_artifact

POSTGRES_IMAGE = "postgres@sha256:67f41722b7a8cbdb868a44a4995c846eddfdc2973bccb291ce937dce88ad5675"
_PG = "/usr/lib/postgresql/17/bin/"
_DB_SCRATCH = "rw,noexec,nosuid,nodev,size=256m,uid=999,gid=999,mode=0700"
_DB_UNUSED = "rw,noexec,nosuid,nodev,size=4096,uid=999,gid=999,mode=0700"
# Trusted fixture contract, never loaded from the candidate wheel. The app role gets only DML,
# so a candidate cannot substitute a view/function/table to manufacture the oracle's receipt.
_SCHEMA = """
CREATE TABLE public.fixture_instance (
 nonce TEXT PRIMARY KEY, template_digest TEXT NOT NULL,
 variant TEXT NOT NULL CHECK (variant IN ('accessible','inaccessible')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE public.service_request (
 id UUID PRIMARY KEY, fixture_nonce TEXT NOT NULL REFERENCES public.fixture_instance(nonce)
 ON DELETE CASCADE, full_name TEXT NOT NULL, email TEXT NOT NULL, category TEXT NOT NULL,
 description TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX service_request_one_per_fixture ON public.service_request(fixture_nonce);
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO candidate;
GRANT SELECT, INSERT, DELETE, TRUNCATE
ON public.fixture_instance, public.service_request TO candidate;
"""
_HTTP = """
import http.client,json,sys
request=json.loads(sys.stdin.buffer.read(16385))
client=http.client.HTTPConnection('127.0.0.1',8081,timeout=3)
client.request(request['method'],request['path'],body=request['body'],headers=request['headers'])
response=client.getresponse()
body=response.read(262145)
if len(body)>262144: raise ValueError('response exceeds bound')
print(json.dumps({'status':response.status,'body':body.decode()}))
client.close()
"""


def _code_identity(code: CodeType) -> dict[str, Any]:
    """Stable executable identity without marshal reference flags or debug filenames."""

    def constant(value: Any) -> Any:
        if isinstance(value, CodeType):
            return _code_identity(value)
        if isinstance(value, (tuple, frozenset)):
            entries = [constant(item) for item in value]
            if isinstance(value, frozenset):
                entries.sort(key=lambda item: json.dumps(item, sort_keys=True))
            return {"type": type(value).__name__, "items": entries}
        if value is None or isinstance(value, (str, bytes, int, float, complex, bool)):
            return {"type": type(value).__name__, "value": repr(value)}
        raise SandboxRefused("unsupported trusted harness code constant")

    return {
        "bytecode": code.co_code.hex(),
        "exceptions": code.co_exceptiontable.hex(),
        "constants": [constant(value) for value in code.co_consts],
        "names": list(code.co_names),
        "variables": list(code.co_varnames),
        "free": list(code.co_freevars),
        "cells": list(code.co_cellvars),
        "flags": code.co_flags,
        "args": code.co_argcount,
        "positionalOnly": code.co_posonlyargcount,
        "keywordOnly": code.co_kwonlyargcount,
        "stack": code.co_stacksize,
    }


@dataclass(frozen=True, slots=True)
class ReferenceRegressionResult:
    artifact_digest: str
    task_id: str
    daemon: DaemonBinding
    checks: tuple[str, ...]
    # Result construction occurs only after exact owned-resource cleanup is confirmed.
    containers: tuple[tuple[str, str, str], ...]  # role, immutable container ID, image ID


class ReferenceRegressions:
    """Trusted supervisor for the owned reference fixture, never a tenant-selectable harness."""

    def __init__(self, *, image: str, daemon: DaemonBinding) -> None:
        self.sandbox = DockerSandbox(SandboxPolicy(image=image, wall_seconds=120), daemon=daemon)
        self.image = image

    def policy_digest(self) -> str:
        # Bind the executing code object (including nested harness functions), not just the
        # mutable source file on disk. This is a local Python-runtime identity, not a wire ABI.
        return digest(
            {
                "version": "owned-reference-regressions-v1",
                "code": _code_identity(ReferenceRegressions.run.__code__),
                "schema": _SCHEMA,
                "httpDriver": _HTTP,
                "postgres": POSTGRES_IMAGE,
                "postgresBinaries": _PG,
                "fixtureDigest": REFERENCE_FIXTURE_DIGEST,
                "fixtureVersion": REFERENCE_FIXTURE_VERSION,
                "browserBridge": gateway_policy(_code_identity),
                "image": self.image,
                "daemonEndpoint": self.sandbox.daemon.endpoint,
                "daemonId": self.sandbox.daemon.daemon_id,
                "memory": MEMORY,
                "pids": PIDS,
                "scratch": SCRATCH,
                "dbScratch": _DB_SCRATCH,
                "unusedVolume": _DB_UNUSED,
                "user": USER,
                "ownerLabel": OWNER_LABEL,
            }
        )

    def run(
        self,
        artifact: SourceSnapshot,
        *,
        cancelled: Callable[[], bool] = lambda: False,
        task_id: str | None = None,
        on_planned: Callable[[str, str, str], None] = lambda role, name, image: None,
        on_created: Callable[[str, str, str], None] = lambda role, container, image: None,
        on_removed: Callable[[str], None] = lambda role: None,
        on_candidate_endpoint: Callable[[CandidateGateway], None] | None = None,
        assert_endpoint_authority: Callable[[], None] = lambda: None,
        assert_endpoint_live: Callable[[], None] = lambda: None,
        assert_candidate_request: Callable[[str], None] = lambda method: None,
        on_endpoint_planned: Callable[[dict[str, Any]], None] = lambda identity: None,
        on_endpoint_bound: Callable[[dict[str, Any]], None] = lambda receipt: None,
        on_endpoint_closed: Callable[[bool], None] = lambda clean: None,
    ) -> ReferenceRegressionResult:
        wheel = "out/accessforge_reference_app-0.0.0-py3-none-any.whl"
        if len(artifact.files) != 1 or artifact.files[0].path != wheel:
            raise SandboxRefused("regressions require the captured owned reference wheel")
        sandbox = self.sandbox
        deadline = time.monotonic() + 120
        task = task_id or str(uuid.uuid4())
        if str(uuid.UUID(task)) != task:
            raise SandboxRefused("regression task ID must be canonical")
        owned: list[tuple[str, str, bool]] = []
        receipts: list[tuple[str, str, str]] = []

        def checked(*args: str, payload: bytes | None = None) -> bytes:
            return sandbox._checked(
                *args, deadline=deadline, input_bytes=payload, cancelled=cancelled
            ).stdout

        def create(role: str, image: str, network: str, *, database: bool = False) -> str:
            sandbox._assert_daemon(deadline=deadline)
            metadata = json.loads(checked("image", "inspect", image))
            image_id = metadata[0]["Id"]
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
                raise SandboxRefused("runtime image has no immutable identity")
            name = f"accessforge-regression-{task}-{role}"
            on_planned(role, name, image_id)
            owned.append((name, role, False))
            tmpfs = {"/work": _DB_SCRATCH if database else SCRATCH}
            if database:
                # Shadow the official image's declared data volume: no implicit host volume.
                tmpfs["/var/lib/postgresql/data"] = _DB_UNUSED
            tmp_args = tuple(
                arg for path, options in tmpfs.items() for arg in ("--tmpfs", f"{path}:{options}")
            )
            user = "999:999" if database else USER
            container = (
                checked(
                    "container",
                    "create",
                    "--pull=never",
                    "--name",
                    name,
                    "--label",
                    f"{OWNER_LABEL}={task}",
                    "--user",
                    user,
                    "--read-only",
                    "--network",
                    network,
                    "--ipc",
                    "none",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--pids-limit",
                    str(PIDS),
                    "--cpus",
                    "1",
                    "--memory",
                    str(MEMORY),
                    "--memory-swap",
                    str(MEMORY),
                    "--ulimit",
                    "nofile=128:128",
                    "--ulimit",
                    "core=0:0",
                    "--log-driver",
                    "none",
                    "--no-healthcheck",
                    *tmp_args,
                    "--workdir",
                    "/work",
                    "--entrypoint",
                    "/bin/sleep",
                    image_id,
                    "86400",
                )
                .decode()
                .strip()
            )
            if not re.fullmatch(r"[a-f0-9]{64}", container):
                raise CleanupUnconfirmed("runtime create returned no immutable ID")
            owned[-1] = (name, role, True)
            item = sandbox._inspect(container, deadline=deadline)
            if (
                item["HostConfig"].get("NetworkMode") != network
                or item["HostConfig"].get("Tmpfs") != tmpfs
                or item["Config"].get("User") != user
            ):
                raise SandboxRefused("runtime role configuration differs from requested boundary")
            # Reuse all existing mount/capability/resource checks after verifying the ONLY
            # role-specific differences. Actual build policy remains network=none, unchanged.
            normalized = copy.deepcopy(item)
            normalized["HostConfig"].update(NetworkMode="none", Tmpfs={"/work": SCRATCH})
            normalized["Config"]["User"] = USER
            sandbox._assert_configuration(normalized, image_id=image_id, task_id=task)
            sandbox._assert_daemon(deadline=deadline)
            on_created(role, container, image_id)
            checked("container", "start", container)
            receipts.append((role, container, image_id))
            return container

        try:
            database = create("database", POSTGRES_IMAGE, "none", database=True)
            admin = secrets.token_hex(24)
            password = secrets.token_hex(24)
            checked(
                "exec",
                "-i",
                database,
                "/bin/tar",
                "-xf",
                "-",
                "-C",
                "/work",
                payload=SourceSnapshot((SourceFile("admin-password", admin.encode()),)).archive(),
            )
            checked(
                "exec",
                database,
                _PG + "initdb",
                "-D",
                "/work/data",
                "-U",
                "postgres",
                "--auth-local=trust",
                "--auth-host=scram-sha-256",
                "--pwfile=/work/admin-password",
                "--no-locale",
            )
            checked(
                "exec",
                database,
                _PG + "pg_ctl",
                "-D",
                "/work/data",
                "-l",
                "/work/pg.log",
                "-o",
                "-h 127.0.0.1 -k /work -c shared_buffers=16MB -c max_connections=10",
                "-w",
                "start",
            )

            def sql(statement: str) -> str:
                return (
                    checked(
                        "exec",
                        "-i",
                        database,
                        _PG + "psql",
                        "-X",
                        "-A",
                        "-t",
                        "-v",
                        "ON_ERROR_STOP=1",
                        "-h",
                        "/work",
                        "-U",
                        "postgres",
                        "-d",
                        "reference",
                        payload=statement.encode(),
                    )
                    .decode()
                    .strip()
                )

            checked(
                "exec",
                "-i",
                database,
                _PG + "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-h",
                "/work",
                "-U",
                "postgres",
                "-d",
                "postgres",
                payload=(
                    f"CREATE ROLE candidate LOGIN PASSWORD '{password}' NOSUPERUSER "
                    "NOCREATEDB NOCREATEROLE NOREPLICATION; "
                    "CREATE DATABASE reference;"
                ).encode(),
            )
            sql(_SCHEMA)
            driver = create("driver", self.image, "container:" + database)
            canary = checked(
                "exec",
                driver,
                "/usr/local/bin/python",
                "-I",
                "-c",
                "import socket,pathlib\n"
                "for host in ('169.254.169.254','1.1.1.1'):\n"
                "    try: connection=socket.create_connection((host,80),timeout=0.25)\n"
                "    except OSError: pass\n"
                "    else: connection.close(); raise RuntimeError('egress available')\n"
                "assert not pathlib.Path('/work/src').exists()\n"
                "assert not pathlib.Path('/var/run/docker.sock').exists()\n"
                "print('isolated')",
            )
            if canary.strip() != b"isolated":
                raise SandboxRefused("private regression network canary failed")
            denied = checked(
                "exec",
                "-i",
                driver,
                "/usr/local/bin/python",
                "-I",
                "-c",
                "import sys,psycopg\n"
                "with psycopg.connect(sys.stdin.read(),autocommit=True) as conn:\n"
                "    for query in ('DROP TABLE public.service_request',"
                "'CREATE TABLE public.forged_oracle (id integer)',"
                "'SELECT rolpassword FROM pg_authid'):\n"
                "        try: conn.execute(query)\n"
                "        except psycopg.errors.InsufficientPrivilege: pass\n"
                "        else: raise RuntimeError('candidate has oracle authority')\n"
                "print('denied')",
                payload=f"postgresql://candidate:{password}@127.0.0.1:5432/reference".encode(),
            )
            if denied.strip() != b"denied":
                raise SandboxRefused("candidate database authority exceeds the fixture contract")
            setup, observer = secrets.token_hex(24), secrets.token_hex(24)
            environment = {
                "REFAPP_DATABASE_URL": f"postgresql://candidate:{password}@127.0.0.1:5432/reference",
                "REFAPP_HOST": "127.0.0.1",
                "REFAPP_PORT": "8081",
                "REFAPP_SETUP_TOKEN": setup,
                "REFAPP_OBSERVER_TOKEN": observer,
                "REFAPP_ENVIRONMENT": "test",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            env_args = tuple(
                arg for key, value in environment.items() for arg in ("-e", f"{key}={value}")
            )
            boot = (
                "import sys;sys.path.insert(0,'/work/src/" + wheel + "');"
                "import uvicorn;from reference_app.app import create_app;"
                "uvicorn.run(create_app(),host='127.0.0.1',port=8081,log_level='error')"
            )

            def start_candidate(role: str) -> str:
                container = create(role, self.image, "container:" + database)
                checked("exec", container, "/bin/mkdir", "/work/src")
                checked(
                    "exec",
                    "-i",
                    container,
                    "/bin/tar",
                    "-xf",
                    "-",
                    "-C",
                    "/work/src",
                    payload=artifact.archive(),
                )
                read_deployed_artifact(container, deadline)
                checked(
                    "exec", "-d", *env_args, container, "/usr/local/bin/python", "-I", "-c", boot
                )
                return container

            def read_deployed_artifact(container: str, end: float) -> SourceSnapshot:
                # Use the immutable runtime image's tar binary, not candidate stdout or markers.
                # Capture is bounded and parsed as inert bytes on the host; no extraction/import.
                payload = sandbox._checked(
                    "exec",
                    container,
                    "/bin/tar",
                    "-cf",
                    "-",
                    "-C",
                    "/work/src",
                    "out",
                    deadline=end,
                    limit=MAX_ARCHIVE_BYTES,
                    cancelled=cancelled,
                ).stdout
                observed = read_artifact(payload)
                if observed.archive_digest != artifact.archive_digest:
                    raise SandboxRefused("deployed candidate artifact bytes or modes changed")
                return observed

            candidate = start_candidate("candidate")

            def http(
                method: str,
                path: str,
                *,
                headers: dict[str, str] | None = None,
                body: str = "",
                request_deadline: float | None = None,
            ) -> dict[str, Any]:
                request = json.dumps(
                    {"method": method, "path": path, "headers": headers or {}, "body": body}
                ).encode()
                if len(request) > 16384:
                    raise SandboxRefused("protected request exceeds bound")
                response = json.loads(
                    sandbox._checked(
                        "exec",
                        "-i",
                        driver,
                        "/usr/local/bin/python",
                        "-I",
                        "-c",
                        _HTTP,
                        input_bytes=request,
                        deadline=min(deadline, request_deadline or time.monotonic() + 5),
                        cancelled=cancelled,
                    ).stdout
                )
                if not isinstance(response, dict):
                    raise SandboxRefused("invalid trusted driver response")
                return response

            def ready() -> None:
                for _ in range(40):
                    try:
                        if http("GET", "/health/ready")["status"] == 200:
                            return
                    except SandboxRefused:
                        pass
                    time.sleep(0.1)
                raise SandboxRefused("captured candidate did not become ready")

            ready()

            if on_candidate_endpoint is not None:
                # An opt-in trusted capability probe, not an automatically exposed browser port
                # or matched-reader attestation. No setup/observer routes enter the bridge.
                assert_endpoint_authority()
                seeded = http(
                    "POST",
                    "/api/_test/fixtures?variant=inaccessible",
                    headers={"x-setup-token": setup},
                )
                declaration = json.loads(seeded["body"])
                if (
                    seeded["status"] != 201
                    or declaration.get("template_digest") != REFERENCE_FIXTURE_DIGEST
                    or declaration.get("template_version") != REFERENCE_FIXTURE_VERSION
                    or sql("SELECT template_digest FROM fixture_instance")
                    != REFERENCE_FIXTURE_DIGEST
                ):
                    raise SandboxRefused("browser candidate fixture identity differs")

                def observe_deployment(end: float) -> dict[str, Any]:
                    assert_endpoint_live()
                    assert_candidate_request("GET")
                    sandbox._assert_daemon(deadline=end)
                    for container in (candidate, driver):
                        item = sandbox._inspect(container, deadline=end)
                        if (
                            item["Id"] != container
                            or not item["State"]["Running"]
                            or item["HostConfig"].get("NetworkMode") != "container:" + database
                        ):
                            raise SandboxRefused("served candidate process identity changed")
                        normalized = copy.deepcopy(item)
                        normalized["HostConfig"]["NetworkMode"] = "none"
                        sandbox._assert_configuration(normalized, image_id=self.image, task_id=task)
                    observed = read_deployed_artifact(candidate, end)
                    assert_endpoint_live()
                    assert_candidate_request("GET")
                    if cancelled() or time.monotonic() >= end:
                        raise SandboxRefused("candidate artifact observation expired")
                    return {
                        "taskId": task,
                        "candidateId": candidate,
                        "imageId": self.image,
                        "daemonId": sandbox.daemon.daemon_id,
                        "artifactDigest": observed.archive_digest,
                        "artifactTreeDigest": observed.tree_digest,
                        "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        "meaning": "DEPLOYED_FILESYSTEM_MEASUREMENT_NOT_EXECUTION_ATTESTATION",
                    }

                def transport(method: str, path: str, body: str) -> dict[str, Any]:
                    end = min(deadline, time.monotonic() + 5)
                    observe_deployment(end)
                    assert_candidate_request(method)
                    response = http(
                        method,
                        path,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        body=body,
                        request_deadline=end,
                    )
                    # If a POST's result cannot be verified, fail without replaying the effect.
                    # These are filesystem samples, not proof against change-and-restore between
                    # samples or arbitrary process-memory behavior inside the isolated candidate.
                    observe_deployment(end)
                    assert_candidate_request(method)
                    return response

                with CandidateGateway(
                    binding=CandidateEndpointBinding(
                        task_id=task,
                        artifact_digest=artifact.archive_digest,
                        # Runtime policy, not an unverified application-reported environment.
                        # Matching it to a sealed reader environment remains controller work.
                        runtime_policy_digest=self.policy_digest(),
                        candidate_id=candidate,
                        driver_id=driver,
                        image_id=self.image,
                        daemon=sandbox.daemon,
                    ),
                    nonce=declaration["nonce"],
                    transport=transport,
                    observe_artifact=lambda: observe_deployment(
                        min(deadline, time.monotonic() + 5)
                    ),
                    on_planned=on_endpoint_planned,
                    on_bound=on_endpoint_bound,
                    on_admit=assert_endpoint_live,
                    on_closed=on_endpoint_closed,
                ) as gateway:
                    on_candidate_endpoint(gateway)
                assert_endpoint_authority()
                # Protected regressions get a fresh fixture after the separate browser probe;
                # callback results never count as assertion or backend regression evidence.
                sql("TRUNCATE service_request, fixture_instance CASCADE")

            checks: list[str] = ["private_network_canary", "candidate_ddl_and_admin_denied"]

            def expect(condition: bool, name: str) -> None:
                if not condition:
                    raise SandboxRefused("protected regression failed: " + name)
                checks.append(name)

            fixture = http(
                "POST", "/api/_test/fixtures?variant=inaccessible", headers={"x-setup-token": setup}
            )
            expect(fixture["status"] == 201, "fixture_creation")
            declared = json.loads(fixture["body"])
            expect(
                declared.get("template_digest") == REFERENCE_FIXTURE_DIGEST
                and declared.get("template_version") == REFERENCE_FIXTURE_VERSION,
                "fixture_definition_identity",
            )
            nonce = declared["nonce"]
            if not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", nonce):
                raise SandboxRefused("invalid candidate fixture nonce")
            expect(sql("SELECT count(*) FROM fixture_instance") == "1", "durable_fixture")
            expect(
                sql("SELECT template_digest FROM fixture_instance") == REFERENCE_FIXTURE_DIGEST,
                "independent_fixture_definition",
            )
            for headers in ({}, {"x-setup-token": observer}, {"x-setup-token": "wrong"}):
                expect(
                    http("POST", "/api/_test/fixtures?variant=inaccessible", headers=headers)[
                        "status"
                    ]
                    == 403,
                    "fixture_creation_authorization_" + str(len(checks)),
                )
                expect(
                    sql("SELECT count(*) FROM fixture_instance") == "1",
                    "unauthorized_fixture_no_write_" + str(len(checks)),
                )
            expect(http("GET", f"/form/{nonce}")["status"] == 200, "form_available")
            valid = {
                "full_name": "Test Person",
                "email": "test.person@example.test",
                "category": "access-request",
                "description": "Keyboard access request for testing.",
            }
            form = {"Content-Type": "application/x-www-form-urlencoded"}
            for field, value in (
                ("email", "not-an-email"),
                ("full_name", ""),
                ("category", "forbidden"),
                ("description", "short"),
            ):
                response = http(
                    "POST", f"/form/{nonce}", headers=form, body=urlencode({**valid, field: value})
                )
                expect(response["status"] == 422, "reject_invalid_" + field)
                expect(
                    sql("SELECT count(*) FROM service_request") == "0", "no_invalid_write_" + field
                )
            for headers in ({}, {"x-observer-token": setup}, {"x-observer-token": "wrong"}):
                expect(
                    http("GET", f"/api/_test/receipt/{nonce}", headers=headers)["status"] == 403,
                    "observer_authorization_" + str(len(checks)),
                )
            accepted = http("POST", f"/form/{nonce}", headers=form, body=urlencode(valid))
            expect(accepted["status"] == 201, "valid_submission")
            rows = json.loads(
                sql(
                    "SELECT coalesce(json_agg(r), '[]') FROM "
                    "(SELECT fixture_nonce,full_name,email,category,description "
                    "FROM service_request) r"
                )
            )
            expect(
                rows == [{"fixture_nonce": nonce, **valid}], "exact_independent_database_receipt"
            )
            expect(
                http("POST", f"/form/{nonce}", headers=form, body=urlencode(valid))["status"]
                == 409,
                "duplicate_conflict",
            )
            for headers in ({}, {"x-setup-token": observer}, {"x-setup-token": "wrong"}):
                expect(
                    http("POST", "/api/_test/reset", headers=headers)["status"] == 403,
                    "reset_authorization_" + str(len(checks)),
                )
                expect(
                    sql("SELECT count(*) FROM service_request") == "1",
                    "unauthorized_reset_preserves_" + str(len(checks)),
                )
            expect(
                http("POST", "/form/nonexistent-fixture", headers=form, body=urlencode(valid))[
                    "status"
                ]
                == 404,
                "unknown_fixture_refused",
            )
            expect(sql("SELECT count(*) FROM service_request") == "1", "exactly_one_after_retries")
            # A genuinely fresh candidate process/filesystem, using the same immutable wheel.
            checked("container", "kill", candidate)
            candidate = start_candidate("restarted-candidate")
            ready()
            expect(http("GET", f"/form/{nonce}")["status"] == 200, "fixture_survives_restart")
            expect(
                http("POST", f"/form/{nonce}", headers=form, body=urlencode(valid))["status"]
                == 409,
                "restart_preserves_duplicate_guard",
            )
            checked("container", "kill", candidate)
            # Close late-write authority before the final independent read. Killing a client
            # alone is not proof that its PostgreSQL backend has finished executing a query.
            sql(
                "ALTER ROLE candidate NOLOGIN; SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity WHERE usename='candidate'"
            )
            for _ in range(30):
                if sql("SELECT count(*) FROM pg_stat_activity WHERE usename='candidate'") == "0":
                    break
                time.sleep(0.1)
            else:
                raise SandboxRefused("candidate database writers have not stopped")
            final_rows = json.loads(
                sql(
                    "SELECT coalesce(json_agg(r), '[]') FROM "
                    "(SELECT fixture_nonce,full_name,email,category,description "
                    "FROM public.service_request) r"
                )
            )
            expect(final_rows == [{"fixture_nonce": nonce, **valid}], "durable_receipt_after_stop")
            expect(
                sql("SELECT has_schema_privilege('candidate','public','CREATE')") == "f",
                "oracle_schema_outside_candidate_authority",
            )
            sandbox._assert_daemon(deadline=deadline)
            result = ReferenceRegressionResult(
                artifact.archive_digest, task, sandbox.daemon, tuple(checks), tuple(receipts)
            )
        finally:
            failures: list[str] = []
            for name, role, confirmed in reversed(owned):
                try:
                    sandbox._cleanup(name, task)
                    if not confirmed:
                        failures.append(role + " create outcome unknown")
                    else:
                        on_removed(role)
                except Exception:
                    failures.append(role + " cleanup unconfirmed")
            if failures:
                raise CleanupUnconfirmed(
                    "; ".join(failures) + f"; reconcile regression task {task}"
                )
        return result
