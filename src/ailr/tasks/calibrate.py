"""Quick tests: run the current prompt on a sample into the test tables, and their agreement with human decisions."""

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from ailr.core.project import Project
from ailr.criteria import load_screening_inputs, resolve_criteria
from ailr.metrics import (
    BINARY_CATEGORIES,
    THREE_WAY_CATEGORIES,
    binarize,
    cohen_kappa,
    cohen_kappa_ci,
    percent_agreement,
)
from ailr.reviewers import LLMReviewer, Reviewer, ScreeningDecision

ProgressCallback = Callable[[int, int, ScreeningDecision | None, Exception | None], None]

# Which records a quick test draws; separate from the LLM decoding seed, which only some providers accept.
DEFAULT_SAMPLE_SEED = 42


@dataclass
class QuickTestSummary:
    run_id: int
    sample_size: int
    candidates_available: int
    ai_counts: dict[str, int] = field(default_factory=lambda: {"include": 0, "exclude": 0, "uncertain": 0})
    failed: int = 0
    failures: list[dict] = field(default_factory=list)


class QuickTestTask:
    """Run AI screening on a random sample with the CURRENT prompt/criteria, into the
    isolated test_runs/test_decisions tables. Never touches screening_decisions."""

    def __init__(self, project: Project, reviewer: Reviewer, stage: str = "screening") -> None:
        self.project = project
        self.reviewer = reviewer
        self.stage = stage

    def run(
        self,
        *,
        n: int = 5,
        source_ids: list[int] | None = None,
        seed: int | None = None,
        note: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> QuickTestSummary:
        all_sources = self.project.db.list_sources(self.project.project_id)
        candidates = [s for s in all_sources if s.abstract]
        if source_ids:
            idset = set(source_ids)
            candidates = [s for s in candidates if s.id in idset]
        candidates_available = len(candidates)
        sample_size = candidates_available if source_ids else min(n, candidates_available)

        prompt_template, criteria_text, criterion_ids, additional_text = load_screening_inputs(
            self.project.root, self.project.config.screening
        )

        run_id = self.project.db.create_test_run(
            project_id=self.project.project_id,
            stage="abstract",
            sample_size=sample_size,
            prompt_snapshot=prompt_template,
            criteria_snapshot=criteria_text,
            note=note,
        )
        summary = QuickTestSummary(run_id=run_id, sample_size=sample_size, candidates_available=candidates_available)

        if sample_size == 0:
            return summary

        if source_ids:
            sample = candidates
        else:
            rng = random.Random(seed if seed is not None else DEFAULT_SAMPLE_SEED)
            sample = rng.sample(candidates, k=sample_size)

        call_metas: list = []
        for idx, source in enumerate(sample, 1):
            try:
                decision = self.reviewer.screen(source, criteria_text, prompt_template, additional_text, criterion_ids=criterion_ids, flag_check=self.project.config.screening.flag_check)
                _collect_metadata(self.reviewer, call_metas)
                self.project.db.insert_test_decision(
                    run_id=run_id,
                    source_id=source.id,
                    decision=decision.decision,
                    reasoning=decision.reasoning,
                    confidence=decision.confidence,
                    matched_criteria=decision.matched_criteria,
                    evidence_quotes=decision.evidence_quotes,
                    flag_check=decision.flag_check,
                )
                summary.ai_counts[decision.decision] += 1
                if on_progress:
                    on_progress(idx, sample_size, decision, None)
            except Exception as e:
                summary.failed += 1
                summary.failures.append({"source_id": source.id, "title": source.title, "error": str(e)})
                if on_progress:
                    on_progress(idx, sample_size, None, e)

        self.project.db.insert_api_calls(self.project.project_id, call_metas)
        return summary


def _collect_metadata(reviewer: Reviewer, call_metas: list) -> None:
    if isinstance(reviewer, LLMReviewer) and reviewer.last_metadata:
        call_metas.append(reviewer.last_metadata)


_KAPPA_CATEGORIES = ["include", "exclude", "uncertain"]


def _latest_by_reviewer_type(project: Project, sample_ids: list[int], stage: str) -> dict[int, dict[str, str]]:
    """{source_id: {reviewer_type: decision}} for these sources at ONE decision stage, latest wins.

    One stage only: a full_text decision must not enter screening κ, and vice versa. ORDER BY id
    so the last row per (source, reviewer_type) is the one kept.
    """
    if not sample_ids:
        return {}
    placeholders = ",".join("?" for _ in sample_ids)
    rows = project.db._conn.execute(
        f"""
        SELECT source_id, reviewer_type, decision FROM screening_decisions
        WHERE source_id IN ({placeholders}) AND stage = ?
        ORDER BY id
        """,
        [*sample_ids, stage],
    ).fetchall()
    by_source: dict[int, dict[str, str]] = {}
    for r in rows:
        by_source.setdefault(r["source_id"], {})[r["reviewer_type"]] = r["decision"]
    return by_source


def _agreement_stats(by_source: dict[int, dict[str, str]]) -> dict:
    """AI-vs-human κ, percent agreement, per-side counts, and the disagreeing sources. κ and
    agreement count `uncertain` as include, as the manuscript reports them, so the target a prompt
    is calibrated against is the figure that gets published; the three-way reading rides along."""
    ai_counts = {c: 0 for c in _KAPPA_CATEGORIES}
    human_counts = {c: 0 for c in _KAPPA_CATEGORIES}
    for v in by_source.values():
        if v.get("ai") in ai_counts:
            ai_counts[v["ai"]] += 1
        if v.get("human") in human_counts:
            human_counts[v["human"]] += 1

    pairs = [(v["ai"], v["human"]) for v in by_source.values() if "ai" in v and "human" in v]
    binary = binarize(pairs)
    nan = float("nan")
    return {
        "paired_count": len(pairs),
        "kappa": cohen_kappa(binary, categories=BINARY_CATEGORIES) if pairs else nan,
        # Wide on a handful of papers, which is the point: it stops a κ from 8 records reading as settled.
        "kappa_ci": cohen_kappa_ci(binary, categories=BINARY_CATEGORIES) if pairs else (nan, nan),
        "agreement": percent_agreement(binary) if pairs else nan,
        "kappa_three_way": cohen_kappa(pairs, categories=THREE_WAY_CATEGORIES) if pairs else nan,
        "agreement_three_way": percent_agreement(pairs) if pairs else nan,
        "ai_counts": ai_counts,
        "human_counts": human_counts,
        "disagreements": [
            {"source_id": sid, "ai": v["ai"], "human": v["human"]}
            for sid, v in by_source.items()
            if "ai" in v and "human" in v and v["ai"] != v["human"]
        ],
    }


@dataclass
class QuickExtractSummary:
    run_id: int
    sample_size: int
    candidates_available: int
    decision_counts: dict[str, int] = field(default_factory=lambda: {"include": 0, "exclude": 0, "uncertain": 0})
    failed: int = 0
    failures: list[dict] = field(default_factory=list)


class ExtractionQuickTestTask:
    """Run AI extraction on a random sample of papers-with-markdown using the CURRENT
    extraction prompt, into the isolated test tables. For iterating the extraction prompt."""

    def __init__(self, project: Project, reviewer: Reviewer) -> None:
        self.project = project
        self.reviewer = reviewer

    def run(
        self,
        *,
        n: int = 3,
        source_ids: list[int] | None = None,
        seed: int | None = None,
        note: str | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> QuickExtractSummary:
        from ailr.core.pdf_paths import resolve_markdown_path
        from ailr.extraction import compose_schema
        from ailr.tasks.extract import _derive_ft_decision

        config = self.project.config
        prompt_template = (self.project.root / config.extraction.prompt).read_text(encoding="utf-8")
        criteria_text, criterion_ids = resolve_criteria(self.project.root, config.screening)
        # Same composed prompt as the real run (tasks/extract.py) — additional included. A
        # calibration that omits any part of it measures a prompt nobody actually runs.
        additional_path = self.project.root / config.extraction.additional
        additional_text = additional_path.read_text(encoding="utf-8") if additional_path.exists() else ""
        fields = compose_schema(self.project.root / config.extraction.schema_path)
        with_quotes = config.extraction.output_format == "with_quotes"
        flag_check = config.extraction.flag_check

        candidates = [
            s for s in self.project.db.list_sources_with_markdown(self.project.project_id)
            if s.markdown_path
        ]
        if source_ids:
            idset = set(source_ids)
            candidates = [s for s in candidates if s.id in idset]
        candidates_available = len(candidates)
        sample_size = candidates_available if source_ids else min(n, candidates_available)

        run_id = self.project.db.create_test_run(
            project_id=self.project.project_id,
            stage="extraction",
            sample_size=sample_size,
            prompt_snapshot=prompt_template,
            criteria_snapshot=criteria_text,
            note=note,
        )
        summary = QuickExtractSummary(run_id=run_id, sample_size=sample_size, candidates_available=candidates_available)
        if sample_size == 0:
            return summary

        if source_ids:
            sample = candidates
        else:
            rng = random.Random(seed if seed is not None else DEFAULT_SAMPLE_SEED)
            sample = rng.sample(candidates, k=sample_size)

        call_metas: list = []
        for idx, source in enumerate(sample, 1):
            md_path = resolve_markdown_path(source.markdown_path, self.project.root, source.id)
            if md_path is None:
                summary.failed += 1
                summary.failures.append({"source_id": source.id, "title": source.title, "error": "markdown file missing"})
                if on_progress:
                    on_progress(idx, sample_size, None, None)
                continue
            try:
                extraction = self.reviewer.extract(
                    source=source,
                    paper_text=md_path.read_text(encoding="utf-8"),
                    fields=fields,
                    prompt_template=prompt_template,
                    criteria_text=criteria_text,
                    additional_text=additional_text,
                    with_quotes=with_quotes,
                    flag_check=flag_check,
                    criterion_ids=criterion_ids,
                )
                _collect_metadata(self.reviewer, call_metas)
                serialized = [
                    {"field": r.field_name, "value": r.value, "quote": r.source_quote, "confidence": r.confidence}
                    for r in extraction.results
                ]
                ft_decision = _derive_ft_decision(extraction.flag_check) if extraction.flag_check else None
                self.project.db.insert_test_extraction(
                    run_id=run_id,
                    source_id=source.id,
                    full_text_decision=ft_decision,
                    fields=serialized,
                    flag_check=extraction.flag_check,
                )
                if ft_decision in summary.decision_counts:
                    summary.decision_counts[ft_decision] += 1
                if on_progress:
                    on_progress(idx, sample_size, None, None)
            except Exception as e:
                summary.failed += 1
                summary.failures.append({"source_id": source.id, "title": source.title, "error": str(e)})
                if on_progress:
                    on_progress(idx, sample_size, None, e)

        self.project.db.insert_api_calls(self.project.project_id, call_metas)
        return summary


def quick_test_agreement(project: Project, run_id: int, test_stage: str = "abstract") -> dict:
    """AI-vs-human agreement for one quick-test run. The AI side comes from the isolated test
    tables, so it reflects the prompt THAT RUN used; the human side is the reviewer's real
    decision, whenever they got around to making it. Recompute on every render: the human may
    decide a paper long after the run, or change their mind."""
    if test_stage == "extraction":
        ai_by_source = {
            r["source_id"]: r["full_text_decision"]
            for r in project.db.list_test_extractions(run_id)
        }
        decision_stage = "full_text"
    else:
        ai_by_source = {r["source_id"]: r["decision"] for r in project.db.list_test_decisions(run_id)}
        decision_stage = "abstract"

    humans = _latest_by_reviewer_type(project, list(ai_by_source), decision_stage)
    by_source: dict[int, dict[str, str]] = {}
    for sid, ai in ai_by_source.items():
        if ai not in _KAPPA_CATEGORIES:  # extraction leaves this null when flag_check is off
            continue
        entry = {"ai": ai}
        human = humans.get(sid, {}).get("human")
        if human:
            entry["human"] = human
        by_source[sid] = entry
    return _agreement_stats(by_source)
