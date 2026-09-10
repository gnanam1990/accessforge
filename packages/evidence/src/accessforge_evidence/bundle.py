"""The evidence bundle format.

A bundle is what leaves the system, and the design question that matters is not what to put in it
but
what a reader can conclude from it. Three claims are kept apart throughout, because they are
routinely
collapsed into one:

1. **The bytes are intact.** Digests match. Checkable offline by anyone.
2. **The service attests this.** A signature over the manifest. Attribution to an issuer, nothing
   more — it says who assembled the bundle, not that what they assembled is true.
3. **A person could use the application.** Not established by any bundle, ever.

So `TrustLevel` is an enum rather than a boolean, and `FULLY_VERIFIABLE` is withheld whenever a
required input is absent. A bundle that had its transcripts redacted is still useful — a reviewer
can
read the chain, the identities and the outcome — but it cannot support recomputing the verdict, and
saying "verified" about it would be the most consequential lie this format could tell.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from accessforge_domain.canonical import canonicalize, digest

#: Bundle format version. A verifier that does not know a version refuses rather than guessing, so
#: this number changing is a deliberate compatibility decision.
BUNDLE_SCHEMA_VERSION = "1.0.0"

#: The canonicalization the digests in a bundle were computed with. Recorded because a verifier
#: computing digests with a different canonicalization would report tampering on an intact bundle,
#: and "the bundle is corrupt" is a much worse message than "this verifier is too old".
CANONICALIZATION_VERSION = "RFC8785+SHA256/1"


class TrustLevel(StrEnum):
    """What a reader may conclude about this bundle's contents.

    Not a boolean. The difference between "everything needed to recompute the verdict is here" and
    "enough to read what happened, with some inputs withheld" is the difference between a bundle
    that supports an independent conclusion and one that asks you to take the issuer's word.
    """

    FULLY_VERIFIABLE = "FULLY_VERIFIABLE"
    """Every input the verifier needs is present and intact. The verdict is recomputable."""

    LIMITED_DISCLOSURE = "LIMITED_DISCLOSURE"
    """Inputs were redacted or withheld. The chain and identities check out; the verdict cannot be
    recomputed from what is here, and this bundle does not claim it can."""

    INCOMPLETE = "INCOMPLETE"
    """Required evidence is missing rather than deliberately withheld. Not a disclosure decision —
    something that should exist does not."""


class EvidenceState(StrEnum):
    """What happened to one piece of evidence. Four states, never three.

    `UNAVAILABLE` is separate from `DELETED` on purpose: deleted is a decision somebody made and
    recorded, unavailable is a fact about right now. Collapsing them would let an outage read as a
    retention action, or a deletion read as a transient problem that might resolve itself.
    """

    RETAINED = "RETAINED"
    REDACTED = "REDACTED"
    DELETED = "DELETED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ArtifactEntry:
    kind: str
    producer_id: str
    content_digest: str
    size_bytes: int
    state: EvidenceState
    #: Present only when the raw bytes were replaced by a redacted view. A different digest, because
    #: one digest describing two byte streams makes later verification check the wrong one.
    redacted_digest: str | None = None
    included: bool = False
    """Whether the bytes travel inside the bundle. A digest without bytes is still a useful claim --
    a holder of the original can confirm the match -- but it is not self-contained evidence."""


@dataclass(frozen=True, slots=True)
class ProducerEntry:
    producer_id: str
    admitted_through: int
    closed_at_sequence: int | None
    source_record_digests: tuple[str, ...] = ()

    @property
    def closed(self) -> bool:
        return self.closed_at_sequence is not None


@dataclass(frozen=True, slots=True)
class ReviewEntry:
    """A human assessment, attributed and kept separate from the machine outcome (INV-12)."""

    review_id: str
    reviewer_id: str
    verdict: str
    observations: str
    limitations: str
    used_assistive_technology: bool
    submitted_at: str


@dataclass(frozen=True, slots=True)
class CompletenessDeclaration:
    """What the issuer says about the evidence set, in the issuer's own voice.

    Separate from the verifier's findings, which are computed by the reader. Both appear in a
    bundle,
    and a disagreement between them is interesting rather than impossible: it means the bundle was
    assembled under one set of facts and is being read under another.
    """

    canonical_chain_contiguous: bool
    all_required_producers_closed: bool
    all_required_artifacts_present: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Bundle:
    """One exported run, as data. Serialization and signing are separate concerns."""

    schema_version: str
    canonicalization_version: str
    workspace_id: str
    run_id: str
    attempt_id: str
    manifest_digest: str
    identities: dict[str, str]
    outcome: str
    outcome_reasons: tuple[str, ...]
    evaluator_version: str
    scope_statement: str
    events: tuple[dict[str, Any], ...]
    producers: tuple[ProducerEntry, ...]
    artifacts: tuple[ArtifactEntry, ...]
    reviews: tuple[ReviewEntry, ...]
    completeness: CompletenessDeclaration
    #: Baseline/candidate pair when this export is a repair comparison (INV-04).
    comparison: dict[str, Any] | None = None
    redaction_notes: tuple[str, ...] = field(default_factory=tuple)

    def trust_level(self) -> TrustLevel:
        """What a reader may conclude. Computed from the contents, never set by the issuer.

        Order matters: INCOMPLETE is checked before LIMITED_DISCLOSURE, because a bundle that is
        both
        missing required evidence *and* redacted is primarily missing evidence. Reporting it as a
        disclosure decision would describe an accident as a choice.
        """
        if not (
            self.completeness.canonical_chain_contiguous
            and self.completeness.all_required_producers_closed
            and self.completeness.all_required_artifacts_present
        ):
            return TrustLevel.INCOMPLETE
        if any(a.state in {EvidenceState.REDACTED, EvidenceState.DELETED} for a in self.artifacts):
            return TrustLevel.LIMITED_DISCLOSURE
        if any(not a.included for a in self.artifacts):
            return TrustLevel.LIMITED_DISCLOSURE
        return TrustLevel.FULLY_VERIFIABLE

    def manifest(self) -> dict[str, Any]:
        """The part that gets signed.

        Digests and identities, not content. Signing the content would make the signature a function
        of megabytes of transcript, and the property wanted is that a reader can check the signature
        first and only then decide whether to trust the bytes it covers.
        """
        return {
            "schemaVersion": self.schema_version,
            "canonicalizationVersion": self.canonicalization_version,
            "workspaceId": self.workspace_id,
            "runId": self.run_id,
            "attemptId": self.attempt_id,
            "manifestDigest": self.manifest_digest,
            "identities": dict(sorted(self.identities.items())),
            "outcome": self.outcome,
            "evaluatorVersion": self.evaluator_version,
            "trustLevel": str(self.trust_level()),
            "eventChainDigest": digest({"events": [_event_for_digest(e) for e in self.events]}),
            "producers": [
                {
                    "producerId": p.producer_id,
                    "admittedThrough": p.admitted_through,
                    "closedAtSequence": p.closed_at_sequence,
                }
                for p in sorted(self.producers, key=lambda p: p.producer_id)
            ],
            "artifacts": [
                {
                    "kind": a.kind,
                    "producerId": a.producer_id,
                    "contentDigest": a.content_digest,
                    "state": str(a.state),
                    "redactedDigest": a.redacted_digest,
                    "included": a.included,
                }
                for a in sorted(self.artifacts, key=lambda a: (a.kind, a.content_digest))
            ],
            "reviewIds": sorted(r.review_id for r in self.reviews),
            "completeness": {
                "canonicalChainContiguous": self.completeness.canonical_chain_contiguous,
                "allRequiredProducersClosed": self.completeness.all_required_producers_closed,
                "allRequiredArtifactsPresent": self.completeness.all_required_artifacts_present,
                "reasons": list(self.completeness.reasons),
            },
        }

    def manifest_bytes(self) -> bytes:
        """Canonical bytes of the manifest, which is what a signature is over.

        RFC8785 canonical JSON rather than `json.dumps`. A signature over a non-canonical encoding
        verifies only against the exact byte sequence that was signed, so a verifier that serialized
        the same data with different key ordering would report a forgery on an intact bundle.
        """
        # `canonicalize` returns the canonical *string*; the signature is over its UTF-8 bytes.
        # Encoding here rather than at each call site means every signer and verifier signs the
        # same byte sequence, which is the only thing a signature is actually over.
        return canonicalize(self.manifest()).encode("utf-8")


def _event_for_digest(event: dict[str, Any]) -> dict[str, Any]:
    """One canonical event, reduced to the fields a chain digest covers.

    `receivedTime` is excluded, matching CONTRACTS section 7: it is assigned by trusted ingestion
    and
    is not part of what the producer attested. Including it would make the chain digest depend on
    when
    the service happened to process each record, so a restore from backup could legitimately
    produce a
    different digest for identical evidence.
    """
    return {
        "sequence": event["sequence"],
        "eventId": str(event["event_id"]),
        "eventType": event["event_type"],
        "previousEventHash": event["previous_event_hash"],
        "payloadDigest": event["payload_digest"],
        "sourceTime": str(event["source_time"]),
    }


def to_json(bundle: Bundle) -> str:
    """Human-readable bundle document. Not the signing input; `manifest_bytes` is."""
    return json.dumps(
        {
            "manifest": bundle.manifest(),
            "events": [_event_for_digest(e) for e in bundle.events],
            "producerSourceDigests": {
                p.producer_id: list(p.source_record_digests) for p in bundle.producers
            },
            "reviews": [
                {
                    "reviewId": r.review_id,
                    "reviewerId": r.reviewer_id,
                    "verdict": r.verdict,
                    "observations": r.observations,
                    "limitations": r.limitations,
                    "usedAssistiveTechnology": r.used_assistive_technology,
                    "submittedAt": r.submitted_at,
                    "establishedBy": "a person's judgement, attributable to them",
                }
                for r in bundle.reviews
            ],
            "machineOutcome": {
                "outcome": bundle.outcome,
                "reasons": list(bundle.outcome_reasons),
                "evaluatorVersion": bundle.evaluator_version,
                "establishedBy": "deterministic evaluation of the recorded evidence",
            },
            "comparison": bundle.comparison,
            "redactionNotes": list(bundle.redaction_notes),
            "scopeStatement": bundle.scope_statement,
            "whatThisDoesNotEstablish": [
                "that the application is usable by people with disabilities in general",
                "that it works with assistive technologies other than the one it names",
                "that untested journeys behave the same way",
                "that any legal or regulatory obligation is satisfied",
                "that the signature over this manifest makes its contents true, rather than "
                "attributing them to an issuer",
            ],
        },
        indent=2,
        sort_keys=True,
    )
