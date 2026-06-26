"""Project: the top-level handle on a review project directory."""

import json
from importlib.resources import files
from pathlib import Path
from typing import Any, Optional

from ailr.core.config import Config, load_config
from ailr.core.database import Database
from ailr.core.source import Source, source_to_record
from ailr.exceptions import (
    InputNotFoundError,
    ProjectNotFoundError,
    UnsupportedFormatError,
)
from ailr.ingest import bibtex as bibtex_ingest
from ailr.ingest import csv as csv_ingest
from ailr.ingest import dedup
from ailr.ingest import ris as ris_ingest

def _authors_str(src: Source) -> Optional[str]:
    return json.dumps(src.authors) if src.authors else None


def _record_score(src: Source) -> tuple:
    """Completeness ranking for choosing which of two title-duplicate records to keep:
    DOI first, then authors, then other bibliographic fields."""
    others = sum(1 for v in (src.abstract, src.year, src.journal, src.pmid) if v)
    return (1 if src.doi else 0, 1 if src.authors else 0, others)


TEMPLATE_FILES = [
    ("config.yaml.tmpl", "lit_review.yaml"),
    ("screening_prompt.txt.tmpl", "prompts/screening.txt"),
    ("extraction_prompt.txt.tmpl", "prompts/extraction.txt"),
    ("schema.yaml.tmpl", "schema.yaml"),
    ("inclusion_criteria.md.tmpl", "inclusion_criteria.md"),
    ("gitignore.tmpl", ".gitignore"),
]


class IngestResult:
    def __init__(
        self,
        parsed: int,
        imported: int,
        deduplicated: int,
        failed: int,
        failures: Optional[list[dict[str, Any]]] = None,
        title_matches: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self.parsed = parsed
        self.imported = imported
        self.deduplicated = deduplicated
        self.failed = failed
        self.failures = failures or []
        self.title_matches = title_matches or []

    def __repr__(self) -> str:
        return (
            f"<IngestResult parsed={self.parsed} imported={self.imported} "
            f"deduplicated={self.deduplicated} failed={self.failed}>"
        )


class Project:
    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        if not (self._root / "lit_review.yaml").exists():
            raise ProjectNotFoundError(f"No lit_review.yaml in {self._root}")
        self._config = load_config(self._root)
        # DB precedence: storage.database_url in yaml (Postgres, shared with the team via the
        # project folder) > local SQLite file. The yaml holds the DB password, so keep it out of
        # public git (the project template gitignores lit_review.yaml).
        db_url = self._config.storage.database_url
        audit_log_path = self._root / self._config.logging.audit_log
        if db_url:
            self._db = Database(db_url, audit_log_path=audit_log_path)
        else:
            db_path = self._root / self._config.storage.database
            self._db = Database(db_path, audit_log_path=audit_log_path)
        self._db.init_schema()
        self._project_id = self._db.get_or_create_project(self._config.project.name)

    @classmethod
    def init(
        cls,
        root: Path,
        mode: str = "assisted",
        preset: Optional[Path] = None,
    ) -> "Project":
        root = Path(root).resolve()
        config_path = root / "lit_review.yaml"
        if config_path.exists():
            raise ProjectNotFoundError(f"Project already initialized at {root}")

        if mode not in ("strict", "assisted", "custom"):
            raise ProjectNotFoundError(f"Unknown mode: {mode}")

        root.mkdir(parents=True, exist_ok=True)
        (root / "prompts").mkdir(exist_ok=True)
        (root / "data" / "raw").mkdir(parents=True, exist_ok=True)
        (root / "data" / "pdfs").mkdir(parents=True, exist_ok=True)
        (root / "data" / "markdown").mkdir(parents=True, exist_ok=True)

        substitutions = {
            "project_name": root.name,
            "mode": mode,
        }
        if preset is not None:
            substitutions["mode_preset"] = str(preset)

        tmpl_pkg = files("ailr.templates")
        for tmpl_name, dest_rel in TEMPLATE_FILES:
            src = tmpl_pkg / tmpl_name
            dst = root / dest_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            _render_template(src.read_text(encoding="utf-8"), dst, substitutions)

        return cls(root)

    @classmethod
    def load(cls, root: Path) -> "Project":
        return cls(root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def config(self) -> Config:
        return self._config

    @property
    def db(self) -> Database:
        return self._db

    @property
    def project_id(self) -> int:
        return self._project_id

    def ingest(
        self,
        file_path: Path,
        source_database: Optional[str] = None,
    ) -> IngestResult:
        file_path = Path(file_path).expanduser().resolve()

        if not file_path.exists():
            raise InputNotFoundError(f"File not found: {file_path}")

        ext = file_path.suffix.lower()
        if ext == ".ris":
            sources = ris_ingest.parse_ris(file_path, source_database=source_database)
        elif ext == ".bib":
            sources = bibtex_ingest.parse_bibtex(file_path, source_database=source_database)
        elif ext in (".csv", ".tsv", ".txt"):
            sources = csv_ingest.parse_csv(file_path, source_database=source_database)
        else:
            raise UnsupportedFormatError(f"Unsupported file extension: {ext}")

        # Empty-string DOIs (e.g. from IEEE RIS) are not "missing": PostgreSQL's (project_id, doi)
        # unique key treats '' as a real value, so two of them collide. Normalize blanks to None.
        for s in sources:
            s.doi = (s.doi or "").strip() or None

        parsed = len(sources)
        sources, batch_dups = dedup.dedup_by_doi(sources)
        for s in sources:
            s.project_id = self._project_id

        # Collect every dropped record (with its full JSON so it can be restored later) and
        # write them in one bulk transaction instead of one INSERT+commit per duplicate.
        def _dup_row(src: Source, reason: str, matched_id: Optional[int] = None) -> tuple:
            return (self._project_id, src.title, _authors_str(src), src.doi, reason,
                    matched_id, json.dumps(source_to_record(src)))

        dup_rows: list[tuple] = [_dup_row(d, "doi (within import)") for d in batch_dups]

        cross_dups = 0
        unique_sources: list[Source] = []

        # Cross-import DOI dedup against existing rows, using one query instead of one per record.
        existing_doi_index = self._db.existing_doi_index(self._project_id)
        for src in sources:
            matched_id = existing_doi_index.get(src.doi.lower().strip()) if src.doi else None
            if matched_id is not None:
                cross_dups += 1
                dup_rows.append(_dup_row(src, "doi", matched_id))
                continue
            unique_sources.append(src)

        existing = self._db.list_sources(project_id=self._project_id)
        candidates_for_insert, title_matches_raw = dedup.dedup_by_title(
            unique_sources, existing, threshold=95
        )

        title_matches: list[dict[str, Any]] = [
            {
                "new_title": new.title,
                "existing_title": existing_src.title,
                "existing_id": existing_src.id,
            }
            for new, existing_src in title_matches_raw
        ]
        # A title match keeps ONE whole record — the more complete one (DOI first, then authors,
        # then other fields) — and drops the other. The dropped record is logged in full so it can
        # be restored if the match was a false positive (no field-level mixing across papers).
        overwrites: list[tuple[int, Source]] = []
        for new, existing_src in title_matches_raw:
            if _record_score(new) > _record_score(existing_src):
                # incoming is strictly more complete: it takes over the existing row (id preserved,
                # so any attached work stays valid); the existing record's old content is the drop.
                overwrites.append((existing_src.id, new))
                dup_rows.append(_dup_row(existing_src, "title", existing_src.id))
            else:
                dup_rows.append(_dup_row(new, "title", existing_src.id))
        self._db.overwrite_sources(overwrites)

        self._db.insert_duplicates(dup_rows)
        imported, failures = self._db.insert_sources(candidates_for_insert)

        return IngestResult(
            parsed=parsed,
            imported=imported,
            deduplicated=len(batch_dups) + cross_dups + len(title_matches_raw),
            failed=len(failures),
            failures=failures,
            title_matches=title_matches,
        )


def _render_template(content: str, dst_path: Path, substitutions: dict[str, str]) -> None:
    for key, value in substitutions.items():
        content = content.replace("{{" + key + "}}", value)
    dst_path.write_text(content, encoding="utf-8")
