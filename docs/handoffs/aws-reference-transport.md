# AWS run-reference transport and outbox publisher

Replaces the declared SQS publisher stub with lazy boto3 delivery to an explicitly configured,
already provisioned AWS queue. This is the optional queue path described in TDD deployment, not
proof of deployed infrastructure or a replacement for the missing physical-reader release gates.
No AWS resources, credentials, model calls, reader operations or live messages were created here.

The closed versioned envelope contains workspace, operation, topic and run reference
(`runId`, `revision`, `status`), not commands, source, credentials or transcripts. Standard and FIFO
queues are supported; FIFO retries preserve the exact body and deduplication identity. Publication
requires a message ID and matching body checksum. A lost response remains unconfirmed; neither
SQS acceptance nor deletion proves business completion.

`AwsSqsTransport.consume_once(handler)` receives one bounded reference without deleting it first.
The handler must re-read workspace-scoped authoritative state, deduplicate the operation and return
only after its transaction commits. Only then is the current receipt acknowledged. Malformed,
cross-workspace or checksum-invalid input never reaches the handler and is not deleted. Handler
failure leaves the message unacknowledged. Provision an appropriate DLQ/redrive and retention
policy separately; this implementation never creates, purges or reconfigures queues.

The one-shot publisher is runnable by a separately authorized deployment operator:

```sh
uv run python -m accessforge_orchestrator.queue_delivery \
  --workspace WORKSPACE_UUID --region us-east-1 \
  --queue-url https://sqs.us-east-1.amazonaws.com/ACCOUNT_ID/QUEUE_NAME --limit 10
```

It reads the existing `ACCESSFORGE_DATABASE_URL` and boto3 credential chain. It commits the outbox
claim before contacting SQS, then acknowledges only the same unexpired worker/attempt claim. Each
message is claimed immediately before sending, so slow batches do not consume
later rows' leases. An invocation excludes previously attempted rows from further claims. Lost
queue replies remain recoverable after the existing five-minute claim lease. No default deployment
or service registration is changed; actually invoking this command sends billable AWS requests.
Use least-privilege send-only IAM for the publisher and separate receive/delete IAM for consumers.
The application handler remains responsible for business authorization; a queue receipt grants none.

Changed-file Ruff/mypy checks passed. SDK Stubber request/response cases and real-PostgreSQL
claim-boundary regressions are authored for CI only, not executed locally. No live AWS validation
or production consumer handler is claimed.

Implementation checked against official boto3 [send_message](https://docs.aws.amazon.com/boto3/latest/reference/services/sqs/client/send_message.html),
[receive_message](https://docs.aws.amazon.com/boto3/latest/reference/services/sqs/client/receive_message.html)
and [delete_message](https://docs.aws.amazon.com/boto3/latest/reference/services/sqs/client/delete_message.html)
contracts. Receipt handles change across deliveries, and duplicate delivery remains possible even
after acknowledgement; deduplication therefore remains a database responsibility.
