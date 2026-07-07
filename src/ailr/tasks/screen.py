"""ScreeningTask: iterate un-screened sources, call reviewer, persist decisions."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

from ailr.core.project import Project
from ailr.criteria import load_screening_inputs
from ailr.reviewers import Reviewer, ScreeningDecision
from ailr.reviewers import LLMReviewer


@dataclass
class ScreenRunSummary:
    total: int = 0
    screened: int = 0
    include: int = 0
    exclude: int = 0
    uncertain: int = 0
    skipped_no_abstract: int = 0
    failed: int = 0
    failures: list[dict] = field(default_factory=list)
    total_cost_estimate: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_input_tokens: int = 0

    def add_decision(self, decision: ScreeningDecision) -> None:
        self.screened += 1
        if decision.decision == "include":
            self.include += 1
        elif decision.decision == "exclude":
            self.exclude += 1
        else:
            self.uncertain += 1


ProgressCallback = Callable[[int, int, Optional[ScreeningDecision], Optional[Exception]], None]


class ScreeningTask:
    def __init__(self, project: Project, reviewer: Reviewer) -> None:
        self.project = project
        self.reviewer = reviewer

    def run(
        self,
        *,
        limit: Optional[int] = None,
        on_progress: Optional[ProgressCallback] = None,
        batch: bool = False,
        workers: Optional[int] = None,
    ) -> ScreenRunSummary:
        config = self.project.config
        prompt_template, criteria_text, criterion_ids, additional_text = load_screening_inputs(
            self.project.root, config.screening
        )
        if workers is None:
            workers = config.screening.workers
        workers = max(1, workers)

        un_screened = self.project.db.list_unscreened(
            project_id=self.project.project_id,
            reviewer_type=self.reviewer.reviewer_type,
        )
        if limit is not None:
            un_screened = un_screened[:limit]

        summary = ScreenRunSummary(total=len(un_screened))

        # Mock runs buffer all decisions and write them in one multi-row INSERT at the end (fast,
        # no per-row Neon round trips); real runs keep the per-row, per-commit path for durability.
        buffer: list[ScreeningDecision] = []

        def _save(decision: ScreeningDecision) -> None:
            if batch:
                buffer.append(decision)
            else:
                self.project.db.insert_screening_decision(decision)

        done = 0
        to_screen = []
        for source in un_screened:
            if not source.abstract:
                summary.skipped_no_abstract += 1
                # Insert an uncertain decision so the source is not screened repeatedly
                placeholder = ScreeningDecision(
                    decision="uncertain",
                    reasoning="Abstract not available; full-text review required.",
                    reviewer_type=self.reviewer.reviewer_type,
                    reviewer_id=self.reviewer.reviewer_id,
                    source_id=source.id,
                    confidence=1,  # bottom of the 1-10 scale used by LLM decisions
                )
                _save(placeholder)
                summary.add_decision(placeholder)
                done += 1
                if on_progress:
                    on_progress(done, summary.total, placeholder, None)
            else:
                to_screen.append(source)

        # LLM calls run in a small thread pool (they mostly wait on the network); all DB writes,
        # summary updates, and progress callbacks stay on this thread.
        def _screen_one(source):
            decision = self.reviewer.screen(
                source, criteria_text, prompt_template, additional_text, criterion_ids=criterion_ids
            )
            decision.source_id = source.id
            meta = self.reviewer.last_metadata if isinstance(self.reviewer, LLMReviewer) else None
            return decision, meta

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_screen_one, s): s for s in to_screen}
            for fut in as_completed(futures):
                source = futures[fut]
                done += 1
                try:
                    decision, meta = fut.result()
                    _save(decision)
                    summary.add_decision(decision)

                    if not batch and meta is not None:
                        self.project.db.insert_api_call(self.project.project_id, meta)
                        summary.total_cost_estimate += meta.cost_estimate
                        summary.total_input_tokens += meta.input_tokens
                        summary.total_output_tokens += meta.output_tokens
                        summary.total_cached_input_tokens += meta.cached_input_tokens

                    if on_progress:
                        on_progress(done, summary.total, decision, None)
                except Exception as e:
                    summary.failed += 1
                    summary.failures.append(
                        {"source_id": source.id, "title": source.title, "error": str(e)}
                    )
                    if on_progress:
                        on_progress(done, summary.total, None, e)

        if batch and buffer:
            with self.project.db._conn.transaction():
                self.project.db.insert_screening_decisions_batch(buffer)
        return summary
