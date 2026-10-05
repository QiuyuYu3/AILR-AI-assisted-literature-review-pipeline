"""Linking PDFs from a Zotero RIS export: the file every later step converts and reads."""

import threading
from pathlib import Path

import pytest

import ailr.ingest.pdf_link as pdf_link
from ailr.core.pdf_paths import resolve_pdf_path
from ailr.core.source import Source
from ailr.exceptions import InputNotFoundError
from ailr.ingest.dedup import normalize_title
from ailr.ingest.pdf_link import (
    _match_source,
    _record_year,
    auto_link_pdfs,
    auto_link_pdfs_on_entry,
    link_pdfs_from_ris,
)
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

    def test_a_title_does_not_match_across_conflicting_dois(self, tmp_project, tmp_path):
        """Two DOIs on one title are two papers, or a preprint and its article; a wrong PDF is worse than none."""
        sid = add_source(tmp_project, "Infant gaze and maternal speech", doi="10.1/maternal")
        _pdf(tmp_path, "other.pdf")
        ris = _ris(tmp_path, _record("Infant gaze and maternal speech", "other.pdf", doi="10.1/other"))

        s = link_pdfs_from_ris(tmp_project, ris)

        assert tmp_project.db.get_source(sid).pdf_path is None
        assert s.unmatched == [{"title": "Infant gaze and maternal speech", "doi": "10.1/other", "doi_differs_from": sid}]

    def test_the_rescan_and_the_cli_name_the_paper_the_doi_kept_out(self, tmp_project, tmp_path):
        from typer.testing import CliRunner

        from ailr.cli import app
        from ailr.ui import preprocess_view
        from tests.helpers import callbacks_of, component_text

        sid = add_source(tmp_project, "Infant gaze and maternal speech", doi="10.1/maternal")
        pdfs = tmp_project.root / "data" / "pdfs"
        _pdf(pdfs, "other.pdf")
        ris = _ris(pdfs, _record("Infant gaze and maternal speech", "other.pdf", doi="10.1/other"))

        alert, _ = callbacks_of(preprocess_view)["_link_pdfs"](1)
        cli = CliRunner().invoke(app, ["import-pdfs", str(tmp_project.root), str(ris)])

        assert f"DOI differs from the paper with the same title (#{sid})" in component_text(alert)
        assert f"DOI differs from #{sid}" in cli.stdout

    @pytest.mark.parametrize(("record_doi", "source_doi"), [(None, "10.1/a"), ("10.1/a", None)])
    def test_a_title_still_matches_when_only_one_side_has_a_doi(self, tmp_project, tmp_path, record_doi, source_doi):
        sid = add_source(tmp_project, "Joint attention in toddlers", doi=source_doi)
        _pdf(tmp_path, "p.pdf")
        ris = _ris(tmp_path, _record("Joint attention in toddlers", "p.pdf", doi=record_doi))

        assert link_pdfs_from_ris(tmp_project, ris).linked == 1
        assert tmp_project.db.get_source(sid).pdf_path is not None

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


# ----- Matching one record to a paper -----

def _norms(sources):
    return [(normalize_title(s.title), s) for s in sources]


class TestPdfMatchSource:
    def test_doi_match_wins_over_title(self):
        by_title = Source(id=1, title="Exact same title", doi="10.1/a")
        by_doi = Source(id=2, title="Something else entirely", doi="10.1/B")
        got = _match_source({"doi": "10.1/b", "title": "Exact same title"},
                            _norms([by_title, by_doi]), {"10.1/a": by_title, "10.1/b": by_doi})
        assert got is by_doi

    def test_a_doi_in_another_notation_still_matches(self):
        by_doi = Source(id=2, title="Something else entirely", doi="10.1/B")
        got = _match_source({"doi": "https://doi.org/10.1/b", "title": "No title match here"},
                            _norms([by_doi]), {"10.1/b": by_doi})
        assert got is by_doi

    def test_title_match_above_threshold(self):
        src = Source(id=1, title="Dyadic gaze coordination in infancy")
        got = _match_source({"title": "Dyadic gaze coordination in infancy"}, _norms([src]), {})
        assert got is src

    def test_no_match_below_threshold(self):
        src = Source(id=1, title="Dyadic gaze coordination in infancy")
        assert _match_source({"title": "Quantum chromodynamics on a lattice"}, _norms([src]), {}) is None

    def test_near_identical_titles_tie_broken_by_year(self):
        """0.20 regression: 'LAEO-Net' vs 'LAEO-Net++' normalize identically; the record's
        year must decide which paper gets the PDF."""
        old = Source(id=1, title="LAEO-Net: gaze detection", year=2019)
        new = Source(id=2, title="LAEO-Net++: gaze detection", year=2020)
        norms = _norms([old, new])
        assert _match_source({"title": "LAEO-Net++: gaze detection", "year": "2020"}, norms, {}) is new
        assert _match_source({"title": "LAEO-Net: gaze detection", "year": "2019"}, norms, {}) is old

    def test_a_short_title_does_not_claim_a_longer_one_containing_its_words(self):
        src = Source(id=1, title="Editorial: special issue on infant cognition")
        assert _match_source({"title": "Editorial"}, _norms([src]), {}) is None

    def test_record_without_title_is_unmatched(self):
        assert _match_source({}, _norms([Source(id=1, title="T")]), {}) is None


class TestRecordYear:
    def test_year_parsed_from_common_keys(self):
        assert _record_year({"year": "2020"}) == 2020
        assert _record_year({"publication_year": "2019/01/01"}) == 2019
        assert _record_year({"date": "Published 2018-05"}) == 2018

    def test_missing_year_is_none(self):
        assert _record_year({}) is None
        assert _record_year({"year": "n.d."}) is None
