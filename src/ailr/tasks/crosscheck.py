"""Run cross-checks over records that already exist and store the findings."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ailr.core.crosscheck import check_extraction, issues_to_records
from ailr.core.pdf_paths import resolve_markdown_path
from ailr.core.project import Project
from ailr.extraction import FieldSpec, compose_schema


ProgressCallback = Callable[[int, int], None]


@dataclass
class CrossCheckSummary:
    checked: int = 0          # (source, extractor) pairs checked
    sources: int = 0          # sources with at least one pair checked
    findings: int = 0
    skipped_no_markdown: int = 0
    skipped_no_extraction: int = 0
    per_issue: dict[str, int] = field(default_factory=dict)

    def text(self) -> str:
        if not self.checked:
            if self.skipped_no_markdown:
                return f"Nothing checked: {self.skipped_no_markdown} source(s) have no converted full text."
            return "Nothing to check: no extractions found for the selected reviewers."
        if not self.findings:
            base = f"Cross-checked {self.checked} extraction(s) across {self.sources} paper(s). Nothing flagged."
        else:
            detail = ", ".join(f"{code}: {n}" for code, n in sorted(self.per_issue.items()))
            base = (
                f"Cross-checked {self.checked} extraction(s) across {self.sources} paper(s) — "
                f"{self.findings} finding(s): {detail}."
            )
        if self.skipped_no_markdown:
            base += f" Skipped {self.skipped_no_markdown} without full text."
        return base


class DeterministicCrossCheckTask:
    """The free layer: schema and verbatim-quote checks against the paper text."""

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
        md_path = self._markdown_for(source_id)
        if md_path is None:
            summary.skipped_no_markdown += 1
            return

        paper_text = md_path.read_text(encoding="utf-8")
        for (target_type, target_id), rows in groups.items():
            self._check_one(source_id, target_type, target_id, rows, fields, paper_text, summary)
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

    def _markdown_for(self, source_id: int) -> Optional[Path]:
        source = self.project.db.get_source(source_id)
        if source is None:
            return None
        return resolve_markdown_path(source.markdown_path, self.project.root, source_id)

    def _check_one(
        self, source_id, target_type, target_id, rows, fields, paper_text, summary
    ) -> None:
        issues = check_extraction(rows, fields, paper_text)
        # Rows arrive ordered by id, so the last one per field is the live record being judged.
        row_ids = {r["field_name"]: r["id"] for r in rows}

        records = issues_to_records(
            issues,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            row_ids=row_ids,
            checked_fields=list(row_ids),
        )
        self.project.db.replace_cross_checks(
            source_id, "extraction", target_type, target_id, "deterministic", records
        )
        summary.checked += 1
        summary.findings += len(issues)
        for issue in issues:
            summary.per_issue[issue.issue_code] = summary.per_issue.get(issue.issue_code, 0) + 1
