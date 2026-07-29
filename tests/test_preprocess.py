"""Full-text preparation regressions:
- strip_references: only a bibliography heading in the LATTER half of the document cuts —
  an early 'References' mention (JSTOR cover page) must not delete the body (0.20)
- PDF linking: near-identical titles (LAEO-Net vs LAEO-Net++) tie-break by year (0.20)
"""

from ailr.core.source import Source
from ailr.ingest.dedup import normalize_title
from ailr.ingest.pdf_link import _match_source, _record_year
from ailr.preprocess import strip_references

_BODY = "Introduction paragraph. " * 200  # long enough that positions are unambiguous


class TestStripReferences:
    def test_heading_in_latter_half_cuts(self):
        text = _BODY + "\n# References\nSmith 2020. Jones 2021."
        out = strip_references(text)
        assert out == _BODY.rstrip()
        assert "Smith 2020" not in out

    def test_plain_uppercase_heading_also_cuts(self):
        text = _BODY + "\nREFERENCES\nSmith 2020."
        assert "Smith 2020" not in strip_references(text)

    def test_early_cover_page_heading_does_not_delete_the_body(self):
        text = "References\nJSTOR cover boilerplate.\n" + _BODY
        assert strip_references(text) == text  # heading is in the first half: keep everything

    def test_early_heading_plus_real_bibliography_cuts_at_the_real_one(self):
        text = "References\ncover page.\n" + _BODY + "\n# References\nSmith 2020."
        out = strip_references(text)
        assert out.startswith("References\ncover page.")
        assert "Smith 2020" not in out
        assert "Introduction paragraph." in out

    def test_inline_mention_is_not_a_heading(self):
        text = _BODY + "\nas listed in the references below, we proceed. " + _BODY
        assert strip_references(text) == text

    def test_no_heading_returns_unchanged(self):
        assert strip_references(_BODY) == _BODY


def _norms(sources):
    return [(normalize_title(s.title), s) for s in sources]


class TestPdfMatchSource:
    def test_doi_match_wins_over_title(self):
        by_title = Source(id=1, title="Exact same title", doi="10.1/a")
        by_doi = Source(id=2, title="Something else entirely", doi="10.1/B")
        got = _match_source({"doi": "10.1/b", "title": "Exact same title"},
                            _norms([by_title, by_doi]), {"10.1/a": by_title, "10.1/b": by_doi})
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
