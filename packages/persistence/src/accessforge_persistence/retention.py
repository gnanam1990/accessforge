"""Retention classes, and the cost of deleting under one.

Retention is not one number. Source, fixture references, reader speech, screen recordings,
diagnostics, model exchanges and review records are collected for different reasons, carry different
risks and are wanted for different lengths of time — and a single "keep evidence for N days" setting
makes a decision about speech recordings by way of a decision about diagnostics.

The rule this module exists to keep visible: **deleting evidence invalidates any completeness claim
that depended on it.** Deletion does not free space and leave everything else true. Module 10's
finalization counts a DELETED required artifact as missing, which is correct and is also the part
somebody discovers a year later. Every class here declares whether deleting under it has that
consequence, and the settings route serves the declaration rather than describing it in prose
somewhere else.

The defaults are conservative in one direction only: the classes most likely to contain something
about a person — speech, recordings, model exchanges — are the shortest, and the ones that are
references rather than content are the longest.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import psycopg


class RetentionError(RuntimeError):
    """A retention policy could not be read or configured."""


@dataclass(frozen=True, slots=True)
class RetentionClass:
    evidence_class: str
    retain_days: int
    consent_required: bool
    invalidates_completeness: bool
    meaning: str


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    revision: int
    entries: tuple[RetentionClass, ...]


#: What each class holds, whether deleting it breaks a completeness claim, and the default period.
#: The defaults are what applies to a workspace nobody has configured — stated here rather than left
#: implicit, because "no policy" would otherwise mean "keep everything forever", which is a decision
#: made by omission.
CLASS_DEFINITIONS: dict[str, tuple[int, bool, bool, str]] = {
    "SOURCE_SNAPSHOT": (
        90,
        False,
        True,
        "Customer source at an exact revision. Deleting it means a run's manifest names a tree "
        "nobody can reproduce, so the run's identity can no longer be checked.",
    ),
    "FIXTURE_REFERENCE": (
        365,
        False,
        False,
        "Template identifiers and per-run nonces, not the values. Deleting them loses the ability "
        "to trace which fixture a run used; it does not remove evidence a verdict rested on.",
    ),
    "READER_SPEECH": (
        30,
        True,
        True,
        "What the screen reader actually announced. This is the evidence a reader assertion is "
        "decided from, so deleting it makes those assertions unsupported. It is also the class "
        "most likely to contain something somebody typed.",
    ),
    "SCREEN_RECORDING": (
        14,
        True,
        False,
        "Optional visual capture. Never the basis of a verdict — a recording is supplementary, and "
        "deleting it costs a reader context rather than proof.",
    ),
    "DIAGNOSTIC": (
        30,
        False,
        False,
        "Timings, error codes and internal state captured for debugging. Deleting it costs an "
        "operator the ability to explain a failure after the fact.",
    ),
    "MODEL_EXCHANGE": (
        30,
        True,
        False,
        "What was sent to a model and what came back. Not an input to any verdict — the evaluator "
        "does not read it — and the class most likely to contain source or task content.",
    ),
    "REVIEW_RECORD": (
        3650,
        False,
        False,
        "A person's assessment and its attribution. Kept longest because it is the record of a "
        "human decision, and shortest-lived audit trails are the ones nobody can reconstruct.",
    ),
}


def default_policy() -> RetentionPolicy:
    """What applies to a workspace nobody has configured.

    Revision 0, and a distinguishable one: an operator reading the settings screen sees that these
    are defaults rather than choices somebody made.
    """
    return RetentionPolicy(
        revision=0,
        entries=tuple(
            RetentionClass(
                evidence_class=name,
                retain_days=days,
                consent_required=consent,
                invalidates_completeness=invalidates,
                meaning=meaning,
            )
            for name, (days, consent, invalidates, meaning) in sorted(CLASS_DEFINITIONS.items())
        ),
    )


def current_policy(
    conn: psycopg.Connection[dict[str, Any]], *, workspace_id: str
) -> RetentionPolicy:
    row = conn.execute(
        "SELECT coalesce(max(revision), 0) AS revision FROM retention_policy "
        "WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()
    revision = int(row["revision"]) if row else 0
    if revision == 0:
        return default_policy()

    rows = conn.execute(
        "SELECT evidence_class, retain_days, consent_required FROM retention_policy "
        "WHERE workspace_id = %s AND revision = %s ORDER BY evidence_class",
        (workspace_id, revision),
    ).fetchall()
    return RetentionPolicy(
        revision=revision,
        entries=tuple(
            RetentionClass(
                evidence_class=str(r["evidence_class"]),
                retain_days=int(r["retain_days"]),
                consent_required=bool(r["consent_required"]),
                # From the definition, never from the row. Whether deleting a class breaks a
                # completeness claim is a property of what the class *is*; storing it per workspace
                # would let it be configured to false, and configuring it false does not make
                # deletion stop breaking anything.
                invalidates_completeness=CLASS_DEFINITIONS[str(r["evidence_class"])][2],
                meaning=CLASS_DEFINITIONS[str(r["evidence_class"])][3],
            )
            for r in rows
        ),
    )


def configure_policy(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    workspace_id: str,
    entries: list[Any],
    configured_by: str,
    expected_revision: int,
) -> int:
    """Append a new policy revision covering every class.

    Every class, not a subset: a partial revision would leave the unlisted classes at whatever the
    previous revision said, and a reader of the new revision would have no way to know which of its
    rows were decisions and which were leftovers.
    """
    row = conn.execute(
        "SELECT coalesce(max(revision), 0) AS revision FROM retention_policy "
        "WHERE workspace_id = %s",
        (workspace_id,),
    ).fetchone()
    latest = int(row["revision"]) if row else 0
    if latest != expected_revision:
        raise RetentionError(
            f"the policy is at revision {latest} and you read revision {expected_revision}. "
            "Somebody changed it while you were deciding."
        )

    supplied: dict[str, tuple[int, bool]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise RetentionError("each class must be an object")
        name = entry.get("evidenceClass")
        days = entry.get("retainDays")
        consent = entry.get("consentRequired", False)
        if name not in CLASS_DEFINITIONS:
            raise RetentionError(
                f"{name!r} is not an evidence class. The classes are "
                f"{', '.join(sorted(CLASS_DEFINITIONS))}."
            )
        if isinstance(days, bool) or not isinstance(days, int) or days < 0:
            raise RetentionError(f"retainDays for {name} must be a whole number of zero or more")
        if not isinstance(consent, bool):
            raise RetentionError(f"consentRequired for {name} must be true or false")
        supplied[str(name)] = (days, consent)

    missing = sorted(set(CLASS_DEFINITIONS) - set(supplied))
    if missing:
        raise RetentionError(
            f"every class must be given a retention: {', '.join(missing)} were omitted. A partial "
            "revision leaves the unlisted classes at whatever the last one said, and a reader "
            "cannot tell a decision from a leftover."
        )

    revision = latest + 1
    for name, (days, consent) in sorted(supplied.items()):
        conn.execute(
            """
            INSERT INTO retention_policy
                (id, workspace_id, revision, evidence_class, retain_days, consent_required,
                 configured_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (str(uuid.uuid4()), workspace_id, revision, name, days, consent, configured_by),
        )
    return revision
