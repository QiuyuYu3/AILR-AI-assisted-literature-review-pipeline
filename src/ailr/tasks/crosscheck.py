"""Run cross-checks over records that already exist and store the findings."""

from dataclasses import dataclass, field
from typing import Callable, Optional

from ailr.core.crosscheck import check_extraction, issues_to_records
from ailr.core.pdf_paths import resolve_markdown_path
from ailr.core.project import Project
from ailr.extraction import compose_schema


ProgressCallback = Callable[[int, int], None]


@dataclass
class CrossCheckSummary:
    checked: int = 0
    findings: int = 0
    skipped_no_markdown: int = 0
    skipped_no_extraction: int = 0
    per_issue: dict[str, int] = field(default_factory=dict)


class DeterministicCrossCheckTask:
    """The free layer: schema and verbatim-quote checks against the paper text."""

    def __init__(self, project: Project) -> None:
        self.project = project

    def run(
        self,
        source_ids: list[int],
        target_type: str = "ai",
        on_progress: Optional[ProgressCallback] = None,
    ) -> CrossCheckSummary:
        fields = compose_schema(self.project.root / self.project.config.extraction.schema_path)
        summary = CrossCheckSummary()
        db = self.project.db

        for done, source_id in enumerate(source_ids, start=1):
            rows = [
                r for r in db.list_extractions(source_id, extractor_type=target_type)
                if not str(r.get("field_name") or "").startswith("_")
            ]
            if not rows:
                summary.skipped_no_extraction += 1
            else:
                source = db.get_source(source_id)
                md_path = (
                    resolve_markdown_path(source.markdown_path, self.project.root, source_id)
                    if source else None
                )
                if md_path is None:
                    summary.skipped_no_markdown += 1
                else:
                    self._check_one(source_id, target_type, rows, fields, md_path, summary)
            if on_progress:
                on_progress(done, len(source_ids))
        return summary

    def _check_one(self, source_id, target_type, rows, fields, md_path, summary) -> None:
        paper_text = md_path.read_text(encoding="utf-8")
        issues = check_extraction(rows, fields, paper_text)

        # Rows arrive ordered by id, so the last one per field is the live record being judged.
        row_ids = {r["field_name"]: r["id"] for r in rows}
        target_id = rows[-1].get("extractor_id") or target_type

        records = issues_to_records(
            issues,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            row_ids=row_ids,
            checked_fields=list(row_ids),
        )
        self.project.db.replace_cross_checks(
            source_id, "extraction", target_type, "deterministic", records
        )
        summary.checked += 1
        summary.findings += len(issues)
        for issue in issues:
            summary.per_issue[issue.issue_code] = summary.per_issue.get(issue.issue_code, 0) + 1
