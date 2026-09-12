"""Post-model evidence and source attribution checks."""

from __future__ import annotations

from .models import DiagnosisHypothesis, DiagnosisProjection, DiagnosisValidation, RepairBrief


class DiagnosisValidator:
    def validate(
        self,
        projection: DiagnosisProjection,
        hypothesis: DiagnosisHypothesis,
        repair_brief: RepairBrief,
    ) -> DiagnosisValidation:
        missing: list[str] = []
        evidence = {item.evidence_id: item for item in projection.evidence}
        for evidence_id in hypothesis.supporting_evidence_ids:
            item = evidence.get(evidence_id)
            if item is None:
                missing.append(f"evidence {evidence_id!r} is not in the projection")
            elif not item.retained:
                missing.append(
                    f"evidence {evidence_id!r} is not retained and cannot support a claim"
                )

        excerpts = {item.path: item for item in projection.source_excerpts}
        location = hypothesis.source_location
        if location is None:
            missing.append("no source location was supported")
        else:
            excerpt = excerpts.get(location.path)
            if excerpt is None:
                missing.append(f"source {location.path!r} is not in the frozen excerpts")
            else:
                if location.file_digest != excerpt.file_digest:
                    missing.append(
                        f"source {location.path!r} does not match its frozen file digest"
                    )
                if location.line_start < excerpt.line_start or location.line_end > excerpt.line_end:
                    missing.append(f"source {location.path!r} line range is outside the excerpt")

        for path in repair_brief.allowed_files:
            if path not in excerpts:
                missing.append(f"repair file {path!r} is outside the authorized source scope")

        false_assertions = [item for item in projection.assertions if item.condition == "FALSE"]
        if not false_assertions:
            missing.append("no protected assertion was observed FALSE")

        if missing:
            return DiagnosisValidation(
                support="UNSUPPORTED",
                hypothesis=hypothesis,
                repair_brief=None,
                missing_information=tuple(dict.fromkeys(missing)),
            )
        return DiagnosisValidation(
            support="SOURCE_LINKED",
            hypothesis=hypothesis,
            repair_brief=repair_brief,
            missing_information=(),
        )
