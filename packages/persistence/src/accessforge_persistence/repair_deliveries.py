"""Immutable delivery receipt; no model text, source copy, approval or verification claim."""

from typing import Any

import psycopg

from accessforge_domain.timestamps import to_rfc3339_utc

from .patches import PatchProposal


class DeliveryRefused(ValueError):
    pass


def by_request(conn: psycopg.Connection[Any], *, request_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT d.*,i.status AS invocation_status FROM repair_delivery d "
        "LEFT JOIN diagnosis_invocation i ON i.operation_id=d.request_id "
        "AND i.workspace_id=d.workspace_id "
        "AND i.purpose='REPAIR' AND i.request_digest=d.request_digest WHERE d.request_id=%s",
        (request_id,),
    ).fetchone()
    if row is None:
        return None
    if row["invocation_status"] not in {"RECORDED", "NOT_CALLED"} or (
        row["outcome"] == "PROPOSED" and row["invocation_status"] != "RECORDED"
    ):
        raise DeliveryRefused("repair result and invocation disposition do not agree")
    return {
        "requestId": str(row["request_id"]),
        "requestDigest": row["request_digest"],
        "inputDigest": row["input_digest"],
        "bindingDigest": row["binding_digest"],
        "outcome": row["outcome"],
        "patchId": None if row["patch_id"] is None else str(row["patch_id"]),
        "patchDigest": row["patch_digest"],
        "recordedAt": to_rfc3339_utc(row["recorded_at"]),
        "meaning": "MODEL_DELIVERY_NOT_PATCH_APPROVAL_APPLICATION_OR_VERIFICATION",
    }


def record(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    request_id: str,
    request_digest: str,
    input_digest: str,
    binding_digest: str,
    patch: PatchProposal | None,
) -> None:
    """Same transaction as proposal creation and invocation settlement; no replacement/replay."""
    conn.execute(
        "INSERT INTO repair_delivery(request_id,workspace_id,request_digest,input_digest,"
        "binding_digest,outcome,patch_id,patch_digest) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            request_id,
            workspace_id,
            request_digest,
            input_digest,
            binding_digest,
            "NO_PROPOSAL" if patch is None else "PROPOSED",
            None if patch is None else patch.patch_id,
            None if patch is None else patch.patch_digest,
        ),
    )
