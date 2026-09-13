"""Publish committed workspace outbox references to an explicitly provisioned SQS queue.

No queue state authorizes desktop actions. This worker only announces database references;
consumers re-read authoritative state and deduplicate the operation before committing any work.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from uuid import UUID, uuid4

from accessforge_persistence import outbox, workspace_connection
from accessforge_persistence.transport import AwsSqsTransport, PublishedReference


@dataclass(frozen=True)
class PublicationReport:
    claimed: int
    published: int
    unconfirmed: int
    superseded: int


def publish_once(
    database_url: str, transport: AwsSqsTransport, *, limit: int = 10
) -> PublicationReport:
    """Claim/commit, publish, then conditionally acknowledge the exact unexpired claim.

    An expired/replaced claim is never acknowledged by an older publisher. Failure leaves the
    durable reference for recovery after the existing outbox claim lease. No implicit immediate
    retry and no database transaction held while waiting on AWS.
    """
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("outbox batch size must be between 1 and 100")
    workspace = str(UUID(transport.workspace_id))
    worker = f"sqs-publisher-{uuid4()}"
    with workspace_connection(database_url, workspace) as conn:
        messages = outbox.claim_messages(conn, claimed_by=worker, limit=limit)
    published = unconfirmed = superseded = 0
    for message in messages:
        if message.workspace_id != workspace:
            raise ValueError("outbox scope differs from queue workspace")
        try:
            transport.publish(
                PublishedReference(message.operation_id, message.topic, message.reference)
            )
        except Exception:
            # Never log raw SDK or reference errors, which may contain endpoints or payloads.
            unconfirmed += 1
            continue
        with workspace_connection(database_url, workspace) as conn:
            row = conn.execute(
                "UPDATE outbox_message SET published_at=clock_timestamp(), claim_expires_at=NULL "
                "WHERE id=%s AND workspace_id=%s AND published_at IS NULL AND claimed_by=%s "
                "AND attempts=%s AND claim_expires_at>clock_timestamp() RETURNING id",
                (message.id, workspace, worker, message.attempts),
            ).fetchone()
        if row is None:
            superseded += 1
        else:
            published += 1
    return PublicationReport(len(messages), published, unconfirmed, superseded)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish one existing workspace outbox batch to SQS"
    )
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--queue-url", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    try:
        transport = AwsSqsTransport(
            args.queue_url, workspace_id=args.workspace, region_name=args.region
        )
        report = publish_once(os.environ["ACCESSFORGE_DATABASE_URL"], transport, limit=args.limit)
    except Exception:
        parser.exit(1, "outbox publication incomplete; reconcile database state before retrying\n")
    print(json.dumps({"meaning": "QUEUE_PUBLICATION_NOT_BUSINESS_COMPLETION", **asdict(report)}))
    if report.unconfirmed or report.superseded:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
