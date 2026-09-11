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

**Deletion is two phases with a commit between them.** The first version called the
object store inside the caller's transaction, and that ordering can produce the
exact failure this feature exists to prevent: an earlier object deletes, a later
one raises, the transaction rolls back, and the store cannot -- leaving bytes gone,
the artifact still RETAINED, no deletion record, and a *complete* evidence set
reported for a run whose evidence no longer exists.

So `record_deletion` marks the artifacts, writes the record and enqueues every
object key; the caller commits; `purge_pending_objects` then deletes the bytes.
The inconsistency window points the safe way now: after the commit the database
says deleted while some bytes may remain, completeness is already invalidated,
nobody is told the bytes are gone until they are, and a retry finishes the job.

Two more decisions matter before changing anything:

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

    objects_enqueued: int = 0
    """Object keys this deletion promised to remove."""

    objects_purged: int = 0
    """How many of those the store has actually released."""

    objects_still_present: int = 0
    """How many it has not.

    A measured count, not `enqueued - purged`. A purge pass takes at most `limit`
    keys, so a pass that removed every key it looked at can still leave a queue
    behind it; subtracting would report that backlog as zero. This number is read
    from the queue, which is the only place that knows.
    """

    statements: tuple[str, ...] = field(default_factory=tuple)
    """What this deletion could not reach, independent of the object store."""

    @property
    def retained(self) -> tuple[str, ...]:
        """Everything this deletion did not reach, including bytes still in the store.

        Composed rather than stored, so the pending-object line cannot go stale or
        be forgotten by a caller who never ran a purge. That caller is the common
        one -- the route answers before the purge starts -- and it is exactly the
        case where omitting the line would read as a completed erasure.
        """
        if self.objects_still_present <= 0:
            return self.statements
        return self.statements + (
            f"{self.objects_still_present} object(s) are recorded as deleted and the "
            "object store has not released them yet. The database already reports "
            "this evidence as deleted and the completeness claim is already "
            "invalidated; the bytes stay queued until a purge retry removes them "
            "-- POST /runs/{runId}/deletions/{deletionId}/retry. Until then, they "
            "exist.",
        )

    def with_purge(self, outcome: PurgeOutcome) -> DeletionReport:
        """The same report, knowing how much of the store actually released.

        Returned rather than mutated because a report is a statement about what
        happened, and a statement that can be edited after the fact is not one.
        """
        from dataclasses import replace

        if outcome.scope != self.deletion_id:
            # A sweep across every pending deletion counts other deletions' backlogs
            # too. Folding that into this report would either invent objects this
            # deletion never enqueued or, worse, report somebody else's cleared queue
            # as this one's -- a deletion claiming bytes are gone on another's work.
            raise DeletionError(
                "this purge outcome measured "
                f"{outcome.scope or 'every pending deletion'}, not deletion "
                f"{self.deletion_id}. A report may only quote a count taken for "
                "itself."
            )
        return replace(
            self,
            objects_purged=self.objects_purged + outcome.purged,
            objects_still_present=outcome.still_pending,
        )

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
            f"{self.objects_purged} of {self.objects_enqueued} object(s) have been "
            f"released by the store. {len(self.retained)} thing(s) were not reached "
            "-- see `retained`."
        )


@dataclass(frozen=True, slots=True)
class PurgeOutcome:
    """What one pass of the object purge achieved.

    `still_pending` above zero is not an error. The database has already committed
    saying the evidence is deleted; these are bytes the store has not yet released,
    and the next pass takes them.
    """

    purged: int
    still_pending: int
    """Everything left in the queue for this scope: failures *and* keys this pass
    never reached because it stopped at its limit."""

    keys_failed: tuple[str, ...] = field(default_factory=tuple)
    """The keys this pass tried and could not delete, with their errors recorded."""

    scope: str | None = None
    """The deletion this pass measured, or None for a sweep across all of them.

    Carried so :meth:`DeletionReport.with_purge` can refuse a count taken for
    something else rather than quoting it as its own.
    """


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


def record_deletion(
    conn: psycopg.Connection[dict[str, Any]],
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
    """Phase one: mark, record, and enqueue. Touches no object store.

    The caller commits after this and then calls :func:`purge_pending_objects`. The
    separation is the correction described in the module docstring: the database
    must be the thing that commits first, so the only window is one where the
    record says deleted and some bytes survive -- never the reverse.

    Scoped to a run rather than offered workspace-wide. A workspace-wide erasure is
    a different operation with a different blast radius, and the one-button version
    is how somebody deletes a year of evidence meaning to delete a week.
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

    deletion_id = str(uuid.uuid4())
    marked: list[tuple[str, str]] = []
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

        artifact_id = str(row["id"])
        marked.append((artifact_id, str(row["object_key"])))
        if row["redacted_object_key"] is not None:
            # The redacted view is a derived object. Leaving it would mean removing
            # the original and keeping a copy of it with some values masked -- still
            # the thing somebody asked to have removed.
            marked.append((artifact_id, str(row["redacted_object_key"])))

        conn.execute(
            """
            UPDATE evidence_artifact
               SET retention = 'DELETED', retention_changed_at = %s,
                   retention_reason = %s
             WHERE id = %s
            """,
            (to_rfc3339_utc(moment), reason, artifact_id),
        )

    # Every event type actually present on this run has to have a class, the same way every
    # artifact kind does. `canonical_event.event_type` carries no CHECK constraint, so an event
    # type added later without a mapping would not be refused here -- it would simply not appear
    # in `event_types` below and its payload would survive a deletion that reported success. The
    # artifact path already refuses an unmapped kind; this is the same failure and gets the same
    # answer.
    present = {
        str(row["event_type"])
        for row in conn.execute(
            "SELECT DISTINCT event_type FROM canonical_event WHERE run_id = %s", (run_id,)
        ).fetchall()
    }
    unclassified = sorted(present - set(EVENT_CLASS))
    if unclassified:
        raise DeletionError(
            f"event type(s) {', '.join(unclassified)} have no retention class. Refused rather "
            "than skipped: an unclassified payload surviving a deletion that reported success "
            "is the failure this operation exists to avoid."
        )

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
    artifacts = len({artifact_id for artifact_id, _ in marked})

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
            artifacts,
            cleared,
            invalidates,
            moment,
        ),
    )

    for artifact_id, object_key in marked:
        conn.execute(
            """
            INSERT INTO evidence_object_purge
                (id, workspace_id, deletion_id, artifact_id, object_key, enqueued_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (deletion_id, object_key) DO NOTHING
            """,
            (str(uuid.uuid4()), workspace_id, deletion_id, artifact_id, object_key, moment),
        )

    return DeletionReport(
        deletion_id=deletion_id,
        requested_by=requested_by,
        requested_at=to_rfc3339_utc(moment),
        classes=classes,
        run_id=run_id,
        attempt_id=attempt_id,
        artifact_bytes_deleted=artifacts,
        artifact_tombstones_kept=artifacts,
        event_payloads_cleared=cleared,
        completeness_invalidated=invalidates,
        objects_enqueued=len(marked),
        objects_purged=0,
        objects_still_present=len(marked),
        statements=_retained_statements(
            conn,
            classes=classes,
            run_id=run_id,
            backup_window_days=backup_window_days,
        ),
    )


def purge_pending_objects(
    conn: psycopg.Connection[dict[str, Any]],
    store: ArtifactStore,
    *,
    deletion_id: str | None = None,
    limit: int = 200,
    now: datetime | None = None,
) -> PurgeOutcome:
    """Phase two: delete the bytes a committed deletion promised to remove.

    Called after the caller commits phase one, and safe to call again: a key that
    failed stays pending with its error, and one already purged is skipped. That is
    what makes an object store outage a delay rather than a silent divergence.

    A failure here does **not** raise. The database has already committed saying the
    evidence is deleted, and raising would tell a caller the deletion failed when
    what actually happened is that some bytes are still queued for removal. The
    outcome says how many remain, and the report says so in words.
    """
    moment = now or datetime.now(UTC)
    if deletion_id is not None and (
        conn.execute("SELECT 1 FROM evidence_deletion WHERE id = %s", (deletion_id,)).fetchone()
        is None
    ):
        # Loud, because the quiet version of this is indistinguishable from success. A purge asked
        # about a deletion this connection cannot see finds no queued keys and reports nothing
        # pending -- which reads exactly like "the store released everything". That is how bytes
        # survive a deletion that reported a clean result. It happens when phase one has not
        # committed yet, or is in another workspace; both are bugs in the caller's ordering.
        raise DeletionError(
            f"deletion {deletion_id} is not visible on this connection, so there is nothing "
            "here to purge. Reported rather than treated as an empty queue: an empty queue "
            "and an invisible one are the same answer and opposite facts. Commit phase one "
            "before purging."
        )
    scope = "SELECT id, object_key FROM evidence_object_purge WHERE purged_at IS NULL"
    params: list[Any] = []
    if deletion_id is not None:
        scope += " AND deletion_id = %s"
        params.append(deletion_id)
    scope += " ORDER BY enqueued_at LIMIT %s"
    params.append(limit)

    purged = 0
    failures: list[str] = []
    for row in conn.execute(scope, params).fetchall():
        try:
            store.delete(key=str(row["object_key"]))
        except Exception as exc:  # noqa: BLE001 - any store failure is a retry, not a crash
            conn.execute(
                "UPDATE evidence_object_purge SET attempts = attempts + 1, last_error = %s "
                " WHERE id = %s",
                (f"{type(exc).__name__}: {exc}"[:500], row["id"]),
            )
            failures.append(str(row["object_key"]))
            continue
        conn.execute(
            "UPDATE evidence_object_purge SET purged_at = %s, attempts = attempts + 1, "
            "    last_error = NULL WHERE id = %s",
            (moment, row["id"]),
        )
        purged += 1

    # Counted, not inferred. `limit` caps what one pass looks at, so "everything I
    # tried succeeded" and "the queue is empty" are different facts, and only the
    # second one means the bytes are gone.
    return PurgeOutcome(
        purged=purged,
        still_pending=pending_purges(conn, deletion_id=deletion_id),
        keys_failed=tuple(failures),
        scope=deletion_id,
    )


def purge_until_drained(
    conn: psycopg.Connection[dict[str, Any]],
    store: ArtifactStore,
    *,
    deletion_id: str,
    limit: int = 200,
    max_passes: int = 50,
    now: datetime | None = None,
) -> PurgeOutcome:
    """Purge one deletion's queue until it stops shrinking.

    One pass takes at most `limit` keys, so a single call leaves a backlog whenever
    the queue is longer than that -- and nothing re-enqueues it, because the
    artifacts are already DELETED and the scope query skips them. Without this the
    "a later pass finishes it" the report promises has nothing to do it.

    Stops on the first pass that removes nothing, which is what a store outage looks
    like: the keys stay pending with their errors and the count stays honest rather
    than the loop spinning against a store that is down. `max_passes` bounds the
    work a single request can do; whatever is left is reported, not hidden.
    """
    total = 0
    outcome = PurgeOutcome(purged=0, still_pending=pending_purges(conn, deletion_id=deletion_id))
    for _ in range(max_passes):
        if outcome.still_pending == 0:
            break
        outcome = purge_pending_objects(conn, store, deletion_id=deletion_id, limit=limit, now=now)
        total += outcome.purged
        if outcome.purged == 0:
            break
    return PurgeOutcome(
        purged=total,
        still_pending=pending_purges(conn, deletion_id=deletion_id),
        keys_failed=outcome.keys_failed,
        scope=deletion_id,
    )


def pending_purges(
    conn: psycopg.Connection[dict[str, Any]], *, deletion_id: str | None = None
) -> int:
    """How many object keys a committed deletion still promises to remove.

    The number an operator needs and a report must not round to zero. While it is
    above zero the database reports evidence as deleted that the store still holds.
    """
    scope = "SELECT count(*) AS n FROM evidence_object_purge WHERE purged_at IS NULL"
    params: list[Any] = []
    if deletion_id is not None:
        scope += " AND deletion_id = %s"
        params.append(deletion_id)
    row = conn.execute(scope, params).fetchone()
    return int(row["n"]) if row else 0


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
