"""Where a source's markdown lives, and how that path survives a shared project.

The project folder is mirrored (Box), so every teammate opens the same DB under a different
absolute root. A path stored as this machine's absolute path resolves nowhere else, which is how
markdown came to be invisible to everyone except whoever ran preprocess: the reader, extraction,
calibration and quote audit all fell through to "no markdown" while the file sat right there.
test_pdf_route.py covers the same ground for PDFs.
"""

from pathlib import Path

from ailr.core.pdf_paths import portable_path, resolve_markdown_path, resolve_pdf_path
from ailr.core.source import Source
from ailr.preprocess import PDFConverter
from ailr.tasks.preprocess import PreprocessTask


def _md(root, sid, text="# Paper\n\nBody.") -> Path:
    p = root / "data" / "markdown" / f"{sid}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class TestResolveMarkdownPath:
    def test_relative_path_resolves_under_the_project(self, tmp_project):
        _md(tmp_project.root, 7)
        got = resolve_markdown_path("data/markdown/7.md", tmp_project.root, 7)
        assert got == tmp_project.root / "data" / "markdown" / "7.md"

    def test_absolute_path_is_used_as_is_when_it_exists(self, tmp_path, tmp_project):
        outside = tmp_path / "elsewhere" / "paper.md"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("# Elsewhere", encoding="utf-8")
        assert resolve_markdown_path(str(outside), tmp_project.root, 7) == outside

    def test_a_teammates_absolute_path_falls_back_to_the_canonical_file(self, tmp_project):
        """The bug: the row holds C:/Users/someone-else/... and the file is here all along."""
        local = _md(tmp_project.root, 7)
        stored = r"C:\Users\someone-else\Box\review\data\markdown\7.md"
        assert resolve_markdown_path(stored, tmp_project.root, 7) == local

    def test_no_recorded_path_still_finds_the_canonical_file(self, tmp_project):
        local = _md(tmp_project.root, 7)
        assert resolve_markdown_path(None, tmp_project.root, 7) == local

    def test_none_when_nothing_is_on_disk(self, tmp_project):
        assert resolve_markdown_path("data/markdown/7.md", tmp_project.root, 7) is None
        assert resolve_markdown_path(None, tmp_project.root, 7) is None

    def test_without_a_source_id_a_dead_path_is_not_guessed_at(self, tmp_project):
        _md(tmp_project.root, 7)
        assert resolve_markdown_path("data/markdown/nope.md", tmp_project.root) is None


class TestPortablePath:
    def test_a_file_inside_the_project_is_stored_relative(self, tmp_project):
        inside = tmp_project.root / "data" / "markdown" / "7.md"
        assert portable_path(inside, tmp_project.root) == Path("data/markdown/7.md")

    def test_round_trip_survives_a_different_project_root(self, tmp_path, tmp_project):
        """What the shared-project case actually needs: store here, resolve under another root."""
        _md(tmp_project.root, 7)
        stored = portable_path(tmp_project.root / "data" / "markdown" / "7.md", tmp_project.root)

        teammate = tmp_path / "another" / "machine" / "proj"
        _md(teammate, 7)
        assert resolve_markdown_path(str(stored), teammate, 7) == teammate / "data" / "markdown" / "7.md"


class _FakeConverter(PDFConverter):
    @property
    def backend_name(self) -> str:
        return "fake"

    def convert(self, pdf_path: Path) -> str:
        return f"# {pdf_path.stem}\n\nConverted body."


class TestPreprocessStoresPortablePaths:
    def _pdf(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 not really a pdf")
        return path

    def test_converted_source_gets_relative_markdown_and_pdf_paths(self, tmp_project):
        sid = tmp_project.db.insert_source(Source(title="A", project_id=tmp_project.project_id))
        self._pdf(tmp_project.root / "data" / "pdfs" / f"{sid}.pdf")

        summary = PreprocessTask(tmp_project, converter=_FakeConverter()).run()
        assert summary.converted == 1

        src = tmp_project.db.get_source(sid)
        assert not Path(src.markdown_path).is_absolute()
        assert not Path(src.pdf_path).is_absolute()
        assert Path(src.markdown_path) == Path(f"data/markdown/{sid}.md")

    def test_a_linked_pdf_path_is_not_rewritten_to_an_absolute_one(self, tmp_project):
        """`import-pdfs` stores a portable path; preprocess used to overwrite it with an absolute
        one, which would have broken PDFs for teammates exactly as markdown was broken."""
        self._pdf(tmp_project.root / "data" / "pdfs" / "zotero" / "paper.pdf")
        sid = tmp_project.db.insert_source(Source(
            title="A", project_id=tmp_project.project_id, pdf_path="data/pdfs/zotero/paper.pdf",
        ))

        PreprocessTask(tmp_project, converter=_FakeConverter()).run()

        src = tmp_project.db.get_source(sid)
        assert Path(src.pdf_path) == Path("data/pdfs/zotero/paper.pdf")

    def test_an_out_of_project_pdf_stays_resolvable(self, tmp_path, tmp_project):
        """Zotero libraries sit outside the project, so the stored form is whatever survives the
        rewrite — what matters is that it still points at the file."""
        outside = self._pdf(tmp_path / "zotero" / "storage" / "paper.pdf")
        sid = tmp_project.db.insert_source(Source(
            title="A", project_id=tmp_project.project_id, pdf_path=str(outside),
        ))

        PreprocessTask(tmp_project, converter=_FakeConverter()).run()

        src = tmp_project.db.get_source(sid)
        got = resolve_pdf_path(str(src.pdf_path), tmp_project.root)
        assert got is not None and got.resolve() == outside.resolve()


def test_the_reader_renders_markdown_recorded_on_another_machine(tmp_project):
    """End to end over the pane the user actually looks at: a teammate's absolute path in the row,
    the file present locally, and the reader must show it instead of 'No markdown yet'."""
    from ailr.ui.extract_view import reader_body

    sid = tmp_project.db.insert_source(Source(title="A", project_id=tmp_project.project_id))
    _md(tmp_project.root, sid, "# Real content")
    tmp_project.db.update_markdown_path(sid, Path(r"C:\Users\someone-else\proj\data\markdown") / f"{sid}.md")

    out = reader_body(tmp_project, sid, "markdown")
    assert out.children == "# Real content"
