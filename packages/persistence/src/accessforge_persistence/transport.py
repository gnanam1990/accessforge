"""Queue transports.

Two implementations behind one interface, with identical semantics: at-least-once delivery of a
reference, never a trusted command. The local transport is what tests use; the AWS one is declared
so the interface is honest about what it will need, and **raises rather than pretending**.

No AWS infrastructure is provisioned and no AWS delivery has been tested. Saying so in code is
better than a configuration flag that quietly turns into a claim.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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


class AwsSqsTransport(Transport):
    """Declared, not implemented.

    Provisioning a queue would create billable infrastructure, which needs explicit authority this
    module does not have. Raising keeps the interface honest: there is no code path that looks like
    AWS delivery and is not.
    """

    def __init__(self, queue_url: str) -> None:
        self.queue_url = queue_url

    def publish(self, message: PublishedReference) -> None:
        raise NotImplementedError(
            "the AWS transport is not implemented. Provisioning a queue is billable and needs "
            "explicit authorization; module 27 owns deployment. Use LocalTransport for tests."
        )

    def drain(self) -> list[PublishedReference]:
        raise NotImplementedError("the AWS transport is not implemented")
