"""The offline verifier.

No account, no network, no database. It takes an archive and a trust root and reports what it could
establish — which is a different thing from reporting pass or fail, and the difference is the
point of
the module.

The structure that makes it honest is that findings are a *list*, each naming one check and its
outcome, and the overall result is derived from them. A verifier that returned a boolean would force
every partial situation into one of two answers, and the situations that actually arise are partial:
a bundle whose chain is intact and whose transcripts were redacted, a bundle signed with a key the
reader obtained from the bundle itself, a bundle missing an observer's closing watermark.

Every check is run even after one fails. Short-circuiting would make a reader fix one problem, re-
run,
and discover the next — and somebody deciding whether to trust an export needs the whole picture in
one pass.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from accessforge_domain.canonical import digest

from .archive import UnsafeArchive, read_archive
from .bundle import BUNDLE_SCHEMA_VERSION, CANONICALIZATION_VERSION, TrustLevel
from .signing import Attestation, TrustRoot, check_attestation


class CheckOutcome(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    """The bundle does not contain what this check examines, and says so deliberately. A redacted
    transcript is not a failed transcript check."""

    UNSUPPORTED = "UNSUPPORTED"
    """This verifier does not know how to check it. Reported rather than skipped: a skipped check
    reads as a passed one in any summary that counts failures."""


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    outcome: CheckOutcome
    detail: str


@dataclass(frozen=True, slots=True)
class VerificationReport:
    findings: tuple[Finding, ...] = field(default_factory=tuple)
    trust_level: TrustLevel | None = None
    attributed_to: str | None = None
    independently_trusted: bool = False

    @property
    def failed(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.outcome is CheckOutcome.FAILED)

    @property
    def integrity_intact(self) -> bool:
        """Whether every check that ran and applied passed.

        Deliberately not called `verified`. "Integrity intact" is a claim about bytes and chains;
        "verified" is a word readers hear as a claim about the application, and this function cannot
        make that one.
        """
        return not self.failed

    @property
    def exit_code(self) -> int:
        """Nonzero whenever any integrity check failed, as the module prompt requires."""
        return 1 if self.failed else 0

    def human_report(self) -> str:
        lines = ["AccessForge evidence verification", "=" * 34, ""]
        by_outcome = {o: [f for f in self.findings if f.outcome is o] for o in CheckOutcome}

        for outcome, heading in (
            (CheckOutcome.FAILED, "FAILED"),
            (CheckOutcome.PASSED, "passed"),
            (CheckOutcome.NOT_APPLICABLE, "not applicable (deliberately absent)"),
            (CheckOutcome.UNSUPPORTED, "UNSUPPORTED by this verifier"),
        ):
            entries = by_outcome[outcome]
            if not entries:
                continue
            lines.append(f"{heading}: {len(entries)}")
            for finding in entries:
                lines.append(f"  - {finding.check}: {finding.detail}")
            lines.append("")

        lines.append(f"Trust level: {self.trust_level or 'UNDETERMINED'}")
        if self.attributed_to:
            lines.append(
                f"Attributed to: {self.attributed_to}"
                + ("" if self.independently_trusted else " (key came from inside the bundle)")
            )
        lines.append("")
        lines.append("What this verification does NOT establish:")
        for claim in (
            "that the application is usable by people with disabilities in general",
            "that the physical events the evidence describes actually occurred -- the digests and "
            "signature attest what the issuing service recorded and attributed, not physical truth",
            "that untested journeys behave the same way",
            "that any legal or regulatory obligation is satisfied",
        ):
            lines.append(f"  - {claim}")
        return "\n".join(lines)


def verify_archive(path: str, *, trust_root: TrustRoot | None = None) -> VerificationReport:
    """Verify a bundle archive. The only entry point a reader needs.

    ``trust_root`` is optional, and its absence is reported rather than treated as a reason to skip
    the signature. A verifier silent about an unchecked signature is a verifier that prints a clean
    report for an unsigned bundle.
    """
    findings: list[Finding] = []

    try:
        members = read_archive(path)
    except UnsafeArchive as exc:
        return VerificationReport(
            findings=(
                Finding(
                    "archive.safety",
                    CheckOutcome.FAILED,
                    f"the archive was refused before anything was read from it: {exc}",
                ),
            )
        )
    findings.append(
        Finding("archive.safety", CheckOutcome.PASSED, "member names, sizes and ratios are bounded")
    )

    try:
        document: dict[str, Any] = json.loads(members["bundle.json"])
        manifest_bytes = members["manifest.canonical.json"]
        attestation_doc = json.loads(members["attestation.json"])
    except (json.JSONDecodeError, KeyError) as exc:
        findings.append(
            Finding("bundle.parse", CheckOutcome.FAILED, f"bundle documents are unreadable: {exc}")
        )
        return VerificationReport(findings=tuple(findings))

    manifest = document.get("manifest", {})

    findings.extend(_check_versions(manifest))
    findings.append(_check_manifest_matches_canonical_bytes(manifest, manifest_bytes))
    findings.append(_check_event_chain(document, manifest))
    findings.extend(_check_producers(manifest))
    findings.extend(_check_artifacts(manifest, members))
    findings.append(_check_identities(manifest))
    findings.append(_check_outcome_present(document))
    findings.append(_check_reviews(document, manifest))

    attribution = _check_signature(manifest_bytes, attestation_doc, trust_root)
    findings.append(attribution[0])

    declared = manifest.get("trustLevel")
    findings.append(_check_trust_level_not_overclaimed(manifest, declared))

    return VerificationReport(
        findings=tuple(findings),
        trust_level=TrustLevel(declared) if declared in set(TrustLevel) else None,
        attributed_to=attribution[1],
        independently_trusted=attribution[2],
    )


def _check_versions(manifest: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    schema = manifest.get("schemaVersion")
    if schema != BUNDLE_SCHEMA_VERSION:
        findings.append(
            Finding(
                "bundle.schemaVersion",
                CheckOutcome.UNSUPPORTED,
                f"the bundle is schema {schema} and this verifier understands "
                f"{BUNDLE_SCHEMA_VERSION}. Reported rather than attempted: a verifier guessing "
                "at a format it does not know would report absences as tampering.",
            )
        )
    else:
        findings.append(Finding("bundle.schemaVersion", CheckOutcome.PASSED, str(schema)))

    canonicalization = manifest.get("canonicalizationVersion")
    if canonicalization != CANONICALIZATION_VERSION:
        findings.append(
            Finding(
                "bundle.canonicalizationVersion",
                CheckOutcome.UNSUPPORTED,
                f"digests were computed with {canonicalization} and this verifier computes "
                f"{CANONICALIZATION_VERSION}. Every digest comparison below would fail for that "
                "reason alone, which would read as tampering on an intact bundle.",
            )
        )
    else:
        findings.append(
            Finding("bundle.canonicalizationVersion", CheckOutcome.PASSED, str(canonicalization))
        )
    return findings


def _check_manifest_matches_canonical_bytes(
    manifest: dict[str, Any], manifest_bytes: bytes
) -> Finding:
    """The readable manifest and the signed bytes must be the same document.

    Without this a bundle could carry a benign `bundle.json` for a human and signed canonical bytes
    describing something else. The signature would verify, the human would read the wrong thing, and
    every other check here would examine the document that was not signed.
    """
    try:
        signed = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        return Finding("manifest.signedBytes", CheckOutcome.FAILED, f"unreadable: {exc}")
    if digest(signed) != digest(manifest):
        return Finding(
            "manifest.signedBytes",
            CheckOutcome.FAILED,
            "the readable manifest and the signed canonical bytes are different documents. A "
            "signature over one says nothing about the other, and the rest of this report would "
            "describe whichever happened to be read.",
        )
    return Finding(
        "manifest.signedBytes", CheckOutcome.PASSED, "the signed bytes are the manifest shown"
    )


def _check_event_chain(document: dict[str, Any], manifest: dict[str, Any]) -> Finding:
    events = document.get("events", [])
    if not events:
        return Finding("events.chain", CheckOutcome.FAILED, "the bundle contains no events")

    sequences = [int(e["sequence"]) for e in events]
    if sequences != sorted(sequences):
        return Finding("events.chain", CheckOutcome.FAILED, "events are not in sequence order")
    expected = list(range(1, len(sequences) + 1))
    if sequences != expected:
        missing = sorted(set(expected) - set(sequences))
        return Finding(
            "events.chain",
            CheckOutcome.FAILED,
            f"the canonical sequence has gaps at {missing}; events are missing from the middle of "
            "the attempt",
        )

    recomputed = digest({"events": events})
    if recomputed != manifest.get("eventChainDigest"):
        return Finding(
            "events.chain",
            CheckOutcome.FAILED,
            "the event chain does not hash to the digest in the manifest; events were added, "
            "removed or altered after signing",
        )
    return Finding(
        "events.chain",
        CheckOutcome.PASSED,
        f"{len(events)} events, contiguous from 1, hashing to the manifest digest",
    )


def _check_producers(manifest: dict[str, Any]) -> list[Finding]:
    """Each producer's closing watermark, checked separately from the chain.

    This is the check that a contiguous chain does not cover. The sequencer assigns consecutive
    positions to whatever it admits, so a producer that stopped sending halfway leaves a perfect
    chain and half the evidence. A canonical chain missing an observer tail is incomplete (INV-06).
    """
    producers = manifest.get("producers", [])
    if not producers:
        return [
            Finding(
                "producers.watermarks",
                CheckOutcome.FAILED,
                "the manifest names no producers, so there is no tail to be missing and no way to "
                "tell whether anything is",
            )
        ]

    findings: list[Finding] = []
    for producer in producers:
        name = producer.get("producerId", "?")
        closed_at = producer.get("closedAtSequence")
        admitted = producer.get("admittedThrough")
        if closed_at is None:
            findings.append(
                Finding(
                    f"producers.watermarks[{name}]",
                    CheckOutcome.FAILED,
                    "this producer never closed its stream. The canonical chain can be perfectly "
                    "contiguous and still be missing everything this producer had left to send.",
                )
            )
        elif closed_at != admitted:
            findings.append(
                Finding(
                    f"producers.watermarks[{name}]",
                    CheckOutcome.FAILED,
                    f"closed at {closed_at} but {admitted} records were admitted; the watermark "
                    "and the admitted tail disagree",
                )
            )
        else:
            findings.append(
                Finding(
                    f"producers.watermarks[{name}]",
                    CheckOutcome.PASSED,
                    f"closed at {closed_at}, matching the admitted tail",
                )
            )
    return findings


def _check_artifacts(manifest: dict[str, Any], members: dict[str, bytes]) -> list[Finding]:
    """Artifact digests, against the bytes when they travel and by declaration when they do not.

    A digest with no bytes is not a failure. It is a claim a holder of the original can check, and
    reporting it as a failure would make every limited-disclosure bundle look tampered with. It is
    also not a pass: the distinction is what NOT_APPLICABLE exists for.
    """
    artifacts = manifest.get("artifacts", [])
    if not artifacts:
        return [
            Finding(
                "artifacts.digests",
                CheckOutcome.NOT_APPLICABLE,
                "the bundle declares no artifacts",
            )
        ]

    from .archive import ALLOWED_MEMBERS  # noqa: F401 - documents where names come from

    findings: list[Finding] = []
    for artifact in artifacts:
        kind = artifact.get("kind", "?")
        expected = artifact.get("contentDigest", "")
        state = artifact.get("state")
        member = f"artifacts/{expected}"

        if state == "DELETED":
            findings.append(
                Finding(
                    f"artifacts.digests[{kind}]",
                    CheckOutcome.NOT_APPLICABLE,
                    "the bytes were deleted and the record says so. The evidence set is smaller "
                    "than it was, which is recorded rather than hidden (INV-15).",
                )
            )
            continue
        if not artifact.get("included"):
            findings.append(
                Finding(
                    f"artifacts.digests[{kind}]",
                    CheckOutcome.NOT_APPLICABLE,
                    f"declared as {expected[:12]}… but not included in this bundle. A holder of "
                    "the original can confirm the match; this verifier cannot, and says so.",
                )
            )
            continue
        if member not in members:
            findings.append(
                Finding(
                    f"artifacts.digests[{kind}]",
                    CheckOutcome.FAILED,
                    "the manifest says this artifact is included and the archive does not contain "
                    "it",
                )
            )
            continue

        import hashlib

        actual = hashlib.sha256(members[member]).hexdigest()
        if actual != expected:
            findings.append(
                Finding(
                    f"artifacts.digests[{kind}]",
                    CheckOutcome.FAILED,
                    f"the included bytes hash to {actual[:12]}… and the manifest says "
                    f"{expected[:12]}…; the artifact was altered after the manifest was written",
                )
            )
        else:
            findings.append(
                Finding(
                    f"artifacts.digests[{kind}]",
                    CheckOutcome.PASSED,
                    f"included bytes hash to the declared digest ({state})",
                )
            )
    return findings


def _check_identities(manifest: dict[str, Any]) -> Finding:
    """Every identity INV-03 requires must be bound.

    Checked against the same set the evaluator uses rather than a list written here, so a change to
    what an outcome must bind cannot leave the verifier accepting less.
    """
    from accessforge_domain.evaluation.identity import ALWAYS_REQUIRED

    identities = manifest.get("identities", {})
    missing = sorted(str(k) for k in ALWAYS_REQUIRED if str(k) not in identities)
    if missing:
        return Finding(
            "identities.bound",
            CheckOutcome.FAILED,
            f"the outcome is not bound to {', '.join(missing)}. An outcome that does not name what "
            "it ran against describes nothing in particular (INV-03).",
        )
    return Finding(
        "identities.bound",
        CheckOutcome.PASSED,
        f"{len(identities)} identities bound, including every one INV-03 requires",
    )


def _check_outcome_present(document: dict[str, Any]) -> Finding:
    machine = document.get("machineOutcome", {})
    outcome = machine.get("outcome")
    if not outcome:
        return Finding("outcome.frozen", CheckOutcome.FAILED, "the bundle records no outcome")
    if not machine.get("evaluatorVersion"):
        return Finding(
            "outcome.frozen",
            CheckOutcome.FAILED,
            "the outcome does not say which evaluator produced it, so a recomputation cannot be "
            "compared against it",
        )
    return Finding(
        "outcome.frozen",
        CheckOutcome.PASSED,
        f"{outcome} from {machine['evaluatorVersion']}, with {len(machine.get('reasons', []))} "
        "recorded reason(s)",
    )


def _check_reviews(document: dict[str, Any], manifest: dict[str, Any]) -> Finding:
    """Reviews present in the document must be the ones the manifest names, and stay attributable.

    A review appearing in the readable document but not in the signed manifest is a review somebody
    added afterwards, which is the shape a fabricated human sign-off takes.
    """
    declared = set(manifest.get("reviewIds", []))
    present = {r.get("reviewId") for r in document.get("reviews", [])}
    if not declared and not present:
        return Finding(
            "reviews.attribution",
            CheckOutcome.NOT_APPLICABLE,
            "no human review is disclosed in this bundle. Absence of a review is not a failed "
            "review; it means no person's assessment is being claimed.",
        )
    if present != declared:
        return Finding(
            "reviews.attribution",
            CheckOutcome.FAILED,
            f"the document carries reviews {sorted(present)} and the signed manifest names "
            f"{sorted(declared)}; a review outside the signature is one added after signing",
        )
    for review in document.get("reviews", []):
        if not review.get("reviewerId") or not review.get("verdict"):
            return Finding(
                "reviews.attribution",
                CheckOutcome.FAILED,
                "a review is present without a reviewer or a verdict, so it is attributable to "
                "nobody",
            )
    return Finding(
        "reviews.attribution",
        CheckOutcome.PASSED,
        f"{len(declared)} review(s), each attributable and named in the signed manifest",
    )


def _check_signature(
    manifest_bytes: bytes, attestation_doc: dict[str, Any], trust_root: TrustRoot | None
) -> tuple[Finding, str | None, bool]:
    if trust_root is None:
        return (
            Finding(
                "attestation.signature",
                CheckOutcome.UNSUPPORTED,
                "no trust root was supplied, so the signature was not checked. Reported rather "
                "than skipped: a verifier silent about an unchecked signature prints a clean "
                "report for an unsigned bundle.",
            ),
            None,
            False,
        )

    attestation = Attestation(
        key_id=str(attestation_doc.get("keyId", "")),
        issuer=str(attestation_doc.get("issuer", "")),
        algorithm=str(attestation_doc.get("algorithm", "")),
        signature_base64=str(attestation_doc.get("signature", "")),
    )
    check = check_attestation(
        manifest_bytes=manifest_bytes, attestation=attestation, trust_root=trust_root
    )
    outcome = CheckOutcome.PASSED if check.signature_matches else CheckOutcome.FAILED
    return (
        Finding("attestation.signature", outcome, check.summary),
        check.attributed_to,
        check.independently_trusted,
    )


def _check_trust_level_not_overclaimed(manifest: dict[str, Any], declared: Any) -> Finding:
    """The declared trust level must be the one the contents support.

    The issuer writes this field, and a bundle claiming FULLY_VERIFIABLE while carrying a redacted
    or
    excluded artifact would be asking a reader to conclude something its own contents do not
    support.
    Recomputing it here is the check that the issuer did not overclaim.
    """
    artifacts = manifest.get("artifacts", [])
    completeness = manifest.get("completeness", {})
    complete = all(
        completeness.get(key, False)
        for key in (
            "canonicalChainContiguous",
            "allRequiredProducersClosed",
            "allRequiredArtifactsPresent",
        )
    )
    withheld = any(
        a.get("state") in {"REDACTED", "DELETED"} or not a.get("included") for a in artifacts
    )

    if not complete:
        supported = TrustLevel.INCOMPLETE
    elif withheld:
        supported = TrustLevel.LIMITED_DISCLOSURE
    else:
        supported = TrustLevel.FULLY_VERIFIABLE

    if declared != str(supported):
        return Finding(
            "bundle.trustLevel",
            CheckOutcome.FAILED,
            f"the bundle declares {declared} and its contents support {supported}. A bundle that "
            "overclaims asks a reader to conclude something it does not carry the inputs for.",
        )
    return Finding(
        "bundle.trustLevel",
        CheckOutcome.PASSED,
        f"{declared}, which matches what the contents support",
    )
