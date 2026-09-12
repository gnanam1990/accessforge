"""One structured record per request, and nothing in it that should not leave the process.

Module 26's remaining gap. The product could already say whether a request succeeded to the caller
who made it and to nobody else: an operator asking "what is this deployment doing" had server access
and `grep` as the answer, which does not survive contact with more than one process.

**The record is a deliberate whitelist, not a redacted dump.** Everything in it is named in
`RequestRecord` and nothing else is collected, because the safe way to keep evidence out of a log is
to never hand it to the logger. A denylist -- collect the request, then strip the dangerous parts --
fails the first time somebody adds a field, and it fails silently, in the artifact you only read
after something has gone wrong.

What that excludes, explicitly: request and response bodies, headers, cookies, tokens, any reader
speech or screenshot or other evidence content, object-store keys, and the text of exceptions. The
exception messages in this codebase carry the specifics that help an operator *at the point of
failure*; a log shipped to a collector is a different audience, and `detail` strings here name
workspaces, projects, digests and occasionally the phrase a screen reader announced.

**Identifiers are excluded too, and that costs something.** There is no workspace id, no principal
id, no run id and no raw path -- only the route template. So a record cannot be attributed to a
tenant, which means this telemetry answers "what is the API doing" and cannot answer "what is that
customer doing". That limit is the privacy boundary, and `requestId` is how a specific complaint is
correlated: it appears in the record and in the RFC7807 document the caller received, and nowhere
else.

**The route template, never the path.** `/v1/workspaces/{workspace_id}/runs/{run_id}` rather than
the URL: the first is one of sixty-odd values and the second contains two tenant identifiers and is
unbounded. High-cardinality labels are also what makes a metrics backend fall over, so this is an
operational requirement as much as a privacy one.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

#: Where a request's resolved correlation id lives for the life of the request. On the ASGI scope
#: rather than `request.state`, because middleware and a route handler see different `Request`
#: objects over the same scope -- the scope is the thing they genuinely share.
SCOPE_REQUEST_ID = "accessforge.request_id"

#: The server-generated correlation id, and the only identifier telemetry emits. Separate from
#: `SCOPE_REQUEST_ID` because that one may be a value the *client* supplied: `X-Request-Id` is
#: echoed
#: in every problem document, which is an established contract, and a caller is free to put a secret
#: in a header they control. Emitting it would have broken the one promise this module makes.
SCOPE_CORRELATION_ID = "accessforge.correlation_id"

#: Where a refusal records its stable problem code, for the one record the middleware emits. The
#: handler does not log: two writers for one request is how a log grows a duplicate that nobody
#: notices until they are counting.
SCOPE_PROBLEM_CODE = "accessforge.problem_code"

#: Emitted when a request matched no route. The raw path is deliberately *not* used as a fallback:
#: an
#: unmatched path is attacker-controlled, unbounded, and the most likely place for a credential
#: somebody pasted into a URL.
UNMATCHED_ROUTE = "<unmatched>"

_log = logging.getLogger("accessforge.telemetry")


class Outcome:
    """What happened, in three words rather than a status range every consumer re-derives."""

    SUCCEEDED = "SUCCEEDED"
    REFUSED = "REFUSED"
    FAILED = "FAILED"


def outcome_for(status_code: int) -> str:
    """Map a status to an outcome.

    4xx is REFUSED rather than FAILED: the server worked correctly and declined, and a dashboard
    that
    counts a 403 as a failure teaches its operators to ignore failures.
    """
    if status_code >= 500:
        return Outcome.FAILED
    if status_code >= 400:
        return Outcome.REFUSED
    return Outcome.SUCCEEDED


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """Everything telemetry says about one request. The whitelist is this class.

    Flat and scalar throughout, so it serialises to one JSON object per line with no nesting for a
    collector to flatten and no room for a dict somebody passed through.
    """

    event: str

    correlation_id: str
    """Server-generated, always. Never the client's `X-Request-Id`, which is echoed in the response
    and in the problem document but is bytes a caller chose -- and a caller may put a credential in
    a header they control. The same value is returned as `X-Correlation-Id` and as `correlationId`
    in
    every problem document, so a customer can quote the identifier that appears in the log."""

    method: str
    route: str
    status: int
    outcome: str
    duration_ms: float

    #: The stable `ProblemCode`, present only on a refusal. Stable is the point: `detail` prose
    #: changes with every clarification, and an alert keyed on prose breaks when somebody improves a
    #: sentence.
    problem_code: str | None = None

    #: True when the response body was streamed. Then `status` describes the headers, which were
    #: sent
    #: before the body existed -- so a 200 here is not a claim that the stream completed.
    streamed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the record's fields at the top level.

    Provided because a deployment should not have to add a dependency to get machine-readable logs,
    and because the alternative -- formatting the fields into a message string -- produces something
    that has to be parsed back out with a regular expression that breaks on the first value
    containing a space.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        }
        emitted = getattr(record, "accessforge", None)
        if isinstance(emitted, dict):
            payload.update(emitted)
        else:
            payload["message"] = record.getMessage()
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def resolve_correlation_id(request: Request) -> str:
    """The server-generated identifier for this request, resolved once.

    Random, never derived from anything about the request, and never read from one: this is the only
    identifier telemetry emits, so it is the one place a client must not be able to reach. The
    client-facing `X-Request-Id` is resolved separately by `resolve_request_id` and stays out of the
    record entirely.
    """
    existing = request.scope.get(SCOPE_CORRELATION_ID)
    if isinstance(existing, str) and existing:
        return existing
    return str(uuid.uuid4())


def resolve_request_id(request: Request) -> str:
    """The client-facing correlation id, resolved once.

    Echoed in every problem document and in `X-Request-Id`, which is an established contract, so a
    supplied value is still honoured. It is deliberately **not** what telemetry records: see
    `resolve_correlation_id`.

    Reads the scope first so a second caller gets the same answer, then a client-supplied
    `X-Request-Id`, then a fresh UUID. Bounded and printable-checked before it is trusted: it is
    echoed in a response header and written to a log, so an unbounded or control-character value
    would be a header-injection and log-forging vector supplied by the client.

    Random when not supplied, never derived from anything about the request. A derived id would leak
    what it was derived from into the one field that appears in an otherwise deliberately
    uninformative
    refusal.
    """
    existing = request.scope.get(SCOPE_REQUEST_ID)
    if isinstance(existing, str) and existing:
        return existing
    supplied = request.headers.get("X-Request-Id")
    if supplied and len(supplied) <= 128 and supplied.isprintable():
        return supplied
    return str(uuid.uuid4())


def route_template(request: Request) -> str:
    """The matched route's template, or a sentinel.

    Starlette puts the matched route on the scope, and its `path` is the template with parameter
    names rather than values. Falling back to `request.url.path` would defeat the whole point: that
    string carries the identifiers the template exists to keep out, and for an unmatched request it
    is whatever the client sent.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    return str(template) if isinstance(template, str) and template else UNMATCHED_ROUTE


def emit(record: RequestRecord, *, logger: logging.Logger | None = None) -> None:
    """Write one record.

    The fields go in `extra` under a single key rather than as separate attributes: `logging`
    rejects
    an `extra` that collides with a `LogRecord` attribute -- `message`, `module`, `args` and a dozen
    others -- and a telemetry field named by somebody who did not know that list would raise at the
    moment it was most needed.

    The message is a fixed string. Everything variable is in the structured payload, so a consumer
    never parses prose.
    """
    (logger or _log).info("request", extra={"accessforge": record.as_dict()})


def record_for(
    request: Request,
    *,
    status_code: int,
    duration_ms: float,
    streamed: bool = False,
) -> RequestRecord:
    """Build the record for a finished request, from the scope and nothing else."""
    problem_code = request.scope.get(SCOPE_PROBLEM_CODE)
    return RequestRecord(
        event="api.request",
        correlation_id=str(request.scope.get(SCOPE_CORRELATION_ID) or ""),
        method=request.method,
        route=route_template(request),
        status=status_code,
        outcome=outcome_for(status_code),
        duration_ms=round(duration_ms, 3),
        problem_code=str(problem_code) if problem_code else None,
        streamed=streamed,
    )


def is_streaming(response: Response) -> bool:
    """Whether this response's body was produced after its status was sent.

    Matters because the status then describes the headers only. A 200 on a stream that aborts
    halfway is not a successful request, and a record reporting it as one would be the most
    confident wrong number in the system.

    Detected from the headers, not from the response class. Every response that passes through
    `BaseHTTPMiddleware` is re-wrapped as a streaming response on the way out, so asking the object
    what it is answers "streaming" for a 404 with a fixed body -- which made the flag true for every
    request and therefore worth nothing.

    A buffered response carries `content-length`, because the server knew the size before sending.
    One that does not, or that declares itself an event stream, had its body produced afterwards.
    """
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("text/event-stream"):
        return True
    return "content-length" not in response.headers


#: Marks the handler this module installs, so a second call recognises its own work. Identity by
#: attribute rather than by type: a deployment is free to add its own `StreamHandler` with a
#: `JsonFormatter`, and replacing that would be this module overruling a decision it does not own.
_INSTALLED = "accessforge_telemetry_handler"


def configure_telemetry_logging(*, stream: Any | None = None) -> logging.Handler | None:
    """Install one JSON handler on the telemetry logger, if nobody has.

    A formatter nothing installs produces plain prose from uvicorn's own root configuration, which
    is
    what the default deployment actually emitted: the machine-readable claim was true of a class
    nobody constructed. This is the wiring that makes it true of a process.

    **Idempotent**, and it has to be: `create_app` is called once per process in production and
    dozens of times in this test suite, and a handler added per call turns one request into as many
    duplicate lines as there have been apps. The marker attribute is how a second call recognises
    its
    own handler rather than counting.

    **`propagate` is turned off.** Otherwise every record is emitted twice -- once as JSON here,
    once
    as prose by the root handler uvicorn configures -- and a consumer reading stdout would find each
    request reported in two formats, one of which is the one this module exists to avoid.

    Returns the handler it installed, or None when one was already there. A caller that wants a
    different destination passes `stream`; a caller that wants no handler at all does not call this.
    """
    logger = logging.getLogger("accessforge.telemetry")
    for existing in logger.handlers:
        if getattr(existing, _INSTALLED, False):
            return None

    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _INSTALLED, True)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    # Records stop here. A test that attaches its own handler still receives them -- handlers on the
    # same logger all run -- so capture keeps working without the duplicate prose.
    logger.propagate = False
    return handler
