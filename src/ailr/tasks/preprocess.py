"""PreprocessTask: convert PDFs in data/pdfs/ into markdown in data/markdown/, update sources rows."""

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ailr.core.local_paths import resolve_pdf_path
from ailr.core.project import Project
from ailr.core.source import Source
from ailr.preprocess import PDFConverter, make_converter, strip_references

ProgressCallback = Callable[[int, int, Optional[Source], Optional[Exception]], None]


def import_markdown_from_folder(project: Project, folder: Path) -> dict[str, Any]:
    """Import already-converted .md files and link them to sources via their pdf_path.

    Matching (in priority order), keyed off each source's `pdf_path` (a Zotero `files/<number>/<name>.pdf`):
      1. the Zotero attachment number (the pdf's parent-folder name) → a `<number>.md` or a file under `<number>/`
      2. the pdf filename stem → a `<stem>.md`
    The matched file is copied to data/markdown/<source_id>.md and recorded as markdown_path.
    """
    folder = Path(folder).expanduser()
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"Not a folder: {folder}")

    md_files = list(folder.rglob("*.md"))
    md_by_stem: dict[str, Path] = {}
    md_by_number: dict[str, Path] = {}
    for p in md_files:
        md_by_stem.setdefault(p.stem, p)
        if p.stem.isdigit():               # <number>.md
            md_by_number.setdefault(p.stem, p)
        if p.parent.name.isdigit():        # <number>/<anything>.md
            md_by_number.setdefault(p.parent.name, p)

    md_dir = project.root / "data" / "markdown"
    md_dir.mkdir(parents=True, exist_ok=True)

    matched = 0
    no_pdf_path = 0
    unmatched: list[str] = []
    for s in project.db.list_sources(project.project_id):
        if not s.pdf_path:
            no_pdf_path += 1
            continue
        pp = Path(s.pdf_path)
        number = pp.parent.name
        src_md = md_by_number.get(number) if number.isdigit() else None
        if src_md is None:
            src_md = md_by_stem.get(pp.stem)
        if src_md is None:
            unmatched.append(pp.stem)
            continue
        dest = md_dir / f"{s.id}.md"
        shutil.copy2(src_md, dest)
        project.db.update_markdown_path(s.id, dest)
        matched += 1

    return {"md_files_found": len(md_files), "matched": matched, "no_pdf_path": no_pdf_path, "unmatched": unmatched}


@dataclass
class PreprocessSummary:
    total_pdfs: int = 0
    converted: int = 0
    skipped_no_match: int = 0
    skipped_already_done: int = 0
    failed: int = 0
    failures: list[dict] = field(default_factory=list)
    unmatched_pdfs: list[str] = field(default_factory=list)
    missing_pdfs: list[dict] = field(default_factory=list)
    low_quality: list[dict] = field(default_factory=list)


class PreprocessTask:
    def __init__(
        self,
        project: Project,
        converter: Optional[PDFConverter] = None,
    ) -> None:
        self.project = project
        self.converter = converter or make_converter(project.config.preprocess.pdf_backend)

    def run(
        self,
        *,
        force: bool = False,
        only_ids: Optional[set[int]] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> PreprocessSummary:
        pdfs_dir = self.project.root / "data" / "pdfs"
        md_dir = self.project.root / "data" / "markdown"
        md_dir.mkdir(parents=True, exist_ok=True)

        sources_by_id = {
            s.id: s for s in self.project.db.list_sources(self.project.project_id) if s.id is not None
        }

        # Resolve each source's PDF: a recorded pdf_path (e.g. from `ailr import-pdfs`) wins;
        # otherwise fall back to a manually-dropped data/pdfs/<id>.pdf.
        summary = PreprocessSummary()
        pdf_by_source = self._resolve_pdfs(sources_by_id, pdfs_dir, summary)
        summary.total_pdfs = len(pdf_by_source)

        strip_refs = self.project.config.preprocess.strip_references
        low_text_threshold = self.project.config.preprocess.low_text_threshold
        items = sorted(pdf_by_source.items())

        for idx, (sid, pdf_file) in enumerate(items, 1):
            source = sources_by_id[sid]
            if only_ids is not None and sid not in only_ids:
                continue

            md_path = md_dir / f"{sid}.md"
            if md_path.exists() and not force:
                summary.skipped_already_done += 1
                if source.markdown_path is None:
                    self.project.db.update_markdown_path(sid, md_path)
                if source.pdf_path is None:
                    self.project.db.update_pdf_path(sid, pdf_file)
                if on_progress:
                    on_progress(idx, len(items), source, None)
                continue

            try:
                md_text = self.converter.convert(pdf_file)
                if strip_refs:
                    md_text = strip_references(md_text)
                md_path.write_text(md_text, encoding="utf-8")
                self.project.db.update_markdown_path(sid, md_path)
                self.project.db.update_pdf_path(sid, pdf_file)
                summary.converted += 1
                if len(md_text.strip()) < low_text_threshold:
                    summary.low_quality.append(
                        {"source_id": sid, "title": source.title, "chars": len(md_text.strip())}
                    )
                if on_progress:
                    on_progress(idx, len(items), source, None)
            except Exception as e:
                summary.failed += 1
                summary.failures.append(
                    {"pdf": pdf_file.name, "source_id": sid, "error": str(e)}
                )
                if on_progress:
                    on_progress(idx, len(items), source, e)

        # Report sources missing PDFs (only those that should have one — included or all)
        for source in sources_by_id.values():
            md_path = md_dir / f"{source.id}.md"
            if not md_path.exists():
                summary.missing_pdfs.append(
                    {"source_id": source.id, "title": source.title, "doi": source.doi}
                )

        return summary

    def _resolve_pdfs(
        self,
        sources_by_id: dict[int, Source],
        pdfs_dir: Path,
        summary: PreprocessSummary,
    ) -> dict[int, Path]:
        pdf_by_source: dict[int, Path] = {}

        for sid, source in sources_by_id.items():
            if source.pdf_path is None:
                continue
            resolved = resolve_pdf_path(source, self.project.root)
            if resolved is not None:
                pdf_by_source[sid] = resolved

        # Manually-dropped data/pdfs/<id>.pdf, matched by integer filename. pdf_path wins.
        for pdf_file in sorted(pdfs_dir.glob("*.pdf")) if pdfs_dir.exists() else []:
            try:
                sid = int(pdf_file.stem)
            except ValueError:
                summary.skipped_no_match += 1
                summary.unmatched_pdfs.append(pdf_file.name)
                continue
            if sid not in sources_by_id:
                summary.skipped_no_match += 1
                summary.unmatched_pdfs.append(pdf_file.name)
                continue
            pdf_by_source.setdefault(sid, pdf_file)

        return pdf_by_source
