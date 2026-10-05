"""Linking PDFs from a Zotero RIS export: the file every later step converts and reads."""

import threading
from pathlib import Path

import pytest

import ailr.ingest.pdf_link as pdf_link
from ailr.core.pdf_paths import resolve_pdf_path
from ailr.exceptions import InputNotFoundError
from ailr.ingest.pdf_link import auto_link_pdfs, auto_link_pdfs_on_entry, link_pdfs_from_ris
from tests.helpers import add_source


def _record(title, attachment=None, doi=None):
    lines = ["TY  - JOUR", f"TI  - {title}"]
    if doi:
        lines.append(f"DO  - {doi}")
    if attachment:
        lines.append(f"L1  - {attachment}")
    return "\n".join(lines + ["ER  - ", ""])


def _ris(folder: Path, *records, name="zotero.ris") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("\n".join(records), encoding="utf-8")
    return path


def _pdf(folder: Path, rel: str) -> Path:
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4")
    return path


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(pdf_link, "_auto_link_sig", {})
    monkeypatch.setattr(pdf_link, "_auto_link_running", set())


class TestLinkFromRis:
    def test_a_relative_attachment_resolves_beside_the_ris(self, tmp_project, tmp_path):
        sid = add_source(tmp_project, "Joint attention in toddlers", doi="10.1/a")
        pdf = _pdf(tmp_path / "export", "files/3/paper.pdf")
        ris = _ris(tmp_path / "export", _record("Something else", "files/3/paper.pdf", doi="10.1/A"))

        s = link_pdfs_from_ris(tmp_project, ris)

        assert (s.total_records, s.linked) == (1, 1)
        assert resolve_pdf_path(tmp_project.db.get_source(sid).pdf_path, tmp_project.root).resolve() == pdf.resolve()

    def test_a_second_run_writes_nothing(self, tmp_project, tmp_path):
        sid = add_source(tmp_project, "Joint attention in toddlers", doi="10.1/a")
        _pdf(tmp_path, "p.pdf")
        ris = _ris(tmp_path, _record("Joint attention in toddlers", "p.pdf", doi="10.1/a"))
        link_pdfs_from_ris(tmp_project, ris)
        calls = []
        real = tmp_project.db.update_pdf_path
        tmp_project.db.update_pdf_path = lambda *a: calls.append(a) or real(*a)

        s = link_pdfs_from_ris(tmp_project, ris)

        assert (s.linked, s.already_linked, calls) == (0, 1, [])
        assert tmp_project.db.get_source(sid).pdf_path is not None

    def test_an_absolute_file_url_with_escapes_is_read(self, tmp_project, tmp_path):
        sid = add_source(tmp_project, "Joint attention in toddlers", doi="10.1/a")
        pdf = _pdf(tmp_path, "My Library/paper one.pdf")
        url = pdf.resolve().as_uri()  # file:///C:/... on Windows, file:///home/... elsewhere
        ris = _ris(tmp_path / "elsewhere", _record("Joint attention in toddlers", url, doi="10.1/a"))

        link_pdfs_from_ris(tmp_project, ris)

        assert resolve_pdf_path(tmp_project.db.get_source(sid).pdf_path, tmp_project.root).resolve() == pdf.resolve()

    def test_a_missing_file_is_reported_and_not_linked(self, tmp_project, tmp_path):
        sid = add_source(tmp_project, "Joint attention in toddlers", doi="10.1/a")
        ris = _ris(tmp_path, _record("Joint attention in toddlers", "gone.pdf", doi="10.1/a"))

        s = link_pdfs_from_ris(tmp_project, ris)

        assert [m["source_id"] for m in s.missing_files] == [sid]
        assert tmp_project.db.get_source(sid).pdf_path is None

    def test_records_without_a_pdf_or_a_match_are_counted(self, tmp_project, tmp_path):
        add_source(tmp_project, "Joint attention in toddlers", doi="10.1/a")
        _pdf(tmp_path, "x.pdf")
        ris = _ris(tmp_path, _record("Joint attention in toddlers"), _record("A paper the review never had", "x.pdf"))

        s = link_pdfs_from_ris(tmp_project, ris)

        assert (s.no_attachment, len(s.unmatched), s.linked) == (1, 1, 0)

    def test_a_missing_ris_is_an_error(self, tmp_project, tmp_path):
        with pytest.raises(InputNotFoundError):
            link_pdfs_from_ris(tmp_project, tmp_path / "none.ris")


class TestAutoLink:
    def _setup(self, tmp_project):
        sid = add_source(tmp_project, "Joint attention in toddlers", doi="10.1/a")
        pdfs = tmp_project.root / "data" / "pdfs"
        _pdf(pdfs, "files/1/p.pdf")
        ris = _ris(pdfs, _record("Joint attention in toddlers", "files/1/p.pdf", doi="10.1/a"))
        return sid, ris

    def test_nothing_to_do_without_the_folder(self, tmp_project):
        assert auto_link_pdfs(tmp_project).total_records == 0

    def test_an_unchanged_export_is_not_parsed_again(self, tmp_project, monkeypatch):
        sid, _ = self._setup(tmp_project)
        assert auto_link_pdfs(tmp_project).linked == 1
        monkeypatch.setattr(pdf_link, "link_pdfs_from_ris", lambda *_: pytest.fail("parsed again"))

        assert auto_link_pdfs(tmp_project).total_records == 0

    def test_a_changed_export_or_a_rescan_parses_again(self, tmp_project):
        sid, ris = self._setup(tmp_project)
        auto_link_pdfs(tmp_project)

        assert auto_link_pdfs(tmp_project, force=True).already_linked == 1
        ris.write_text(ris.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        assert auto_link_pdfs(tmp_project).already_linked == 1

    def test_one_broken_export_does_not_stop_the_others(self, tmp_project):
        sid, _ = self._setup(tmp_project)
        (tmp_project.root / "data" / "pdfs" / "a_broken.ris").write_bytes(b"\xff\xfe\x00garbage")

        assert auto_link_pdfs(tmp_project).linked == 1


class TestOnEntry:
    def test_the_first_entry_links_before_returning(self, tmp_project):
        sid = add_source(tmp_project, "Joint attention in toddlers", doi="10.1/a")
        pdfs = tmp_project.root / "data" / "pdfs"
        _pdf(pdfs, "p.pdf")
        _ris(pdfs, _record("Joint attention in toddlers", "p.pdf", doi="10.1/a"))

        auto_link_pdfs_on_entry(tmp_project)

        assert tmp_project.db.get_source(sid).pdf_path is not None

    def test_later_entries_run_in_the_background_one_at_a_time(self, tmp_project, monkeypatch):
        pdf_link._auto_link_sig[str(tmp_project.root)] = ()
        started, release = threading.Event(), threading.Event()
        runs = []

        def slow(project, force=False):
            runs.append(1)
            started.set()
            release.wait(5)

        monkeypatch.setattr(pdf_link, "auto_link_pdfs", slow)
        auto_link_pdfs_on_entry(tmp_project)
        assert started.wait(5)
        auto_link_pdfs_on_entry(tmp_project)  # still running: must not start a second one
        release.set()

        for _ in range(100):
            if str(tmp_project.root) not in pdf_link._auto_link_running:
                break
            threading.Event().wait(0.02)
        assert runs == [1] and str(tmp_project.root) not in pdf_link._auto_link_running
