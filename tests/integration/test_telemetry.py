"""One structured record per request, and nothing in it that should not leave the process.

Module 26's remaining gap. The product could already tell a caller whether their request succeeded
and could tell an operator nothing at all: the answer to "what is this deployment doing" was server
access and `grep`, which does not survive more than one process.

The tests that justify the design are the leakage ones. They do not check that specific dangerous
strings are absent -- a denylist passes until somebody adds a field -- they assert the record's key
set is **exactly** the whitelist, so a field added without thought fails here rather than in an
incident review. Then they plant identifiable secrets in the body, the headers and the path of real
requests and assert none of it reaches the log.

Against the real application with real PostgreSQL, because what matters is what the middleware
actually emits for a request that went all the way through, not what a unit test hands it.

Requirements: FR-025 (module 26's telemetry gap).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from accessforge_api.telemetry import (
    UNMATCHED_ROUTE,
    JsonFormatter,
    Outcome,
    RequestRecord,
    outcome_for,
)
from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x610))
WS_OTHER = str(uuid.UUID(int=0x611))
OWNER = str(uuid.UUID(int=0x612))

#: Every field a record may carry. The whitelist, asserted rather than described: a record with a
#: field that is not here has started collecting something nobody reviewed.
PERMITTED_FIELDS = {
    "event",
    "correlation_id",
    "method",
    "route",
    "status",
    "outcome",
    "duration_ms",
    "problem_code",
    "streamed",
}

#: Planted in bodies, headers and paths. Distinctive enough that a substring search over the whole
#: serialised log is conclusive.
SECRET = "sup3rsecret-canary-2f9a"


@pytest.fixture()
def db(test_database_url: str) -> Iterator[str]:
    assert_row_level_security_enforced(test_database_url)
    migrate(test_database_url)
    with unscoped_connection(test_database_url) as conn:
        conn.execute("TRUNCATE workspace RESTART IDENTITY CASCADE")
        conn.execute("TRUNCATE app_user CASCADE")
        conn.execute("DELETE FROM rate_limit_bucket")
    with unscoped_connection(test_database_url) as conn:
        for ws, name in ((WS, "A"), (WS_OTHER, "B")):
            conn.execute("INSERT INTO workspace (id, name) VALUES (%s, %s)", (ws, name))
        conn.execute("INSERT INTO app_user (id, email) VALUES (%s, 'owner@example.test')", (OWNER,))
    with workspace_connection(test_database_url, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) VALUES (%s,%s,'OWNER')",
            (WS, OWNER),
        )
    yield test_database_url


class _Capture(logging.Handler):
    """Collects what was actually emitted, including the serialised form.

    The serialised text matters as much as the fields: a leak could arrive through the formatter
    rather than the record, and a test that only inspected the dict would not see it.
    """

    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []
        self.lines: list[str] = []
        self.setFormatter(JsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        payload = getattr(record, "accessforge", None)
        if isinstance(payload, dict):
            self.records.append(dict(payload))
        self.lines.append(self.format(record))


@pytest.fixture()
def captured() -> Iterator[_Capture]:
    """Attached to the telemetry logger only, so nothing else in the suite is collected."""
    handler = _Capture()
    logger = logging.getLogger("accessforge.telemetry")
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


@pytest.fixture()
def api(db: str) -> Iterator[object]:
    from fastapi.testclient import TestClient

    from accessforge_api.app import create_app
    from accessforge_api.config import ApiSettings

    settings = ApiSettings(
        database_url=db,
        evidence_endpoint_url=os.environ.get("OBJECT_STORE_ENDPOINT", "http://127.0.0.1:9000"),
        evidence_bucket=os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
        evidence_access_key=os.environ.get("OBJECT_STORE_ACCESS_KEY", "accessforge"),
        evidence_secret_key=os.environ.get("OBJECT_STORE_SECRET_KEY", "unset-for-this-test"),
        environment="test",
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _sign_in(db_url: str, client: object) -> str:
    from accessforge_api.auth import SESSION_COOKIE, issue_session

    with workspace_connection(db_url, WS) as conn:
        issued = issue_session(conn, user_id=OWNER)
    client.cookies.set(SESSION_COOKIE, issued.session_token)  # type: ignore[attr-defined]
    return issued.csrf_token


# --- one record, with the required fields
# ----------------------------------------------------------


def test_a_successful_request_emits_one_record_with_every_required_field(
    db: str, api: object, captured: _Capture
) -> None:
    """The ordinary case, and the shape everything else is compared against."""
    csrf = _sign_in(db, api)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects",
        json={"name": "telemetry"},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 201, response.text

    assert len(captured.records) == 1, captured.records
    record = captured.records[0]
    assert record["event"] == "api.request"
    assert record["method"] == "POST"
    # The template, not the path: no workspace id anywhere in it.
    assert record["route"] == "/v1/workspaces/{workspace_id}/projects"
    assert record["status"] == 201
    assert record["outcome"] == Outcome.SUCCEEDED
    assert isinstance(record["duration_ms"], float) and record["duration_ms"] >= 0
    # The server's identifier, which is the only one telemetry emits, returned so a caller can
    # quote the value that actually appears in the log.
    assert record["correlation_id"] == response.headers["X-Correlation-Id"]
    uuid.UUID(record["correlation_id"])
    assert "problem_code" not in record


def test_exactly_one_record_per_request_across_several(
    db: str, api: object, captured: _Capture
) -> None:
    """No duplicate from a second writer, and none missing.

    The refusal handler records its code on the scope and does not log, so a refused request
    produces
    one record rather than two. Counting is the only way to notice that: a duplicate looks exactly
    like real traffic.
    """
    csrf = _sign_in(db, api)
    api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects", json={"name": "one"}, headers={"x-csrf-token": csrf}
    )
    api.get(f"/v1/workspaces/{WS}/projects")  # type: ignore[attr-defined]
    api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects", json={}, headers={"x-csrf-token": csrf}
    )

    assert len(captured.records) == 3, [r["route"] for r in captured.records]
    assert [r["method"] for r in captured.records] == ["POST", "GET", "POST"]


def test_a_refusal_carries_the_stable_problem_code_and_is_not_called_a_success(
    db: str, api: object, captured: _Capture
) -> None:
    """A 4xx is REFUSED, not FAILED, and it names the code rather than the prose.

    `detail` changes with every clarification; the code does not, so an alert keyed on the code does
    not break when somebody improves a sentence. And counting a 403 as a failure teaches operators
    to
    ignore failures.
    """
    csrf = _sign_in(db, api)
    refused = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects", json={}, headers={"x-csrf-token": csrf}
    )
    assert refused.status_code == 400, refused.text

    record = captured.records[-1]
    assert record["status"] == 400
    assert record["outcome"] == Outcome.REFUSED
    assert record["problem_code"] == "INVALID_INPUT"
    assert record["correlation_id"] == refused.json()["correlationId"], (
        "the record and the problem document disagree about the correlation id, so a customer's "
        "complaint cannot be matched to a record"
    )


def test_an_unauthenticated_request_is_recorded(db: str, api: object, captured: _Capture) -> None:
    """The requests that never reach a route are the ones an attack looks like."""
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects", json={"name": "anon"}
    )
    assert response.status_code in {401, 403}, response.text
    record = captured.records[-1]
    assert record["outcome"] == Outcome.REFUSED
    assert record["problem_code"] in {"NOT_AUTHENTICATED", "CSRF_REQUIRED"}


def test_an_unmatched_path_is_recorded_without_the_path(
    db: str, api: object, captured: _Capture
) -> None:
    """An unmatched path is attacker-controlled and unbounded.

    Using it as the route would make the label high-cardinality and would copy whatever somebody put
    in a URL -- a token pasted into a query string being the case that matters -- straight into the
    log. So the route is a sentinel and the path is not recorded at all.
    """
    response = api.get(f"/v1/nope/{SECRET}")  # type: ignore[attr-defined]
    assert response.status_code == 404

    record = captured.records[-1]
    assert record["route"] == UNMATCHED_ROUTE
    assert SECRET not in json.dumps(captured.records)
    assert SECRET not in "\n".join(captured.lines)


def test_a_validation_error_is_recorded_with_a_code_and_no_field_detail(
    db: str, api: object, captured: _Capture
) -> None:
    """FastAPI's own validation path, which never reaches a route body.

    A validation error names the field and often echoes the value, which is how a body carrying a
    token ends up in a log. The record carries the code and nothing else from it.
    """
    _sign_in(db, api)
    response = api.get(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/runs", params={"limit": SECRET}
    )
    assert response.status_code == 400, response.text

    record = captured.records[-1]
    assert record["outcome"] == Outcome.REFUSED
    assert SECRET not in json.dumps(captured.records)


def test_the_health_probes_are_not_recorded(db: str, api: object, captured: _Capture) -> None:
    """A load balancer polling every second would bury every other signal.

    Silenced by route template, which is the same value that appears in a record -- so what to quiet
    is read off a record rather than guessed.
    """
    assert api.get("/health/live").status_code == 200  # type: ignore[attr-defined]
    assert captured.records == []


# --- non-leakage
# -----------------------------------------------------------------------------------


def test_the_record_carries_only_the_whitelisted_fields(
    db: str, api: object, captured: _Capture
) -> None:
    """A whitelist asserted, not described.

    The safe way to keep evidence out of a log is never to hand it to the logger, and the way that
    stays true is for an unreviewed field to fail a test. A denylist -- collect then strip -- passes
    until somebody adds a field, and then fails silently in the artifact you read after an incident.
    """
    csrf = _sign_in(db, api)
    api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects", json={"name": "fields"}, headers={"x-csrf-token": csrf}
    )
    api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects", json={}, headers={"x-csrf-token": csrf}
    )

    assert captured.records
    for record in captured.records:
        unexpected = set(record) - PERMITTED_FIELDS
        assert unexpected == set(), f"a record grew fields nobody reviewed: {sorted(unexpected)}"

    # And the dataclass itself, so adding a field is caught even before a request exercises it.
    assert set(RequestRecord.__dataclass_fields__) == PERMITTED_FIELDS


def test_nothing_from_the_body_headers_or_cookies_reaches_the_log(
    db: str, api: object, captured: _Capture
) -> None:
    """Planted in every channel a caller controls, then searched for across the whole log.

    The serialised lines are searched as well as the fields: a leak can arrive through a formatter
    as
    easily as through a record, and a test that only inspected the dict would not see it.
    """
    csrf = _sign_in(db, api)
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects",
        json={"name": f"project-{SECRET}", "repositoryUrl": f"https://example.test/{SECRET}"},
        headers={
            "x-csrf-token": csrf,
            "Authorization": f"Bearer {SECRET}",
            "X-Custom-Secret": SECRET,
            "Cookie": f"accessforge_session=leaked-{SECRET}",
        },
    )
    assert response.status_code in {201, 400, 401}, response.text

    serialised = json.dumps(captured.records) + "\n".join(captured.lines)
    assert SECRET not in serialised, "a caller-supplied value reached the telemetry record"
    # Nor the session cookie, under any name.
    assert "accessforge_session" not in serialised
    assert "Bearer" not in serialised


def test_no_identifier_from_the_path_reaches_the_log(
    db: str, api: object, captured: _Capture
) -> None:
    """The route template is the whole mechanism, so it is asserted against the real ids.

    Excluding the workspace id is a deliberate cost: a record cannot be attributed to a tenant, so
    this telemetry answers what the API is doing and not what a customer is doing. `requestId` is
    how
    a specific complaint is correlated.
    """
    csrf = _sign_in(db, api)
    created = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects", json={"name": "ids"}, headers={"x-csrf-token": csrf}
    )
    project_id = created.json().get("projectId", "")
    api.get(f"/v1/workspaces/{WS}/projects")  # type: ignore[attr-defined]

    serialised = json.dumps(captured.records) + "\n".join(captured.lines)
    for identifier in (WS, OWNER, project_id):
        if identifier:
            assert identifier not in serialised, f"{identifier} reached the telemetry record"


def test_no_exception_text_reaches_the_log(db: str, api: object, captured: _Capture) -> None:
    """Refusal prose is written for an operator at the point of failure, not for a collector.

    The `detail` strings in this codebase name workspaces, projects, digests and occasionally the
    phrase a screen reader announced. The record carries the stable code instead.
    """
    csrf = _sign_in(db, api)
    refused = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects", json={}, headers={"x-csrf-token": csrf}
    )
    detail = refused.json()["detail"]
    assert detail, "this test needs a refusal that carries prose"

    serialised = json.dumps(captured.records) + "\n".join(captured.lines)
    assert detail not in serialised
    # A distinctive fragment too, in case the whole string is never contiguous.
    assert detail.split(".")[0] not in serialised


# --- the emitted form
# ------------------------------------------------------------------------------


def test_each_record_serialises_as_one_json_object_per_line(
    db: str, api: object, captured: _Capture
) -> None:
    """Machine-readable without a dependency, and parseable without a regular expression.

    The message is a fixed string and everything variable is structured, so no consumer ever parses
    prose -- which is the failure mode of formatting fields into a sentence: the first value
    containing a space breaks the parser.
    """
    csrf = _sign_in(db, api)
    api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects", json={"name": "json"}, headers={"x-csrf-token": csrf}
    )

    assert len(captured.lines) == 1
    line = captured.lines[0]
    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed["event"] == "api.request"
    assert parsed["route"] == "/v1/workspaces/{workspace_id}/projects"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "accessforge.telemetry"
    assert "timestamp" in parsed


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, Outcome.SUCCEEDED),
        (201, Outcome.SUCCEEDED),
        (304, Outcome.SUCCEEDED),
        (400, Outcome.REFUSED),
        (403, Outcome.REFUSED),
        (404, Outcome.REFUSED),
        (429, Outcome.REFUSED),
        (500, Outcome.FAILED),
        (503, Outcome.FAILED),
    ],
)
def test_the_outcome_separates_a_refusal_from_a_failure(status: int, expected: str) -> None:
    """A 4xx means the server worked and declined. Calling that a failure trains operators to ignore
    failures, which is worse than having no outcome field at all."""
    assert outcome_for(status) == expected


# --- the paths an operator most needs, and most likely to be missing ----------------------------


def test_an_unhandled_exception_is_recorded_as_a_failure_and_still_raised(
    db: str, api: object, captured: _Capture
) -> None:
    """The one outcome an operator most needs to see, and the easiest to lose.

    An exception escaping `call_next` means there is no response to read a status from. Letting it
    past unrecorded would leave crashes as the only thing telemetry never mentions; swallowing it to
    produce a record would turn a crash into a silent 200. So it is recorded as a 500 and re-raised
    unchanged.

    A route is added to this app only -- the fixture builds a fresh one per test -- so nothing here
    changes the application under any other test.
    """
    from fastapi.testclient import TestClient

    from accessforge_api.app import create_app
    from accessforge_api.config import ApiSettings

    settings = ApiSettings(
        database_url=db,
        evidence_endpoint_url="http://127.0.0.1:9000",
        evidence_bucket="b",
        evidence_access_key="k",
        evidence_secret_key="s",
        environment="test",
    )
    app = create_app(settings)

    @app.get("/v1/workspaces/{workspace_id}/_boom")
    def _boom(workspace_id: str) -> dict[str, str]:
        raise RuntimeError(f"internal detail naming {SECRET}")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(f"/v1/workspaces/{WS}/_boom")
    assert response.status_code == 500

    record = captured.records[-1]
    assert record["status"] == 500
    assert record["outcome"] == Outcome.FAILED
    assert record["route"] == "/v1/workspaces/{workspace_id}/_boom"
    # The exception's own text is not in the record. It is the least reviewed string in any codebase
    # and the most likely to name something it should not.
    serialised = json.dumps(captured.records) + "\n".join(captured.lines)
    assert SECRET not in serialised
    assert "RuntimeError" not in serialised


class _FakeResponse:
    """Just enough of a response to ask `is_streaming` the question.

    A controlled object rather than a real one, because the property under test is a decision about
    headers and nothing else. Driving the live event stream to establish it was the wrong
    instrument:
    that route produces events for as long as a client listens, so a test that opened it and read
    nothing waited for a server-side timeout rather than for an answer.
    """

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


@pytest.mark.parametrize(
    ("headers", "streamed", "why"),
    [
        ({"content-type": "application/json", "content-length": "42"}, False, "buffered json"),
        ({"content-type": "application/problem+json", "content-length": "200"}, False, "a refusal"),
        ({"content-type": "text/event-stream"}, True, "an event stream"),
        (
            {"content-type": "text/event-stream", "content-length": "0"},
            True,
            "an event stream declaring a length, which is still a stream",
        ),
        ({"content-type": "application/json"}, True, "chunked: no length was known in advance"),
    ],
)
def test_streamed_is_decided_by_the_headers_not_the_response_class(
    headers: dict[str, str], streamed: bool, why: str
) -> None:
    """A buffered response carries `content-length`; a streamed one cannot.

    Decided from the headers because every response passing through `BaseHTTPMiddleware` is
    re-wrapped
    as a streaming response on the way out -- so asking the object what class it is answers
    "streaming" for a 404 with a fixed body. That made the flag true for every request and therefore
    worth nothing, which is what this parametrisation exists to stop happening again.
    """
    from accessforge_api.telemetry import is_streaming

    assert is_streaming(_FakeResponse(headers)) is streamed, why  # type: ignore[arg-type]


@pytest.mark.timeout(30)
def test_a_bounded_stream_is_recorded_as_streamed_and_a_buffered_reply_is_not(
    db: str, captured: _Capture
) -> None:
    """The same distinction through the real middleware, on a stream that ends.

    A finite generator rather than the product's event stream: this asserts that the middleware
    classifies a streamed response correctly, and for that the stream only has to be a stream.
    Using the unbounded one conflated "is it recorded correctly" with "does it ever finish", and the
    second question hung the suite for five minutes.

    Both directions in one test, because the flag is only meaningful if it distinguishes: a
    `streamed` that is always true carries no information, and that is exactly the bug this found.
    """
    from collections.abc import Iterator as _Iterator

    from fastapi.testclient import TestClient
    from starlette.responses import StreamingResponse

    from accessforge_api.app import create_app
    from accessforge_api.config import ApiSettings

    app = create_app(
        ApiSettings(
            database_url=db,
            evidence_endpoint_url="http://127.0.0.1:9000",
            evidence_bucket="b",
            evidence_access_key="k",
            evidence_secret_key="s",
            environment="test",
        )
    )

    @app.get("/v1/workspaces/{workspace_id}/_finite-stream")
    def _finite(workspace_id: str) -> StreamingResponse:
        def body() -> _Iterator[bytes]:
            yield b"one\n"
            yield b"two\n"

        return StreamingResponse(body(), media_type="text/event-stream")

    @app.get("/v1/workspaces/{workspace_id}/_buffered")
    def _buffered(workspace_id: str) -> dict[str, str]:
        return {"ok": "yes"}

    with TestClient(app) as client:
        streamed = client.get(f"/v1/workspaces/{WS}/_finite-stream")
        buffered = client.get(f"/v1/workspaces/{WS}/_buffered")
    assert streamed.status_code == 200
    assert streamed.text == "one\ntwo\n"
    assert buffered.status_code == 200

    by_route = {r["route"]: r for r in captured.records}
    stream_record = by_route["/v1/workspaces/{workspace_id}/_finite-stream"]
    buffered_record = by_route["/v1/workspaces/{workspace_id}/_buffered"]

    assert stream_record["streamed"] is True, (
        "a streamed response was recorded as an ordinary one, so a stream that aborts halfway "
        "would be indistinguishable from one that completed"
    )
    assert buffered_record["streamed"] is False, (
        "a buffered reply was recorded as streamed, which makes the flag carry no information"
    )
    # The status on the streamed record describes the headers, which is why the flag has to be
    # there:
    # 200 was sent before the body existed.
    assert stream_record["status"] == 200
    assert stream_record["outcome"] == Outcome.SUCCEEDED


def test_a_secret_in_the_client_supplied_request_id_never_reaches_the_log(
    db: str, api: object, captured: _Capture
) -> None:
    """The hole an independent review found, and the reason there are two identifiers.

    `X-Request-Id` is echoed in every problem document -- an established contract with its own
    test -- and it is a header a caller fills in. So it is bytes the caller chose, and a caller may
    put a credential there, by accident or to see where it ends up. Recording it contradicted the
    one
    promise this module makes, and the leakage tests missed it because they planted canaries in
    every header *except* the one that was emitted.

    Both halves are asserted. The value is still echoed, because removing that would break a
    documented contract for no privacy gain -- the caller already has it. And it appears nowhere in
    the log, where a separate server-generated id stands in its place.
    """
    csrf = _sign_in(db, api)
    poisoned = f"req-{SECRET}"
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects",
        json={"name": "poisoned-id"},
        headers={"x-csrf-token": csrf, "X-Request-Id": poisoned},
    )
    assert response.status_code == 201, response.text

    # Compatibility: the caller's own value comes back where they put it.
    assert response.headers["X-Request-Id"] == poisoned

    serialised = json.dumps(captured.records) + "\n".join(captured.lines)
    assert SECRET not in serialised, "a client-supplied header value reached the telemetry record"
    assert poisoned not in serialised

    # And what was recorded is a server-generated id the caller can still quote.
    record = captured.records[-1]
    uuid.UUID(record["correlation_id"])
    assert record["correlation_id"] == response.headers["X-Correlation-Id"]


def test_a_poisoned_request_id_is_still_absent_from_a_refusal_record(
    db: str, api: object, captured: _Capture
) -> None:
    """The same on the refusal path, which is where a problem document echoes the value.

    A refusal is the response a caller is most likely to quote, so it is the one where the two
    identifiers have to coexist without the client's reaching the log.
    """
    csrf = _sign_in(db, api)
    poisoned = f"req-{SECRET}"
    refused = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects",
        json={},
        headers={"x-csrf-token": csrf, "X-Request-Id": poisoned},
    )
    assert refused.status_code == 400, refused.text
    body = refused.json()

    # The document carries both: the caller's id, unchanged, and the server's.
    assert body["requestId"] == poisoned
    uuid.UUID(body["correlationId"])

    serialised = json.dumps(captured.records) + "\n".join(captured.lines)
    assert SECRET not in serialised
    assert captured.records[-1]["correlation_id"] == body["correlationId"]


# --- the configuration a deployment actually gets ------------------------------------------------


def test_a_default_app_writes_one_json_line_per_request_to_stdout(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second blocker: a formatter nothing installs formats nothing.

    `JsonFormatter` existed and `create_app` never attached it, so the default uvicorn deployment
    emitted prose from the root configuration -- the machine-readable claim was true of a class
    nobody
    constructed.

    So this must not install anything itself. An earlier version called
    `configure_telemetry_logging` before building the app, which meant removing the call from
    `create_app` changed nothing it could see -- a mutation check caught that, and it was testing
    its
    own setup rather than the product. `sys.stdout` is replaced *before* `create_app` runs, because
    `logging` binds the stream when the handler is created, so whatever `create_app` installs writes
    here.
    """
    import io
    import sys

    from fastapi.testclient import TestClient

    from accessforge_api.app import create_app
    from accessforge_api.config import ApiSettings

    logger = logging.getLogger("accessforge.telemetry")
    saved = list(logger.handlers)
    saved_propagate = logger.propagate
    for handler in saved:
        logger.removeHandler(handler)

    buffer = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buffer)
    try:
        # Nothing is configured here on purpose. If `create_app` does not wire the handler, this
        # buffer stays empty and the test fails -- which is the blocker.
        app = create_app(
            ApiSettings(
                database_url=db,
                evidence_endpoint_url="http://127.0.0.1:9000",
                evidence_bucket="b",
                evidence_access_key="k",
                evidence_secret_key="s",
                environment="test",
            )
        )
        with TestClient(app) as client:
            response = client.get(f"/v1/workspaces/{WS}/projects")
        assert response.status_code in {200, 401, 403}, response.status_code
    finally:
        monkeypatch.undo()
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        for handler in saved:
            logger.addHandler(handler)
        logger.propagate = saved_propagate

    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1, (
        f"expected one JSON record on stdout from a default app, got {len(lines)}: {lines}"
    )
    parsed = json.loads(lines[0])
    assert parsed["event"] == "api.request"
    assert parsed["route"] == "/v1/workspaces/{workspace_id}/projects"
    assert parsed["method"] == "GET"
    assert set(parsed) - {"level", "logger", "timestamp"} <= PERMITTED_FIELDS


def test_records_do_not_propagate_to_the_root_logger(db: str) -> None:
    """One record per request, in one format.

    Uvicorn configures the root logger, so a propagating record is emitted twice: once as JSON here
    and once as prose there. A consumer reading stdout would find every request reported in two
    formats, one of them the format this module exists to avoid -- and the prose one carries the
    logger's own message rather than the structured payload.

    Asserted with a handler on the root logger, which is the only place the duplicate would appear.
    """
    import io

    from accessforge_api.telemetry import configure_telemetry_logging, emit, record_for

    telemetry = logging.getLogger("accessforge.telemetry")
    saved = list(telemetry.handlers)
    saved_propagate = telemetry.propagate
    for handler in saved:
        telemetry.removeHandler(handler)

    root = logging.getLogger()
    seen_at_root: list[str] = []

    class _Root(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen_at_root.append(record.name)

    root_handler = _Root()
    root.addHandler(root_handler)
    stream = io.StringIO()
    try:
        configure_telemetry_logging(stream=stream)

        class _Scope:
            scope: dict[str, Any] = {}
            method = "GET"

        emit(record_for(_Scope(), status_code=200, duration_ms=1.0))  # type: ignore[arg-type]
    finally:
        root.removeHandler(root_handler)
        for handler in list(telemetry.handlers):
            telemetry.removeHandler(handler)
        for handler in saved:
            telemetry.addHandler(handler)
        telemetry.propagate = saved_propagate

    assert [name for name in seen_at_root if name == "accessforge.telemetry"] == [], (
        "records reached the root logger, so every request is reported twice -- once as JSON and "
        "once as prose"
    )
    assert len([line for line in stream.getvalue().splitlines() if line.strip()]) == 1


def test_configuring_twice_does_not_double_the_records(db: str) -> None:
    """Idempotent, because it has to be.

    `create_app` runs once per process in production and dozens of times in this suite. A handler
    added per call turns one request into as many duplicate lines as there have been apps, and
    duplicate records look exactly like real traffic.
    """
    import io

    from accessforge_api.telemetry import configure_telemetry_logging, emit, record_for

    logger = logging.getLogger("accessforge.telemetry")
    saved = list(logger.handlers)
    saved_propagate = logger.propagate
    for handler in saved:
        logger.removeHandler(handler)

    stream = io.StringIO()
    try:
        first = configure_telemetry_logging(stream=stream)
        second = configure_telemetry_logging(stream=stream)
        third = configure_telemetry_logging(stream=io.StringIO())
        assert first is not None
        assert second is None, "a second call installed another handler"
        assert third is None, "a third call installed another handler"
        assert len([h for h in logger.handlers if h is first]) == 1
        assert len(logger.handlers) == 1

        class _Scope:
            scope: dict[str, Any] = {}
            method = "GET"

        emit(record_for(_Scope(), status_code=200, duration_ms=1.0))  # type: ignore[arg-type]
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        for handler in saved:
            logger.addHandler(handler)
        logger.propagate = saved_propagate

    assert len([line for line in stream.getvalue().splitlines() if line.strip()]) == 1


def test_the_installed_handler_does_not_silence_a_test_capture(db: str, captured: _Capture) -> None:
    """Capture and the installed handler coexist, which the whole suite depends on.

    `propagate` is turned off so the root handler uvicorn configures does not also print each record
    as prose. That suppresses ancestors, not siblings: a handler attached to this same logger still
    receives everything, which is why every other test in this file works with the default
    configuration in place.
    """
    import io

    from fastapi.testclient import TestClient

    from accessforge_api.app import create_app
    from accessforge_api.config import ApiSettings
    from accessforge_api.telemetry import configure_telemetry_logging

    logger = logging.getLogger("accessforge.telemetry")
    stream = io.StringIO()
    installed = configure_telemetry_logging(stream=stream)
    try:
        app = create_app(
            ApiSettings(
                database_url=db,
                evidence_endpoint_url="http://127.0.0.1:9000",
                evidence_bucket="b",
                evidence_access_key="k",
                evidence_secret_key="s",
                environment="test",
            )
        )
        with TestClient(app) as client:
            client.get(f"/v1/workspaces/{WS}/projects")
    finally:
        if installed is not None:
            logger.removeHandler(installed)

    # The capture fixture saw it. Whether this particular call installed a handler depends on what
    # the suite left behind, so the installed stream is only checked when it did.
    assert len(captured.records) == 1, captured.records
    if installed is not None:
        assert len([ln for ln in stream.getvalue().splitlines() if ln.strip()]) == 1


# --- the two bugs PR #34's review found --------------------------------------------------------


def _header_and_document_agree(response: object) -> None:
    """The invariant: the id in the document is the id in the header beside it.

    `ProblemDetail` mints a fresh UUID when no `request_id` is passed, and twenty-eight construction
    sites pass none. Each answered with an id that matched nothing else on the response, so a caller
    quoting it was quoting a number that existed once and then nowhere.
    """
    body = response.json()  # type: ignore[attr-defined]
    assert body["requestId"] == response.headers["X-Request-Id"], (  # type: ignore[attr-defined]
        "the problem document and X-Request-Id disagree, so the id a caller quotes matches nothing"
    )
    # And the server identifier is present and distinct from the caller-facing one's source.
    uuid.UUID(body["correlationId"])
    assert body["correlationId"] == response.headers["X-Correlation-Id"]  # type: ignore[attr-defined]


@pytest.mark.parametrize("supplied", [None, "caller-chosen-id-7"])
def test_an_early_session_refusal_uses_the_resolved_request_id(
    db: str, api: object, captured: _Capture, supplied: str | None
) -> None:
    """A sign-in refusal is raised before any route resolves a `RequestContext`.

    Eight of the id-less constructions are in the session routes, which is the path a caller hits
    first and the one most likely to be quoted in a support ticket. Both halves are parametrised: a
    supplied id must be honoured, and a generated one must still be the same id the header carries.
    """
    headers = {"X-Request-Id": supplied} if supplied else {}
    response = api.post("/v1/sessions", json={}, headers=headers)  # type: ignore[attr-defined]
    assert response.status_code >= 400, response.text

    _header_and_document_agree(response)
    if supplied:
        assert response.json()["requestId"] == supplied
    else:
        uuid.UUID(response.json()["requestId"])

    # The record carries the server id, never the caller's.
    record = captured.records[-1]
    assert record["correlation_id"] == response.json()["correlationId"]
    if supplied:
        assert supplied not in json.dumps(captured.records)


@pytest.mark.parametrize("supplied", [None, "caller-chosen-id-9"])
def test_a_validation_refusal_uses_the_resolved_request_id(
    db: str, api: object, captured: _Capture, supplied: str | None
) -> None:
    """FastAPI's own validation runs before a route body, so there is no context to take an id from.

    This was the clearest case: the handler constructed its document with no `request_id` at all, so
    every validation failure answered with an id that appeared nowhere else.
    """
    _sign_in(db, api)
    headers = {"X-Request-Id": supplied} if supplied else {}
    response = api.get(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/runs", params={"limit": "not-a-number"}, headers=headers
    )
    assert response.status_code == 400, response.text

    _header_and_document_agree(response)
    if supplied:
        assert response.json()["requestId"] == supplied


@pytest.mark.parametrize("supplied", [None, "caller-chosen-id-11"])
def test_an_authorization_refusal_uses_the_resolved_request_id(
    db: str, api: object, supplied: str | None
) -> None:
    """A route-level refusal, which does pass a context id -- so this proves the override is a no-op
    there rather than a change."""
    headers = {"X-Request-Id": supplied} if supplied else {}
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects", json={"name": "x"}, headers=headers
    )
    assert response.status_code in {401, 403}, response.text
    _header_and_document_agree(response)


def test_a_successful_response_carries_both_identifiers(db: str, api: object) -> None:
    """Success has no document, so the headers are the whole contract there."""
    csrf = _sign_in(db, api)
    supplied = "caller-chosen-id-13"
    response = api.post(  # type: ignore[attr-defined]
        f"/v1/workspaces/{WS}/projects",
        json={"name": "both-ids"},
        headers={"x-csrf-token": csrf, "X-Request-Id": supplied},
    )
    assert response.status_code == 201, response.text
    assert response.headers["X-Request-Id"] == supplied
    uuid.UUID(response.headers["X-Correlation-Id"])
    assert response.headers["X-Correlation-Id"] != supplied


def test_a_quiet_route_stays_silent_when_it_raises(db: str, captured: _Capture) -> None:
    """Silence that depends on the outcome is not silence.

    The quiet list was applied only after a normal response, so a crashing health probe emitted a
    record a second -- from the one route an operator silenced precisely because it is polled
    constantly, and in the situation where the log is least readable.

    A non-quiet route raising the same way is the control: without it this test would also pass
    against a middleware that had simply stopped recording exceptions at all.
    """
    from fastapi.testclient import TestClient

    from accessforge_api.app import create_app
    from accessforge_api.config import ApiSettings

    app = create_app(
        ApiSettings(
            database_url=db,
            evidence_endpoint_url="http://127.0.0.1:9000",
            evidence_bucket="b",
            evidence_access_key="k",
            evidence_secret_key="s",
            environment="test",
            telemetry_quiet_routes=frozenset({"/_quiet-boom"}),
        )
    )

    @app.get("/_quiet-boom")
    def _quiet_boom() -> dict[str, str]:
        raise RuntimeError("quiet route failing")

    @app.get("/_loud-boom")
    def _loud_boom() -> dict[str, str]:
        raise RuntimeError("loud route failing")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/_quiet-boom").status_code == 500
        assert client.get("/_loud-boom").status_code == 500

    routes = [r["route"] for r in captured.records]
    assert "/_quiet-boom" not in routes, (
        "a silenced route emitted a record when it crashed, which is the one moment its volume "
        "matters most"
    )
    # The control: exceptions are still recorded for routes nobody silenced.
    assert routes.count("/_loud-boom") == 1, routes
    loud = next(r for r in captured.records if r["route"] == "/_loud-boom")
    assert loud["status"] == 500
    assert loud["outcome"] == Outcome.FAILED


def test_a_quiet_route_stays_silent_when_it_succeeds(db: str, captured: _Capture) -> None:
    """The other half of exactness, so 'quiet' cannot come to mean 'quiet only on failure'."""
    from fastapi.testclient import TestClient

    from accessforge_api.app import create_app
    from accessforge_api.config import ApiSettings

    app = create_app(
        ApiSettings(
            database_url=db,
            evidence_endpoint_url="http://127.0.0.1:9000",
            evidence_bucket="b",
            evidence_access_key="k",
            evidence_secret_key="s",
            environment="test",
            telemetry_quiet_routes=frozenset({"/_quiet-ok"}),
        )
    )

    @app.get("/_quiet-ok")
    def _quiet_ok() -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/_loud-ok")
    def _loud_ok() -> dict[str, str]:
        return {"ok": "yes"}

    with TestClient(app) as client:
        assert client.get("/_quiet-ok").status_code == 200
        assert client.get("/_loud-ok").status_code == 200

    routes = [r["route"] for r in captured.records]
    assert "/_quiet-ok" not in routes
    assert routes.count("/_loud-ok") == 1, routes
