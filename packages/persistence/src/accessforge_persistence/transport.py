"""Queue transports.

At-least-once delivery of a reference, never a trusted command. Local transport is explicit;
AWS transport uses an already provisioned queue and never creates infrastructure. No real AWS
delivery is claimed by the inert SDK-contract checks.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from accessforge_domain.states import RunStatus


@dataclass(frozen=True, slots=True)
class PublishedReference:
    """What a transport carries.

    A reference and a stable operation identity — never mutable command data. The consumer re-reads
    authoritative state, so a duplicated or delayed message cannot justify an action the current
    state does not.
    """

    operation_id: str
    topic: str
    reference: dict[str, Any]


class Transport(ABC):
    """Delivery is at-least-once. Every implementation must be safe to call twice."""

    @abstractmethod
    def publish(self, message: PublishedReference) -> None: ...

    @abstractmethod
    def drain(self) -> list[PublishedReference]:
        """Return and clear what has been published. Test affordance; not a consumer API."""


@dataclass
class LocalTransport(Transport):
    """In-process transport for tests and single-machine development.

    Explicitly configured rather than a silent default, so a test cannot pass while believing it
    exercised a real queue.
    """

    delivered: list[PublishedReference] = field(default_factory=list)

    def publish(self, message: PublishedReference) -> None:
        self.delivered.append(message)

    def drain(self) -> list[PublishedReference]:
        out = list(self.delivered)
        self.delivered.clear()
        return out


class TransportRefused(ValueError):
    """Invalid configuration/reference or unconfirmed queue delivery; never business completion."""


def _uuid(value: Any) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise TransportRefused("canonical queue identity required")
    return value


def _body(message: PublishedReference, workspace_id: str) -> str:
    _uuid(message.operation_id)
    reference = message.reference
    if (
        not isinstance(message.topic, str)
        or not re.fullmatch(r"run\.[a-z][a-z_.]{0,63}", message.topic)
        or not isinstance(reference, dict)
        or set(reference) != {"runId", "revision", "status"}
        or type(reference["revision"]) is not int
        or not 1 <= reference["revision"] <= 9223372036854775807
        or reference["status"] not in {status.value for status in RunStatus}
    ):
        raise TransportRefused("only bounded run references may enter the queue")
    _uuid(reference["runId"])
    return json.dumps(
        {
            "schemaVersion": 1,
            "workspaceId": workspace_id,
            "operationId": message.operation_id,
            "topic": message.topic,
            "reference": reference,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class AwsSqsTransport(Transport):
    """Workspace-bound SQS run-reference delivery, with explicit post-commit acknowledgement.

    Construction does not load credentials or call AWS. publish/consume_once are billable when
    used with real AWS; deployment and invocation require operator authorization. The private
    optional client is a trusted dependency-injection port, not user-controlled queue routing.
    """

    def __init__(
        self, queue_url: str, *, workspace_id: str, region_name: str, client: Any = None
    ) -> None:
        self.workspace_id = _uuid(workspace_id)
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", region_name):
            raise TransportRefused("explicit AWS region required")
        host = rf"sqs\.{re.escape(region_name)}\.amazonaws\.com"
        if region_name.startswith("cn-"):
            host += r"\.cn"
        queue_name = r"(?:[A-Za-z0-9_-]{1,80}|[A-Za-z0-9_-]{1,75}\.fifo)"
        if not re.fullmatch(rf"https://{host}/[0-9]{{12}}/{queue_name}", queue_url):
            raise TransportRefused(
                "canonical AWS queue URL matching the configured region required"
            )
        self.queue_url = queue_url
        self.region_name = region_name
        self._client = client

    def _sdk(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "sqs",
                region_name=self.region_name,
                endpoint_url=self.queue_url.rsplit("/", 2)[0],
                config=Config(
                    connect_timeout=5,
                    read_timeout=25,
                    retries={"mode": "standard", "total_max_attempts": 1},
                ),
            )
        return self._client

    def publish(self, message: PublishedReference) -> None:
        body = _body(message, self.workspace_id)
        args: dict[str, Any] = {"QueueUrl": self.queue_url, "MessageBody": body}
        if self.queue_url.endswith(".fifo"):
            args["MessageGroupId"] = self.workspace_id
            # Stable across retries; differing payloads must not be silently suppressed by FIFO.
            args["MessageDeduplicationId"] = hashlib.sha256(body.encode()).hexdigest()
        try:
            receipt = self._sdk().send_message(**args)
            if (
                not isinstance(receipt, dict)
                or not isinstance(receipt.get("MessageId"), str)
                or not receipt["MessageId"]
                or receipt.get("MD5OfMessageBody")
                != hashlib.md5(body.encode(), usedforsecurity=False).hexdigest()
            ):
                raise TransportRefused("queue acceptance unavailable")
        except Exception:
            # A lost reply is unconfirmed, not proof of non-delivery. The durable outbox may retry
            # the same operation; consumers must deduplicate against authoritative database state.
            raise TransportRefused(
                "queue publication unconfirmed; retain the outbox reference"
            ) from None

    def consume_once(
        self,
        handle_committed: Callable[[PublishedReference], object],
        *,
        wait_seconds: int = 10,
        visibility_seconds: int = 60,
    ) -> bool:
        """One reference; delete only after the handler commits authoritative DB work.

        The handler must re-read workspace-scoped state and deduplicate operation_id, then return
        only after commit. Receipt expiry is not execution authority. Handler failure, malformed
        input or lost delete response leaves delivery unresolved and safe for redelivery.
        Configure an operator-owned DLQ policy for poison messages; this code never purges them.
        """
        if (
            type(wait_seconds) is not int
            or not 0 <= wait_seconds <= 20
            or type(visibility_seconds) is not int
            or not 1 <= visibility_seconds <= 43200
        ):
            raise TransportRefused("bounded queue receive timings required")
        try:
            response = self._sdk().receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=wait_seconds,
                VisibilityTimeout=visibility_seconds,
            )
            messages = response.get("Messages", [])
            if not isinstance(messages, list) or len(messages) > 1:
                raise TransportRefused("queue receive shape differs")
            if not messages:
                return False
            raw = messages[0]
            body = raw.get("Body")
            handle = raw.get("ReceiptHandle")
            if (
                not isinstance(body, str)
                or len(body.encode()) > 4096
                or not isinstance(handle, str)
                or not handle
                or len(handle) > 4096
                or raw.get("MD5OfBody")
                != hashlib.md5(body.encode(), usedforsecurity=False).hexdigest()
            ):
                raise TransportRefused("queue delivery framing differs")
            value = json.loads(body)
            if (
                not isinstance(value, dict)
                or set(value)
                != {"schemaVersion", "workspaceId", "operationId", "topic", "reference"}
                or type(value["schemaVersion"]) is not int
                or value["schemaVersion"] != 1
                or value["workspaceId"] != self.workspace_id
            ):
                raise TransportRefused("queue delivery workspace or schema differs")
            message = PublishedReference(value["operationId"], value["topic"], value["reference"])
            if _body(message, self.workspace_id) != body:
                raise TransportRefused("queue delivery is not canonical")
        except Exception:
            raise TransportRefused("queue receive unconfirmed; no delivery acknowledged") from None
        result = handle_committed(message)
        if result is not None:
            if inspect.iscoroutine(result):
                result.close()
            raise TransportRefused("handler must commit synchronously; no delivery acknowledged")
        try:
            self._sdk().delete_message(QueueUrl=self.queue_url, ReceiptHandle=handle)
        except Exception:
            raise TransportRefused(
                "queue acknowledgement unconfirmed; handler must deduplicate"
            ) from None
        return True

    def drain(self) -> list[PublishedReference]:
        raise TypeError(
            "drain is local-test-only; SQS requires consume_once with a committed handler"
        )
