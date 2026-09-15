"""Join retained observer-authored conditions to the original action boundaries.

Only call after finalizer original-byte, producer, attempt, lease and closed-tail checks.
This is not an upload validator and never evaluates a counter into an observer condition.
The configured independent observer, not these source records, owns installation authority.
"""

from __future__ import annotations

from typing import Any

from accessforge_domain.canonical import digest
from accessforge_domain.evaluation.assertions import AssertionOutcome, Provenance
from accessforge_domain.journeys.assertions import AssertionKind, AssertionSet
from accessforge_domain.reference_effect_scope import REFERENCE_EFFECT_POLICY_DIGEST
from accessforge_domain.states import Condition

from .execution_artifacts import Refused

CONDITION_FORMAT = "accessforge.reference-effect-conditions.v1"


def observed_assertions(
    snapshots: dict[str, Any], assertions: AssertionSet
) -> dict[str, AssertionOutcome]:
    records = snapshots["EFFECT_RECEIPT"]["records"]
    lifecycle = [r for r in records if "collectorPhase" in r["payload"]["sourceRecord"]]
    # Historical lifecycle-only receipts are not retroactively given conditions.
    if not any("conditionFormat" in r["payload"]["sourceRecord"] for r in lifecycle):
        return {}
    if len(lifecycle) != 2:
        raise Refused("unique original reference collector lifecycle unavailable")
    ready, closed = lifecycle
    start, end = (r["payload"]["sourceRecord"] for r in lifecycle)
    final = records[-1]
    final_source = final["payload"]["sourceRecord"]
    if (
        start.get("collectorPhase") != "READY"
        or end.get("collectorPhase") != "CLOSED"
        or end.get("conditionFormat") != CONDITION_FORMAT
        or "conditionFormat" in start
        or start.get("assertionObservations") != []
        or end.get("startEventId") != ready["eventId"]
        or ready["eventId"] == closed["eventId"]
        or not ready["sequence"] < closed["sequence"] < final["sequence"]
        or final_source.get("finalSample") is not True
    ):
        raise Refused("reference collector closure does not bind its original READY")
    for record in (*lifecycle, final):
        payload = record["payload"]
        source = payload["sourceRecord"]
        if (
            record["eventType"] != "EFFECT_RECEIPT"
            or payload.get("serviceIdentity") != "OBSERVER"
            or payload.get("producerId") != final["payload"].get("producerId")
            or source.get("assertionSetDigest") != digest(assertions.canonical_form())
            or any(
                not isinstance(source.get(key), str)
                or not source[key]
                or source[key] != final_source.get(key)
                for key in ("environmentConfigurationDigest", "fixtureInstanceId")
            )
        ):
            raise Refused("reference conditions differ from the original observer binding")
    for source in (start, end):
        if (
            source.get("finalSample") is not False
            or source.get("policyDigest") != REFERENCE_EFFECT_POLICY_DIGEST
            or source.get("effect") != "CREATE_TEST_REQUEST"
            or any(
                not isinstance(source.get(key), str)
                or not source[key]
                or source[key] != start.get(key)
                for key in ("scopeDigest", "clockEpoch", "startNs")
            )
        ):
            raise Refused("reference lifecycle scope or clock differs")
    # Canonical sequence ordering, not a caller's afterActionSequence or wall clock,
    # proves READY was admitted before dispatch and CLOSED after the settled STOP.
    actions = snapshots["ACTION_TRACE"]["records"]
    if not actions or len(actions) % 2:
        raise Refused("reference conditions require original settled action pairs")
    action_ids: set[str] = set()
    previous = ready["sequence"]
    for index in range(0, len(actions), 2):
        intent, result = actions[index : index + 2]
        command, completed = (r["payload"]["sourceRecord"] for r in (intent, result))
        number = index // 2 + 1
        action_id = command.get("actionId")
        if (
            intent["eventType"] != "ACTION_INTENT"
            or result["eventType"] != "ACTION_RESULT"
            or any(r["payload"].get("serviceIdentity") != "SUPERVISOR" for r in (intent, result))
            or not isinstance(action_id, str)
            or not action_id
            or action_id in action_ids
            or completed.get("actionId") != action_id
            or type(command.get("sequence")) is not int
            or type(completed.get("sequence")) is not int
            or command["sequence"] != number
            or completed["sequence"] != number
            or not previous < intent["sequence"] < result["sequence"] < closed["sequence"]
            or (command.get("action") == "STOP" and index != len(actions) - 2)
        ):
            raise Refused("reference action coverage ordering or identity differs")
        action_ids.add(action_id)
        previous = result["sequence"]
    if (
        command.get("action") != "STOP"
        or completed.get("status") != "SUCCEEDED"
        or type(start.get("afterActionSequence")) is not int
        or start["afterActionSequence"] != 0
        or any(
            type(s.get("afterActionSequence")) is not int
            or s["afterActionSequence"] != len(actions) // 2
            for s in (end, final_source)
        )
    ):
        raise Refused("reference closure lacks original successful final STOP")
    allowed = {
        a.assertion_id: a for a in assertions.assertions if a.kind is AssertionKind.FORBIDDEN_EFFECT
    }
    authored = end.get("assertionObservations")
    if not isinstance(authored, list):
        raise Refused("reference observer conditions unavailable")
    values: dict[str, AssertionOutcome] = {}
    for item in authored:
        if not isinstance(item, dict) or not isinstance(item.get("assertionId"), str):
            raise Refused("reference observer condition malformed")
        key = item["assertionId"]
        fields = {"assertionId", "kind", "condition", "provenance"}
        unknown = item.get("condition") == "UNKNOWN"
        if unknown:
            fields.add("unknownReason")
        if (
            key not in allowed
            or key in values
            or set(item) != fields
            or item.get("kind") != "FORBIDDEN_EFFECT"
            or item.get("provenance") != "OBSERVER_AUTHORED"
            or item.get("condition") not in {"TRUE", "FALSE", "UNKNOWN"}
            or (
                unknown
                and (not isinstance(item["unknownReason"], str) or not item["unknownReason"])
            )
        ):
            raise Refused("reference observer supplied conflicting or foreign conditions")
        rule = allowed[key].evaluation_rule
        if not unknown and (
            rule is None
            or rule.rule_type != "CONTINUOUS_EFFECT_ABSENCE"
            or rule.effect != "CREATE_TEST_REQUEST"
            or rule.scope_digest != REFERENCE_EFFECT_POLICY_DIGEST
        ):
            raise Refused("reference observer condition has no matching frozen policy")
        values[key] = AssertionOutcome(
            key,
            AssertionKind.FORBIDDEN_EFFECT,
            Condition(item["condition"]),
            Provenance.OBSERVER_AUTHORED,
            (ready["eventId"], closed["eventId"]),
            item.get("unknownReason"),
        )
    return values
