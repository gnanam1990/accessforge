# Module 26 addendum — write rate limits

Closes the gap `STATUS.md` recorded as "no telemetry or rate limits", on the rate-limit half.
Telemetry remains open (P4).

## What the gap was

Entitlements already bounded *how much* a workspace consumes over a billing period. Nothing bounded
*how fast* anyone could ask. Those are different controls: a caller can spend a month's entitlement in
a second, and a caller with nothing left can still cost a connection and a transaction per request.

## What exists now

**What is limited:** authenticated mutating requests to `/v1/workspaces/...` — every `POST`, `PUT`,
`PATCH` and `DELETE` that reaches `build_context`, which is every such route, since each one gets
there through `authorize`. Each is limited twice, per principal and per workspace, and both buckets
must admit it.

**What is not:** the two routes outside that prefix, `POST /v1/sessions` and `DELETE /v1/session`.
Neither has a key the limiter can trust — sign-in has no principal yet and sign-out has no workspace —
and neither is covered by any claim in this document. Reads are not limited either. The uncovered set
is asserted by `the session routes are documented as uncovered` so it cannot grow silently.

| | Default | Setting |
|---|---|---|
| Per principal | 120 writes/minute | `ACCESSFORGE_RATE_LIMIT_PRINCIPAL_PER_MINUTE` |
| Per workspace | 600 writes/minute | `ACCESSFORGE_RATE_LIMIT_WORKSPACE_PER_MINUTE` |
| Burst | 1× one minute's allowance | `ACCESSFORGE_RATE_LIMIT_BURST_MULTIPLIER` |

Three decisions worth reading before changing this.

**The charge is durable independently of the request.** The limiter runs on its own short-lived
connection and commits before the route can fail. It used to run on the request's connection, which
`workspace_scope` holds inside a single transaction for the whole request — so any later refusal, a
permission denial or a domain error, rolled the transaction back and the tokens came back with it.
Precisely the requests worth limiting were the ones never charged: a caller without permission could
probe every workspace write route for ever, and a caller whose writes kept failing could retry for
ever, while the limiter reported itself working. A second connection per limited request is the cost of that,
and it is the point rather than an oversight — a decision durable only when the request succeeds is
not a limit.

Both buckets share that one transaction, so they move together. A refused request consumes nothing:
if the workspace bucket denies after the principal bucket was decremented, the exception leaves the
block, the transaction rolls back and neither token is spent. Otherwise a refusal from one bucket
would quietly drain the other, and a caller over their workspace limit would lose their personal
allowance to refusals they got no work from. Locks are always taken principal-then-workspace, so two
requests from one principal in different workspaces cannot deadlock.

If the limiter cannot be evaluated at all — no configured database — the write is **refused**, not
admitted. A limiter that fails open turns a misconfiguration into an unlimited write path whose only
symptom is silence.

**The bucket lives in PostgreSQL.** An in-process counter is correct only while there is exactly one
API process. Two behind a load balancer each enforce their own half, so the effective limit rises as
the deployment scales — a limit that loosens when you add capacity is not a limit.
`the bucket is shared across processes` is the test an in-process counter fails and every other test
here passes.

**One statement decides.** The refill, the comparison and the decrement are a single
`INSERT ... ON CONFLICT DO UPDATE ... WHERE`, so two concurrent requests for the same bucket serialize
on the row and exactly one gets the last token. Read-then-write would let both read the same count,
both find room and both proceed — a limiter that stops working under the load it exists for.
`concurrent requests do not both get the last token` holds one transaction open while another
contends.

**A token bucket, not a fixed window.** A fixed window lets a caller spend the whole allowance at the
end of one window and again at the start of the next, so the observed burst is twice the configured
rate at every boundary.

## The refusal

`429` with `code: RATE_LIMITED`, distinct from `QUOTA_EXHAUSTED` which is also `429`. The distinction
is the point: a rate returns on its own, a quota does not return without a new entitlement, and a
client that cannot tell them apart either retries forever or gives up a second too early.

```
HTTP/1.1 429 Too Many Requests
Content-Type: application/problem+json
Retry-After: 2

{"code": "RATE_LIMITED", "scope": "PRINCIPAL", "limitPerMinute": 3, "burstCapacity": 3,
 "retryAfterSeconds": 2, "detail": "... This is not a quota ..."}
```

`limitPerMinute` is the **sustained rate** and `burstCapacity` is how many may arrive at once. They
are equal only when the burst multiplier is one. Reporting the capacity as the per-minute limit told a
caller on a 120/minute policy with a burst of two that their limit was 240 — a rate they cannot
sustain, and a number appearing in nothing an operator configured, so a client pacing itself against
it would be refused indefinitely while believing it was inside the limit. The rate is recovered from
the refill itself, so the reported limit and the enforced limit cannot be different numbers.

`Retry-After` is whole seconds, never zero — advising an immediate retry invites the loop the limit
exists to stop — and it carries the same number as `retryAfterSeconds` in the body. The header is what
intermediaries and SDK retry policies read; the body is what a person reading a log sees. Both are
documented on every mutating operation the limiter actually reaches, and on nothing else — not on
reads, and not on the two session routes, which cannot send this refusal. A contract promising a
response the server cannot produce is worse than one that omits it: a client writes a retry path for
something that never arrives. `the contract documents retry-after on exactly the limited operations`
asserts the set as an equality, so it cannot drift in either direction.

## Why identity cannot be chosen by the caller

Enforcement is inside `build_context`, the one place every route reaches through `authorize`. The
principal is the one `resolve_human_principal` returned from the session cookie and a live membership;
the workspace is the path segment that membership was checked against. No header and no body field
reaches the bucket key. A limiter keyed on anything a caller supplies is one the caller turns off by
supplying something else, which is why `X-Forwarded-For`, `X-Real-IP`, `X-User-Id`, `X-Workspace-Id`
and a body `workspaceId` are all exercised in tests and all change nothing.

**Ordering.** Authenticated → CSRF → body/path agreement → membership (404) → **rate limit (429)** →
permission (403).

- After membership, so a non-member still gets the uniform 404 and cannot use the limit as a side
  channel for whether a workspace exists.
- Before permission, because a request about to be refused for permissions is still a request
  somebody made; not charging for it would leave an unmetered channel for probing every route.
- Before idempotency, so a key cannot buy a bypass — the cheapest request, repeated and served from
  storage, would otherwise be uncounted.

A principal's bucket is **global**, not per (principal, workspace). Otherwise being invited to another
tenant would multiply a human's write rate, so the control could be widened by an invitation. That is
the one deliberate exception to workspace isolation in the schema: the policy admits rows whose
`workspace_id` is NULL. What it exposes is a counter and a user id, to a connection that can only
reach it through this application, which only ever writes the bucket its own session resolved. A
workspace bucket still carries its `workspace_id` and is invisible to other tenants — asserted.

## Adversarial review, before committing

| Checked | Result |
|---|---|
| Alternate write verbs | `MUTATING_METHODS` is POST/PUT/PATCH/DELETE; POST and PUT are exercised, and a mutant limiting only POST is caught |
| Alternate routes | Enforcement is in the single chokepoint; a test asserts the complete set of mutating routes outside `/v1/workspaces/` is exactly the two session routes |
| Double charging | No handler calls `authorize` twice (checked by AST across every route module), and one request spends exactly one token from each bucket |
| Missing or spoofed identity | Unauthenticated is 401 with no bucket created; four spoof headers change nothing |
| Concurrency | Two connections contend for one row; the loser is refused |
| Stale bucket cleanup | `prune_idle_buckets`, hooked into the existing maintenance sweep; a pruned bucket returns full, which is what an idle bucket already was |
| Clock boundary | A backwards clock mints no tokens; refill timing checked at 1s and 2s |
| RLS / tenancy | A workspace bucket is invisible from another tenant; opening the policy is caught |
| Idempotent replay | A valid key does not bypass the limit |
| Rollback of the business transaction | The charge survives it: repeated 403s and repeated 400s both reach RATE_LIMITED with no project committed |

13 mutation checks across the limiter, the enforcement point and the schema; 12 caught. After
maintainer review, nine more over the three findings below — all caught, including one per restored
0021 assertion.

## Maintainer review, and what it found

**`limitPerMinute` was the burst capacity, not the rate.** Fixed as above, with a burst-of-two
regression asserting six admitted, the seventh refused, and the two numbers reported separately.

**The contract promised RATE_LIMITED on routes that cannot send it.** The 429 was attached to every
mutating operation, including the two session routes. Now attached only to the operations under
`/v1/workspaces/`, which is the same predicate the runtime uses.

**Moving migration 0021's coverage had weakened it.** When 0021 stopped being the newest migration its
assertions became name-and-existence checks, which is how a guard quietly stops being checked: the
test keeps passing while the thing it named turns into something else. Restored in full — `DELETE`
semantics inside `content_matches_operation`, both `patch_change` uniqueness constraints, the
non-empty repair surface, all five FORCE RLS tables, and the finding foreign key as RESTRICT — and
each is individually mutation-checked.

## Limitations — read these

- **The two session routes are not limited, and cannot be by this mechanism.**
  `POST /v1/sessions` has no principal and no workspace — it is the request that creates one — so the
  only candidate key is a network address, which arrives in a header a caller controls.
  `DELETE /v1/session` has a principal but no workspace. Closing this needs a trustworthy client
  address, which is a deployment concern (a proxy that sets it, and a setting for how many hops to
  trust). A test asserts the uncovered set so it cannot grow silently.
- **Limits are global configuration, not per workspace.** One pair of numbers for the deployment.
  A noisy tenant is limited at the same rate as a quiet one. Per-workspace policy is a table and a
  settings route away and is deliberately not in this slice.
- **The `GREATEST(0, ...)` clock clamp is defence in depth, not covered behaviour.** With the `>= 1`
  guard in the same statement a negative elapsed term can only reduce the available count, so the
  update never runs and tokens never go negative. A mutation check confirmed removing the clamp
  changes nothing any test can observe. It stays because it makes the arithmetic correct on its own
  terms; it is recorded here rather than claimed as tested.
- **Refusals raised before identity is resolved are not charged.** An unexpected body field, a
  malformed body, a missing session and a missing CSRF token are all refused inside `authorize` or
  earlier, before a principal exists — and there is no verified identity to charge, so they are not
  counted. The limiter cannot close this without keying on something a caller supplies, which is the
  mistake it avoids everywhere else. A caller can therefore retry *malformed* writes without limit;
  they reach no business logic and write nothing, but they do cost a request.
- **A second database connection per limited request.** The price of a durable charge. Not measured
  under load.
- **No load test.** The concurrency guarantee is argued from one statement and shown with two
  contending connections. Behaviour under thousands of concurrent writers on one bucket — lock
  contention, latency — has not been measured.
