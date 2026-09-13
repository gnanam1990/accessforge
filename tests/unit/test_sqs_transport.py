"""Real installed boto3 request/response validation, without network or AWS credentials."""

import hashlib
from uuid import uuid4

import boto3
import pytest
from botocore.stub import Stubber

from accessforge_persistence.transport import (
    AwsSqsTransport,
    PublishedReference,
    TransportRefused,
    _body,
)

QUEUE = "https://sqs.us-east-1.amazonaws.com/123456789012/accessforge.fifo"


def fixture() -> tuple[AwsSqsTransport, Stubber, PublishedReference]:
    client = boto3.client(
        "sqs",
        region_name="us-east-1",
        aws_access_key_id="inert-test",
        aws_secret_access_key="inert-test",
        aws_session_token="inert-test",
    )
    transport = AwsSqsTransport(
        QUEUE, workspace_id=str(uuid4()), region_name="us-east-1", client=client
    )
    message = PublishedReference(
        str(uuid4()),
        "run.queued",
        {
            "runId": str(uuid4()),
            "revision": 1,
            "status": "QUEUED",
        },
    )
    return transport, Stubber(client), message


def test_fifo_publish_retries_keep_exact_body_and_deduplication_identity() -> None:
    transport, stub, message = fixture()
    body = _body(message, transport.workspace_id)
    request = {
        "QueueUrl": QUEUE,
        "MessageBody": body,
        "MessageGroupId": transport.workspace_id,
        "MessageDeduplicationId": hashlib.sha256(body.encode()).hexdigest(),
    }
    receipt = {
        "MessageId": str(uuid4()),
        "MD5OfMessageBody": hashlib.md5(body.encode(), usedforsecurity=False).hexdigest(),
    }
    stub.add_response("send_message", receipt, request)
    stub.add_response("send_message", receipt, request)
    with stub:
        transport.publish(message)
        transport.publish(message)
    stub.assert_no_pending_responses()


@pytest.mark.parametrize("fault", [None, "handler", "workspace", "checksum", "extra"])
def test_receive_acknowledges_only_matching_reference_after_committed_handler(
    fault: str | None,
) -> None:
    transport, stub, message = fixture()
    body = _body(message, str(uuid4()) if fault == "workspace" else transport.workspace_id)
    if fault == "extra":
        body = body[:-1] + ',"command":"execute"}'
    checksum = hashlib.md5(body.encode(), usedforsecurity=False).hexdigest()
    stub.add_response(
        "receive_message",
        {
            "Messages": [
                {
                    "MessageId": str(uuid4()),
                    "ReceiptHandle": "current-delivery-handle",
                    "Body": body,
                    "MD5OfBody": "0" * 32 if fault == "checksum" else checksum,
                }
            ]
        },
        {
            "QueueUrl": QUEUE,
            "MaxNumberOfMessages": 1,
            "WaitTimeSeconds": 10,
            "VisibilityTimeout": 60,
        },
    )
    if fault is None:
        stub.add_response(
            "delete_message",
            {},
            {
                "QueueUrl": QUEUE,
                "ReceiptHandle": "current-delivery-handle",
            },
        )
    handled = []

    def handler(received: PublishedReference) -> None:
        handled.append(received)
        if fault == "handler":
            raise RuntimeError("database commit failed")

    with stub:
        if fault == "handler":
            with pytest.raises(RuntimeError, match="commit failed"):
                transport.consume_once(handler)
        elif fault:
            with pytest.raises(TransportRefused):
                transport.consume_once(handler)
        else:
            assert transport.consume_once(handler)
    assert handled == ([message] if fault in {None, "handler"} else [])
    stub.assert_no_pending_responses()


def test_unknown_send_does_not_claim_publication_and_invalid_config_never_calls_aws() -> None:
    transport, stub, message = fixture()
    stub.add_client_error("send_message", service_error_code="RequestThrottled")
    with stub, pytest.raises(TransportRefused, match="publication unconfirmed"):
        transport.publish(message)
    for url in [
        "http://localhost/queue",
        QUEUE + "?redirect=yes",
        QUEUE.replace("us-east-1", "us-west-2"),
    ]:
        with pytest.raises(TransportRefused):
            AwsSqsTransport(url, workspace_id=transport.workspace_id, region_name="us-east-1")
    with pytest.raises(TypeError, match="local-test-only"):
        transport.drain()
