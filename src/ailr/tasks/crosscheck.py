"""Run cross-checks over records that already exist and store the findings."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ailr.core.crosscheck import check_extraction, issues_to_records, llm_verdicts_to_records
from ailr.core.pdf_paths import resolve_markdown_path
from ailr.core.project import Project
from ailr.crosschecker import LLMCrossChecker, load_prompt
from ailr.extraction import FieldSpec, compose_schema


ProgressCallback = Callable[[int, int], None]


@dataclass
class CrossCheckSummary:
    checked: int = 0          # (source, extractor) pairs checked
    sources: int = 0          # sources with at least one pair checked
    findings: int = 0         # non-agreeing verdicts
    skipped_no_markdown: int = 0
    skipped_no_extraction: int = 0
    failed: int = 0
    failures: list[dict] = field(default_factory=list)
    per_issue: dict[str, int] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0

    def text(self) -> str:
        if not self.checked:
            if self.failed:
                return f"Nothing checked: all {self.failed} attempt(s) failed. {self._failure_tail()}"
            if self.skipped_no_markdown:
                return f"Nothing checked: {self.skipped_no_markdown} source(s) have no converted full text."
            return "Nothing to check: no extractions found for the selected reviewers."

        base = f"Cross-checked {self.checked} extraction(s) across {self.sources} paper(s)"
        if not self.findings:
            base += ". Nothing flagged."
        elif self.per_issue:
            detail = ", ".join(f"{code}: {n}" for code, n in sorted(self.per_issue.items()))
            base += f" — {self.findings} finding(s): {detail}."
        else:
            base += f" — {self.findings} finding(s)."
        if self.input_tokens or self.output_tokens:
            base += f" {self.input_tokens + self.output_tokens:,} tokens."
        if self.skipped_no_markdown:
            base += f" Skipped {self.skipped_no_markdown} without full text."
        if self.failed:
            base += f" {self.failed} failed. {self._failure_tail()}"
        return base

    def _failure_tail(self) -> str:
        return " | ".join(f"#{f.get('source_id')}: {f.get('error')}" for f in self.failures[:3])


class _CrossCheckTask:
    """Shared walk over (source, extractor) pairs. Subclasses supply the check itself."""

    check_kind = ""
    stage = "extraction"

    def __init__(self, project: Project) -> None:
        self.project = project

    def run(
        self,
        source_ids: list[int],
        targets: Optional[list[str]] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> CrossCheckSummary:
        if targets is None:
            targets = list(self.project.config.crosscheck.targets)
        fields = compose_schema(self.project.root / self.project.config.extraction.schema_path)
        summary = CrossCheckSummary()

        for done, source_id in enumerate(source_ids, start=1):
            self._check_source(source_id, targets, fields, summary)
            if on_progress:
                on_progress(done, len(source_ids))
        return summary

    def _check_source(
        self, source_id: int, targets: list[str], fields: list[FieldSpec], summary: CrossCheckSummary
    ) -> None:
        groups = self._extractor_groups(source_id, targets)
        if not groups:
            summary.skipped_no_extraction += 1
            return
        source = self.project.db.get_source(source_id)
        md_path = (
            resolve_markdown_path(source.markdown_path, self.project.root, source_id)
            if source else None
        )
        if md_path is None:
            summary.skipped_no_markdown += 1
            return

        paper_text = md_path.read_text(encoding="utf-8")
        any_checked = False
        for (target_type, target_id), rows in groups.items():
            try:
                self._check_one(source, target_type, target_id, rows, fields, paper_text, summary)
                any_checked = True
            except Exception as e:
                summary.failed += 1
                summary.failures.append({"source_id": source_id, "error": str(e)})
        if any_checked:
            summary.sources += 1

    def _extractor_groups(
        self, source_id: int, targets: list[str]
    ) -> dict[tuple[str, str], list[dict]]:
        """Rows split per (extractor_type, extractor_id): a source can carry an AI extraction and
        one row set per human reviewer, and each is checked and stored on its own."""
        groups: dict[tuple[str, str], list[dict]] = {}
        for target_type in targets:
            for row in self.project.db.list_extractions(source_id, extractor_type=target_type):
                if str(row.get("field_name") or "").startswith("_"):
                    continue
                key = (target_type, row.get("extractor_id") or target_type)
                groups.setdefault(key, []).append(row)
        return groups

    def _store(self, source_id, target_type, target_id, records) -> None:
        self.project.db.replace_cross_checks(
            source_id, self.stage, target_type, target_id, self.check_kind, records
        )

    def _check_one(self, source, target_type, target_id, rows, fields, paper_text, summary) -> None:
        raise NotImplementedError


class DeterministicCrossCheckTask(_CrossCheckTask):
    """The free layer: schema and verbatim-quote checks against the paper text."""

    check_kind = "deterministic"

    def _check_one(self, source, target_type, target_id, rows, fields, paper_text, summary) -> None:
        issues = check_extraction(rows, fields, paper_text)
        # Rows arrive ordered by id, so the last one per field is the live record being judged.
        row_ids = {r["field_name"]: r["id"] for r in rows}

        records = issues_to_records(
            issues,
            source_id=source.id,
            target_type=target_type,
            target_id=target_id,
            row_ids=row_ids,
            checked_fields=list(row_ids),
            stage=self.stage,
        )
        self._store(source.id, target_type, target_id, records)
        summary.checked += 1
        summary.findings += len(issues)
        for issue in issues:
            summary.per_issue[issue.issue_code] = summary.per_issue.get(issue.issue_code, 0) + 1


class LLMCrossCheckTask(_CrossCheckTask):
    """The paid layer: a second model judges each recorded value against its quote and the paper."""

    check_kind = "llm"

    def __init__(self, project: Project, checker: LLMCrossChecker) -> None:
        super().__init__(project)
        self.checker = checker
        self._prompt_template: Optional[str] = None
        self._additional: Optional[str] = None

    def _prompt(self) -> str:
        if self._prompt_template is None:
            self._prompt_template = load_prompt(
                self.project.root, self.project.config.crosscheck.prompt
            )
        return self._prompt_template

    def _additional_text(self) -> str:
        if self._additional is None:
            path = self.project.root / self.project.config.crosscheck.additional
            self._additional = path.read_text(encoding="utf-8") if path.exists() else ""
        return self._additional

    def _check_one(self, source, target_type, target_id, rows, fields, paper_text, summary) -> None:
        # One row per field, newest first, so the model judges the live record and not a superseded one.
        latest = {r["field_name"]: r for r in rows}
        live_rows = list(latest.values())

        verdicts = self.checker.check(
            source, paper_text, live_rows, fields, self._prompt(),
            project_name=self.project.config.project.name,
            additional=self._additional_text(),
        )
        metadata = self.checker.last_metadata
        if metadata is not None:
            self.project.db.insert_api_call(self.project.project_id, metadata)
            summary.input_tokens += getattr(metadata, "input_tokens", 0) or 0
            summary.output_tokens += getattr(metadata, "output_tokens", 0) or 0

        records = llm_verdicts_to_records(
            verdicts,
            source_id=source.id,
            target_type=target_type,
            target_id=target_id,
            row_ids={r["field_name"]: r["id"] for r in live_rows},
            checker_id=self.checker.checker_id,
            llm_params=self.checker.llm_params(),
            prompt_version=self.checker.prompt_version,
            stage=self.stage,
        )
        self._store(source.id, target_type, target_id, records)
        summary.checked += 1
        summary.findings += sum(1 for v in verdicts.values() if v.get("verdict") != "agree")


def quick_test_target_id(run_id: int) -> str:
    """cross_checks has no run column, so the run is carried in target_id. That keeps the replace
    key distinct per run, so re-checking one run never clears another's findings."""
    return f"run:{run_id}"


def _quick_test_rows(test_extraction: dict) -> list[dict]:
    """A quick-test paper stores its fields as one JSON blob; unpack it into the per-field row
    shape the checker reads. Every row points back at the same test_extractions id."""
    return [
        {
            "id": test_extraction["id"],
            "field_name": f.get("field"),
            "value": f.get("value"),
            "source_quote": f.get("quote"),
        }
        for f in test_extraction.get("fields", [])
        if f.get("field") and not str(f["field"]).startswith("_")
    ]


class QuickTestCrossCheckTask(LLMCrossCheckTask):
    """The same cross-check, run over a quick-test run's output instead of the project's own
    extractions. Calibration is the rehearsal: same checker, same prompt, smaller sample."""

    stage = "quick_test"

    def __init__(self, project: Project, checker: LLMCrossChecker, run_id: int) -> None:
        super().__init__(project, checker)
        self.run_id = run_id
        self._rows_by_source: dict[int, list[dict]] = {}

    def run_for_run(self, on_progress: Optional[ProgressCallback] = None) -> CrossCheckSummary:
        self._rows_by_source = {
            r["source_id"]: _quick_test_rows(r)
            for r in self.project.db.list_test_extractions(self.run_id)
        }
        return self.run(list(self._rows_by_source), targets=["ai"], on_progress=on_progress)

    def _extractor_groups(self, source_id, targets):
        rows = self._rows_by_source.get(source_id) or []
        return {("ai", quick_test_target_id(self.run_id)): rows} if rows else {}
