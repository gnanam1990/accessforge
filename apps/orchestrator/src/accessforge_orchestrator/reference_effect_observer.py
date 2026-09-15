"""Independent observer lifecycle records in the existing authenticated evidence stream.

Operator-service embedding only: no public upload endpoint, model tool, privilege grant or
automatic installation. Construct in the independent observer process, not the navigator.
Ready must be retained before dispatch; finish requires the original settled STOP action.
These records do not close the ordinary completion observer stream or mint a run verdict.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from accessforge_domain.authorization import (
    MachinePrincipal,
    ServiceIdentity,
    assert_may_submit_event,
)
from accessforge_domain.canonical import digest
from accessforge_domain.effect_monitor import EffectCoverage
from accessforge_domain.evaluation.rules import reference_effect_monitor_assertions
from accessforge_domain.journeys.assertions import AssertionKind, AssertionSet
from accessforge_domain.reference_effect_scope import (
    REFERENCE_EFFECT_POLICY_DIGEST,
    ReferenceEffectBinding,
)
from accessforge_domain.timestamps import to_rfc3339_utc
from accessforge_persistence import sequencer, workspace_connection
from accessforge_persistence.evidence.effect_collector import ReferenceEffectCollector

from .completion_observer import Refused, _context


class ReferenceEffectObserver:
    """Single-use owner of one independent connection and its original collector attempt.

    Configuration is supplied by the trusted operator service. The sealed credential reference
    is checked before/after every measurement; the database role is checked by the collector.
    On uncertain commit, reconcile original source IDs rather than calling begin/finish again.
    Use abort() on abandonment. A process owner must enforce its external wall-clock deadline.
    """

    def __init__(
        self,
        database_url: str,
        application_database_url: str,
        *,
        workspace_id: str,
        run_id: str,
        credential_ref: str,
        application_role: str,
        installation_id: str,
        expected_attempt_id: str | None = None,
    ) -> None:
        self._workspace = str(UUID(workspace_id))
        self._run = str(UUID(run_id))
        self._installation = str(UUID(installation_id))
        self._expected_attempt = (
            None if expected_attempt_id is None else str(UUID(expected_attempt_id))
        )
        if not credential_ref or len(credential_ref) > 256 or not application_role:
            raise Refused("independent collector configuration unavailable")
        self._database = make_conninfo(database_url, connect_timeout=5)
        self._application = make_conninfo(application_database_url, connect_timeout=5)
        self._credential = credential_ref
        self._application_role = application_role
        self._epoch = str(uuid4())
        self._start_id, self._close_id = str(uuid4()), str(uuid4())
        self._state = "NEW"
        self._collector: ReferenceEffectCollector | None = None
        self._before: dict[str, Any] | None = None
        self._start_event: str | None = None
        self._start = 0

    def _context(self, conn: psycopg.Connection[Any], *, startup: bool) -> dict[str, Any]:
        conn.execute("SET LOCAL statement_timeout='5s'")
        conn.execute("SET LOCAL lock_timeout='1s'")
        context = _context(
            conn,
            self._workspace,
            self._run,
            self._credential,
            before_dispatch=startup,
        )
        if self._expected_attempt is not None and context["attempt"] != self._expected_attempt:
            raise Refused("observer process belongs to a different original attempt")
        return context

    @staticmethod
    def _identity(context: dict[str, Any]) -> dict[str, Any]:
        return {
            k: v
            for k, v in context.items()
            if k not in {"afterActionSequence", "lastAction", "lastResult"}
        }

    def _append(
        self,
        conn: psycopg.Connection[Any],
        context: dict[str, Any],
        *,
        phase: str,
        record_id: str,
        extra: dict[str, Any],
    ) -> str:
        principal = MachinePrincipal(
            service_identity=ServiceIdentity.OBSERVER,
            workspace_id=self._workspace,
            credential_id=self._credential,
            run_id=self._run,
        )
        assert_may_submit_event(principal, "EFFECT_RECEIPT")
        stream = conn.execute(
            "SELECT admitted_through FROM producer_stream WHERE attempt_id=%s AND producer_id=%s",
            (context["attempt"], context["producer"]),
        ).fetchone()
        sequence = 1 if stream is None else int(stream["admitted_through"]) + 1
        if sequence > 128:
            raise Refused("independent observer record budget exhausted")
        observed_at = datetime.now(UTC)
        source = {
            "collectorPhase": phase,
            "environmentConfigurationDigest": context["environmentConfigurationDigest"],
            "assertionSetDigest": context["assertionSetDigest"],
            "fixtureInstanceId": context["fixtureId"],
            "afterActionSequence": context["afterActionSequence"],
            "observedAt": to_rfc3339_utc(observed_at),
            "effect": "CREATE_TEST_REQUEST",
            "clockEpoch": self._epoch,
            "policyDigest": REFERENCE_EFFECT_POLICY_DIGEST,
            # Lifecycle coverage is NOT the current-row task-completion measurement.
            "finalSample": False,
            "measurement": "UNKNOWN",
            "count": None,
            "assertionObservations": [],
            **extra,
        }
        event = sequencer.admit_record(
            conn,
            workspace_id=self._workspace,
            run_id=self._run,
            attempt_id=context["attempt"],
            lease_epoch=context["epoch"],
            producer_id=context["producer"],
            source_record_id=record_id,
            producer_sequence=sequence,
            event_type="EFFECT_RECEIPT",
            manifest_digest=context["manifestDigest"],
            source_time=observed_at,
            payload={
                "producerId": context["producer"],
                "producerSequence": sequence,
                "sourceRecordId": record_id,
                "sourceRecordDigest": digest(source),
                "sourceRecord": source,
                "serviceIdentity": "OBSERVER",
            },
        )
        return event.event_id

    def begin(self) -> str:
        if self._state != "NEW":
            self.abort()
            raise Refused("effect observer cannot start or replay")
        self._state = "STARTING"
        try:
            with workspace_connection(self._database, self._workspace) as conn:
                before = self._context(conn, startup=True)
            contract = AssertionSet.from_canonical_form(before["assertionContract"])
            if not any(
                item.kind is AssertionKind.FORBIDDEN_EFFECT
                and item.evaluation_rule is not None
                and item.evaluation_rule.rule_type == "CONTINUOUS_EFFECT_ABSENCE"
                and item.evaluation_rule.effect == "CREATE_TEST_REQUEST"
                and item.evaluation_rule.scope_digest == REFERENCE_EFFECT_POLICY_DIGEST
                for item in contract.assertions
            ):
                raise Refused("frozen reference-effect policy unavailable")
            binding = ReferenceEffectBinding(
                self._run,
                before["attempt"],
                self._installation,
                before["fixtureNonce"],
            )
            application = psycopg.connect(self._application, row_factory=dict_row)
            try:
                self._collector = ReferenceEffectCollector(
                    application,
                    application_role=self._application_role,
                    installation_id=self._installation,
                    fixture_nonce=binding.fixture_nonce,
                    run_id=self._run,
                    attempt_id=binding.attempt_id,
                    clock_epoch=self._epoch,
                )
            except Exception:
                application.close()
                raise
            self._start = self._collector.begin()
            with workspace_connection(self._database, self._workspace) as conn:
                after = self._context(conn, startup=True)
                if before != after:
                    raise Refused("execution changed during collector startup")
                previous = conn.execute(
                    "SELECT 1 FROM canonical_event WHERE run_id=%s AND attempt_id=%s "
                    "AND event_type='EFFECT_RECEIPT' "
                    "AND payload->'sourceRecord'->>'collectorPhase'='READY' LIMIT 1",
                    (self._run, before["attempt"]),
                ).fetchone()
                if previous is not None:
                    raise Refused("original collector startup must be reconciled, not replaced")
                self._start_event = self._append(
                    conn,
                    after,
                    phase="READY",
                    record_id=self._start_id,
                    extra={
                        "scopeDigest": binding.measurement_scope_digest(),
                        "startNs": str(self._start),
                    },
                )
            self._before = before
            self._state = "ACTIVE"
            return self._start_event
        except Exception:
            self.abort()
            raise Refused(
                "effect observer startup unconfirmed; reconcile original record"
            ) from None

    def finish(self) -> str:
        if self._state != "ACTIVE" or self._collector is None or self._before is None:
            self.abort()
            raise Refused("effect observer has no active collector")
        self._state = "CLOSING"
        try:
            with workspace_connection(self._database, self._workspace) as conn:
                before = self._context(conn, startup=False)
                if (
                    self._identity(before) != self._identity(self._before)
                    or before["lastAction"] != "STOP"
                    or before["lastResult"] != "SUCCEEDED"
                ):
                    raise Refused("original successful STOP and collector binding required")
            coverage = self._collector.finish()
            self._validate_coverage(coverage)
            # The protected observer resolves its own configured installation and reserved
            # fixture. The finalizer must never manufacture this binding from uploaded JSON.
            binding = ReferenceEffectBinding(
                self._run, before["attempt"], self._installation, before["fixtureNonce"]
            )
            conditions = reference_effect_monitor_assertions(
                AssertionSet.from_canonical_form(before["assertionContract"]),
                expected=coverage.window,
                binding=binding,
                measured=coverage,
            )
            with workspace_connection(self._database, self._workspace) as conn:
                after = self._context(conn, startup=False)
                if after != before:
                    raise Refused("execution changed during collector closure")
                event = self._append(
                    conn,
                    after,
                    phase="CLOSED",
                    record_id=self._close_id,
                    extra={
                        "conditionFormat": "accessforge.reference-effect-conditions.v1",
                        "assertionObservations": conditions,
                        "startEventId": self._start_event,
                        "scopeDigest": coverage.window.scope_digest,
                        # Decimal strings preserve monotonic nanoseconds above JSON safe-int range.
                        "startNs": str(coverage.window.start_ns),
                        "endNs": str(coverage.window.end_ns),
                        "intervals": [
                            {
                                "startNs": str(i.start_ns),
                                "endNs": str(i.end_ns),
                                "occurrences": str(i.occurrences),
                            }
                            for i in coverage.intervals
                        ],
                    },
                )
            self._state = "CLOSED"
            return event
        except Exception:
            self.abort()
            raise Refused(
                "effect observer closure unconfirmed; reconcile original record"
            ) from None

    def _validate_coverage(self, coverage: EffectCoverage) -> None:
        assert self._before is not None
        binding = ReferenceEffectBinding(
            self._run,
            self._before["attempt"],
            self._installation,
            self._before["fixtureNonce"],
        )
        window = coverage.window
        if (
            window.run_id != self._run
            or window.attempt_id != binding.attempt_id
            or window.scope_digest != binding.measurement_scope_digest()
            or window.clock_epoch != self._epoch
            or window.effect != "CREATE_TEST_REQUEST"
            or window.start_ns != self._start
        ):
            raise Refused("collector returned a different execution scope")

    def abort(self) -> None:
        self._state = "CLOSED"
        if self._collector is not None:
            self._collector.abort()
