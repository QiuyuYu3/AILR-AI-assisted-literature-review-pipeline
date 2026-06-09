"""CalibrationTask: sample N sources, run AI on them, report agreement vs human decisions."""

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Optional

from ailr.core.config import resolve_stage_llm
from ailr.core.project import Project
from ailr.core.source import Source
from ailr.metrics import cohen_kappa, percent_agreement
from ailr.reviewers import Reviewer, ScreeningDecision
from ailr.reviewers import LLMReviewer

ProgressCallback = Callable[[int, int, Optional[ScreeningDecision], Optional[Exception]], None]


@dataclass
class QuickTestSummary:
    run_id: int
    sample_size: int
    candidates_available: int
    ai_counts: dict[str, int] = field(default_factory=lambda: {"include": 0, "exclude": 0, "uncertain": 0})
    failed: int = 0
    failures: list[dict] = field(default_factory=list)
    total_cost_estimate: float = 0.0


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
        seed: Optional[int] = None,
        note: Optional[str] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> QuickTestSummary:
        all_sources = self.project.db.list_sources(self.project.project_id)
        candidates = [s for s in all_sources if s.abstract]
        candidates_available = len(candidates)
        sample_size = min(n, candidates_available)

        criteria_path = self.project.root / self.project.config.screening.criteria
        prompt_path = self.project.root / self.project.config.screening.prompt
        criteria_text = criteria_path.read_text(encoding="utf-8")
        prompt_template = prompt_path.read_text(encoding="utf-8")

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

        rng = random.Random(seed if seed is not None else self.project.config.llm.seed or 0)
        sample = rng.sample(candidates, k=sample_size)

        for idx, source in enumerate(sample, 1):
            try:
                decision = self.reviewer.screen(source, criteria_text, prompt_template)
                self.project.db.insert_test_decision(
                    run_id=run_id,
                    source_id=source.id,
                    decision=decision.decision,
                    reasoning=decision.reasoning,
                    confidence=decision.confidence,
                    matched_criteria=decision.matched_criteria,
                    evidence_quotes=decision.evidence_quotes,
                )
                summary.ai_counts[decision.decision] += 1
                if isinstance(self.reviewer, LLMReviewer) and self.reviewer.last_metadata:
                    summary.total_cost_estimate += self.reviewer.last_metadata.cost_estimate
                if on_progress:
                    on_progress(idx, sample_size, decision, None)
            except Exception as e:
                summary.failed += 1
                summary.failures.append({"source_id": source.id, "title": source.title, "error": str(e)})
                if on_progress:
                    on_progress(idx, sample_size, None, e)

        self.project.db.set_test_run_cost(run_id, summary.total_cost_estimate)
        return summary


def sample_agreement(project: Project, sample_ids: list[int]) -> dict:
    """AI-vs-human agreement on a set of sources (abstract screening decisions).
    Used by the calibration UI to recompute κ after humans review the sample."""
    result = {
        "paired_count": 0,
        "kappa": float("nan"),
        "agreement": float("nan"),
        "ai_counts": {"include": 0, "exclude": 0, "uncertain": 0},
        "human_counts": {"include": 0, "exclude": 0, "uncertain": 0},
        "disagreements": [],
    }
    if not sample_ids:
        return result

    placeholders = ",".join("?" for _ in sample_ids)
    rows = project.db._conn.execute(
        f"""
        SELECT source_id, reviewer_type, decision FROM screening_decisions
        WHERE source_id IN ({placeholders}) AND stage = 'abstract'
        """,
        sample_ids,
    ).fetchall()

    by_source: dict[int, dict[str, str]] = {}
    for r in rows:
        by_source.setdefault(r["source_id"], {})[r["reviewer_type"]] = r["decision"]

    for v in by_source.values():
        if v.get("ai") in result["ai_counts"]:
            result["ai_counts"][v["ai"]] += 1
        if v.get("human") in result["human_counts"]:
            result["human_counts"][v["human"]] += 1

    pairs = [(v["ai"], v["human"]) for v in by_source.values() if "ai" in v and "human" in v]
    result["paired_count"] = len(pairs)
    if pairs:
        result["kappa"] = cohen_kappa(pairs, categories=["include", "exclude", "uncertain"])
        result["agreement"] = percent_agreement(pairs)
    result["disagreements"] = [
        {"source_id": sid, "ai": v["ai"], "human": v["human"]}
        for sid, v in by_source.items()
        if "ai" in v and "human" in v and v["ai"] != v["human"]
    ]
    return result


@dataclass
class CalibrationSummary:
    stage: str
    sample_round: int
    sample_size: int
    candidates_available: int
    ai_counts: dict[str, int] = field(default_factory=lambda: {"include": 0, "exclude": 0, "uncertain": 0})
    human_counts: dict[str, int] = field(default_factory=lambda: {"include": 0, "exclude": 0, "uncertain": 0})
    paired_count: int = 0
    kappa: float = float("nan")
    agreement: float = float("nan")
    failed: int = 0
    failures: list[dict] = field(default_factory=list)
    total_cost_estimate: float = 0.0


@dataclass
class QuickExtractSummary:
    run_id: int
    sample_size: int
    candidates_available: int
    decision_counts: dict[str, int] = field(default_factory=lambda: {"include": 0, "exclude": 0, "uncertain": 0})
    failed: int = 0
    failures: list[dict] = field(default_factory=list)
    total_cost_estimate: float = 0.0


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
        seed: Optional[int] = None,
        note: Optional[str] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> QuickExtractSummary:
        from pathlib import Path
        from ailr.extraction import compose_schema
        from ailr.tasks.extract import _derive_ft_decision

        config = self.project.config
        prompt_template = (self.project.root / config.extraction.prompt).read_text(encoding="utf-8")
        criteria_text = (self.project.root / config.screening.criteria).read_text(encoding="utf-8")
        fields = compose_schema(self.project.root / config.extraction.schema_path)
        with_quotes = config.extraction.output_format == "with_quotes"
        flag_check = config.extraction.flag_check

        candidates = [
            s for s in self.project.db.list_sources_with_markdown(self.project.project_id)
            if s.markdown_path
        ]
        candidates_available = len(candidates)
        sample_size = min(n, candidates_available)

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

        rng = random.Random(seed if seed is not None else self.project.config.llm.seed or 0)
        sample = rng.sample(candidates, k=sample_size)

        for idx, source in enumerate(sample, 1):
            md_path = Path(source.markdown_path)
            if not md_path.is_absolute():
                md_path = self.project.root / md_path
            if not md_path.exists():
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
                    with_quotes=with_quotes,
                    flag_check=flag_check,
                )
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
                if isinstance(self.reviewer, LLMReviewer) and self.reviewer.last_metadata:
                    summary.total_cost_estimate += self.reviewer.last_metadata.cost_estimate
                if on_progress:
                    on_progress(idx, sample_size, None, None)
            except Exception as e:
                summary.failed += 1
                summary.failures.append({"source_id": source.id, "title": source.title, "error": str(e)})
                if on_progress:
                    on_progress(idx, sample_size, None, e)

        self.project.db.set_test_run_cost(run_id, summary.total_cost_estimate)
        return summary


class CalibrationTask:
    def __init__(self, project: Project, reviewer: Reviewer, stage: str = "screening") -> None:
        if stage not in ("screening", "extraction"):
            raise ValueError(f"Unknown stage: {stage}")
        self.project = project
        self.reviewer = reviewer
        self.stage = stage

    def determine_sample_size(self, n_arg: Optional[int], candidates_available: int) -> int:
        if n_arg is not None:
            return min(n_arg, candidates_available)

        if self.stage == "screening":
            cal_cfg = self.project.config.screening.calibration
        else:
            cal_cfg = self.project.config.extraction.calibration

        if cal_cfg.n is not None:
            return min(cal_cfg.n, candidates_available)

        target = max(round(candidates_available * cal_cfg.fraction), cal_cfg.min)
        return min(target, candidates_available)

    def run(
        self,
        *,
        n: Optional[int] = None,
        seed: Optional[int] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> CalibrationSummary:
        if self.stage == "extraction":
            raise NotImplementedError("Extraction calibration arrives with Phase 4.")

        candidates = self.project.db.list_calibration_candidates(
            project_id=self.project.project_id, stage=self.stage
        )
        candidates_available = len(candidates)

        sample_size = self.determine_sample_size(n, candidates_available)
        sample_round = self.project.db.next_sample_round(self.project.project_id, self.stage)

        summary = CalibrationSummary(
            stage=self.stage,
            sample_round=sample_round,
            sample_size=sample_size,
            candidates_available=candidates_available,
        )

        if sample_size == 0:
            return summary

        rng_seed = seed if seed is not None else self.project.config.llm.seed or 0
        rng = random.Random(rng_seed + sample_round)
        sample = rng.sample(candidates, k=sample_size)
        sample_ids = [s.id for s in sample if s.id is not None]

        self.project.db.create_calibration_sample(
            project_id=self.project.project_id,
            source_ids=sample_ids,
            stage=self.stage,
            sample_round=sample_round,
        )

        criteria_path = self.project.root / self.project.config.screening.criteria
        prompt_path = self.project.root / self.project.config.screening.prompt
        criteria_text = criteria_path.read_text(encoding="utf-8")
        prompt_template = prompt_path.read_text(encoding="utf-8")

        for idx, source in enumerate(sample, 1):
            existing_ai = self._existing_ai_decision(source.id)
            if existing_ai is not None:
                summary.ai_counts[existing_ai] += 1
                if on_progress:
                    on_progress(idx, sample_size, None, None)
                continue

            try:
                decision = self.reviewer.screen(source, criteria_text, prompt_template)
                decision.source_id = source.id
                self.project.db.insert_screening_decision(decision)
                summary.ai_counts[decision.decision] += 1

                if isinstance(self.reviewer, LLMReviewer) and self.reviewer.last_metadata:
                    meta = self.reviewer.last_metadata
                    self.project.db.insert_api_call(self.project.project_id, meta)
                    summary.total_cost_estimate += meta.cost_estimate

                if on_progress:
                    on_progress(idx, sample_size, decision, None)
            except Exception as e:
                summary.failed += 1
                summary.failures.append(
                    {"source_id": source.id, "title": source.title, "error": str(e)}
                )
                if on_progress:
                    on_progress(idx, sample_size, None, e)

        self._compute_agreement(summary, sample_ids)
        return summary

    def _existing_ai_decision(self, source_id: Optional[int]) -> Optional[str]:
        if source_id is None:
            return None
        row = self.project.db._conn.execute(
            "SELECT decision FROM screening_decisions WHERE source_id = ? AND reviewer_type = 'ai' ORDER BY id DESC LIMIT 1",
            (source_id,),
        ).fetchone()
        return row["decision"] if row else None

    def _compute_agreement(self, summary: CalibrationSummary, sample_ids: list[int]) -> None:
        if not sample_ids:
            return

        placeholders = ",".join("?" for _ in sample_ids)
        sql = f"""
            SELECT
                d.source_id,
                d.reviewer_type,
                d.decision
            FROM screening_decisions d
            WHERE d.source_id IN ({placeholders})
        """
        rows = self.project.db._conn.execute(sql, sample_ids).fetchall()

        by_source: dict[int, dict[str, str]] = {}
        for r in rows:
            by_source.setdefault(r["source_id"], {})[r["reviewer_type"]] = r["decision"]

        for human_decision in [v.get("human") for v in by_source.values()]:
            if human_decision:
                summary.human_counts[human_decision] = summary.human_counts.get(human_decision, 0) + 1

        pairs = [
            (v["ai"], v["human"])
            for v in by_source.values()
            if "ai" in v and "human" in v
        ]
        summary.paired_count = len(pairs)
        if pairs:
            summary.kappa = cohen_kappa(pairs, categories=["include", "exclude", "uncertain"])
            summary.agreement = percent_agreement(pairs)
