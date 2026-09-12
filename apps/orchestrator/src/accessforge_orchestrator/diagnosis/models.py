"""Closed diagnosis input, model-output and repair-brief contracts."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _SealedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceIdentity(_SealedModel):
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    tree_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class EvidenceReference(_SealedModel):
    evidence_id: str = Field(min_length=1, max_length=200)
    kind: Literal[
        "READER_OBSERVATION",
        "ACTION",
        "ASSERTION_RESULT",
        "PREFLIGHT",
        "COMPLETION_OBSERVATION",
        "LOG",
    ]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    retained: bool


class ProtectedAssertion(_SealedModel):
    assertion_id: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    condition: Literal["TRUE", "FALSE", "UNKNOWN"]


class SourceExcerpt(_SealedModel):
    path: str = Field(min_length=1, max_length=500)
    file_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    text: str = Field(max_length=50000)

    @model_validator(mode="after")
    def ordered_lines(self) -> Self:
        if self.line_end < self.line_start:
            raise ValueError("source excerpt line_end cannot precede line_start")
        return self


class DiagnosisProjection(_SealedModel):
    """Privileged diagnosis input. It is deliberately unrelated to NavigatorProjection."""

    run_id: str = Field(min_length=1)
    run_outcome: Literal["FAIL", "INCONCLUSIVE"]
    source: SourceIdentity
    component_name: str = Field(min_length=1, max_length=300)
    assertions: tuple[ProtectedAssertion, ...] = Field(min_length=1, max_length=100)
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=500)
    source_excerpts: tuple[SourceExcerpt, ...] = Field(max_length=40)

    @model_validator(mode="after")
    def identities_are_unique(self) -> Self:
        assertion_ids = [item.assertion_id for item in self.assertions]
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("assertion identifiers must be unique")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence identifiers must be unique")
        return self


class SourceLocation(_SealedModel):
    path: str = Field(min_length=1, max_length=500)
    file_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered_lines(self) -> Self:
        if self.line_end < self.line_start:
            raise ValueError("source location line_end cannot precede line_start")
        return self


class DiagnosisHypothesis(_SealedModel):
    """Structured model output with no finding-state, verdict or patch authority."""

    observed_obstacle: str = Field(min_length=1, max_length=4000)
    affected_task_step: str = Field(min_length=1, max_length=2000)
    source_location: SourceLocation | None = None
    supporting_evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=50)
    alternative_explanations: tuple[str, ...] = Field(min_length=1, max_length=10)
    uncertainty: str = Field(min_length=1, max_length=3000)
    compliance_assessment: Literal["NOT_ASSESSED"] = "NOT_ASSESSED"


class RepairBrief(_SealedModel):
    """The only module-14 input; it contains scope and intent, never executable code."""

    allowed_files: tuple[str, ...] = Field(min_length=1, max_length=20)
    intended_behavior: str = Field(min_length=1, max_length=3000)
    functional_constraints: tuple[str, ...] = Field(min_length=1, max_length=30)
    protected_surfaces: tuple[str, ...] = Field(min_length=1, max_length=30)
    stop_recommendation: str | None = Field(default=None, max_length=3000)


class DiagnosisDraft(_SealedModel):
    hypothesis: DiagnosisHypothesis
    repair_brief: RepairBrief


class DiagnosisValidation(_SealedModel):
    support: Literal["SOURCE_LINKED", "UNSUPPORTED"]
    hypothesis: DiagnosisHypothesis | None
    repair_brief: RepairBrief | None
    missing_information: tuple[str, ...]
