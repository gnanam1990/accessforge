# Module 26 addendum — structured request telemetry

Closes the remaining half of the gap `STATUS.md` recorded as "no telemetry or rate limits". Rate
limits merged as PR #33; this is the telemetry.

## What the gap was

The product could tell a caller whether their request succeeded, and could tell an operator nothing.
"What is this deployment doing" was answered by server access and `grep`, which stops working the
moment there is more than one process.

## What exists now

One structured record per request, emitted by a single ASGI middleware, on the
`accessforge.telemetry` logger. `create_app` installs a JSON handler on stdout, so the default
deployment emits machine-readable records with no further configuration — the first version provided
a `JsonFormatter` that nothing attached, which meant the default uvicorn process emitted prose from
the root configuration and the machine-readable claim was true of a class nobody constructed.

The installation is idempotent (`create_app` runs once per process in production and dozens of times
in this suite, and a handler per call turns one request into that many duplicate lines) and it turns
`propagate` off, because otherwise every record is emitted twice: once as JSON here and once as prose
by the root handler uvicorn configures. A handler attached to the same logger — a test capture, or a
deployment's own — still receives everything, since `propagate` suppresses ancestors rather than
siblings.

```json
{"event":"api.request","correlation_id":"3f2c…","method":"POST",
 "route":"/v1/workspaces/{workspace_id}/runs","status":429,"outcome":"REFUSED",
 "duration_ms":4.118,"problem_code":"RATE_LIMITED","streamed":false,
 "level":"INFO","logger":"accessforge.telemetry","timestamp":"2026-09-12T19:04:11+0000"}
```

| Field | Why it is there |
|---|---|
| `route` | The **template**, never the path. `/v1/workspaces/{workspace_id}/runs` is one of ~62 values; the URL contains two tenant identifiers and is unbounded. |
| `outcome` | `SUCCEEDED` / `REFUSED` / `FAILED`. 4xx is REFUSED: the server worked and declined, and a dashboard counting a 403 as a failure teaches operators to ignore failures. |
| `problem_code` | The stable `ProblemCode`, on refusals only. `detail` prose changes with every clarification; an alert keyed on prose breaks when somebody improves a sentence. |
| `correlation_id` | **Server-generated, always.** Returned as `X-Correlation-Id` and as `correlationId` in every problem document, so a caller can quote the identifier that appears in the log. |
| `duration_ms`, `method`, `status` | The minimum an operator needs to see load, latency and error rate. |
| `streamed` | Derived from the **response headers** — no `content-length`, or a `text/event-stream` content type — meaning the body was produced after the status was sent. It does not observe the body and guarantees nothing about completion: a `200` with `streamed: true` says the headers claimed success, not that the stream finished. |

**Middleware, not handlers.** It sees requests that never reach a route — an unmatched path, a
validation failure — which is where an attack looks like traffic. It sees the *final* status, after the
problem handler has converted a refusal. And it runs once, so no arrangement of handlers produces two
records or none. The refusal handlers record only the stable code on the ASGI scope and log nothing;
two writers for one request is how a log acquires a duplicate nobody notices until they are counting.

**An escaping exception is recorded as a 500 and re-raised unchanged.** Letting it past unrecorded
would leave crashes as the only outcome telemetry never mentions; swallowing it to produce a record
would turn a crash into a silent 200.

**Silence is exact, on both paths.** The quiet list applies to a route's exceptions as well as its
normal responses. Applying it only after a normal response meant a crashing health probe emitted a
record a second — from the one route an operator silenced precisely because it is polled constantly,
and in the situation where the log is least readable. A non-quiet route raising the same way is the
control, so "quiet" cannot quietly come to mean "records nothing at all".

**Health probes are silenced by default** (`ACCESSFORGE_TELEMETRY_QUIET_ROUTES`). A load balancer
polling `/health/live` every second produces 86,400 records a day that say nothing, and a signal
buried in noise is one nobody reads. The silencing list uses route templates — the same values that
appear in `route` — so what to quiet is read off a record rather than guessed.

## The privacy boundary

The **request-specific** fields are a whitelist, not a redacted dump: every one of them is a field of
`RequestRecord`, and nothing else about the request is collected, because the safe way to keep evidence
out of a log is never to hand it to the logger. A denylist — collect the request, then strip the
dangerous parts — passes until somebody adds a field, and then fails silently in the artifact you only
read after an incident.

The emitted line is not *only* those fields. `JsonFormatter` adds three pieces of fixed metadata —
`level`, `logger` and `timestamp` — which describe the log event rather than the request and carry
nothing derived from it. The test that pins the whitelist excludes exactly those three and asserts
everything else is a `RequestRecord` field.

Never recorded: request or response bodies, headers, cookies, tokens, reader speech, screenshots or
any other evidence content, object-store keys, and the text of exceptions or refusals.

**Two identifiers, because one of them is the caller's.** `X-Request-Id` is echoed in every problem
document — an established contract with its own test — and it is a header a caller fills in, so it is
bytes the caller chose and may contain a credential. The first version of this work recorded it, which
contradicted the promise above; an independent review found it, and the leakage tests had missed it
because they planted canaries in every header *except* the one that was emitted.

So there are two:

| | Source | Where it appears |
|---|---|---|
| `requestId` / `X-Request-Id` | the caller's, if supplied | the problem document and the response header — **never** telemetry |
| `correlationId` / `X-Correlation-Id` | always server-generated | the problem document, the response header, and the record |

The cost is that a caller who supplies their own id cannot find it in the log: their value is absent
by design. `correlationId` is additive in the problem document precisely so support has an identifier
that is in both places.

**Every problem response uses the resolved id**, whatever constructed it. `ProblemDetail` mints a
fresh UUID when no `request_id` is passed and twenty-eight construction sites pass none — every early
refusal in the session routes among them, plus FastAPI's validation handler, which runs before any
route body exists to hold a context. Each of those answered with an id matching neither the
`X-Request-Id` header on the same response nor anything else, so a caller quoting it was quoting a
number that existed for one response and then nowhere. The two exception handlers now take the id
from the scope unconditionally: a route that passed `context.request_id` passed this same value, so it
is a no-op there, and being unconditional means no construction site can reintroduce the divergence.

**Identifiers are excluded too, and that costs something.** No workspace id, no principal id, no run
id, no raw path. So a record **cannot be attributed to a tenant**: this telemetry answers "what is
the API doing" and cannot answer "what is that customer doing". Per-tenant analytics would need a
different mechanism with its own review. The correlation path for a specific complaint is
`correlation_id`, which is server-generated and is the only identifier in the record; it reaches the
caller as the `X-Correlation-Id` header and as `correlationId` in every problem document. A
client-supplied `request_id` appears **only** in the response — echoed as `X-Request-Id` and as
`requestId` in the problem document — and never in telemetry.

## Evidence behind those claims

Not asserted by description. The leakage tests plant a distinctive canary in the body, in four
headers including `Authorization` and `Cookie`, and in an unmatched path, then search the serialised
log — both the structured fields and the formatted lines, since a leak can arrive through a formatter
as easily as through a record. A further test asserts the record's key set is **exactly** the
whitelist and that `RequestRecord`'s fields are exactly that set, so a field added without review
fails there rather than in an incident review. Another asserts no workspace id, principal id or
project id appears anywhere in the log for requests that used all three.

43 tests. 19 mutation checks over the middleware, the record, the logging configuration and the
problem handlers, all caught: the raw path as a route
label, an unreviewed field, a refusal reported as a failure, the request id regenerated per call site
(which would make the record uncorrelatable), a second writer duplicating records, quiet routes
ignored, refusal prose carried into the record, and three over the streamed/buffered distinction.

## What the reviews found in my own work

**PR #34's review found two more.** Problem documents could answer with a freshly minted id that
disagreed with `X-Request-Id` on the same response — twenty-eight construction sites pass no
`request_id`, so this reached every early session refusal and every validation failure. And the quiet
route list was applied only after a normal response, so a silenced route emitted a 500 record per
request when it crashed. Both are fixed at their single points and both have regressions covering
supplied and generated ids, with a non-quiet control proving the silence is exact rather than total.



**The emitted identifier was the client's header.** Covered above. The fix is two identifiers, and
the adversarial test now plants a secret in `X-Request-Id` itself, asserts it is still echoed (the
contract is preserved) and that it appears nowhere in the serialised log.

**The JSON handler was never installed.** Covered above. The test for it was also weak: it called
`configure_telemetry_logging` itself before building the app, so removing the call from `create_app`
changed nothing it could see — it was testing its own setup rather than the product. A mutation check
caught that. It now replaces `sys.stdout` before `create_app` runs and installs nothing, so an empty
buffer fails the test.



The first `streamed` implementation asked the response object what class it was. Every response
passing through `BaseHTTPMiddleware` is re-wrapped as a streaming response on the way out, so it
answered "streaming" for a 404 with a fixed body — the flag was true for **every** request and
therefore carried no information. It is now decided from the headers: a buffered response carries
`content-length` because the server knew the size in advance. The test asserts both directions in one
case, because a flag that is always true passes any test that only checks the true side.

The test that caught it was also wrong to begin with: it drove the product's real event stream, which
produces events for as long as a client listens, so it hung for five minutes rather than failing. The
semantics are now pinned by five deterministic cases over controlled headers plus one bounded
integration case using a finite generator through the real middleware.

## Limitations — read these

- **No per-tenant attribution**, by design, as above. If operations later needs it, that is a new
  decision with its own privacy review, not a field somebody adds.
- **No metrics backend and no traces.** These are log records. There is no counter, no histogram and
  no span; latency percentiles come from whatever aggregates the logs. Wiring OpenTelemetry is a
  separate slice with a dependency and a deployment story.
- **`streamed` is a header-derived signal and observes nothing about the body.** It is inferred from
  the absence of `content-length` or a `text/event-stream` content type — never from watching the
  response — so it guarantees nothing about completion. A stream that aborts halfway still records
  the 200 it sent, with `streamed: true` as the only warning. Recording the true end of a
  stream needs the middleware to wrap the body iterator, which was not done here.
- **Nothing is emitted for requests the ASGI server rejects before the app** — a malformed request
  line, a TLS failure. Those live in the server's own log.
- **No sampling and no rate limit on the records themselves.** One record per request is fine at this
  scale and is a cost to reconsider under real load; it has not been load tested.
