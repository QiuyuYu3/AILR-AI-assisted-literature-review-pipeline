"""ExtractionTask: iterate include'd sources with markdown, call reviewer.extract, persist results."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from ailr.core.pdf_paths import resolve_markdown_path
from ailr.core.project import Project
from ailr.core.source import Source
from ailr.criteria import resolve_criteria
from ailr.extraction import compose_schema
from ailr.quote_audit import audit_fields
from ailr.reviewers import (
    ExtractionResult,
    LLMReviewer,
    Reviewer,
    ScreeningDecision,
)

ProgressCallback = Callable[[int, int, Source | None, Exception | None], None]


@dataclass
class ExtractRunSummary:
    total_candidates: int = 0
    extracted: int = 0
    skipped_already_done: int = 0
    skipped_no_markdown: int = 0
    failed: int = 0
    failures: list[dict] = field(default_factory=list)
    archived: int = 0  # rows from a previous AI run retired by a forced re-extract
    rerun_no_verdict: int = 0  # papers this model had extracted without a full-text verdict, run again
    no_verdict_elsewhere: list[int] = field(default_factory=list)  # same gap, extracted by another source
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_input_tokens: int = 0
    # Verbatim quote audit over this run's extractions (real runs only; mock quotes are noise).
    quote_values: int = 0
    quote_quoted: int = 0
    quote_checked: int = 0
    quote_verbatim: int = 0


class ExtractionTask:
    def __init__(self, project: Project, reviewer: Reviewer) -> None:
        self.project = project
        self.reviewer = reviewer

    def run(
        self,
        *,
        limit: int | None = None,
        only_includes: bool = True,
        force: bool = False,
        on_progress: ProgressCallback | None = None,
        batch: bool = False,
        workers: int | None = None,
        source_ids: list[int] | None = None,
    ) -> ExtractRunSummary:
        config = self.project.config
        prompt_path = self.project.root / config.extraction.prompt
        schema_path = self.project.root / config.extraction.schema_path
        additional_path = self.project.root / config.extraction.additional

        prompt_template = prompt_path.read_text(encoding="utf-8")
        criteria_text, criterion_ids = resolve_criteria(self.project.root, config.screening)
        additional_text = additional_path.read_text(encoding="utf-8") if additional_path.exists() else ""
        fields = compose_schema(schema_path)
        with_quotes = config.extraction.output_format == "with_quotes"
        flag_check = config.extraction.flag_check
        if workers is None:
            workers = config.extraction.workers
        workers = max(1, workers)

        candidates = self._select_candidates(only_includes=only_includes)
        if source_ids is not None:
            wanted = set(source_ids)
            candidates = [s for s in candidates if s.id in wanted]
        if limit is not None:
            candidates = candidates[:limit]

        summary = ExtractRunSummary(total_candidates=len(candidates))

        already_done: set[int] = set()
        if not force:
            already_done = self.project.db.sources_with_extraction(
                [s.id for s in candidates if s.id is not None], self.reviewer.reviewer_type
            )
        redo: set[int] = set()
        if flag_check and already_done and not batch:
            # Full text waits for the verdict, so an extraction without one is unfinished. Only this
            # model's own runs are redone; another source's would mix models, so they are reported.
            unverdicted = already_done - set(self.project.db.get_latest_ai_decisions(list(already_done), stage="full_text"))
            redo = self.project.db.sources_with_extraction(
                list(unverdicted), self.reviewer.reviewer_type, extractor_id=self.reviewer.reviewer_id
            )
            already_done -= redo
            summary.rerun_no_verdict = len(redo)
            summary.no_verdict_elsewhere = sorted(unverdicted - redo)

        # Skips are decided up front; only real LLM calls go to the pool below.
        done = 0
        to_extract: list[tuple] = []
        for source in candidates:
            md_path = resolve_markdown_path(source.markdown_path, self.project.root, source.id)
            if md_path is None:
                summary.skipped_no_markdown += 1
            elif not force and source.id in already_done:
                summary.skipped_already_done += 1
            else:
                to_extract.append((source, md_path))
                continue
            done += 1
            if on_progress:
                on_progress(done, len(candidates), source, None)

        # Mock runs buffer everything and write it in a few multi-row INSERTs at the end (fast, no
        # per-row Neon round trips); real runs commit paper by paper for durability.
        all_results: list[ExtractionResult] = []
        all_ft_decisions: list[ScreeningDecision] = []
        # Telemetry is buffered either way: not review data, so no need for per-row durability.
        call_metas: list = []

        # LLM calls run in a small thread pool (they mostly wait on the network); all DB writes,
        # summary updates, and progress callbacks stay on this thread.
        def _extract_one(source, md_path):
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
                criterion_ids=criterion_ids,
            )
            meta = self.reviewer.last_metadata if isinstance(self.reviewer, LLMReviewer) else None
            # Audited here because paper_text is in hand; pure string work, thread-safe.
            audit = None
            if not batch:
                audit = audit_fields(
                    ((r.field_name, r.value, r.source_quote) for r in extraction.results),
                    paper_text, source_id=source.id,
                )
            return extraction, meta, audit

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_extract_one, s, p): s for s, p in to_extract}
            for fut in as_completed(futures):
                source = futures[fut]
                done += 1
                try:
                    extraction, meta, audit = fut.result()
                    extraction.source_id = source.id
                    if audit is not None:
                        summary.quote_values += audit.values
                        summary.quote_quoted += audit.quoted
                        summary.quote_checked += audit.checked
                        summary.quote_verbatim += audit.verbatim

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
                            llm_params=next((r.llm_params for r in extraction.results if r.llm_params), None),
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
                        # A forced re-extract only ever APPENDS rows, so note where the previous AI
                        # run ends before writing; it is retired below once the new rows are in.
                        # Doing it in that order means a failed call leaves the old extraction alone.
                        previous_max = (
                            self.project.db.max_ai_extraction_id(source.id)
                            if (force or source.id in redo) and self.reviewer.reviewer_type == "ai"
                            else None
                        )
                        # One transaction per paper: a failure part-way through leaves none of it.
                        with self.project.db._conn.transaction():
                            for result in extraction.results:
                                self.project.db.insert_extraction(result)
                            if extraction.flag_check is not None:
                                self.project.db.insert_flag_check(
                                    source_id=source.id,
                                    extractor_type=self.reviewer.reviewer_type,
                                    extractor_id=self.reviewer.reviewer_id,
                                    flag_check=extraction.flag_check,
                                )
                                if force:
                                    # This run derives a new full-text verdict for the same paper and
                                    # reviewer; drop the one its own earlier run wrote rather than
                                    # leaving two verdicts that only MAX(id) can tell apart.
                                    self.project.db.delete_stage_decisions(
                                        source.id, "full_text", reviewer_type=self.reviewer.reviewer_type
                                    )
                                self.project.db.insert_screening_decision(ft_decision)
                            if previous_max is not None:
                                summary.archived += self.project.db.archive_ai_extractions_upto(
                                    source.id, previous_max
                                )

                    if not batch and meta is not None:
                        call_metas.append(meta)
                        summary.total_input_tokens += meta.input_tokens
                        summary.total_output_tokens += meta.output_tokens
                        summary.total_cached_input_tokens += meta.cached_input_tokens

                    summary.extracted += 1
                    if on_progress:
                        on_progress(done, len(candidates), source, None)
                except Exception as e:
                    summary.failed += 1
                    # Type included: several DB/IO errors stringify to an empty message, which
                    # reported as "failed" with no reason at all.
                    summary.failures.append(
                        {"source_id": source.id, "title": source.title, "error": f"{type(e).__name__}: {e}"}
                    )
                    if on_progress:
                        on_progress(done, len(candidates), source, e)

        if batch and (all_results or all_ft_decisions):
            with self.project.db._conn.transaction():
                self.project.db.insert_extractions(all_results)
                self.project.db.insert_screening_decisions_batch(all_ft_decisions)
        self.project.db.insert_api_calls(self.project.project_id, call_metas)
        return summary

    def _select_candidates(self, only_includes: bool) -> list[Source]:
        # Candidates = papers in the full-text queue (abstract screening settled on include) that
        # have full-text markdown. The full-text inclusion verdict is an OUTPUT of extraction (the
        # AI's _flag_check re-checks the full text), so it can't be a precondition here.
        if only_includes:
            queue = self.project.db.list_full_text_candidates(
                self.project.project_id, workflow=self.project.config.screening_workflow("abstract")
            )
            return [s for s in queue if s.markdown_path]
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
