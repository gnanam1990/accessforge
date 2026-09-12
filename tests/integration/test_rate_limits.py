"""How fast one principal, and one workspace, may write.

Module 26 bounded *how much* a workspace consumes over a billing period and nothing bounded *how
fast* anybody could ask. Those are different controls: a caller can spend a month's entitlement in a
second, and a caller with nothing left can still cost a connection per request.

Two tests justify the design. `the bucket is shared across processes` opens two independent
connections, the way two API processes behind a load balancer are independent, and shows they share
one allowance -- an in-process counter passes every other test in this file and fails that one.
`concurrent requests do not both get the last token` holds a transaction open while a second
contends for the same row, which is the case a read-then-write limiter gets wrong under exactly the
load it exists for.

Against real PostgreSQL, because the atomicity is the product: the refill, the comparison and the
decrement are one statement, and nothing about that can be shown against a fake.

Requirements: FR-025 (module 26's rate-limiting gap).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from accessforge_persistence import (
    assert_row_level_security_enforced,
    migrate,
    rate_limits,
    unscoped_connection,
    workspace_connection,
)

pytestmark = pytest.mark.integration

WS = str(uuid.UUID(int=0x510))
WS_OTHER = str(uuid.UUID(int=0x511))
OWNER = str(uuid.UUID(int=0x512))
MEMBER = str(uuid.UUID(int=0x513))

#: A deliberately tiny allowance so boundaries are exact rather than approximate: three requests at
#: once, one returning every two seconds.
CAPACITY = 3
REFILL = 0.5
AT = datetime(2026, 9, 12, 12, 0, 0, tzinfo=UTC)


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
        for user, email in ((OWNER, "owner@example.test"), (MEMBER, "member@example.test")):
            conn.execute("INSERT INTO app_user (id, email) VALUES (%s, %s)", (user, email))
    yield test_database_url


def _consume(
    db_url: str,
    *,
    scope_kind: str = "PRINCIPAL",
    scope_id: str = OWNER,
    workspace: str = WS,
    now: datetime = AT,
    capacity: int = CAPACITY,
    refill: float = REFILL,
) -> rate_limits.Decision:
    with workspace_connection(db_url, workspace) as conn:
        return rate_limits.consume(
            conn,
            scope_kind=scope_kind,
            scope_id=scope_id,
            capacity=capacity,
            refill_per_second=refill,
            now=now,
        )


# --- boundaries ---------------------------------------------------------------------------------


def test_the_allowance_is_exactly_the_capacity_then_refused(db: str) -> None:
    """Three admitted, the fourth refused, at one instant. The boundary is the contract."""
    decisions = [_consume(db) for _ in range(CAPACITY)]
    assert [d.allowed for d in decisions] == [True] * CAPACITY
    # Remaining counts down to zero and is never negative: a caller reading it can tell how close
    # they are without having to be refused to find out.
    assert [d.remaining for d in decisions] == [2, 1, 0]

    refused = _consume(db)
    assert refused.allowed is False
    assert refused.remaining == 0
    assert refused.burst_capacity == CAPACITY
    # The sustained rate, recovered from the refill, not the burst capacity. At 0.5/second that is
    # 30 a minute, which is a different number from the capacity of 3.
    assert refused.limit_per_minute == 30


def test_a_token_returns_after_the_refill_interval_and_not_before(db: str) -> None:
    """The retry-after is advice that has to be true, so it is checked against the clock.

    One token every two seconds at this rate. At one second there is still nothing; at two there is
    exactly one, and spending it empties the bucket again.
    """
    for _ in range(CAPACITY):
        assert _consume(db).allowed is True
    refused = _consume(db)
    assert refused.allowed is False
    assert refused.retry_after_seconds == 2

    too_soon = _consume(db, now=AT + timedelta(seconds=1))
    assert too_soon.allowed is False, "a token appeared before the rate allowed one"

    on_time = _consume(db, now=AT + timedelta(seconds=2))
    assert on_time.allowed is True
    assert _consume(db, now=AT + timedelta(seconds=2)).allowed is False


def test_retry_after_is_never_zero(db: str) -> None:
    """Advising an immediate retry invites the tight loop a limiter exists to stop."""
    generous = {"capacity": 1, "refill": 100.0}
    assert _consume(db, **generous).allowed is True  # type: ignore[arg-type]
    refused = _consume(db, **generous)  # type: ignore[arg-type]
    assert refused.allowed is False
    assert refused.retry_after_seconds >= 1


def test_the_bucket_never_refills_past_its_capacity(db: str) -> None:
    """An idle week does not buy a week's worth of burst."""
    assert _consume(db).allowed is True
    later = AT + timedelta(days=7)
    allowed = [_consume(db, now=later).allowed for _ in range(CAPACITY + 1)]
    assert allowed == [True] * CAPACITY + [False]


def test_a_clock_that_steps_backwards_does_not_mint_tokens(db: str) -> None:
    """Clock skew between API processes is ordinary, and must not become free allowance.

    What this establishes is the observable behaviour: a request bearing an earlier timestamp is
    refused and the stored token count stays valid. It does **not** distinguish the clamp in the
    refill arithmetic -- with the `>= 1` guard in the same statement, a negative elapsed term can
    only make the available count smaller, so the update never runs and tokens never go negative.
    A mutation check confirmed it: removing the clamp changes nothing any test can see.
    The clamp stays because it makes the arithmetic correct on its own terms rather than correct
    because of what another clause happens to prevent, and the handoff records that it is defence in
    depth rather than covered behaviour.
    """
    for _ in range(CAPACITY):
        assert _consume(db).allowed is True

    backwards = _consume(db, now=AT - timedelta(minutes=5))
    assert backwards.allowed is False
    with workspace_connection(db, WS) as conn:
        row = rate_limits.bucket_state(conn, scope_kind="PRINCIPAL", scope_id=OWNER)
    assert row is not None
    assert float(row["tokens"]) >= 0.0


def test_one_request_spends_exactly_one_token_from_each_bucket(db: str) -> None:
    """Not two, and not none.

    A route that resolved authority twice would charge twice and halve the effective limit without
    anything failing -- the limit would just be quietly wrong. Asserted on the arithmetic so the
    property is pinned rather than inferred from a structural check elsewhere.
    """
    first = _consume(db)
    assert first.remaining == CAPACITY - 1
    second = _consume(db)
    assert second.remaining == CAPACITY - 2


# --- independent buckets ------------------------------------------------------------------------


def test_principal_and_workspace_buckets_are_independent(db: str) -> None:
    """Exhausting one must not touch the other.

    A busy member must not be able to spend the workspace's whole allowance, and a busy workspace
    must not leave that member unable to write in a different tenant.
    """
    for _ in range(CAPACITY):
        assert _consume(db, scope_kind="PRINCIPAL", scope_id=OWNER).allowed is True
    assert _consume(db, scope_kind="PRINCIPAL", scope_id=OWNER).allowed is False

    # The workspace bucket is untouched by that.
    assert _consume(db, scope_kind="WORKSPACE", scope_id=WS).allowed is True
    # And another principal is unaffected.
    assert _consume(db, scope_kind="PRINCIPAL", scope_id=MEMBER).allowed is True


def test_a_principals_allowance_is_one_allowance_across_workspaces(db: str) -> None:
    """Membership of another tenant must not multiply a principal's write rate.

    This is why the principal bucket is global rather than per (user, workspace): a per-workspace
    principal bucket would mean being invited to a second workspace doubles the rate a single human
    can write at, so the control could be widened by an invitation.
    """
    for _ in range(CAPACITY):
        assert _consume(db, scope_id=OWNER, workspace=WS).allowed is True
    # Same human, different workspace, same bucket.
    assert _consume(db, scope_id=OWNER, workspace=WS_OTHER).allowed is False


def test_one_workspaces_bucket_is_invisible_to_another(db: str) -> None:
    """Tenancy still holds for the rows that carry a workspace.

    The policy admits global principal rows on purpose; it must not admit another tenant's workspace
    row, or a bucket would be readable across the boundary everything else in this schema enforces.
    """
    assert _consume(db, scope_kind="WORKSPACE", scope_id=WS, workspace=WS).allowed is True

    with workspace_connection(db, WS_OTHER) as conn:
        assert rate_limits.bucket_state(conn, scope_kind="WORKSPACE", scope_id=WS) is None
    with workspace_connection(db, WS) as conn:
        assert rate_limits.bucket_state(conn, scope_kind="WORKSPACE", scope_id=WS) is not None


# --- concurrency and persistence ----------------------------------------------------------------


def test_the_bucket_is_shared_across_processes(db: str) -> None:
    """The test an in-process counter cannot pass.

    Each request opens its own connection and commits, which is what separate API processes behind a
    load balancer do. The allowance is spent across them collectively: if it lived in a process,
    each would enforce its own copy and the real limit would be whatever the deployment happened to
    be scaled to -- a limit that loosens as you add capacity is not a limit.

    Deliberately *not* two simultaneously open transactions. Those would block on the same row by
    design, which is the subject of the next test rather than this one.
    """
    spent = 0
    for _ in range(CAPACITY):
        with workspace_connection(db, WS) as conn:
            assert (
                rate_limits.consume(
                    conn,
                    scope_kind="PRINCIPAL",
                    scope_id=OWNER,
                    capacity=CAPACITY,
                    refill_per_second=REFILL,
                    now=AT,
                ).allowed
                is True
            )
        spent += 1
    assert spent == CAPACITY

    # A different connection, and the allowance is already gone.
    with workspace_connection(db, WS) as fresh:
        refused = rate_limits.consume(
            fresh,
            scope_kind="PRINCIPAL",
            scope_id=OWNER,
            capacity=CAPACITY,
            refill_per_second=REFILL,
            now=AT,
        )
        # And the state one connection wrote is the state another reads, which is the mechanism.
        row = rate_limits.bucket_state(fresh, scope_kind="PRINCIPAL", scope_id=OWNER)
    assert refused.allowed is False, "a second connection was handed its own allowance"
    assert row is not None and float(row["tokens"]) < 1.0


def test_concurrent_requests_do_not_both_get_the_last_token(db: str) -> None:
    """The case a read-then-write limiter gets wrong under the load it exists for.

    One connection spends the last token and holds its transaction open; a second tries for the same
    row. The second must block on the row and then be refused -- not read the pre-decrement count,
    find room, and proceed. A statement timeout turns "blocked for ever" into a failure rather
    than a hang.
    """
    import threading

    outcome: dict[str, object] = {}

    with workspace_connection(db, WS) as holder:
        for _ in range(CAPACITY):
            assert (
                rate_limits.consume(
                    holder,
                    scope_kind="PRINCIPAL",
                    scope_id=OWNER,
                    capacity=CAPACITY,
                    refill_per_second=REFILL,
                    now=AT,
                ).allowed
                is True
            )

        def contend() -> None:
            try:
                with workspace_connection(db, WS) as other:
                    other.execute("SELECT set_config('statement_timeout', '8s', true)")
                    outcome["decision"] = rate_limits.consume(
                        other,
                        scope_kind="PRINCIPAL",
                        scope_id=OWNER,
                        capacity=CAPACITY,
                        refill_per_second=REFILL,
                        now=AT,
                    )
            except Exception as exc:  # noqa: BLE001 - reported rather than lost in the thread
                outcome["error"] = exc

        thread = threading.Thread(target=contend)
        thread.start()
        # The holder's transaction commits when this block exits, which is what releases the row.
        thread.join(timeout=2)

    thread.join(timeout=10)
    assert "error" not in outcome, outcome.get("error")
    decision = outcome["decision"]
    assert isinstance(decision, rate_limits.Decision)
    assert decision.allowed is False, "two callers both spent the last token"


def test_an_unknown_scope_or_impossible_limit_is_refused_loudly(db: str) -> None:
    """A misconfigured limiter must not quietly admit everything or refuse everything."""
    with workspace_connection(db, WS) as conn:
        for kwargs, expected in (
            ({"scope_kind": "EVERYONE"}, "unknown rate-limit scope"),
            ({"capacity": 0}, "admits nothing at all"),
            ({"refill_per_second": 0.0}, "never returns a token"),
        ):
            call = {
                "scope_kind": "PRINCIPAL",
                "scope_id": OWNER,
                "capacity": CAPACITY,
                "refill_per_second": REFILL,
                "now": AT,
                **kwargs,
            }
            with pytest.raises(rate_limits.RateLimitError, match=expected):
                rate_limits.consume(conn, **call)  # type: ignore[arg-type]


# --- pruning ------------------------------------------------------------------------------------


def test_pruning_removes_idle_buckets_and_leaves_active_ones(db: str) -> None:
    """Derived rows that would otherwise grow for ever, one per principal that ever wrote."""
    assert _consume(db, scope_id=OWNER).allowed is True
    assert _consume(db, scope_id=MEMBER, now=AT + timedelta(hours=3)).allowed is True

    with unscoped_connection(db) as conn:
        removed = rate_limits.prune_idle_buckets(
            conn, idle_for=timedelta(hours=1), now=AT + timedelta(hours=3)
        )
    assert removed == 1

    with workspace_connection(db, WS) as conn:
        assert rate_limits.bucket_state(conn, scope_kind="PRINCIPAL", scope_id=OWNER) is None
        assert rate_limits.bucket_state(conn, scope_kind="PRINCIPAL", scope_id=MEMBER) is not None


def test_pruning_refuses_a_zero_idle_window(db: str) -> None:
    """It would delete the bucket of a request in flight, handing its caller a fresh allowance."""
    with unscoped_connection(db) as conn:
        with pytest.raises(rate_limits.RateLimitError, match="zero seconds idle"):
            rate_limits.prune_idle_buckets(conn, idle_for=timedelta(0), now=AT)


def test_a_pruned_bucket_comes_back_full_which_is_what_it_already_was(db: str) -> None:
    """Pruning is safe precisely because an idle bucket and an absent one mean the same thing."""
    assert _consume(db).allowed is True
    with unscoped_connection(db) as conn:
        rate_limits.prune_idle_buckets(
            conn, idle_for=timedelta(hours=1), now=AT + timedelta(hours=2)
        )
    allowed = [_consume(db, now=AT + timedelta(hours=2)).allowed for _ in range(CAPACITY + 1)]
    assert allowed == [True] * CAPACITY + [False]


# --- through the HTTP surface ---------------------------------------------------------------------


@pytest.fixture()
def api(db: str) -> Iterator[TestClient]:
    """The real application, with a tiny allowance so a boundary is reachable in a test."""
    from accessforge_api.app import create_app
    from accessforge_api.config import ApiSettings

    settings = ApiSettings(
        database_url=db,
        evidence_endpoint_url=os.environ.get("OBJECT_STORE_ENDPOINT", "http://127.0.0.1:9000"),
        evidence_bucket=os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
        evidence_access_key=os.environ.get("OBJECT_STORE_ACCESS_KEY", "accessforge"),
        evidence_secret_key=os.environ.get("OBJECT_STORE_SECRET_KEY", "unset-for-this-test"),
        environment="test",
        rate_limit_principal_per_minute=3,
        rate_limit_workspace_per_minute=60,
    )
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) VALUES (%s,%s,'OWNER')",
            (WS, OWNER),
        )
    with TestClient(create_app(settings)) as client:
        yield client


def _sign_in(db_url: str, client: TestClient, user_id: str = OWNER) -> str:
    from accessforge_api.auth import SESSION_COOKIE, issue_session

    with workspace_connection(db_url, WS) as conn:
        issued = issue_session(conn, user_id=user_id)
    client.cookies.set(SESSION_COOKIE, issued.session_token)
    return issued.csrf_token


def _write(client: TestClient, csrf: str, **headers: str) -> Response:
    """One cheap write. `POST /projects` needs only a name, so the 429 is about the limit."""
    return client.post(
        f"/v1/workspaces/{WS}/projects",
        json={"name": f"p-{uuid.uuid4().hex[:8]}"},
        headers={"x-csrf-token": csrf, **headers},
    )


def test_a_write_over_the_limit_is_an_rfc7807_429_with_retry_after(
    db: str, api: TestClient
) -> None:
    """The refusal a client has to be able to act on.

    Three facts a caller needs and a 429 alone does not carry: that this is a rate and not a spent
    quota, how long to wait, and which bucket. All three are in the document, and the wait is also
    in
    the header every intermediary and SDK retry policy reads.
    """
    csrf = _sign_in(db, api)
    for _ in range(3):
        assert _write(api, csrf).status_code == 201

    refused = _write(api, csrf)
    assert refused.status_code == 429
    content_type = refused.headers["content-type"]
    assert content_type.startswith("application/problem+json")
    body = refused.json()
    assert body["code"] == "RATE_LIMITED"
    assert body["status"] == 429
    assert body["scope"] == "PRINCIPAL"
    assert body["limitPerMinute"] == 3
    assert body["retryAfterSeconds"] >= 1
    # The header and the body agree. Two numbers that could disagree is a contract nobody can trust.
    header_wait = refused.headers["Retry-After"]
    assert header_wait == str(body["retryAfterSeconds"])
    assert "not a quota" in body["detail"]


def test_a_refused_write_did_not_happen(db: str, api: TestClient) -> None:
    """A limiter that refuses after the work is a logger."""
    csrf = _sign_in(db, api)
    for _ in range(3):
        assert _write(api, csrf).status_code == 201

    with workspace_connection(db, WS) as conn:
        before = conn.execute("SELECT count(*) AS n FROM project").fetchone()
    assert _write(api, csrf).status_code == 429
    with workspace_connection(db, WS) as conn:
        after = conn.execute("SELECT count(*) AS n FROM project").fetchone()
    assert before is not None and after is not None
    assert int(after["n"]) == int(before["n"])


def test_reads_are_not_charged(db: str, api: TestClient) -> None:
    """The limit is on writes. A caller who has been refused can still see why."""
    csrf = _sign_in(db, api)
    for _ in range(3):
        assert _write(api, csrf).status_code == 201
    assert _write(api, csrf).status_code == 429

    for _ in range(10):
        listing = api.get(f"/v1/workspaces/{WS}/projects")
        assert listing.status_code == 200, listing.text


@pytest.mark.parametrize("verb", ["post", "put"])
def test_every_write_verb_is_charged(db: str, api: TestClient, verb: str) -> None:
    """POST and PUT both go through the same chokepoint.

    Enforcement lives in `build_context`, which every route reaches through `authorize`, so a new
    route cannot be added without it. This checks the verbs rather than trusting that: a limiter
    that
    only noticed POST would be bypassed by the first PUT somebody wrote.
    """
    csrf = _sign_in(db, api)
    created = _write(api, csrf)
    assert created.status_code == 201, created.text
    project_id = created.json()["projectId"]

    # Cleared so the assertion is about *this* request creating the buckets.
    with workspace_connection(db, WS) as conn:
        conn.execute("DELETE FROM rate_limit_bucket")

    if verb == "post":
        response = _write(api, csrf)
    else:
        response = api.put(
            f"/v1/workspaces/{WS}/projects/{project_id}/repair-surface",
            json={"paths": ["src"]},
            headers={"x-csrf-token": csrf},
        )
    assert response.status_code < 400, response.text

    with workspace_connection(db, WS) as conn:
        principal = rate_limits.bucket_state(conn, scope_kind="PRINCIPAL", scope_id=OWNER)
        workspace = rate_limits.bucket_state(conn, scope_kind="WORKSPACE", scope_id=WS)
    assert principal is not None, f"a {verb.upper()} was not charged to the principal"
    assert workspace is not None, f"a {verb.upper()} was not charged to the workspace"


def test_the_bucket_ignores_everything_the_caller_can_set(db: str, api: TestClient) -> None:
    """Identity comes from the session and the path, never from a header or a body field.

    A limiter keyed on anything a caller chooses is one the caller turns off by choosing
    differently.
    Each of these is a plausible attempt: a spoofed forwarded address, a spoofed user, and a
    workspace named in the body rather than the path.
    """
    csrf = _sign_in(db, api)
    spoofs = [
        {"X-Forwarded-For": "10.1.1.1"},
        {"X-Real-IP": "10.1.1.2"},
        {"X-User-Id": str(uuid.uuid4())},
        {"X-Workspace-Id": str(uuid.uuid4())},
    ]
    for index, headers in enumerate(spoofs):
        response = _write(api, csrf, **headers)
        # The first three are admitted and charged; by the fourth the allowance is gone -- which is
        # the point: changing the header changed nothing about which bucket was charged.
        assert response.status_code == (201 if index < 3 else 429), response.text

    with workspace_connection(db, WS) as conn:
        rows = conn.execute("SELECT count(*) AS n FROM rate_limit_bucket").fetchone()
    # Exactly two buckets: one principal, one workspace. A spoofable key would have made more.
    assert rows is not None and int(rows["n"]) == 2


def test_a_body_supplied_workspace_id_still_cannot_choose_the_bucket(
    db: str, api: TestClient
) -> None:
    """A body `workspaceId` is already refused as an authority claim, and must not reach the
    bucket either.
    """
    csrf = _sign_in(db, api)
    response = api.post(
        f"/v1/workspaces/{WS}/projects",
        json={"name": "from-body", "workspaceId": WS_OTHER},
        headers={"x-csrf-token": csrf},
    )
    # Refused for the existing reason, unchanged by this work.
    assert response.status_code == 400, response.text
    with workspace_connection(db, WS_OTHER) as conn:
        assert rate_limits.bucket_state(conn, scope_kind="WORKSPACE", scope_id=WS_OTHER) is None


def test_an_idempotency_key_does_not_buy_a_bypass(db: str, api: TestClient) -> None:
    """The limit is charged before idempotency is consulted, and that ordering is the guarantee.

    `authorize` runs at the top of every route and `run_idempotently` inside it, so a replay cannot
    reach its stored response without passing the limiter first. If it could, an idempotency key
    would be a way to write as fast as you like: the cheapest request, repeated, served from storage
    and uncounted.

    Asserted on the refusal rather than on a successful replay, because this is the direction that
    matters -- a caller holding a valid key must still be refused while they are over the rate.
    """
    csrf = _sign_in(db, api)
    for _ in range(3):
        assert _write(api, csrf).status_code == 201

    refused = api.post(
        f"/v1/workspaces/{WS}/projects",
        json={"name": "keyed"},
        headers={"x-csrf-token": csrf, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert refused.status_code == 429, refused.text
    assert refused.json()["code"] == "RATE_LIMITED"


def test_an_unauthenticated_caller_cannot_spend_anybody_s_allowance(
    db: str, api: TestClient
) -> None:
    """Authentication is resolved first, so there is no anonymous channel into a bucket."""
    api.cookies.clear()
    response = api.post(
        f"/v1/workspaces/{WS}/projects", json={"name": "anon"}, headers={"x-csrf-token": "nope"}
    )
    assert response.status_code == 401, response.text
    with unscoped_connection(db) as conn:
        rows = conn.execute("SELECT count(*) AS n FROM rate_limit_bucket").fetchone()
    assert rows is not None and int(rows["n"]) == 0


def test_a_non_member_gets_not_found_before_any_bucket_is_touched(db: str, api: TestClient) -> None:
    """The 404 that hides whether a workspace exists still comes first.

    If the limit were charged before membership, a non-member could tell a workspace that exists
    from
    one that does not by watching whether they were rate limited -- which would put the existence
    oracle back through a side channel.
    """
    csrf = _sign_in(db, api)
    response = api.post(
        f"/v1/workspaces/{WS_OTHER}/projects",
        json={"name": "not-mine"},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 404, response.text
    with unscoped_connection(db) as conn:
        other = conn.execute(
            "SELECT 1 FROM rate_limit_bucket WHERE scope_id = %s", (WS_OTHER,)
        ).fetchone()
    assert other is None


def test_the_session_routes_are_documented_as_uncovered(db: str, api: TestClient) -> None:
    """The honest limitation, asserted so it cannot drift silently.

    `POST /v1/sessions` has no principal and no workspace yet -- the request that creates one -- so
    there is nothing trustworthy to key a bucket on. The only candidate is a network address, which
    arrives in a header a caller controls, and keying a limit on that would be the exact mistake
    this
    work avoids everywhere else. `DELETE /v1/session` has a principal but no workspace.

    So neither is rate limited, and this test records that rather than letting a reader assume
    "every mutating route" included them. Closing it needs a trustworthy client address, which is a
    deployment concern (a proxy that sets it and a setting saying how many hops to trust).
    """
    import json as _json

    contract = _json.loads(
        (
            __import__("pathlib").Path(__file__).resolve().parents[2] / "contracts/openapi.json"
        ).read_text()
    )
    uncovered = {
        f"{method.upper()} {path}"
        for path, ops in contract["paths"].items()
        for method in ops
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and not path.startswith("/v1/workspaces/")
    }
    assert uncovered == {"POST /v1/sessions", "DELETE /v1/session"}, (
        "a mutating route outside /v1/workspaces/ appeared; it is not reached by the rate limit "
        f"chokepoint in build_context and needs its own decision: {sorted(uncovered)}"
    )


# --- the rate and the burst are different numbers -----------------------------------------------


@pytest.fixture()
def bursty_api(db: str) -> Iterator[TestClient]:
    """The same application with a burst of two, which is what separates the two numbers."""
    from accessforge_api.app import create_app
    from accessforge_api.config import ApiSettings

    settings = ApiSettings(
        database_url=db,
        evidence_endpoint_url=os.environ.get("OBJECT_STORE_ENDPOINT", "http://127.0.0.1:9000"),
        evidence_bucket=os.environ.get("OBJECT_STORE_BUCKET", "accessforge-evidence"),
        evidence_access_key=os.environ.get("OBJECT_STORE_ACCESS_KEY", "accessforge"),
        evidence_secret_key=os.environ.get("OBJECT_STORE_SECRET_KEY", "unset-for-this-test"),
        environment="test",
        rate_limit_principal_per_minute=3,
        rate_limit_workspace_per_minute=600,
        rate_limit_burst_multiplier=2.0,
    )
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) VALUES (%s,%s,'OWNER')",
            (WS, OWNER),
        )
    with TestClient(create_app(settings)) as client:
        yield client


def test_a_burst_above_one_does_not_inflate_the_reported_rate(
    db: str, bursty_api: TestClient
) -> None:
    """The two numbers the refusal reports are the rate and the burst, and they are not the same.

    `limitPerMinute` was the capacity, which is the per-minute allowance times the burst. So a
    120/minute policy with a burst of two told callers their limit was 240 -- a rate they cannot
    sustain, and a number appearing in nothing an operator had configured. A client pacing itself
    against it would be refused indefinitely while believing it was within the limit.

    At 3/minute with a burst of two: six admitted at once, the seventh refused, and the refusal says
    the rate is 3 and the burst is 6.
    """
    csrf = _sign_in(db, bursty_api)
    for index in range(6):
        response = _write(bursty_api, csrf)
        assert response.status_code == 201, f"request {index + 1} of the burst: {response.text}"

    refused = _write(bursty_api, csrf)
    assert refused.status_code == 429, refused.text
    body = refused.json()
    assert body["limitPerMinute"] == 3, "the sustained rate was reported as the burst capacity"
    assert body["burstCapacity"] == 6
    # And the prose agrees with the numbers, since that is what a person reads in a log.
    assert "3 writes per minute with a burst of 6" in body["detail"]


def test_without_a_burst_the_two_numbers_coincide_and_the_prose_says_one_thing(
    db: str, api: TestClient
) -> None:
    """The default case must not start mentioning a burst that is not there."""
    csrf = _sign_in(db, api)
    for _ in range(3):
        assert _write(api, csrf).status_code == 201
    body = _write(api, csrf).json()
    assert body["limitPerMinute"] == body["burstCapacity"] == 3
    assert "with a burst of" not in body["detail"]


def test_the_contract_documents_retry_after_on_exactly_the_limited_operations(db: str) -> None:
    """The generated 429 must match what the server can actually send.

    Every mutating operation used to carry the RATE_LIMITED response, including the two session
    routes, which are not limited and can never send one. A contract promising a refusal the server
    cannot produce is worse than one that omits it: a client writes a retry path for a response that
    never arrives, and nothing fails until something depends on it.

    Read from the generated document so the assertion is about the artifact consumers receive.
    """
    import json as _json
    from pathlib import Path as _Path

    contract = _json.loads(
        (_Path(__file__).resolve().parents[2] / "contracts/openapi.json").read_text()
    )
    writes = {
        f"{method.upper()} {path}"
        for path, ops in contract["paths"].items()
        for method in ops
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
    }
    promising = {
        f"{method.upper()} {path}"
        for path, ops in contract["paths"].items()
        for method, operation in ops.items()
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and "Retry-After" in operation["responses"]["429"].get("headers", {})
    }

    # Exactly the two session routes are silent, and they are exactly the two the limiter cannot
    # reach. Asserted as an equality so the set cannot drift in either direction: a new unlimited
    # route that claims a Retry-After fails here, and so does a limited one that omits it.
    assert writes - promising == {"POST /v1/sessions", "DELETE /v1/session"}
    assert promising, "no operation documents the rate-limit refusal at all"

    # And every read stays silent about it, because reads are not limited.
    reads_promising = {
        f"GET {path}"
        for path, ops in contract["paths"].items()
        if "get" in ops and "Retry-After" in ops["get"]["responses"]["429"].get("headers", {})
    }
    assert reads_promising == set()


# --- the charge outlives the request's own transaction
# ---------------------------------------------


@pytest.fixture()
def reviewer_api(db: str, api: TestClient) -> TestClient:
    """The same application with a reviewer signed in: a member who may read but not write.

    A reviewer holds EVIDENCE_READ and PATCH_REVIEW and nothing that creates a project, so every
    attempt is a permission denial -- which is exactly the request that used to cost nothing.
    """
    with workspace_connection(db, WS) as conn:
        conn.execute(
            "INSERT INTO workspace_membership (workspace_id, user_id, role) "
            "VALUES (%s, %s, 'REVIEWER')",
            (WS, MEMBER),
        )
    _sign_in(db, api, user_id=MEMBER)
    return api


def test_repeated_permission_denied_writes_become_rate_limited(
    db: str, reviewer_api: TestClient
) -> None:
    """The blocker. A request refused for permissions must still be charged.

    The decrements used to run on the request's connection, inside the single transaction
    `workspace_scope` holds for the whole request -- so the 403 rolled them back with everything
    else.
    A caller with no permission could therefore probe every write route for ever, at any rate, while
    the limiter reported itself working. Precisely the requests worth limiting were the ones that
    were
    never counted.

    Three denials exhaust the principal allowance of three; the fourth is the limiter, not the
    permission check. That ordering is the proof: the 429 can only appear if the first three were
    charged despite failing.
    """
    csrf = _sign_in(db, reviewer_api, user_id=MEMBER)
    for index in range(3):
        denied = _write(reviewer_api, csrf)
        assert denied.status_code == 403, f"attempt {index + 1}: {denied.text}"

    limited = _write(reviewer_api, csrf)
    assert limited.status_code == 429, limited.text
    assert limited.json()["code"] == "RATE_LIMITED"
    assert limited.json()["scope"] == "PRINCIPAL"

    # And none of it wrote anything. The charge is durable; the business transaction is not.
    with workspace_connection(db, WS) as conn:
        projects = conn.execute("SELECT count(*) AS n FROM project").fetchone()
        bucket = rate_limits.bucket_state(conn, scope_kind="PRINCIPAL", scope_id=MEMBER)
    assert projects is not None and int(projects["n"]) == 0, "a denied write committed data"
    assert bucket is not None, "the denied writes were never charged"
    assert float(bucket["tokens"]) < 1.0


def test_repeated_invalid_writes_become_rate_limited(db: str, api: TestClient) -> None:
    """The same for a write that fails on its own terms rather than on authority.

    A body missing a required field is refused by the route, after `authorize` has returned and the
    limiter has committed, so the attempt counts. Otherwise a caller could retry an invalid write at
    any rate for ever -- the cheapest possible request, costing a connection and a transaction each
    time, and never charged.

    Note which failure this uses. A body carrying an *unexpected* field is refused inside
    `authorize`
    *before* any identity is resolved, so there is no principal to charge and it is not counted;
    that
    gap is recorded in the handoff. This uses a failure the route itself raises, which is the class
    the blocker was about.
    """
    csrf = _sign_in(db, api)
    for index in range(3):
        invalid = api.post(
            f"/v1/workspaces/{WS}/projects",
            json={},
            headers={"x-csrf-token": csrf},
        )
        assert invalid.status_code == 400, f"attempt {index + 1}: {invalid.text}"

    limited = api.post(
        f"/v1/workspaces/{WS}/projects",
        json={"name": "valid-now"},
        headers={"x-csrf-token": csrf},
    )
    assert limited.status_code == 429, limited.text
    with workspace_connection(db, WS) as conn:
        projects = conn.execute("SELECT count(*) AS n FROM project").fetchone()
    assert projects is not None and int(projects["n"]) == 0


def test_a_refused_request_spends_no_token_from_the_other_bucket(db: str) -> None:
    """Both buckets move together, so being refused by one does not drain the other.

    The two decrements share one transaction and a denial leaves it by exception, so nothing is
    spent.
    Otherwise a caller over their workspace limit would lose their personal allowance to refusals
    they
    got no work from -- and would stay refused after the workspace recovered.
    """
    # Workspace bucket empty, principal bucket untouched.
    for _ in range(CAPACITY):
        assert _consume(db, scope_kind="WORKSPACE", scope_id=WS).allowed is True

    from accessforge_api.dependencies import _Denied, enforce_write_rate_limit

    class _Req:
        method = "POST"

        class app:  # noqa: N801 - mimicking Starlette's shape, not naming a class for export
            class state:
                pass

    request = _Req()
    request.app.state.config = type(  # type: ignore[attr-defined]
        "C",
        (),
        {
            "database_url": db,
            "rate_limit_principal_per_minute": CAPACITY,
            "rate_limit_workspace_per_minute": CAPACITY,
            "rate_limit_burst_multiplier": 1.0,
        },
    )()

    from accessforge_api.problems import ProblemDetail

    with pytest.raises(ProblemDetail) as refusal:
        enforce_write_rate_limit(
            request,  # type: ignore[arg-type]
            principal_id=OWNER,
            workspace_id=WS,
            request_id="test",
            now=AT,
        )
    assert refusal.value.extra["scope"] == "WORKSPACE"
    assert _Denied is not None  # the refusal travels out of the transaction as this type

    # The principal's bucket was decremented inside that transaction and rolled back with it, so the
    # principal still has a full allowance.
    with workspace_connection(db, WS) as conn:
        principal = rate_limits.bucket_state(conn, scope_kind="PRINCIPAL", scope_id=OWNER)
    assert principal is None, "a workspace refusal spent a principal token"


def test_the_charge_survives_a_rollback_of_the_business_transaction(
    db: str, api: TestClient
) -> None:
    """Stated as the mechanism rather than the symptom.

    The limiter's transaction is separate, so its decrement is committed while the request's own
    transaction is still open -- and stays committed when that one rolls back. This is the property
    the two tests above depend on, asserted directly: one failed write, one token gone.
    """
    csrf = _sign_in(db, api)
    failed = api.post(
        f"/v1/workspaces/{WS}/projects",
        json={},
        headers={"x-csrf-token": csrf},
    )
    assert failed.status_code == 400, failed.text

    with workspace_connection(db, WS) as conn:
        principal = rate_limits.bucket_state(conn, scope_kind="PRINCIPAL", scope_id=OWNER)
        workspace = rate_limits.bucket_state(conn, scope_kind="WORKSPACE", scope_id=WS)
        projects = conn.execute("SELECT count(*) AS n FROM project").fetchone()
    assert principal is not None and workspace is not None, (
        "the failed write was not charged, so its transaction took the decrements with it"
    )
    assert projects is not None and int(projects["n"]) == 0


def test_a_process_with_no_database_refuses_the_write_rather_than_admitting_it(db: str) -> None:
    """A limiter that cannot evaluate must fail closed.

    If the bucket is unreachable the honest answer is that this request cannot be shown to be
    within the limit, so it is refused. Failing open would turn a misconfiguration into an unlimited
    write path, and the symptom would be silence: every request admitted, nothing in the logs, the
    limit simply absent.
    """
    from accessforge_api.dependencies import enforce_write_rate_limit
    from accessforge_api.problems import ProblemCode, ProblemDetail

    class _Request:
        method = "POST"

        class app:  # noqa: N801 - mimics Starlette's attribute shape
            class state:
                config = type("C", (), {"database_url": ""})()

    with pytest.raises(ProblemDetail) as refusal:
        enforce_write_rate_limit(
            _Request(),  # type: ignore[arg-type]
            principal_id=OWNER,
            workspace_id=WS,
            request_id="test",
            now=AT,
        )
    assert refusal.value.code is ProblemCode.DEPENDENCY_UNAVAILABLE
    assert "fails open is not a limiter" in refusal.value.detail
