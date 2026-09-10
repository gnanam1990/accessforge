"""Freezing a journey version, listing versions, and reporting what may be authored.

Three routes, and the shape of the first one carries most of the module's meaning.

**Freezing is a create, not an edit.** There is no `PATCH /journeys/{id}`, and there cannot be: the
table refuses every UPDATE with a trigger, because success criteria that could change mid-run would
make every outcome provisional. Editing produces a new version that names its predecessor, and runs
already sealed against the old one keep it.

**Validation failures keep their two kinds apart.** `JourneyError` means the draft is malformed and
answers 400. `CapabilityError` means the draft is well formed and asks for something the platform
does not do — an unsupported reader action, a key chord that reaches the operating system — and
answers 422 carrying the domain's own capability code. Collapsing them would tell an author to fix
their typing when the real answer is that AccessForge cannot do the thing they asked for.

**Every refusal names the field it is about.** Not because the domain exceptions carry one, but
because each section of the body is constructed in its own step, so the route knows which part it
was assembling when the exception arrived. A form cannot link an error to a control without it, and
UI-UX section 4 requires exactly that link.
"""

from __future__ import annotations

from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request, Response, status

from accessforge_api.dependencies import clamp_page_size
from accessforge_api.problems import ProblemCode, ProblemDetail, not_found
from accessforge_api.routes._common import as_body, authorize, workspace_scope
from accessforge_domain.authorization.roles import Permission
from accessforge_domain.journeys import (
    ActionBudget,
    Assertion,
    AssertionKind,
    AssertionSet,
    CapabilityError,
    FixtureBinding,
    JourneyDraft,
    JourneyError,
    TaskIntent,
    UnknownReason,
)
from accessforge_domain.journeys.dsl import (
    ALLOWED_ACTIONS,
    ALLOWED_KEY_CHORDS,
    MAX_ACTIONS,
    MAX_WALL_TIME_SECONDS,
)
from accessforge_persistence import journeys

router = APIRouter(prefix="/v1/workspaces/{workspace_id}", tags=["journeys"])

Conn = Annotated[psycopg.Connection[Any], Depends(workspace_scope)]

_JOURNEY_FIELDS = frozenset(
    {
        "projectId",
        "name",
        "platform",
        "intent",
        "assertions",
        "fixture",
        "budget",
        "allowedActions",
        "allowedKeyChords",
        "allowedEffects",
        "supersedes",
    }
)


def _text(source: dict[str, Any], key: str, field: str, request_id: str) -> str:
    """Read a string, refusing anything that is not one.

    `str(value)` is what this replaced, and it silently changed the request: `null` became the
    four-character string `"None"`, a number became its decimal form, and an object became its
    Python repr. A journey version is immutable once frozen, so a coerced value is a coerced value
    forever — and its digest is the digest of something the author never submitted.
    """
    value = source.get(key)
    if not isinstance(value, str):
        raise _field_error(field, f"{key} must be text", request_id)
    return value


def _flag(source: dict[str, Any], key: str, field: str, request_id: str, *, default: bool) -> bool:
    """Read a boolean, refusing anything that is not one.

    `bool(value)` accepted the string `"false"` as true, which is the single most common way a
    "required" flag ends up meaning its opposite.
    """
    if key not in source:
        return default
    value = source[key]
    if not isinstance(value, bool):
        raise _field_error(field, f"{key} must be true or false", request_id)
    return value


def _whole_number(source: dict[str, Any], key: str, field: str, request_id: str) -> int:
    """Read an integer, refusing a float, a numeric string, or a boolean.

    `int(value)` truncated 1.9 to 1 — a budget quietly smaller than the one that was authorized —
    and accepted `True` as 1. `bool` is excluded explicitly because it is a subclass of `int`.
    """
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _field_error(field, f"{key} must be a whole number", request_id)
    return value


def _field_error(field: str, message: str, request_id: str) -> ProblemDetail:
    """A 400 that names the control it is about, so a form can link to it."""
    return ProblemDetail(
        ProblemCode.INVALID_INPUT, message, extra={"field": field}, request_id=request_id
    )


def _mapping(body: dict[str, Any], key: str, request_id: str) -> dict[str, Any]:
    value = body.get(key)
    if not isinstance(value, dict):
        raise _field_error(key, f"{key} must be an object", request_id)
    return value


def _string_map(source: dict[str, Any], key: str, field: str, request_id: str) -> dict[str, str]:
    value = source.get(key, {})
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise _field_error(field, f"{key} must be an object of strings", request_id)
    return dict(value)


def _string_set(body: dict[str, Any], key: str, request_id: str) -> frozenset[str]:
    value = body.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _field_error(key, f"{key} must be a list of strings", request_id)
    return frozenset(value)


def _build_draft(body: dict[str, Any], request_id: str) -> JourneyDraft:
    """Turn a request body into a draft, or raise a problem that names the offending field.

    Each section is built separately so the field is known when the domain refuses it. A single
    try/except around the whole construction would produce an accurate message attached to nothing.
    """
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise _field_error("name", "a journey needs a name", request_id)

    platform = body.get("platform")
    if not isinstance(platform, str) or not platform:
        raise _field_error("platform", "a journey needs a platform", request_id)

    intent_body = _mapping(body, "intent", request_id)
    try:
        intent = TaskIntent(
            summary=_text(intent_body, "summary", "intent", request_id),
            start_url=_text(intent_body, "startUrl", "intent", request_id),
            success_condition=_text(intent_body, "successCondition", "intent", request_id),
        )
    except JourneyError as exc:
        # The message says which of the three it is; the field is the section, because that is the
        # granularity the form groups them at.
        raise _field_error("intent", str(exc), request_id) from exc

    assertions_body = body.get("assertions")
    if not isinstance(assertions_body, list) or not assertions_body:
        raise _field_error("assertions", "a journey needs at least one assertion", request_id)
    built: list[Assertion] = []
    for index, entry in enumerate(assertions_body):
        if not isinstance(entry, dict):
            raise _field_error(
                f"assertions[{index}]", "each assertion must be an object", request_id
            )
        try:
            field = f"assertions[{index}]"
            reasons = entry.get("unknownReasons", [])
            if not isinstance(reasons, list) or not all(isinstance(x, str) for x in reasons):
                raise _field_error(field, "unknownReasons must be a list of strings", request_id)
            built.append(
                Assertion(
                    assertion_id=_text(entry, "assertionId", field, request_id),
                    kind=AssertionKind(_text(entry, "kind", field, request_id)),
                    description=_text(entry, "description", field, request_id),
                    required=_flag(entry, "required", field, request_id, default=True),
                    unknown_reasons=frozenset(UnknownReason(reason) for reason in reasons),
                )
            )
        except ValueError as exc:
            raise _field_error(f"assertions[{index}]", str(exc), request_id) from exc
    try:
        assertion_set = AssertionSet(tuple(built))
    except ValueError as exc:
        raise _field_error("assertions", str(exc), request_id) from exc

    fixture_body = _mapping(body, "fixture", request_id)
    try:
        fixture = FixtureBinding(
            template_id=_text(fixture_body, "templateId", "fixture", request_id),
            navigator_values=_string_map(fixture_body, "navigatorValues", "fixture", request_id),
            reset_values=_string_map(fixture_body, "resetValues", "fixture", request_id),
            observer_config=_string_map(fixture_body, "observerConfig", "fixture", request_id),
        )
    except JourneyError as exc:
        raise _field_error("fixture", str(exc), request_id) from exc

    budget_body = _mapping(body, "budget", request_id)
    try:
        budget = ActionBudget(
            max_actions=_whole_number(budget_body, "maxActions", "budget", request_id),
            wall_time_seconds=_whole_number(budget_body, "wallTimeSeconds", "budget", request_id),
        )
    except JourneyError as exc:
        raise _field_error("budget", str(exc), request_id) from exc

    return JourneyDraft(
        name=name,
        intent=intent,
        assertions=assertion_set,
        fixture=fixture,
        budget=budget,
        platform=platform,
        allowed_actions=_string_set(body, "allowedActions", request_id),
        allowed_key_chords=_string_set(body, "allowedKeyChords", request_id),
        allowed_effects=_string_set(body, "allowedEffects", request_id),
    )


@router.post("/journeys", status_code=status.HTTP_201_CREATED)
def freeze_journey_version(
    workspace_id: str, request: Request, conn: Conn, payload: dict[str, Any], response: Response
) -> dict[str, Any]:
    """Compile and seal a new immutable journey version.

    201, not 202: unlike a run, this finishes inside the request. The response carries the digests
    that were actually sealed, so a caller can show a reviewer what freezing committed to rather
    than a re-derivation of it.
    """
    body = as_body(payload)
    context = authorize(
        conn, request, workspace_id, Permission.PROJECT_CONFIGURE, body, _JOURNEY_FIELDS
    )

    project_id = body.get("projectId")
    if not isinstance(project_id, str) or not project_id:
        raise _field_error("projectId", "a journey belongs to a project", context.request_id)

    supersedes = body.get("supersedes")
    if supersedes is not None and not isinstance(supersedes, str):
        raise _field_error("supersedes", "supersedes must be a version id", context.request_id)

    draft = _build_draft(body, context.request_id)

    try:
        compiled = journeys.freeze_version(
            conn,
            workspace_id=workspace_id,
            project_id=project_id,
            draft=draft,
            supersedes=supersedes,
        )
    except CapabilityError as exc:
        # 422. The request is well formed and the platform cannot do what it asks, which sends the
        # author somewhere entirely different from a malformed body.
        raise ProblemDetail(
            ProblemCode.UNSUPPORTED_CAPABILITY,
            str(exc),
            extra={"capabilityCode": exc.code},
            request_id=context.request_id,
        ) from exc
    except JourneyError as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT, str(exc), request_id=context.request_id
        ) from exc
    except journeys.JourneyPersistenceError as exc:
        # The field comes from the exception. Hard-coding `projectId` here sent an operator to the
        # wrong control for every supersession failure, which is the one case where the message and
        # the highlighted field disagreed about what was wrong.
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            str(exc),
            extra={} if exc.field is None else {"field": exc.field},
            request_id=context.request_id,
        ) from exc

    version = compiled.version
    response.headers["Location"] = f"/v1/workspaces/{workspace_id}/journeys/{version.version_id}"
    return {
        "journeyVersionId": version.version_id,
        "journeyDigest": version.journey_digest,
        "assertionSetDigest": version.assertion_set_digest,
        "fixtureDigest": version.fixture_digest,
        "navigatorPolicyDigest": version.navigator_policy_digest,
        "reviewerSummary": compiled.reviewer_summary,
        "supersedes": supersedes,
        "meaning": (
            "This version is frozen. Editing it creates a successor with a new digest; runs "
            "already sealed against this version keep it, and freezing authorizes nothing to run."
        ),
    }


@router.get("/projects/{project_id}/journeys")
def list_journey_versions(
    workspace_id: str,
    project_id: str,
    request: Request,
    conn: Conn,
    after: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    size = clamp_page_size(limit)
    try:
        rows = journeys.list_versions(conn, project_id=project_id, after=after, limit=size + 1)
        successors = journeys.superseded_by(conn, project_id=project_id)
    except journeys.JourneyPersistenceError as exc:
        raise ProblemDetail(
            ProblemCode.INVALID_INPUT,
            str(exc),
            extra={} if exc.field is None else {"field": exc.field},
        ) from exc

    items = [
        {
            "journeyVersionId": str(r["id"]),
            "name": str(r["name"]),
            "platform": str(r["platform"]),
            "journeyDigest": str(r["journey_digest"]),
            "assertionSetDigest": str(r["assertion_set_digest"]),
            "fixtureDigest": str(r["fixture_digest"]),
            "navigatorPolicyDigest": str(r["navigator_policy_digest"]),
            "reviewerSummary": r["reviewer_summary"],
            "supersedes": None if r["supersedes"] is None else str(r["supersedes"]),
            # Whether a *later* version replaced this one. Read across the whole project rather than
            # within the page, because a successor on a later page would otherwise make a superseded
            # version look current — and current is what an operator runs.
            "supersededBy": successors.get(str(r["id"])),
            "createdAt": str(r["created_at"]),
        }
        for r in rows[:size]
    ]
    return {
        "items": items,
        "nextCursor": items[-1]["journeyVersionId"] if len(rows) > size else None,
    }


@router.get("/journey-capabilities")
def journey_capabilities(workspace_id: str, request: Request, conn: Conn) -> dict[str, Any]:
    """What a journey may ask for, from the domain's own allowlists.

    Served rather than duplicated in the client, because a client-side copy is a second definition
    of the policy and it will disagree the day the allowlist changes. An author offered a control
    the server refuses is being invited to fail.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    return {
        "allowedActions": sorted(ALLOWED_ACTIONS),
        "allowedKeyChordsByPlatform": {
            platform: sorted(chords) for platform, chords in sorted(ALLOWED_KEY_CHORDS.items())
        },
        "allowedEffects": ["FIXTURE_SUBMIT", "FIXTURE_RESET"],
        "assertionKinds": [kind.value for kind in AssertionKind],
        "unknownReasons": [reason.value for reason in UnknownReason],
        "maxActions": MAX_ACTIONS,
        "maxWallTimeSeconds": MAX_WALL_TIME_SECONDS,
        "effectsMeaning": (
            "Effects are confined to owned test fixtures. No journey may authorize an external "
            "submission, an email or a payment."
        ),
    }


@router.get("/journeys/{journey_version_id}/policy")
def journey_navigator_policy(
    workspace_id: str, journey_version_id: str, request: Request, conn: Conn
) -> dict[str, Any]:
    """The navigator policy for a frozen version, exactly as sealed.

    Served so a reviewer can read what the navigator will be given. It contains no oracle material
    by construction — the compiler builds it that way — and showing it is how that claim becomes
    checkable by a person rather than asserted in a document.
    """
    authorize(conn, request, workspace_id, Permission.EVIDENCE_READ)
    row = conn.execute(
        "SELECT navigator_policy, navigator_policy_digest FROM journey_version WHERE id = %s",
        (journey_version_id,),
    ).fetchone()
    if row is None:
        raise not_found()
    return {
        "navigatorPolicy": row["navigator_policy"],
        "navigatorPolicyDigest": str(row["navigator_policy_digest"]),
    }
