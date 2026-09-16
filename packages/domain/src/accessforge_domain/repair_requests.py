"""Closed billable repair-request contract; profile changes always require new consent."""

import re
from typing import Any
from uuid import UUID

from .canonical import canonicalize, digest
from .diagnosis_requests import model_profile as diagnosis_profile


def model_profile() -> dict[str, Any]:
    return {
        **diagnosis_profile(),
        "invocation_output_tokens": 4096,
        "invocation_total_tokens": 50000,
        "max_context_characters": 200000,
    }


def validate(payload: dict[str, Any]) -> None:
    identifiers = {"diagnosisId", "projectId", "sourceSnapshotId"}
    digests = {
        "diagnosisDigest",
        "manifestDigest",
        "sourceTreeDigest",
        "evaluationDigest",
        "repairSurfaceDigest",
        "modelProfileDigest",
    }
    fields = (
        identifiers
        | digests
        | {"supersedes", "billableCallAcknowledged", "separateReviewAcknowledged"}
    )
    if (
        set(payload) != fields
        or len(canonicalize(payload)) > 8000
        or payload["billableCallAcknowledged"] is not True
        or type(payload["separateReviewAcknowledged"]) is not bool
    ):
        raise ValueError("exact reviewed scope and explicit billable consent required")
    for key in identifiers:
        value = payload[key]
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError("canonical repair scope identifiers required")
    for key in digests:
        if not isinstance(payload[key], str) or not re.fullmatch(r"[0-9a-f]{64}", payload[key]):
            raise ValueError("exact repair scope digests required")
    if payload["modelProfileDigest"] != digest(model_profile()):
        raise ValueError("repair model profile changed; review it again")
    previous = payload["supersedes"]
    if previous is not None and (not isinstance(previous, str) or str(UUID(previous)) != previous):
        raise ValueError("canonical predecessor request required")
