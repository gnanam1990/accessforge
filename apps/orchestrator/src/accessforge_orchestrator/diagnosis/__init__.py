"""Evidence-grounded diagnosis boundaries."""

from .agent import DiagnosisAgentProfile, DiagnosisWorker, build_diagnosis_agent
from .models import (
    DiagnosisDraft,
    DiagnosisHypothesis,
    DiagnosisProjection,
    DiagnosisValidation,
    EvidenceReference,
    ProtectedAssertion,
    RepairBrief,
    SourceExcerpt,
    SourceIdentity,
    SourceLocation,
)
from .source import FrozenSourceReader, FrozenSourceScope, SourceReadRefused
from .validation import DiagnosisValidator

__all__ = [
    "DiagnosisAgentProfile",
    "DiagnosisDraft",
    "DiagnosisHypothesis",
    "DiagnosisProjection",
    "DiagnosisValidation",
    "DiagnosisValidator",
    "DiagnosisWorker",
    "EvidenceReference",
    "FrozenSourceReader",
    "FrozenSourceScope",
    "ProtectedAssertion",
    "RepairBrief",
    "SourceExcerpt",
    "SourceIdentity",
    "SourceLocation",
    "SourceReadRefused",
    "build_diagnosis_agent",
]
