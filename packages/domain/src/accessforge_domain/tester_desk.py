"""Tester Desk domain vocabulary: commissions, briefs, outcomes, eligibility and comparison.

The Tester Desk procures an independent human retest of one user journey on one
release/browser/assistive-technology combination. This module is pure: every rule a route or
persistence function relies on is validated and derivable here, so the HTTP boundary and the
database agree on what a brief, an observation or a payable means.

Two separations the product depends on:

* **Task outcome vs deliverable acceptance** (INV-04). ``task_result`` is derived only from step
  observations; a reviewer accepts *evidence completeness*, never a product verdict. A checkout
  that fails earns the posted fee when the evidence contract is met.
* **Human observation vs automation** (INV-08). The only ``observation_source`` the schema accepts
  is a tester attestation; there is no value an automated producer could claim, by construction.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from .canonical import digest
from .timestamps import parse_rfc3339_utc


class BriefError(ValueError):
    """A commission brief is not well-formed. Refused before it can be hashed or persisted."""


class EligibilityError(ValueError):
    """A tester cannot claim this commission. The message says exactly which term failed."""


class StepResult(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    UNREACHABLE = "UNREACHABLE"
    INCONCLUSIVE = "INCONCLUSIVE"


class TaskResult(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


class DeliverableDecision(StrEnum):
    ACCEPTED = "ACCEPTED"
    NEEDS_REVISION = "NEEDS_REVISION"


class ObservationSource(StrEnum):
    """How an observation was produced. The closed vocabulary is the invariant.

    ``HUMAN_MANUAL`` is the only value: the tester attested they performed the step with the
    declared assistive technology. Anything else -- a probe, a synthesised transcript, an imported
    run -- has no representation here, which is what keeps automation-only evidence out of a
    human-observed result (INV-08).
    """

    HUMAN_MANUAL = "HUMAN_MANUAL"


class EvidenceKind(StrEnum):
    ACTION_TRACE = "ACTION_TRACE"
    SPEECH_TRANSCRIPT = "SPEECH_TRANSCRIPT"
    SCREENSHOT = "SCREENSHOT"
    NOTE = "NOTE"


#: Content types each evidence kind may carry. An allowlist, because a "recording" that is actually
#: HTML or video the brief did not permit is precisely the disallowed upload T-006 exercises.
ALLOWED_CONTENT_TYPES: dict[EvidenceKind, frozenset[str]] = {
    EvidenceKind.ACTION_TRACE: frozenset({"application/json"}),
    EvidenceKind.SPEECH_TRANSCRIPT: frozenset({"application/json", "text/plain"}),
    EvidenceKind.SCREENSHOT: frozenset({"image/png"}),
    EvidenceKind.NOTE: frozenset({"text/plain", "application/json"}),
}

#: Evidence kinds that are never acceptable on a tester-desk attachment regardless of the brief.
#: Screen recordings and audio are out of the first release's evidence contract; a brief cannot
#: whitelist them, and the upload path cannot create a downloadable artifact for them (INV-03).
NEVER_PERMITTED_KINDS: frozenset[str] = frozenset({"SCREEN_RECORDING", "AUDIO_RECORDING"})

MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def validate_reference(reference: Any) -> str:
    if not isinstance(reference, str) or not _REFERENCE.match(reference):
        raise BriefError(
            "commission reference must be 1-80 characters of letters, digits, '.', '_' or '-'"
        )
    return reference


def validate_currency(currency: Any) -> str:
    if not isinstance(currency, str) or not _CURRENCY.match(currency):
        raise BriefError("currency must be a three-letter ISO 4217 code such as USD")
    return currency


def validate_fee_minor(fee_minor: Any) -> int:
    if type(fee_minor) is not int or fee_minor < 0 or fee_minor >= 2**53:
        raise BriefError("fee must be a nonnegative integer in the currency's minor units")
    return fee_minor


def validate_release_digest(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise BriefError("release digest must be a 64-character lowercase hex SHA-256")
    return value


def validate_task_steps(steps: Any) -> list[dict[str, str]]:
    if not isinstance(steps, list) or not (1 <= len(steps) <= 100):
        raise BriefError("a brief needs between one and one hundred task steps")
    seen: set[str] = set()
    normalized: list[dict[str, str]] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise BriefError(f"task step {index} must be an object")
        step_id = step.get("stepId")
        title = step.get("title")
        if not isinstance(step_id, str) or not (1 <= len(step_id) <= 80):
            raise BriefError(f"task step {index} needs a stepId of 1-80 characters")
        if step_id in seen:
            raise BriefError(f"duplicate task step id {step_id!r}")
        seen.add(step_id)
        if not isinstance(title, str) or not (1 <= len(title.strip()) <= 300):
            raise BriefError(f"task step {step_id!r} needs a title of 1-300 characters")
        path = step.get("path")
        if path is not None and (not isinstance(path, str) or len(path) > 2000):
            raise BriefError(f"task step {step_id!r} path must be at most 2000 characters")
        normalized_step = {"stepId": step_id, "title": title.strip()}
        if path:
            normalized_step["path"] = path
        normalized.append(normalized_step)
    return normalized


def validate_environment(environment: Any) -> dict[str, str]:
    """The exact technology combination the retest covers.

    Exactness is the product: 'Safari 18 with VoiceOver' and 'Safari 18 with NVDA' are different
    commissions, which is what makes release comparison legitimate only across matching
    dimensions (INV-07's boundary condition).
    """
    if not isinstance(environment, dict):
        raise BriefError("environment must be an object")
    normalized: dict[str, str] = {}
    for field in ("browserName", "browserVersion", "assistiveTechnology"):
        value = environment.get(field)
        if not isinstance(value, str) or not (1 <= len(value.strip()) <= 100):
            raise BriefError(f"environment.{field} is required, 1-100 characters")
        normalized[field] = value.strip()
    input_mode = environment.get("inputMode")
    if input_mode is not None:
        if not isinstance(input_mode, str) or not (1 <= len(input_mode) <= 60):
            raise BriefError("environment.inputMode must be 1-60 characters when present")
        normalized["inputMode"] = input_mode
    return normalized


def validate_rubric(rubric: Any, *, step_ids: set[str]) -> dict[str, Any]:
    """The deliverable contract: which steps must carry an outcome and which evidence must exist.

    ``requiredStepIds`` defaults to every step; a step the tester cannot reach still earns an
    outcome (UNREACHABLE), so 'all steps reported' is satisfiable even when the product breaks.
    """
    if not isinstance(rubric, dict):
        raise BriefError("rubric must be an object")
    required = rubric.get("requiredStepIds")
    if required is None:
        required_ids = sorted(step_ids)
    elif isinstance(required, list) and all(isinstance(s, str) for s in required):
        unknown = sorted(set(required) - step_ids)
        if unknown:
            raise BriefError(f"rubric requires steps the journey does not contain: {unknown}")
        required_ids = sorted(set(required))
    else:
        raise BriefError("rubric.requiredStepIds must be a list of step ids")
    evidence = rubric.get("requiredEvidenceKinds", [])
    if not isinstance(evidence, list) or not all(isinstance(k, str) for k in evidence):
        raise BriefError("rubric.requiredEvidenceKinds must be a list of evidence kinds")
    for kind in evidence:
        if kind not in {k.value for k in EvidenceKind}:
            raise BriefError(f"rubric requires an unknown evidence kind {kind!r}")
    notes = rubric.get("notes")
    if notes is not None and (not isinstance(notes, str) or len(notes) > 4000):
        raise BriefError("rubric.notes must be at most 4000 characters")
    normalized: dict[str, Any] = {
        "requiredStepIds": required_ids,
        "requiredEvidenceKinds": sorted(set(evidence)),
    }
    if notes:
        normalized["notes"] = notes
    return normalized


def validate_permitted_evidence(kinds: Any) -> list[str]:
    if not isinstance(kinds, list) or not kinds or not all(isinstance(k, str) for k in kinds):
        raise BriefError("permittedEvidence must be a nonempty list of evidence kinds")
    known = {k.value for k in EvidenceKind}
    for kind in kinds:
        if kind in NEVER_PERMITTED_KINDS:
            raise BriefError(f"evidence kind {kind} is not permitted on any brief")
        if kind not in known:
            raise BriefError(f"unknown evidence kind {kind!r}")
    return sorted(set(kinds))


def validate_consent_terms_version(version: Any) -> int:
    if type(version) is not int or version < 1:
        raise BriefError("consentTermsVersion must be a positive integer")
    return version


def brief_document(
    *,
    release_label: str,
    release_digest: str,
    target_url: str,
    task_steps: list[dict[str, Any]],
    environment: dict[str, Any],
    rubric: dict[str, Any],
    permitted_evidence: list[str],
    fee_minor: int,
    currency: str,
    consent_terms_version: int,
    deadline: str | None,
) -> dict[str, Any]:
    """The canonical commercial terms that ``brief_hash`` commits to.

    access_note is deliberately excluded: it is a delivery detail for the assigned tester, not a
    term of the contract, so rotating credentials must not invalidate the brief's identity.
    """
    if deadline is not None:
        parse_rfc3339_utc(deadline, field="deadline")
    return {
        "consentTermsVersion": validate_consent_terms_version(consent_terms_version),
        "currency": validate_currency(currency),
        "deadline": deadline,
        "environment": validate_environment(environment),
        "feeMinor": validate_fee_minor(fee_minor),
        "permittedEvidence": validate_permitted_evidence(permitted_evidence),
        "releaseDigest": validate_release_digest(release_digest),
        "releaseLabel": _validate_label(release_label),
        "rubric": validate_rubric(
            rubric,
            step_ids={s["stepId"] for s in validate_task_steps(task_steps)},
        ),
        "targetUrl": _validate_url(target_url),
        "taskSteps": validate_task_steps(task_steps),
    }


def _validate_label(value: Any) -> str:
    if not isinstance(value, str) or not (1 <= len(value.strip()) <= 80):
        raise BriefError("release label must be 1-80 characters")
    return value.strip()


def _validate_url(value: Any) -> str:
    if not isinstance(value, str) or not (8 <= len(value) <= 2000):
        raise BriefError("target URL must be 8-2000 characters")
    scheme = value.split(":", 1)[0].lower()
    if scheme not in {"https", "http"}:
        raise BriefError("target URL must use http or https")
    return value


def compute_brief_hash(document: dict[str, Any]) -> str:
    return digest(document)


# --- outcomes ---------------------------------------------------------------------------------


def derive_task_result(
    observations: dict[str, str], *, rubric: dict[str, Any]
) -> TaskResult:
    """The tested task's outcome, derived only from step observations.

    Complete-and-failed is a real result: the product broke, and the tester reported it. The value
    of the deliverable is the evidence, not the verdict.
    """
    required = rubric["requiredStepIds"]
    results = [observations.get(step_id) for step_id in required]
    if any(r in (StepResult.FAILED.value, StepResult.UNREACHABLE.value) for r in results):
        return TaskResult.FAILED
    if all(r == StepResult.PASSED.value for r in results):
        return TaskResult.PASSED
    return TaskResult.INCONCLUSIVE


def rubric_completeness(
    *,
    step_ids: list[str],
    observations: dict[str, str],
    attachment_kinds: set[str],
    rubric: dict[str, Any],
    permitted_evidence: list[str],
) -> list[str]:
    """What is missing for a submission to satisfy the deliverable rubric.

    Returns the empty list when complete; otherwise human-readable gaps so the tester sees exactly
    what remains instead of a bare refusal.
    """
    missing: list[str] = []
    for step_id in rubric["requiredStepIds"]:
        if observations.get(step_id) not in {r.value for r in StepResult}:
            missing.append(f"step {step_id} has no recorded outcome")
    for kind in rubric["requiredEvidenceKinds"]:
        if kind not in permitted_evidence:
            missing.append(f"rubric requires {kind} evidence the brief does not permit")
        elif kind not in attachment_kinds:
            missing.append(f"no permitted {kind} attachment is present")
    return missing


# --- eligibility and consent -------------------------------------------------------------------


def assert_eligible(
    *,
    tester_capabilities: dict[str, str],
    environment: dict[str, str],
    tester_status: str,
    payout_reference: str | None,
    declared_conflicts: set[str],
    commission_id: str,
    commission_reference: str,
    consent_version_offered: int,
    consent_version_required: int,
    consent_accepted: bool,
    offer_open: bool,
    now_deadline_passed: bool,
) -> None:
    """Why this tester may claim this commission, or exactly why not (INV-02).

    Each failure names the missing input so the surface can show the responsible actor and the
    next possible action instead of a bare denial.
    """
    if not offer_open:
        raise EligibilityError("this commission is not open for claims")
    if now_deadline_passed:
        raise EligibilityError("this offer has expired")
    if tester_status != "ACTIVE":
        raise EligibilityError("tester enrollment is not active")
    if commission_id in declared_conflicts or commission_reference in declared_conflicts:
        raise EligibilityError("a declared conflict removes this commission from your offers")
    for field in ("browserName", "browserVersion", "assistiveTechnology"):
        required = environment[field]
        offered = tester_capabilities.get(field)
        if offered != required:
            raise EligibilityError(
                f"environment requires {field}={required}; the roster records {offered!r}"
            )
    if not payout_reference:
        raise EligibilityError(
            "a payout reference is required before claiming paid work; enrolment lacks one"
        )
    if not consent_accepted or consent_version_offered != consent_version_required:
        raise EligibilityError(
            f"claiming requires reviewing consent terms version {consent_version_required}"
        )


# --- release comparison ------------------------------------------------------------------------


def compare_commissions(
    *,
    base: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """A retest comparison honest about what it compares (INV-07's edge).

    'Same journey on a fixed release' is the only dimension a retest can prove. Different
    assistive technology, browser or environment means the rows sit next to each other with an
    explicit caveat rather than a verdict, and a previous finding never passes the new release.
    """
    caveats: list[str] = []
    for field in ("browserName", "browserVersion", "assistiveTechnology", "inputMode"):
        left = base["environment"].get(field)
        right = candidate["environment"].get(field)
        if left != right:
            caveats.append(
                f"environment.{field} differs ({left!r} vs {right!r}): outcomes are not directly "
                "comparable"
            )
    if base.get("releaseLabel") == candidate.get("releaseLabel"):
        caveats.append("both commissions pin the same release; a same-release comparison is a "
                       "rerun, not a retest of a fix")
    if base.get("defectKey") and base.get("defectKey") == candidate.get("defectKey"):
        link = "SAME_DEFECT_RETEST"
    elif base.get("predecessorCommissionId") == candidate.get("commissionId") or candidate.get(
        "predecessorCommissionId"
    ) == base.get("commissionId"):
        link = "LINKED_RETEST"
    else:
        link = "UNRELATED"
        caveats.append("commissions are not linked by defect or predecessor; comparison is "
                       "informational only")
    return {
        "link": link,
        "caveats": caveats,
        "base": _comparison_row(base),
        "candidate": _comparison_row(candidate),
    }


def _comparison_row(commission: dict[str, Any]) -> dict[str, Any]:
    return {
        "commissionId": commission.get("commissionId"),
        "releaseLabel": commission.get("releaseLabel"),
        "taskResult": commission.get("taskResult"),
        "deliverableDecision": commission.get("deliverableDecision"),
        "environment": commission.get("environment"),
    }
