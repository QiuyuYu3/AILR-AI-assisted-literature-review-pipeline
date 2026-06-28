"""ExtractionTask: iterate include'd sources with markdown, call reviewer.extract, persist results."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ailr.core.project import Project
from ailr.core.source import Source
from ailr.extraction import compose_schema
from ailr.reviewers import ExtractionResult, Reviewer, ScreeningDecision, SourceExtraction
from ailr.reviewers import LLMReviewer

ProgressCallback = Callable[[int, int, Optional[Source], Optional[Exception]], None]


@dataclass
class ExtractRunSummary:
    total_candidates: int = 0
    extracted: int = 0
    skipped_already_done: int = 0
    skipped_no_markdown: int = 0
    failed: int = 0
    failures: list[dict] = field(default_factory=list)
    total_cost_estimate: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_input_tokens: int = 0


class ExtractionTask:
    def __init__(self, project: Project, reviewer: Reviewer) -> None:
        self.project = project
        self.reviewer = reviewer

    def run(
        self,
        *,
        limit: Optional[int] = None,
        only_includes: bool = True,
        force: bool = False,
        on_progress: Optional[ProgressCallback] = None,
        batch: bool = False,
    ) -> ExtractRunSummary:
        config = self.project.config
        prompt_path = self.project.root / config.extraction.prompt
        schema_path = self.project.root / config.extraction.schema_path
        criteria_path = self.project.root / config.screening.criteria
        additional_path = self.project.root / config.extraction.additional

        prompt_template = prompt_path.read_text(encoding="utf-8")
        criteria_text = criteria_path.read_text(encoding="utf-8")
        additional_text = additional_path.read_text(encoding="utf-8") if additional_path.exists() else ""
        fields = compose_schema(schema_path)
        with_quotes = config.extraction.output_format == "with_quotes"
        flag_check = config.extraction.flag_check

        candidates = self._select_candidates(only_includes=only_includes)
        if limit is not None:
            candidates = candidates[:limit]

        summary = ExtractRunSummary(total_candidates=len(candidates))

        # Mock runs buffer everything and write it in a few multi-row INSERTs at the end (fast, no
        # per-row Neon round trips); real runs keep the per-row, per-commit path for durability.
        all_results: list[ExtractionResult] = []
        all_ft_decisions: list[ScreeningDecision] = []

        for idx, source in enumerate(candidates, 1):
            if not source.markdown_path:
                summary.skipped_no_markdown += 1
                if on_progress:
                    on_progress(idx, len(candidates), source, None)
                continue

            md_path = Path(source.markdown_path)
            if not md_path.is_absolute():
                md_path = self.project.root / md_path
            if not md_path.exists():
                summary.skipped_no_markdown += 1
                if on_progress:
                    on_progress(idx, len(candidates), source, None)
                continue

            if not force and self.project.db.has_extraction(source.id, self.reviewer.reviewer_type):
                summary.skipped_already_done += 1
                if on_progress:
                    on_progress(idx, len(candidates), source, None)
                continue

            try:
                paper_text = md_path.read_text(encoding="utf-8")
                extraction = self.reviewer.extract(
                    source=source,
                    paper_text=paper_text,
                    fields=fields,
                    prompt_template=prompt_template,
                    criteria_text=criteria_text,
                    additional_text=additional_text,
                    with_quotes=with_quotes,
                    flag_check=flag_check,
                )
                extraction.source_id = source.id

                for result in extraction.results:
                    result.source_id = source.id

                # Derive a full-text screening decision from the flag_check verdicts and persist it so
                # the FT review tab + conflict detection work uniformly.
                ft_decision = None
                if extraction.flag_check is not None:
                    ft_decision = ScreeningDecision(
                        decision=_derive_ft_decision(extraction.flag_check),
                        reasoning="(derived from extraction flag_check)",
                        reviewer_type=self.reviewer.reviewer_type,
                        reviewer_id=self.reviewer.reviewer_id,
                        source_id=source.id,
                        stage="full_text",
                        confidence=_avg_flag_confidence(extraction.flag_check),
                    )

                if batch:
                    all_results.extend(extraction.results)
                    if extraction.flag_check is not None:
                        all_results.append(ExtractionResult(
                            extractor_type=self.reviewer.reviewer_type,
                            extractor_id=self.reviewer.reviewer_id,
                            field_name="_flag_check",
                            value=extraction.flag_check,
                            source_id=source.id,
                        ))
                        all_ft_decisions.append(ft_decision)
                else:
                    for result in extraction.results:
                        self.project.db.insert_extraction(result)
                    if extraction.flag_check is not None:
                        self.project.db.insert_flag_check(
                            source_id=source.id,
                            extractor_type=self.reviewer.reviewer_type,
                            extractor_id=self.reviewer.reviewer_id,
                            flag_check=extraction.flag_check,
                        )
                        self.project.db.insert_screening_decision(ft_decision)

                if not batch and isinstance(self.reviewer, LLMReviewer) and self.reviewer.last_metadata:
                    meta = self.reviewer.last_metadata
                    self.project.db.insert_api_call(self.project.project_id, meta)
                    summary.total_cost_estimate += meta.cost_estimate
                    summary.total_input_tokens += meta.input_tokens
                    summary.total_output_tokens += meta.output_tokens
                    summary.total_cached_input_tokens += meta.cached_input_tokens

                summary.extracted += 1
                if on_progress:
                    on_progress(idx, len(candidates), source, None)
            except Exception as e:
                summary.failed += 1
                summary.failures.append(
                    {"source_id": source.id, "title": source.title, "error": str(e)}
                )
                if on_progress:
                    on_progress(idx, len(candidates), source, e)

        if batch and (all_results or all_ft_decisions):
            with self.project.db._conn.transaction():
                self.project.db.insert_extractions(all_results)
                self.project.db.insert_screening_decisions_batch(all_ft_decisions)
        return summary

    def _select_candidates(self, only_includes: bool) -> list[Source]:
        # Candidates = papers that passed *screening* inclusion and have full-text markdown.
        # The full-text inclusion verdict is an OUTPUT of extraction (the AI's _flag_check
        # re-checks the full text), so it can't be a precondition here.
        if only_includes:
            return self.project.db.list_abstract_includes_with_markdown(self.project.project_id)
        return self.project.db.list_sources_with_markdown(self.project.project_id)


def _derive_ft_decision(flag_check: list[dict]) -> str:
    """Aggregate per-criterion verdicts into a single full-text screening decision.
    Any FAIL -> exclude. Else any UNCERTAIN -> uncertain. Else include.
    """
    if not flag_check:
        return "uncertain"
    verdicts = [(item.get("verdict") or "").upper() for item in flag_check]
    if any(v == "FAIL" for v in verdicts):
        return "exclude"
    if any(v == "UNCERTAIN" for v in verdicts):
        return "uncertain"
    if all(v == "PASS" for v in verdicts):
        return "include"
    return "uncertain"


def _avg_flag_confidence(flag_check: list[dict]) -> float | None:
    if not flag_check:
        return None
    confs = [item.get("confidence") for item in flag_check if isinstance(item.get("confidence"), (int, float))]
    if not confs:
        return None
    return float(sum(confs) / len(confs))
