"""Deleting evidence across everywhere it went, and reporting honestly what survived.

FR-020 asks for retention and deletion reaching "primary objects, derived views,
exports, caches and supported backups", and states the limit in the same sentence:
"local downloaded copies cannot be remotely recalled". That sentence is the design.
A deletion that quietly omitted what it cannot reach would be the most dangerous
feature in this product -- somebody would invoke it, see success, and tell a
regulator or a customer that the data is gone.

So every deletion returns a :class:`DeletionReport` naming what was removed **and
what was not**, and the second list is not optional, not a footnote, and never
empty. Three things always survive:

* **Audit metadata.** Who asked, when, over what scope, and a tombstone row for
  every artifact whose bytes went. Deleting those would make the deletion itself
  unprovable, and an erasure nobody can demonstrate is worse than none.
* **Backups taken before now.** A restore brings the content back. Module 27's
  drill is the proof restores work; this is the cost of that. The report says how
  long the window is.
* **Copies already downloaded.** An export handed to somebody is theirs.

Two decisions inside the mechanism matter before changing anything:

**The event chain survives the payload.** `canonical_event` carries `payload`,
`payload_digest` and `previous_event_hash`. Deletion replaces the payload and keeps
both digests, so the hash chain still verifies end to end while the content is
gone: a reader can still prove no event was inserted or removed, and can see which
ones they can no longer read. Deleting the rows would break the chain at that point
and make every *undeleted* event after it unverifiable -- destroying the evidence
somebody kept.

**A tombstone is the point, not a leftover.** `delete_artifact_bytes` keeps the
artifact row with `retention = 'DELETED'`, and finalization counts a DELETED
required artifact as *missing*. That is how deletion invalidates a completeness
claim instead of silently improving one: remove the row and an export would report
a complete evidence set for a run whose evidence is gone.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from accessforge_domain.timestamps import to_rfc3339_utc

from .evidence.objectstore import ArtifactStore
from .retention import CLASS_DEFINITIONS


class DeletionError(RuntimeError):
    """A deletion was refused."""


class UnknownEvidenceClass(DeletionError):
    """A class nobody defined. Refused rather than read as nothing to delete."""


#: Which retention class each artifact kind belongs to.
#:
#: Explicit per kind, with every kind present. A default would be the dangerous
#: option: an unmapped kind inheriting `DIAGNOSTIC` would make deleting
#: proof-bearing evidence look like clearing a log, because DIAGNOSTIC is the class
#: whose deletion breaks no completeness claim. A kind added without a line here
#: raises instead.
ARTIFACT_CLASS: dict[str, str] = {
    # What the reader announced -- the evidence a reader assertion is decided from.
    "SPEECH_TRANSCRIPT": "READER_SPEECH",
    # Visual capture, explicitly supplementary.
    "SCREENSHOT": "SCREEN_RECORDING",
    # Timings, internal state, the supervisor's own log. None decides a verdict.
    "DIAGNOSTIC_LOG": "DIAGNOSTIC",
    "RUNNER_JOURNAL": "DIAGNOSTIC",
    "PREFLIGHT_RECORD": "DIAGNOSTIC",
    # ACTION_TRACE and EFFECT_RECEIPT are deliberately *not* DIAGNOSTIC. A trace is
    # the record of what was done to somebody's application and a receipt is proof
    # an effect occurred -- both are what an ambiguous action is adjudicated from.
    # Classing them as diagnostics would make deleting the proof of an OS action a
    # routine log cleanup breaking no completeness claim, which is the inference
    # INV-09 exists to prevent.
    "ACTION_TRACE": "READER_SPEECH",
    "EFFECT_RECEIPT": "READER_SPEECH",
}

#: Which class each canonical event type's payload belongs to, on the same terms.
EVENT_CLASS: dict[str, str] = {
    "READER_OBSERVATION": "READER_SPEECH",
    "ASSERTION_OBSERVATION": "READER_SPEECH",
    "ACTION_INTENT": "READER_SPEECH",
    "ACTION_RESULT": "READER_SPEECH",
    "EFFECT_RECEIPT": "READER_SPEECH",
    "PREFLIGHT_RESULT": "DIAGNOSTIC",
    "BUDGET_EVENT": "DIAGNOSTIC",
    "INTERRUPTION": "DIAGNOSTIC",
    # A run's own boundaries carry no captured content -- they are structural, and a
    # chain missing them could not be read at all. Mapped to a name nothing deletes.
    "RUN_STARTED": "STRUCTURAL",
    "RUN_FINISHED": "STRUCTURAL",
}

#: The placeholder left in a cleared payload.
#:
#: JSON rather than SQL NULL, because every reader of this column expects an object
#: and a NULL would turn "deleted" into a driver error somewhere downstream. The
#: text says what happened, so somebody looking at one row knows without consulting
#: a policy document.
DELETED_PAYLOAD = {
    "deleted": True,
    "meaning": (
        "This event's content was deleted under a retention policy. Its digest and "
        "its place in the hash chain are unchanged, so the chain still verifies and "
        "nothing was inserted or removed -- but what it recorded cannot be read."
    ),
}


@dataclass(frozen=True, slots=True)
class DeletionReport:
    """What a deletion removed, and what it could not.

    The second half is why this type exists. A boolean return would let a caller say
    "deleted" and mean it, and the one thing nobody may say after this operation is
    that the data is gone without qualification.
    """

    deletion_id: str
    requested_by: str
    requested_at: str
    classes: tuple[str, ...]
    run_id: str
    attempt_id: str | None

    artifact_bytes_deleted: int
    artifact_tombstones_kept: int
    event_payloads_cleared: int

    completeness_invalidated: bool
    """True when a class whose deletion breaks a completeness claim was included.

    Reported rather than left to the caller, because the inference runs the wrong
    way in practice: a run with less evidence looks tidier, and finalization counts
    a deleted required artifact as missing precisely so that it does not.
    """

    retained: tuple[str, ...] = field(default_factory=tuple)

    @property
    def summary(self) -> str:
        consequence = (
            "Completeness claims that depended on this evidence are now unsupported. "
            if self.completeness_invalidated
            else "No completeness claim depended on these classes. "
        )
        return (
            f"deleted the bytes of {self.artifact_bytes_deleted} artifact(s) and "
            f"cleared {self.event_payloads_cleared} event payload(s) for "
            f"{', '.join(self.classes)}. {self.artifact_tombstones_kept} tombstone "
            f"row(s) were kept on purpose. {consequence}"
            f"{len(self.retained)} thing(s) were not reached -- see `retained`."
        )


def _assert_classes_known(classes: tuple[str, ...]) -> None:
    if not classes:
        raise DeletionError(
            "name at least one evidence class. A deletion of nothing reports success "
            "and removes nothing, which is the one outcome a person invoking this "
            "must never be handed."
        )
    unknown = sorted(set(classes) - set(CLASS_DEFINITIONS))
    if unknown:
        raise UnknownEvidenceClass(
            f"no retention class named {', '.join(unknown)}. Refused rather than read "
            "as nothing to delete: a typo in a class name would otherwise report a "
            "successful deletion that removed nothing at all."
        )


def _retained_statements(
    conn: psycopg.Connection[dict[str, Any]],
    *,
    classes: tuple[str, ...],
    run_id: str,
    backup_window_days: int,
) -> tuple[str, ...]:
    """Everything this deletion did not reach, in words, every time.

    Assembled from actual state rather than written as boilerplate: the export
    sentence names how many exist and how many were downloaded, because "somebody
    may hold a copy" and "four people downloaded it" call for different
    conversations.
    """
    exports = conn.execute(
        """
        SELECT count(DISTINCT e.id) AS built, count(d.id) AS downloads
          FROM evidence_export e
          LEFT JOIN evidence_export_download d ON d.export_id = e.id
         WHERE e.run_id = %s
        """,
        (run_id,),
    ).fetchone()
    built = int(exports["built"]) if exports else 0
    downloads = int(exports["downloads"]) if exports else 0

    statements = [
        "Audit metadata is retained: this deletion's own record, and a tombstone row "
        "for every artifact whose bytes were removed. Deleting those would make the "
        "deletion unprovable, and an erasure nobody can demonstrate is worse than "
        "none.",
        f"Backups taken in the last {backup_window_days} day(s) still contain this "
        "evidence. A restore from one brings it back. Nothing here reaches a backup, "
        "and nothing should: a deletion that could reach into backups could also be "
        "used to rewrite them.",
        "The hash chain is intact and each cleared event keeps its digest, so a "
        "reader can still prove no event was inserted or removed -- and can see "
        "exactly which ones they can no longer read.",
    ]

    if built == 0:
        statements.append(
            "No export of this run was ever built, so no bundle of it left this "
            "system by that route."
        )
    else:
        statements.append(
            f"{built} export(s) of this run were built and downloaded {downloads} "
            "time(s). A downloaded bundle is the recipient's copy and cannot be "
            "recalled. It still contains whatever it contained when it was made."
        )

    if "REVIEW_RECORD" not in classes:
        statements.append(
            "Human review records were not included. A person's assessment and its "
            "attribution record a decision somebody made, and deleting the evidence "
            "a review rested on does not unmake the review."
        )

    return tuple(statements)


def delete_evidence(
    conn: psycopg.Connection[dict[str, Any]],
    store: ArtifactStore,
    *,
    workspace_id: str,
    run_id: str,
    classes: tuple[str, ...],
    reason: str,
    requested_by: str,
    attempt_id: str | None = None,
    backup_window_days: int = 35,
    now: datetime | None = None,
) -> DeletionReport:
    """Delete the named evidence classes for one run, or one attempt of it.

    Scoped to a run rather than offered workspace-wide. A workspace-wide erasure is
    a different operation with a different blast radius, and the one-button version
    is how somebody deletes a year of evidence meaning to delete a week.

    `reason` is required and recorded. A deletion nobody can explain later is the
    one an auditor asks about first.
    """
    moment = now or datetime.now(UTC)
    _assert_classes_known(classes)

    if not reason.strip():
        raise DeletionError(
            "state a reason. It is recorded with the deletion, and a deletion nobody "
            "can explain is the one an auditor asks about first."
        )

    scope = (
        "SELECT id, kind, object_key, redacted_object_key FROM evidence_artifact "
        " WHERE run_id = %s AND retention <> 'DELETED'"
    )
    params: list[Any] = [run_id]
    if attempt_id is not None:
        scope += " AND attempt_id = %s"
        params.append(attempt_id)

    deleted_bytes = 0
    for row in conn.execute(scope + " FOR UPDATE", params).fetchall():
        kind = str(row["kind"])
        if kind not in ARTIFACT_CLASS:
            raise DeletionError(
                f"artifact kind {kind!r} has no retention class. Refused rather than "
                "skipped: an unclassified artifact surviving a deletion that "
                "reported success is the failure this operation exists to avoid."
            )
        if ARTIFACT_CLASS[kind] not in classes:
            continue

        store.delete(key=str(row["object_key"]))
        if row["redacted_object_key"] is not None:
            # The redacted view is a derived object. Leaving it would mean removing
            # the original and keeping a copy of it with some values masked -- still
            # the thing somebody asked to have removed.
            store.delete(key=str(row["redacted_object_key"]))
        conn.execute(
            """
            UPDATE evidence_artifact
               SET retention = 'DELETED', retention_changed_at = %s,
                   retention_reason = %s
             WHERE id = %s
            """,
            (to_rfc3339_utc(moment), reason, row["id"]),
        )
        deleted_bytes += 1

    placeholder = json.dumps(DELETED_PAYLOAD)
    event_types = [kind for kind, name in EVENT_CLASS.items() if name in classes]
    cleared = 0
    if event_types:
        # The chain survives: `payload` is replaced, `payload_digest` and
        # `previous_event_hash` are untouched.
        event_scope = (
            "UPDATE canonical_event SET payload = %s::jsonb "
            " WHERE run_id = %s AND event_type = ANY(%s) "
            "   AND payload <> %s::jsonb"
        )
        event_params: list[Any] = [placeholder, run_id, event_types, placeholder]
        if attempt_id is not None:
            event_scope += " AND attempt_id = %s"
            event_params.append(attempt_id)
        cleared = conn.execute(event_scope, event_params).rowcount

    invalidates = any(CLASS_DEFINITIONS[name][2] for name in classes)
    deletion_id = str(uuid.uuid4())

    conn.execute(
        """
        INSERT INTO evidence_deletion
            (id, workspace_id, run_id, attempt_id, evidence_classes, reason,
             requested_by, artifact_bytes_deleted, event_payloads_cleared,
             completeness_invalidated, requested_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            deletion_id,
            workspace_id,
            run_id,
            attempt_id,
            list(classes),
            reason,
            requested_by,
            deleted_bytes,
            cleared,
            invalidates,
            moment,
        ),
    )

    return DeletionReport(
        deletion_id=deletion_id,
        requested_by=requested_by,
        requested_at=to_rfc3339_utc(moment),
        classes=classes,
        run_id=run_id,
        attempt_id=attempt_id,
        artifact_bytes_deleted=deleted_bytes,
        artifact_tombstones_kept=deleted_bytes,
        event_payloads_cleared=cleared,
        completeness_invalidated=invalidates,
        retained=_retained_statements(
            conn,
            classes=classes,
            run_id=run_id,
            backup_window_days=backup_window_days,
        ),
    )


def deletions_for_run(
    conn: psycopg.Connection[dict[str, Any]], *, run_id: str
) -> list[dict[str, Any]]:
    """Every deletion recorded against this run, oldest first.

    Exists so an export and a reviewer can see that evidence was deliberately
    removed rather than never captured. "Absent" and "deleted" are different facts,
    and a reader who cannot tell them apart will assume whichever suits them.
    """
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT id, attempt_id, evidence_classes, reason, requested_by,
                   artifact_bytes_deleted, event_payloads_cleared,
                   completeness_invalidated, requested_at
              FROM evidence_deletion
             WHERE run_id = %s
             ORDER BY requested_at, id
            """,
            (run_id,),
        ).fetchall()
    ]


def backup_window_end(*, taken_at: datetime, retain_days: int) -> str:
    """When a backup containing deleted evidence stops existing.

    A date, not a reassurance. "Backups expire eventually" is what somebody says
    when they have not worked out the answer, and the person asking needs to know
    whether it is next week or next year.
    """
    return to_rfc3339_utc(taken_at + timedelta(days=retain_days))
