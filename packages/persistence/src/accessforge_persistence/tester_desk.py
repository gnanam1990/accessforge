"""Tester Desk persistence: commissions, immutable briefs, clients, enrollment, evidence.

Every function takes an already workspace-scoped connection (see ``workspace_connection``): the
tenant predicate is enforced by row-level security, and the functions carry it anyway so a direct
call outside the HTTP boundary still answers the same way.

The brief is immutable by schema trigger; this module adds the surrounding discipline — the digest
is computed from canonical terms at write time and re-verified at read time, so a row that drifted
reads as an integrity failure rather than as quietly wrong terms.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from accessforge_domain import tester_desk as domain
from accessforge_domain.timestamps import parse_rfc3339_utc, to_rfc3339_utc


class CommissionError(ValueError):
    """A commission operation was refused: state, integrity or contract."""


class EnrollmentError(ValueError):
    """A tester-enrollment or claim operation was refused."""


# One error class shared with the domain so routes catch refusals raised on
# either side of the boundary.
EvidenceError = domain.EvidenceError


class ReviewError(ValueError):
    """A review decision was refused."""


class CompensationError(ValueError):
    """A payable or reconciliation operation was refused."""


class ReportError(ValueError):
    """A client report operation was refused."""


def _require_uuid(value: str, what: str) -> str:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise CommissionError(f"{what} is not a valid identifier") from exc
    return value


# --- clients and viewers -----------------------------------------------------------------------


def create_client(
    conn: psycopg.Connection[Any], *, workspace_id: str, name: str
) -> dict[str, Any]:
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 200):
        raise CommissionError("client name must be 1-200 characters")
    client_id = str(uuid.uuid4())
    row = conn.execute(
        "INSERT INTO td_client (id, workspace_id, name) VALUES (%s,%s,%s) "
        "ON CONFLICT (workspace_id, name) DO NOTHING "
        "RETURNING id, name, created_at",
        (client_id, workspace_id, name.strip()),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT id, name, created_at FROM td_client "
            "WHERE workspace_id=%s AND name=%s",
            (workspace_id, name.strip()),
        ).fetchone()
    return _client_view(row)


def _client_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "clientId": str(row["id"]),
        "name": row["name"],
        "createdAt": to_rfc3339_utc(row["created_at"]),
    }


def list_clients(conn: psycopg.Connection[Any], *, workspace_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, name, created_at FROM td_client WHERE workspace_id=%s ORDER BY name",
        (workspace_id,),
    ).fetchall()
    return [_client_view(r) for r in rows]


def grant_client_viewer(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    client_id: str,
    user_id: str,
    granted_by: str,
) -> dict[str, Any]:
    _require_uuid(client_id, "client")
    _require_uuid(user_id, "user")
    if conn.execute(
        "SELECT id FROM td_client WHERE id=%s AND workspace_id=%s FOR SHARE",
        (client_id, workspace_id),
    ).fetchone() is None:
        raise CommissionError("client unavailable")
    if conn.execute(
        "SELECT id FROM app_user WHERE id=%s AND disabled_at IS NULL", (user_id,)
    ).fetchone() is None:
        raise CommissionError("user account unavailable")
    conn.execute(
        "INSERT INTO td_client_viewer (workspace_id, client_id, user_id, granted_by) "
        "VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (workspace_id, client_id, user_id) DO UPDATE SET revoked_at=NULL",
        (workspace_id, client_id, user_id, granted_by),
    )
    return {"clientId": client_id, "userId": user_id, "granted": True}


def revoke_client_viewer(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    client_id: str,
    user_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        "UPDATE td_client_viewer SET revoked_at=clock_timestamp() "
        "WHERE workspace_id=%s AND client_id=%s AND user_id=%s AND revoked_at IS NULL "
        "RETURNING user_id",
        (workspace_id, client_id, user_id),
    ).fetchone()
    if row is None:
        raise CommissionError("no active viewer grant")
    return {"clientId": client_id, "userId": user_id, "granted": False}


def require_client_viewer(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    client_id: str,
    user_id: str,
) -> None:
    if conn.execute(
        "SELECT 1 FROM td_client_viewer WHERE workspace_id=%s AND client_id=%s AND user_id=%s "
        "AND revoked_at IS NULL",
        (workspace_id, client_id, user_id),
    ).fetchone() is None:
        raise ReportError("no client report access for this identity")


def client_grant_for_user(
    conn: psycopg.Connection[Any], *, workspace_id: str, user_id: str
) -> list[str]:
    rows = conn.execute(
        "SELECT client_id FROM td_client_viewer WHERE workspace_id=%s AND user_id=%s "
        "AND revoked_at IS NULL",
        (workspace_id, user_id),
    ).fetchall()
    return [str(r["client_id"]) for r in rows]


# --- consent terms ------------------------------------------------------------------------------


def publish_consent_terms(
    conn: psycopg.Connection[Any], *, workspace_id: str, body: str, actor_id: str
) -> dict[str, Any]:
    if not isinstance(body, str) or not (1 <= len(body.strip()) <= 20000):
        raise CommissionError("consent terms body must be 1-20000 characters")
    row = conn.execute(
        "SELECT coalesce(max(version),0)+1 AS v FROM td_consent_terms WHERE workspace_id=%s",
        (workspace_id,),
    ).fetchone()
    version = int(row["v"])
    conn.execute(
        "INSERT INTO td_consent_terms (workspace_id, version, body, created_by) "
        "VALUES (%s,%s,%s,%s)",
        (workspace_id, version, body.strip(), actor_id),
    )
    return {"version": version, "body": body.strip()}


def consent_terms(
    conn: psycopg.Connection[Any], *, workspace_id: str, version: int | None = None
) -> dict[str, Any] | None:
    if version is None:
        row = conn.execute(
            "SELECT version, body, created_at FROM td_consent_terms WHERE workspace_id=%s "
            "ORDER BY version DESC LIMIT 1",
            (workspace_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT version, body, created_at FROM td_consent_terms "
            "WHERE workspace_id=%s AND version=%s",
            (workspace_id, version),
        ).fetchone()
    if row is None:
        return None
    return {
        "version": int(row["version"]),
        "body": row["body"],
        "createdAt": to_rfc3339_utc(row["created_at"]),
    }


# --- commissions and briefs ---------------------------------------------------------------------


def _brief_document_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "consentTermsVersion": int(row["consent_terms_version"]),
        "currency": row["currency"],
        "deadline": None if row["deadline"] is None else to_rfc3339_utc(row["deadline"]),
        "environment": row["environment"],
        "feeMinor": int(row["fee_minor"]),
        "permittedEvidence": row["permitted_evidence"],
        "releaseDigest": row["release_digest"],
        "releaseLabel": row["release_label"],
        "rubric": row["rubric"],
        "targetUrl": row["target_url"],
        "taskSteps": row["task_steps"],
    }


def _brief_view(row: dict[str, Any], *, include_access_note: bool) -> dict[str, Any]:
    document = _brief_document_from_row(row)
    if domain.compute_brief_hash(document) != row["brief_hash"]:
        raise CommissionError("brief revision integrity check failed")
    view = {
        "revision": int(row["revision"]),
        "releaseLabel": row["release_label"],
        "releaseDigest": row["release_digest"],
        "targetUrl": row["target_url"],
        "taskSteps": row["task_steps"],
        "environment": row["environment"],
        "rubric": row["rubric"],
        "permittedEvidence": row["permitted_evidence"],
        "feeMinor": int(row["fee_minor"]),
        "currency": row["currency"],
        "consentTermsVersion": int(row["consent_terms_version"]),
        "deadline": document["deadline"],
        "briefHash": row["brief_hash"],
        "authoredBy": str(row["authored_by"]),
        "createdAt": to_rfc3339_utc(row["created_at"]),
    }
    if include_access_note:
        view["accessNote"] = row["access_note"]
    return view


def create_commission(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    actor_id: str,
    client_id: str,
    reference: str,
    brief: dict[str, Any],
    defect_key: str | None = None,
    predecessor_commission_id: str | None = None,
    offer_expires_at: str | None = None,
) -> dict[str, Any]:
    """Create a draft commission carrying brief revision 1.

    Nothing is claimable yet: DRAFT means the agency can still author. Publishing is a separate,
    explicit step that freezes the revision.
    """
    _require_uuid(client_id, "client")
    domain.validate_reference(reference)
    access_note = brief.get("accessNote")
    document = domain.brief_document(
        **{k: v for k, v in brief.items() if k != "accessNote"}
    )
    if conn.execute(
        "SELECT id FROM td_client WHERE id=%s AND workspace_id=%s", (client_id, workspace_id)
    ).fetchone() is None:
        raise CommissionError("client unavailable")
    if conn.execute(
        "SELECT 1 FROM td_consent_terms WHERE workspace_id=%s AND version=%s",
        (workspace_id, document["consentTermsVersion"]),
    ).fetchone() is None:
        raise CommissionError(
            f"consent terms version {document['consentTermsVersion']} is not published"
        )
    if predecessor_commission_id is not None:
        _require_uuid(predecessor_commission_id, "predecessor commission")
        predecessor = conn.execute(
            "SELECT id, status FROM td_commission WHERE id=%s AND workspace_id=%s",
            (predecessor_commission_id, workspace_id),
        ).fetchone()
        if predecessor is None:
            raise CommissionError("predecessor commission unavailable")
    expires_sql = None
    if offer_expires_at is not None:
        expires_sql = parse_rfc3339_utc(offer_expires_at, field="offerExpiresAt")
    commission_id = str(uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO td_commission (id, workspace_id, client_id, reference, defect_key, "
            "predecessor_commission_id, offer_expires_at, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                commission_id,
                workspace_id,
                client_id,
                reference,
                defect_key,
                predecessor_commission_id,
                expires_sql,
                actor_id,
            ),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise CommissionError(
            f"reference {reference!r} is already used in this workspace"
        ) from exc
    _insert_brief_revision(
        conn,
        workspace_id=workspace_id,
        commission_id=commission_id,
        revision=1,
        document=document,
        access_note=access_note,
        actor_id=actor_id,
    )
    return read_commission(conn, workspace_id=workspace_id, commission_id=commission_id)


def _insert_brief_revision(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    commission_id: str,
    revision: int,
    document: dict[str, Any],
    access_note: str | None,
    actor_id: str,
) -> None:
    conn.execute(
        "INSERT INTO td_brief_revision (commission_id, workspace_id, revision, release_label, "
        "release_digest, target_url, access_note, task_steps, environment, rubric, "
        "permitted_evidence, fee_minor, currency, consent_terms_version, deadline, brief_hash, "
        "authored_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            commission_id,
            workspace_id,
            revision,
            document["releaseLabel"],
            document["releaseDigest"],
            document["targetUrl"],
            access_note,
            Jsonb(document["taskSteps"]),
            Jsonb(document["environment"]),
            Jsonb(document["rubric"]),
            Jsonb(document["permittedEvidence"]),
            document["feeMinor"],
            document["currency"],
            document["consentTermsVersion"],
            (
                None
                if document["deadline"] is None
                else parse_rfc3339_utc(document["deadline"], field="deadline")
            ),
            domain.compute_brief_hash(document),
            actor_id,
        ),
    )


def author_brief_revision(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    commission_id: str,
    actor_id: str,
    brief: dict[str, Any],
) -> dict[str, Any]:
    """Author the next brief revision while the commission is still draft.

    Once a commission is open the terms are frozen: a substantive change is a retest commission
    linked by defect, not an edit. That is what keeps 'change the fee after acceptance' a
    different commission's business rather than a rewrite of a signed brief (INV-01).
    """
    _require_uuid(commission_id, "commission")
    commission = conn.execute(
        "SELECT status FROM td_commission WHERE id=%s AND workspace_id=%s FOR UPDATE",
        (commission_id, workspace_id),
    ).fetchone()
    if commission is None:
        raise LookupError("commission unavailable")
    if commission["status"] != "DRAFT":
        raise CommissionError(
            "brief terms can only be re-authored while the commission is a draft; "
            "open a linked retest commission to change released terms"
        )
    access_note = brief.get("accessNote")
    document = domain.brief_document(
        **{k: v for k, v in brief.items() if k != "accessNote"}
    )
    if conn.execute(
        "SELECT 1 FROM td_consent_terms WHERE workspace_id=%s AND version=%s",
        (workspace_id, document["consentTermsVersion"]),
    ).fetchone() is None:
        raise CommissionError(
            f"consent terms version {document['consentTermsVersion']} is not published"
        )
    row = conn.execute(
        "SELECT coalesce(max(revision),0)+1 AS v FROM td_brief_revision WHERE commission_id=%s",
        (commission_id,),
    ).fetchone()
    revision = int(row["v"])
    _insert_brief_revision(
        conn,
        workspace_id=workspace_id,
        commission_id=commission_id,
        revision=revision,
        document=document,
        access_note=access_note,
        actor_id=actor_id,
    )
    return read_brief_revision(
        conn,
        workspace_id=workspace_id,
        commission_id=commission_id,
        revision=revision,
        include_access_note=True,
    )


def publish_commission(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    commission_id: str,
) -> dict[str, Any]:
    """Freeze the latest brief revision and open the offer to eligible testers."""
    _require_uuid(commission_id, "commission")
    commission = conn.execute(
        "SELECT status FROM td_commission WHERE id=%s AND workspace_id=%s FOR UPDATE",
        (commission_id, workspace_id),
    ).fetchone()
    if commission is None:
        raise LookupError("commission unavailable")
    if commission["status"] not in {"DRAFT", "EXPIRED"}:
        raise CommissionError(
            f"commission is {commission['status']}; only a draft or an expired offer publishes"
        )
    latest = conn.execute(
        "SELECT revision FROM td_brief_revision WHERE commission_id=%s "
        "ORDER BY revision DESC LIMIT 1",
        (commission_id,),
    ).fetchone()
    if latest is None:  # unreachable: creation always writes revision 1
        raise CommissionError("commission has no brief to publish")
    conn.execute(
        "UPDATE td_commission SET status='OPEN', current_brief_revision=%s "
        "WHERE id=%s",
        (int(latest["revision"]), commission_id),
    )
    return read_commission(conn, workspace_id=workspace_id, commission_id=commission_id)


def read_brief_revision(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    commission_id: str,
    revision: int,
    include_access_note: bool = False,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM td_brief_revision WHERE commission_id=%s AND workspace_id=%s "
        "AND revision=%s",
        (commission_id, workspace_id, revision),
    ).fetchone()
    if row is None:
        raise LookupError("brief revision unavailable")
    return _brief_view(row, include_access_note=include_access_note)


def read_commission(
    conn: psycopg.Connection[Any], *, workspace_id: str, commission_id: str
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM td_commission WHERE id=%s AND workspace_id=%s",
        (commission_id, workspace_id),
    ).fetchone()
    if row is None:
        raise LookupError("commission unavailable")
    return _commission_view(conn, row, include_access_note=True)


def _commission_view(
    conn: psycopg.Connection[Any],
    row: dict[str, Any],
    *,
    include_access_note: bool,
) -> dict[str, Any]:
    view = {
        "commissionId": str(row["id"]),
        "workspaceId": str(row["workspace_id"]),
        "clientId": str(row["client_id"]),
        "reference": row["reference"],
        "defectKey": row["defect_key"],
        "predecessorCommissionId": (
            None if row["predecessor_commission_id"] is None
            else str(row["predecessor_commission_id"])
        ),
        "status": row["status"],
        "currentBriefRevision": row["current_brief_revision"],
        "offerExpiresAt": (
            None if row["offer_expires_at"] is None
            else to_rfc3339_utc(row["offer_expires_at"])
        ),
        "createdBy": str(row["created_by"]),
        "createdAt": to_rfc3339_utc(row["created_at"]),
    }
    if row["current_brief_revision"] is not None:
        brief = read_brief_revision(
            conn,
            workspace_id=str(row["workspace_id"]),
            commission_id=str(row["id"]),
            revision=int(row["current_brief_revision"]),
            include_access_note=include_access_note,
        )
        view["brief"] = brief
    return view


def list_commissions(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    status: str | None = None,
) -> list[dict[str, Any]]:
    if status is None:
        rows = conn.execute(
            "SELECT * FROM td_commission WHERE workspace_id=%s ORDER BY created_at DESC LIMIT 200",
            (workspace_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM td_commission WHERE workspace_id=%s AND status=%s "
            "ORDER BY created_at DESC LIMIT 200",
            (workspace_id, status),
        ).fetchall()
    return [
        _commission_view(conn, r, include_access_note=False) for r in rows
    ]


def expire_offers(conn: psycopg.Connection[Any], *, workspace_id: str) -> int:
    """Move OPEN commissions past their offer window to EXPIRED. Returns the count moved."""
    rows = conn.execute(
        "UPDATE td_commission SET status='EXPIRED' "
        "WHERE workspace_id=%s AND status='OPEN' AND offer_expires_at IS NOT NULL "
        "AND offer_expires_at <= clock_timestamp() RETURNING id",
        (workspace_id,),
    ).fetchall()
    return len(rows)


# --- tester enrollment, offers and claims ---------------------------------------------------------


def enroll_tester(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    actor_id: str,
    user_id: str,
    display_name: str,
    capabilities: dict[str, Any],
    payout_reference: str | None,
    conflicts: list[str] | None = None,
) -> dict[str, Any]:
    """Add a person to the tester roster. Restricted: only TESTER_DESK_ADMIN callers reach here.

    Capabilities are declarations the claim path checks exactly; they are not proof of equipment,
    and the roster row says so rather than implying vetting that did not happen.
    """
    _require_uuid(user_id, "user")
    if not isinstance(display_name, str) or not (1 <= len(display_name.strip()) <= 200):
        raise EnrollmentError("tester display name must be 1-200 characters")
    caps = domain.validate_environment(
        {
            "browserName": (capabilities or {}).get("browserName"),
            "browserVersion": (capabilities or {}).get("browserVersion"),
            "assistiveTechnology": (capabilities or {}).get("assistiveTechnology"),
        }
    )
    if payout_reference is not None and (
        not isinstance(payout_reference, str) or not (1 <= len(payout_reference) <= 200)
    ):
        raise EnrollmentError("payout reference must be 1-200 characters when present")
    declared = conflicts or []
    if not isinstance(declared, list) or not all(isinstance(c, str) for c in declared):
        raise EnrollmentError("conflicts must be a list of strings")
    if conn.execute(
        "SELECT id FROM app_user WHERE id=%s AND disabled_at IS NULL", (user_id,)
    ).fetchone() is None:
        raise EnrollmentError("user account unavailable")
    tester_id = str(uuid.uuid4())
    row = conn.execute(
        "INSERT INTO td_tester (id, workspace_id, user_id, display_name, browser_name, "
        "browser_version, assistive_technology, payout_reference, conflicts, enrolled_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (workspace_id, user_id) DO NOTHING RETURNING *",
        (
            tester_id,
            workspace_id,
            user_id,
            display_name.strip(),
            caps["browserName"],
            caps["browserVersion"],
            caps["assistiveTechnology"],
            payout_reference,
            Jsonb(sorted(set(declared))),
            actor_id,
        ),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM td_tester WHERE workspace_id=%s AND user_id=%s",
            (workspace_id, user_id),
        ).fetchone()
    return _tester_view(row)


def _tester_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "testerId": str(row["id"]),
        "userId": str(row["user_id"]),
        "displayName": row["display_name"],
        "capabilities": {
            "browserName": row["browser_name"],
            "browserVersion": row["browser_version"],
            "assistiveTechnology": row["assistive_technology"],
        },
        "payoutReference": row["payout_reference"],
        "conflicts": row["conflicts"],
        "status": row["status"],
        "enrolledAt": to_rfc3339_utc(row["enrolled_at"]),
    }


def enrollment_for_user(
    conn: psycopg.Connection[Any], *, workspace_id: str, user_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM td_tester WHERE workspace_id=%s AND user_id=%s",
        (workspace_id, user_id),
    ).fetchone()
    return None if row is None else _tester_view(row)


def list_testers(conn: psycopg.Connection[Any], *, workspace_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM td_tester WHERE workspace_id=%s ORDER BY enrolled_at DESC LIMIT 200",
        (workspace_id,),
    ).fetchall()
    return [_tester_view(r) for r in rows]


def set_tester_status(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    tester_id: str,
    status: str,
    reason: str,
) -> dict[str, Any]:
    if status not in {"ACTIVE", "SUSPENDED", "WITHDRAWN"}:
        raise EnrollmentError("unknown tester status")
    row = conn.execute(
        "UPDATE td_tester SET status=%s, status_changed_at=clock_timestamp() "
        "WHERE id=%s AND workspace_id=%s RETURNING *",
        (status, tester_id, workspace_id),
    ).fetchone()
    if row is None:
        raise LookupError("tester enrollment unavailable")
    conn.execute(
        "INSERT INTO outbox_message (workspace_id, operation_id, topic, reference) "
        "VALUES (%s,%s,%s,%s)",
        (
            workspace_id,
            str(uuid.uuid4()),
            "tester-desk.tester.status",
            Jsonb({"testerId": tester_id, "status": status, "reason": reason}),
        ),
    )
    return _tester_view(row)


def offers_for_tester(
    conn: psycopg.Connection[Any], *, workspace_id: str, enrollment: dict[str, Any]
) -> list[dict[str, Any]]:
    """What this tester may see: open, unexpired, environment-matching, unconflicted offers.

    Scoped visibility is enforced here, not in the client: a commission the tester may not see
    never leaves the database.
    """
    expire_offers(conn, workspace_id=workspace_id)
    rows = conn.execute(
        "SELECT c.*, b.environment AS brief_environment FROM td_commission c "
        "JOIN td_brief_revision b ON b.commission_id=c.id AND b.revision=c.current_brief_revision "
        "WHERE c.workspace_id=%s AND c.status='OPEN' "
        "AND (c.offer_expires_at IS NULL OR c.offer_expires_at > clock_timestamp())",
        (workspace_id,),
    ).fetchall()
    conflicts = set(enrollment.get("conflicts") or [])
    caps = enrollment["capabilities"]
    offers: list[dict[str, Any]] = []
    for row in rows:
        env = row["brief_environment"]
        if row["reference"] in conflicts or str(row["id"]) in conflicts:
            continue
        if any(
            env.get(field) != caps.get(field)
            for field in ("browserName", "browserVersion", "assistiveTechnology")
        ):
            continue
        offers.append(_commission_view(conn, row, include_access_note=False))
    return offers


def claim_offer(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    commission_id: str,
    enrollment: dict[str, Any],
    consent_terms_version: Any,
    consent_accepted: Any,
) -> dict[str, Any]:
    """Atomically claim an open offer (INV-02).

    The commission row is locked before the checks run, so two racing claims serialize here; the
    partial unique index on live assignments is the backstop that makes 'one winner' a fact rather
    than a hope. The loser sees the same refusal a latecomer sees.
    """
    _require_uuid(commission_id, "commission")
    commission = conn.execute(
        "SELECT * FROM td_commission WHERE id=%s AND workspace_id=%s FOR UPDATE",
        (commission_id, workspace_id),
    ).fetchone()
    if commission is None:
        raise LookupError("commission unavailable")
    brief_row = conn.execute(
        "SELECT * FROM td_brief_revision WHERE commission_id=%s AND revision=%s",
        (commission_id, commission["current_brief_revision"]),
    ).fetchone()
    if brief_row is None:
        raise EnrollmentError("commission has no open brief")
    expired = (
        commission["offer_expires_at"] is not None
        and commission["offer_expires_at"] <= datetime.now(UTC)
    )
    conflicts = set(enrollment.get("conflicts") or [])
    try:
        domain.assert_eligible(
            tester_capabilities=enrollment["capabilities"],
            environment=brief_row["environment"],
            tester_status=enrollment["status"],
            payout_reference=enrollment["payoutReference"],
            declared_conflicts=conflicts,
            commission_id=str(commission["id"]),
            commission_reference=commission["reference"],
            consent_version_offered=(
                consent_terms_version if type(consent_terms_version) is int else -1
            ),
            consent_version_required=int(brief_row["consent_terms_version"]),
            consent_accepted=consent_accepted is True,
            offer_open=commission["status"] == "OPEN",
            now_deadline_passed=bool(expired),
        )
    except domain.EligibilityError as exc:
        raise EnrollmentError(str(exc)) from exc
    existing = conn.execute(
        "SELECT id FROM td_assignment WHERE commission_id=%s AND status IN "
        "('ACTIVE','SUBMITTED','UNDER_REVIEW','COMPLETED')",
        (commission_id,),
    ).fetchone()
    if existing is not None:
        raise EnrollmentError("this commission has already been claimed")
    assignment_id = str(uuid.uuid4())
    expires_at = None
    if brief_row["deadline"] is not None:
        expires_at = brief_row["deadline"]
    try:
        conn.execute(
            "INSERT INTO td_assignment (id, workspace_id, commission_id, brief_revision, "
            "tester_id, consent_terms_version, expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                assignment_id,
                workspace_id,
                commission_id,
                int(brief_row["revision"]),
                enrollment["testerId"],
                int(brief_row["consent_terms_version"]),
                expires_at,
            ),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise EnrollmentError("this commission has already been claimed") from exc
    conn.execute(
        "UPDATE td_commission SET status='ASSIGNED' WHERE id=%s AND status='OPEN'",
        (commission_id,),
    )
    return read_assignment(conn, workspace_id=workspace_id, assignment_id=assignment_id)


def read_assignment(
    conn: psycopg.Connection[Any], *, workspace_id: str, assignment_id: str
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM td_assignment WHERE id=%s AND workspace_id=%s",
        (assignment_id, workspace_id),
    ).fetchone()
    if row is None:
        raise LookupError("assignment unavailable")
    return _assignment_view(conn, row)


def _assignment_view(conn: psycopg.Connection[Any], row: dict[str, Any]) -> dict[str, Any]:
    brief = read_brief_revision(
        conn,
        workspace_id=str(row["workspace_id"]),
        commission_id=str(row["commission_id"]),
        revision=int(row["brief_revision"]),
        include_access_note=True,
    )
    return {
        "assignmentId": str(row["id"]),
        "commissionId": str(row["commission_id"]),
        "briefRevision": int(row["brief_revision"]),
        "briefHash": brief["briefHash"],
        "brief": brief,
        "testerId": str(row["tester_id"]),
        "consentTermsVersion": int(row["consent_terms_version"]),
        "consentAt": to_rfc3339_utc(row["consent_at"]),
        "status": row["status"],
        "claimedAt": to_rfc3339_utc(row["claimed_at"]),
        "expiresAt": None if row["expires_at"] is None else to_rfc3339_utc(row["expires_at"]),
    }


def assignments_for_tester(
    conn: psycopg.Connection[Any], *, workspace_id: str, tester_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM td_assignment WHERE workspace_id=%s AND tester_id=%s "
        "ORDER BY claimed_at DESC LIMIT 100",
        (workspace_id, tester_id),
    ).fetchall()
    return [_assignment_view(conn, r) for r in rows]


def assignment_for_tester(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    assignment_id: str,
    tester_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM td_assignment WHERE id=%s AND workspace_id=%s AND tester_id=%s",
        (assignment_id, workspace_id, tester_id),
    ).fetchone()
    if row is None:
        raise LookupError("assignment unavailable")
    return _assignment_view(conn, row)


def withdraw_assignment(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    assignment_id: str,
    tester_id: str,
) -> dict[str, Any]:
    """Consent withdrawal: the tester leaves, collection stops, the offer reopens if it can.

    Retained evidence is governed by the workspace retention policy; the withdrawal here only
    ends the tester's participation and releases the exclusive claim.
    """
    row = conn.execute(
        "SELECT * FROM td_assignment WHERE id=%s AND workspace_id=%s AND tester_id=%s FOR UPDATE",
        (assignment_id, workspace_id, tester_id),
    ).fetchone()
    if row is None:
        raise LookupError("assignment unavailable")
    if row["status"] == "ACTIVE":
        conn.execute(
            "UPDATE td_assignment SET status='WITHDRAWN' WHERE id=%s", (assignment_id,)
        )
        conn.execute(
            "UPDATE td_commission SET status='OPEN' WHERE id=%s AND status='ASSIGNED' "
            "AND (offer_expires_at IS NULL OR offer_expires_at > clock_timestamp())",
            (row["commission_id"],),
        )
    return read_assignment(conn, workspace_id=workspace_id, assignment_id=assignment_id)


# --- evidence capture: observation drafts and attachments ---------------------------------------


def _lock_assignment_for_capture(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    assignment_id: str,
    tester_id: str,
) -> dict[str, Any]:
    """Fetch the owning tester's live assignment row for evidence writes.

    Only an ``ACTIVE`` assignment accepts drafts or attachments; once a submission is sealed the
    state machine moves the row past ACTIVE and capture closes. A foreign tester or a stale id is
    a plain ``LookupError`` -- the row's existence is not disclosed (INV-03).
    """
    row = conn.execute(
        "SELECT * FROM td_assignment WHERE id=%s AND workspace_id=%s AND tester_id=%s FOR UPDATE",
        (assignment_id, workspace_id, tester_id),
    ).fetchone()
    if row is None:
        raise LookupError("assignment unavailable")
    if row["status"] != "ACTIVE":
        raise EvidenceError(
            f"assignment is {row['status'].lower().replace('_', ' ')}; evidence capture is closed"
        )
    return row


def _draft_view(row: dict[str, Any] | None, *, assignment_id: str) -> dict[str, Any]:
    if row is None:
        return {"assignmentId": assignment_id, "draftRevision": 0,
                "observations": [], "updatedAt": None}
    return {
        "assignmentId": str(row["assignment_id"]),
        "draftRevision": int(row["draft_revision"]),
        "observations": row["observations"],
        "updatedAt": to_rfc3339_utc(row["updated_at"]),
    }


def read_draft(
    conn: psycopg.Connection[Any], *, workspace_id: str, assignment_id: str
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM td_observation_draft WHERE assignment_id=%s AND workspace_id=%s",
        (assignment_id, workspace_id),
    ).fetchone()
    return _draft_view(row, assignment_id=assignment_id)


def save_draft(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    assignment_id: str,
    tester_id: str,
    observations: Any,
    base_revision: int | None = None,
) -> dict[str, Any]:
    """Autosave one capture draft. Partial drafts are first-class: the tester can reload later
    and recover exactly what was stored (T-005).

    ``base_revision`` is optimistic concurrency for a tester running two capture sessions: a stale
    writer gets a conflict instead of silently overwriting the newer autosave.
    """
    assignment = _lock_assignment_for_capture(
        conn, workspace_id=workspace_id, assignment_id=assignment_id, tester_id=tester_id
    )
    brief = read_brief_revision(
        conn,
        workspace_id=workspace_id,
        commission_id=str(assignment["commission_id"]),
        revision=int(assignment["brief_revision"]),
    )
    step_ids = {s["stepId"] for s in brief["taskSteps"]}
    cleaned = domain.validate_observations(observations, step_ids=step_ids)

    existing = conn.execute(
        "SELECT draft_revision FROM td_observation_draft "
        "WHERE assignment_id=%s AND workspace_id=%s",
        (assignment_id, workspace_id),
    ).fetchone()
    current = 0 if existing is None else int(existing["draft_revision"])
    if base_revision is not None and base_revision != current:
        raise EvidenceError(
            f"draft has moved to revision {current}; reload before writing over it"
        )
    conn.execute(
        "INSERT INTO td_observation_draft "
        "(assignment_id, workspace_id, tester_id, observations, draft_revision, updated_at) "
        "VALUES (%s,%s,%s,%s,1,clock_timestamp()) "
        "ON CONFLICT (assignment_id) DO UPDATE SET "
        "observations=EXCLUDED.observations, "
        "draft_revision=td_observation_draft.draft_revision + 1, "
        "updated_at=clock_timestamp()",
        (assignment_id, workspace_id, tester_id, Jsonb(cleaned)),
    )
    return read_draft(conn, workspace_id=workspace_id, assignment_id=assignment_id)


def add_attachment(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    assignment_id: str,
    tester_id: str,
    kind: Any,
    content_type: Any,
    filename: Any,
    content: bytes,
) -> dict[str, Any]:
    """Store one digest-backed attachment after the evidence gate passes.

    The sha256 is computed here from the persisted bytes -- an artifact's digest is a property of
    its content, not a claim the client made. A duplicate upload (same assignment, same digest)
    returns the existing row so a retried upload stays idempotent.
    """
    assignment = _lock_assignment_for_capture(
        conn, workspace_id=workspace_id, assignment_id=assignment_id, tester_id=tester_id
    )
    brief = read_brief_revision(
        conn,
        workspace_id=workspace_id,
        commission_id=str(assignment["commission_id"]),
        revision=int(assignment["brief_revision"]),
    )
    evidence_kind, normalized_type = domain.assert_attachment_permitted(
        kind=kind,
        content_type=content_type,
        permitted_evidence=brief["permittedEvidence"],
        byte_size=len(content),
    )
    if not isinstance(filename, str) or not filename.strip():
        raise EvidenceError("attachment filename is required")
    digest_hex = hashlib.sha256(content).hexdigest()
    attachment_id = uuid.uuid4()
    row = conn.execute(
        "INSERT INTO td_evidence_attachment "
        "(id, workspace_id, assignment_id, tester_id, kind, content_type, filename, sha256, "
        "byte_size, content) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (assignment_id, sha256) DO NOTHING "
        "RETURNING *",
        (
            attachment_id,
            workspace_id,
            assignment_id,
            tester_id,
            evidence_kind.value,
            normalized_type,
            filename.strip()[:200],
            digest_hex,
            len(content),
            content,
        ),
    ).fetchone()
    if row is None:  # identical bytes already attached to this assignment
        row = conn.execute(
            "SELECT * FROM td_evidence_attachment "
            "WHERE assignment_id=%s AND workspace_id=%s AND sha256=%s",
            (assignment_id, workspace_id, digest_hex),
        ).fetchone()
    return _attachment_view(row)


def _attachment_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "attachmentId": str(row["id"]),
        "assignmentId": str(row["assignment_id"]),
        "kind": row["kind"],
        "contentType": row["content_type"],
        "filename": row["filename"],
        "sha256": row["sha256"],
        "byteSize": int(row["byte_size"]),
        "createdAt": to_rfc3339_utc(row["created_at"]),
    }


def list_attachments(
    conn: psycopg.Connection[Any], *, workspace_id: str, assignment_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM td_evidence_attachment WHERE assignment_id=%s AND workspace_id=%s "
        "ORDER BY created_at, id",
        (assignment_id, workspace_id),
    ).fetchall()
    return [_attachment_view(r) for r in rows]


def read_attachment(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    assignment_id: str,
    attachment_id: str,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM td_evidence_attachment WHERE id=%s AND assignment_id=%s "
        "AND workspace_id=%s",
        (attachment_id, assignment_id, workspace_id),
    ).fetchone()
    if row is None:
        raise LookupError("attachment unavailable")
    return _attachment_view(row)


def attachment_content(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    assignment_id: str,
    attachment_id: str,
) -> dict[str, Any]:
    """Metadata plus the persisted bytes. Recomputes the digest at read time so a corrupted row
    answers as an integrity failure rather than quietly wrong evidence (NFR-01)."""
    row = conn.execute(
        "SELECT * FROM td_evidence_attachment WHERE id=%s AND assignment_id=%s "
        "AND workspace_id=%s",
        (attachment_id, assignment_id, workspace_id),
    ).fetchone()
    if row is None:
        raise LookupError("attachment unavailable")
    content = bytes(row["content"])
    actual = hashlib.sha256(content).hexdigest()
    if actual != row["sha256"]:
        raise EvidenceError(
            f"attachment {attachment_id} failed its digest check and cannot be served"
        )
    view = _attachment_view(row)
    view["content"] = content
    return view


def evidence_progress(
    conn: psycopg.Connection[Any], *, workspace_id: str, assignment_id: str
) -> dict[str, Any]:
    """Rubric completeness for one assignment: what still blocks a submittable deliverable."""
    row = conn.execute(
        "SELECT * FROM td_assignment WHERE id=%s AND workspace_id=%s",
        (assignment_id, workspace_id),
    ).fetchone()
    if row is None:
        raise LookupError("assignment unavailable")
    brief = read_brief_revision(
        conn,
        workspace_id=workspace_id,
        commission_id=str(row["commission_id"]),
        revision=int(row["brief_revision"]),
    )
    draft = read_draft(conn, workspace_id=workspace_id, assignment_id=assignment_id)
    kinds = {a["kind"] for a in list_attachments(conn, workspace_id=workspace_id,
                                               assignment_id=assignment_id)}
    observations = {o["stepId"]: o["result"] for o in draft["observations"]}
    missing = domain.rubric_completeness(
        step_ids=[s["stepId"] for s in brief["taskSteps"]],
        observations=observations,
        attachment_kinds=kinds,
        rubric=brief["rubric"],
        permitted_evidence=brief["permittedEvidence"],
    )
    return {
        "assignmentId": assignment_id,
        "taskResult": domain.derive_task_result(observations, rubric=brief["rubric"]).value,
        "missing": missing,
        "submittable": not missing,
        "draftRevision": draft["draftRevision"],
    }


# --- submission and review -------------------------------------------------------


def _submission_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "submissionId": str(row["id"]),
        "assignmentId": str(row["assignment_id"]),
        "revision": int(row["revision"]),
        "submissionHash": row["submission_hash"],
        "taskResult": row["task_result"],
        "observations": row["observations"],
        "attachmentManifest": row["attachment_manifest"],
        "sealedAt": to_rfc3339_utc(row["sealed_at"]),
    }


def submit_assignment(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    assignment_id: str,
    tester_id: str,
) -> dict[str, Any]:
    """Seal the current draft + attachment manifest into an immutable submission.

    The rubric must be satisfied at seal time: missing steps or required evidence kinds are a
    refusal listing the gaps, not a partial submission. The digest covers observations and the
    attachment manifest, so the reviewer's decision can bind to exactly this evidence set.
    """
    assignment = conn.execute(
        "SELECT * FROM td_assignment WHERE id=%s AND workspace_id=%s AND tester_id=%s FOR UPDATE",
        (assignment_id, workspace_id, tester_id),
    ).fetchone()
    if assignment is None:
        raise LookupError("assignment unavailable")
    if assignment["status"] != "ACTIVE":
        raise ReviewError(
            f"assignment is {assignment['status'].lower().replace('_', ' ')}; "
            "only an active assignment can be submitted"
        )
    brief = read_brief_revision(
        conn,
        workspace_id=workspace_id,
        commission_id=str(assignment["commission_id"]),
        revision=int(assignment["brief_revision"]),
    )
    draft = read_draft(conn, workspace_id=workspace_id, assignment_id=assignment_id)
    attachments = list_attachments(
        conn, workspace_id=workspace_id, assignment_id=assignment_id
    )
    observations = {o["stepId"]: o["result"] for o in draft["observations"]}
    missing = domain.rubric_completeness(
        step_ids=[s["stepId"] for s in brief["taskSteps"]],
        observations=observations,
        attachment_kinds={a["kind"] for a in attachments},
        rubric=brief["rubric"],
        permitted_evidence=brief["permittedEvidence"],
    )
    if missing:
        raise ReviewError("submission is incomplete: " + "; ".join(missing))

    manifest = [
        {
            "attachmentId": a["attachmentId"],
            "kind": a["kind"],
            "sha256": a["sha256"],
            "contentType": a["contentType"],
            "filename": a["filename"],
        }
        for a in attachments
    ]
    revision_row = conn.execute(
        "SELECT COALESCE(MAX(revision), 0) + 1 AS next FROM td_submission "
        "WHERE assignment_id=%s AND workspace_id=%s",
        (assignment_id, workspace_id),
    ).fetchone()
    revision = int(revision_row["next"])
    submission_hash = domain.compute_brief_hash(
        {
            "assignmentId": assignment_id,
            "briefHash": brief["briefHash"],
            "revision": revision,
            "observations": draft["observations"],
            "attachments": manifest,
        }
    )
    task_result = domain.derive_task_result(observations, rubric=brief["rubric"]).value
    submission_id = uuid.uuid4()
    row = conn.execute(
        "INSERT INTO td_submission "
        "(id, workspace_id, assignment_id, revision, submission_hash, task_result, "
        "observations, attachment_manifest) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (
            submission_id,
            workspace_id,
            assignment_id,
            revision,
            submission_hash,
            task_result,
            Jsonb(draft["observations"]),
            Jsonb(manifest),
        ),
    ).fetchone()
    conn.execute(
        "UPDATE td_assignment SET status='UNDER_REVIEW' WHERE id=%s", (assignment_id,)
    )
    conn.execute(
        "UPDATE td_commission SET status='IN_REVIEW' WHERE id=%s "
        "AND status IN ('ASSIGNED','NEEDS_REVISION')",
        (assignment["commission_id"],),
    )
    return _submission_view(row)


def list_submissions(
    conn: psycopg.Connection[Any], *, workspace_id: str, assignment_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM td_submission WHERE assignment_id=%s AND workspace_id=%s "
        "ORDER BY revision",
        (assignment_id, workspace_id),
    ).fetchall()
    views = [_submission_view(r) for r in rows]
    for view in views:
        decision = conn.execute(
            "SELECT * FROM td_review_decision WHERE submission_id=%s AND workspace_id=%s",
            (view["submissionId"], workspace_id),
        ).fetchone()
        view["decision"] = _decision_view(decision) if decision else None
    return views


def latest_submission(
    conn: psycopg.Connection[Any], *, workspace_id: str, assignment_id: str
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM td_submission WHERE assignment_id=%s AND workspace_id=%s "
        "ORDER BY revision DESC LIMIT 1",
        (assignment_id, workspace_id),
    ).fetchone()
    return None if row is None else _submission_view(row)


def _decision_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "decisionId": str(row["id"]),
        "submissionId": str(row["submission_id"]),
        "submissionHash": row["submission_hash"],
        "decision": row["decision"],
        "justification": row["justification"],
        "decidedBy": str(row["decided_by"]),
        "decidedAt": to_rfc3339_utc(row["decided_at"]),
    }


def review_queue(
    conn: psycopg.Connection[Any], *, workspace_id: str
) -> list[dict[str, Any]]:
    """Assignments awaiting a reviewer decision, newest first."""
    rows = conn.execute(
        "SELECT a.*, c.reference FROM td_assignment a "
        "JOIN td_commission c ON c.id = a.commission_id AND c.workspace_id = a.workspace_id "
        "WHERE a.workspace_id=%s AND a.status='UNDER_REVIEW' ORDER BY a.claimed_at DESC",
        (workspace_id,),
    ).fetchall()
    return [
        {
            "assignmentId": str(r["id"]),
            "commissionId": str(r["commission_id"]),
            "reference": r["reference"],
            "testerId": str(r["tester_id"]),
            "briefRevision": int(r["brief_revision"]),
            "latestSubmission": (
                latest_submission(
                    conn, workspace_id=workspace_id, assignment_id=str(r["id"])
                )
            ),
        }
        for r in rows
    ]


def decide_submission(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    assignment_id: str,
    submission_hash: Any,
    decision: Any,
    justification: Any,
    decided_by: str,
) -> dict[str, Any]:
    """Record a reviewer's decision bound to a submission digest.

    The digest must match the assignment's *latest* sealed submission -- approving an obsolete
    revision is a conflict (T-008), because the evidence the reviewer looked at is no longer the
    evidence of record. A second decision on an already-decided submission is likewise a
    conflict; the tester revises by sealing a new revision.
    """
    assignment = conn.execute(
        "SELECT * FROM td_assignment WHERE id=%s AND workspace_id=%s FOR UPDATE",
        (assignment_id, workspace_id),
    ).fetchone()
    if assignment is None:
        raise LookupError("assignment unavailable")
    if assignment["status"] != "UNDER_REVIEW":
        raise ReviewError(
            f"assignment is {assignment['status'].lower().replace('_', ' ')}; "
            "nothing awaits review"
        )
    latest = latest_submission(
        conn, workspace_id=workspace_id, assignment_id=assignment_id
    )
    if latest is None:
        raise ReviewError("assignment has no sealed submission")
    if submission_hash != latest["submissionHash"]:
        raise ReviewError(
            "submission digest is obsolete: the tester has sealed a newer revision "
            f"({latest['submissionHash'][:12]}…); review that instead"
        )
    if decision == "ACCEPTED":
        decision_value = "ACCEPTED"
    elif decision == "NEEDS_REVISION":
        decision_value = "NEEDS_REVISION"
    else:
        raise ReviewError("decision must be ACCEPTED or NEEDS_REVISION")
    if not isinstance(justification, str) or not justification.strip():
        raise ReviewError("a justification is required for every review decision")

    try:
        row = conn.execute(
            "INSERT INTO td_review_decision "
            "(id, workspace_id, submission_id, submission_hash, assignment_id, decision, "
            "justification, decided_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                uuid.uuid4(),
                workspace_id,
                latest["submissionId"],
                latest["submissionHash"],
                assignment_id,
                decision_value,
                justification.strip(),
                decided_by,
            ),
        ).fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise ReviewError("this submission already carries a decision") from exc

    if decision_value == "ACCEPTED":
        conn.execute(
            "UPDATE td_assignment SET status='COMPLETED' WHERE id=%s", (assignment_id,)
        )
        conn.execute(
            "UPDATE td_commission SET status='ACCEPTED' WHERE id=%s",
            (assignment["commission_id"],),
        )
    else:
        conn.execute(
            "UPDATE td_assignment SET status='ACTIVE' WHERE id=%s", (assignment_id,)
        )
        conn.execute(
            "UPDATE td_commission SET status='NEEDS_REVISION' WHERE id=%s",
            (assignment["commission_id"],),
        )
    return _decision_view(row)
