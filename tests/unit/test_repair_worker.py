"""Synthetic model output only: never invokes a provider, edits source, builds or verifies."""

from threading import Event
from typing import Any, cast

import pytest
from strands.agent.agent_result import AgentResult
from strands.types.agent import Limits

from accessforge_domain.canonical import digest
from accessforge_orchestrator.diagnosis.models import DiagnosisValidation
from accessforge_orchestrator.repair.worker import (
    DraftChange,
    OriginalFile,
    RepairAgentProfile,
    RepairDraft,
    RepairInput,
    RepairWorker,
)


def inputs() -> RepairInput:
    import hashlib

    original = OriginalFile(
        path="src/form.ts",
        text="<button>Save</button>\r\n",
        sha256=hashlib.sha256(b"<button>Save</button>\r\n").hexdigest(),
        mode="100644",
    )
    diagnosis = DiagnosisValidation.model_validate(
        {
            "support": "SOURCE_LINKED",
            "missing_information": [],
            "hypothesis": {
                "observed_obstacle": "Save has no useful name",
                "affected_task_step": "Submit",
                "source_location": {
                    "path": original.path,
                    "file_digest": original.sha256,
                    "line_start": 1,
                    "line_end": 1,
                },
                "supporting_evidence_ids": ["evidence-1"],
                "alternative_explanations": ["Focus moved"],
                "uncertainty": "Needs independent rerun",
                "compliance_assessment": "NOT_ASSESSED",
            },
            "repair_brief": {
                "allowed_files": [original.path],
                "intended_behavior": "Give Save a meaningful name",
                "functional_constraints": ["Preserve validation and submission"],
                "protected_surfaces": ["tests"],
                "stop_recommendation": None,
            },
        }
    )
    return RepairInput(
        workspace_id="ws-1",
        finding_id="finding-1",
        diagnosis_id="diagnosis-1",
        diagnosis_digest=digest(diagnosis.model_dump(mode="json")),
        base_manifest_digest="a" * 64,
        source={"commit_sha": "b" * 40, "tree_digest": "c" * 64},  # type: ignore[arg-type]
        diagnosis=diagnosis,
        application_paths=("src",),
        files=(original,),
    )


class FakeAgent:
    def __init__(self, draft: RepairDraft | None, cancel_after: bool = False) -> None:
        self.draft, self.cancel_after = draft, cancel_after
        self.prompts: list[str] = []

    async def invoke_async(
        self,
        prompt: str,
        *,
        structured_output_model: type[RepairDraft],
        limits: Limits,
        cancel_signal: Event,
    ) -> AgentResult:
        assert structured_output_model is RepairDraft and limits["turns"] == 1
        self.prompts.append(prompt)
        if self.cancel_after:
            cancel_signal.set()
        return cast(AgentResult, type("Result", (), {"structured_output": self.draft})())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", [None, "scope", "digest", "no-op", "duplicate", "cancel", "missing"]
)
async def test_complete_bound_proposal_or_explicit_refusal(fault: str | None) -> None:
    scope = inputs()
    change = DraftChange(
        path=scope.files[0].path,
        original_sha256=scope.files[0].sha256,
        content='<button aria-label="Save form">Save</button>\r\n',
    )
    if fault == "scope":
        change = change.model_copy(update={"path": "tests/oracle.ts"})
    elif fault == "digest":
        change = change.model_copy(update={"original_sha256": "0" * 64})
    elif fault == "no-op":
        change = change.model_copy(update={"content": scope.files[0].text})
    draft = RepairDraft(
        changes=(change, change) if fault == "duplicate" else (change,),
        rationale="Associate a useful name",
        uncertainty="Not a verified repair",
    )
    agent = FakeAgent(None if fault == "missing" else draft, cancel_after=fault == "cancel")
    entered = Event()
    result = await RepairWorker(
        profile=RepairAgentProfile(), agent_builder=lambda _: agent
    ).propose(scope, on_provider_invoke=entered.set)
    assert entered.is_set() and len(agent.prompts) == 1
    assert result.input_digest == digest(
        {
            "repairInput": scope.model_dump(mode="json"),
            "modelProfile": RepairAgentProfile().model_dump(mode="json"),
        }
    )
    if fault is None:
        assert result.status == "PROPOSAL_READY" and result.patch_digest is not None
        assert result.changes[0].content == change.content
    else:
        assert (
            result.status == "UNAVAILABLE" and result.changes == () and result.patch_digest is None
        )
    assert result.meaning == "MODEL_DRAFT_NOT_PERSISTED_APPROVED_APPLIED_OR_VERIFIED"


@pytest.mark.asyncio
async def test_cancellation_and_context_limit_refuse_before_provider_entry() -> None:
    def forbidden(_: Event) -> Any:
        raise AssertionError("must not construct agent")

    for cancelled in (True, False):
        fence, entered = Event(), Event()
        if cancelled:
            fence.set()
        result = await RepairWorker(
            profile=RepairAgentProfile(max_context_characters=1000), agent_builder=forbidden
        ).propose(inputs(), cancel_signal=fence, on_provider_invoke=entered.set)
        assert result.status == "UNAVAILABLE" and not entered.is_set()


def test_unbound_original_or_stop_recommendation_cannot_be_model_input() -> None:
    scope = inputs().model_dump(mode="json")
    scope["files"][0]["text"] = "other source"
    with pytest.raises(ValueError):
        RepairInput.model_validate(scope)
    scope = inputs().model_dump(mode="json")
    scope["diagnosis"]["repair_brief"]["stop_recommendation"] = "Missing evidence"
    scope["diagnosis_digest"] = digest(scope["diagnosis"])
    with pytest.raises(ValueError):
        RepairInput.model_validate(scope)
