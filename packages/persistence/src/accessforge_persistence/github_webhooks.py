"""Transactional replay receipts after trusted authentication; never work authorization.

The integration service must resolve the workspace and App from trusted receiver configuration,
authenticate raw bytes and verify current installation/repository access before processing events.
No caller-provided workspace header is an authority. There is no public ingress route yet.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import psycopg


class Refused(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Receipt:
    body_id: str
    new_body: bool


def record_authenticated(
    conn: psycopg.Connection[Any],
    *,
    workspace_id: str,
    app_id: int,
    body_digest: str,
    installation_id: int,
    repository_id: int | None,
    delivery_id: str,
) -> Receipt:
    """Deduplicate bytes and retain every header alias in the caller's transaction.

    A repeated body with another delivery ID is not new. Reusing any seen delivery ID with
    different bytes is refused, including aliases seen only on a replay. Savepoint rollback
    prevents a conflicting header from leaving an orphan body even if the caller catches it.
    `new_body` is not a dispatch grant or proof of publication; commit can still fail.
    """
    if (
        any(type(v) is not int or not 1 <= v <= 2**63 - 1 for v in (app_id, installation_id))
        or (
            repository_id is not None
            and (type(repository_id) is not int or not 1 <= repository_id <= 2**63 - 1)
        )
        or not isinstance(body_digest, str)
        or len(body_digest) != 64
        or any(c not in "0123456789abcdef" for c in body_digest)
    ):
        raise Refused("webhook receipt identity malformed")
    for value in (workspace_id, delivery_id):
        try:
            if not isinstance(value, str) or str(UUID(value)) != value:
                raise ValueError
        except ValueError:
            raise Refused("webhook receipt UUID malformed") from None
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO github_webhook_body"
            "(id,workspace_id,app_id,body_digest,installation_id,repository_id) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(workspace_id,app_id,body_digest) "
            "DO NOTHING RETURNING id",
            (str(uuid4()), workspace_id, app_id, body_digest, installation_id, repository_id),
        ).fetchone()
        new_body = row is not None
        retained = conn.execute(
            "SELECT id,installation_id,repository_id FROM github_webhook_body "
            "WHERE workspace_id=%s AND app_id=%s AND body_digest=%s",
            (workspace_id, app_id, body_digest),
        ).fetchone()
        if retained is None or (retained["installation_id"], retained["repository_id"]) != (
            installation_id,
            repository_id,
        ):
            raise Refused("webhook body identity differs")
        body_id = str(retained["id"])
        conn.execute(
            "INSERT INTO github_webhook_delivery(workspace_id,app_id,delivery_id,body_id) "
            "VALUES(%s,%s,%s,%s) ON CONFLICT(workspace_id,app_id,delivery_id) DO NOTHING",
            (workspace_id, app_id, delivery_id, body_id),
        )
        alias = conn.execute(
            "SELECT body_id FROM github_webhook_delivery "
            "WHERE workspace_id=%s AND app_id=%s AND delivery_id=%s",
            (workspace_id, app_id, delivery_id),
        ).fetchone()
        if alias is None or str(alias["body_id"]) != body_id:
            raise Refused("webhook delivery identity was reused for different bytes")
        return Receipt(body_id, new_body)
