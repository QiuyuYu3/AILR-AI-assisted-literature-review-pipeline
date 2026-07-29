"""Import + deduplication rules:
- blank DOIs are stored as NULL so they never collide on the (project_id, doi) unique key (0.19)
- a title match keeps the MORE COMPLETE record (DOI first, then authors) and logs the drop (0.19)
- DOI dedup within an import and against existing rows
"""

from ailr.core.project import _record_score
from ailr.core.source import Source
from ailr.ingest import dedup


class TestDedupFunctions:
    def test_normalize_title(self):
        assert dedup.normalize_title("LAEO-Net++:  A   Deep Model!") == "laeo net a deep model"

    def test_doi_dedup_is_case_insensitive(self):
        a = Source(title="A", doi="10.1/ABC")
        b = Source(title="A again", doi="10.1/abc")
        c = Source(title="No doi")
        unique, dups = dedup.dedup_by_doi([a, b, c])
        assert unique == [a, c] and dups == [b]

    def test_missing_doi_never_counts_as_duplicate(self):
        a, b = Source(title="X"), Source(title="Y")
        unique, dups = dedup.dedup_by_doi([a, b])
        assert unique == [a, b] and dups == []

    def test_title_dedup_matches_near_identical(self):
        existing = [Source(id=1, title="Dyadic gaze coordination in infancy")]
        new = [Source(title="Dyadic gaze coordination in infancy."),
               Source(title="A completely different topic entirely")]
        kept, matched = dedup.dedup_by_title(new, existing, threshold=95)
        assert [s.title for s in kept] == ["A completely different topic entirely"]
        assert [(n.title[:10], e.id) for n, e in matched] == [("Dyadic gaz", 1)]

    def test_record_score_prefers_doi_then_authors(self):
        bare = Source(title="T")
        with_authors = Source(title="T", authors=["Lee, J"])
        with_doi = Source(title="T", doi="10.1/x")
        full = Source(title="T", doi="10.1/x", authors=["Lee, J"], year=2020, journal="J")
        assert _record_score(bare) < _record_score(with_authors) < _record_score(with_doi) < _record_score(full)


def _write_ris(path, records: list[str]):
    path.write_text("\n".join(records), encoding="utf-8")
    return path


_RIS_A = """TY  - JOUR
TI  - Dyadic gaze coordination in infancy
AU  - Lee, J
PY  - 2021
DO  - 10.1/dyad
AB  - An abstract.
ER  -
"""

_RIS_A_BARE = """TY  - JOUR
TI  - Dyadic gaze coordination in infancy
ER  -
"""


class TestIngestPipeline:
    def test_blank_dois_do_not_collide(self, tmp_project, tmp_path):
        ris = _write_ris(tmp_path / "in.ris", [
            "TY  - JOUR", "TI  - First paper without doi", "DO  - ", "ER  - ", "",
            "TY  - JOUR", "TI  - Second paper without doi", "DO  - ", "ER  - ", "",
        ])
        result = tmp_project.ingest(ris, source_database="test")
        assert result.imported == 2 and result.failed == 0
        dois = [s.doi for s in tmp_project.db.list_sources(tmp_project.project_id)]
        assert dois == [None, None]  # blanks normalized to NULL, not ''

    def test_same_doi_within_one_import_is_deduplicated(self, tmp_project, tmp_path):
        ris = _write_ris(tmp_path / "in.ris", [
            "TY  - JOUR", "TI  - Original", "DO  - 10.1/same", "ER  - ", "",
            "TY  - JOUR", "TI  - Copy of original", "DO  - 10.1/SAME", "ER  - ", "",
        ])
        result = tmp_project.ingest(ris, source_database="test")
        assert result.imported == 1 and result.deduplicated == 1
        dups = tmp_project.db.list_duplicates(tmp_project.project_id)
        assert len(dups) == 1

    def test_existing_doi_blocks_reimport(self, tmp_project, tmp_path):
        tmp_project.ingest(_write_ris(tmp_path / "a.ris", [_RIS_A]), source_database="test")
        result = tmp_project.ingest(_write_ris(tmp_path / "b.ris", [_RIS_A]), source_database="test")
        assert result.imported == 0 and result.deduplicated == 1
        assert len(tmp_project.db.list_sources(tmp_project.project_id)) == 1

    def test_title_match_keeps_the_more_complete_record(self, tmp_project, tmp_path):
        # first import the bare record (no DOI/authors), then the complete one with the same title
        tmp_project.ingest(_write_ris(tmp_path / "bare.ris", [_RIS_A_BARE]), source_database="test")
        [bare] = tmp_project.db.list_sources(tmp_project.project_id)
        assert bare.doi is None
        result = tmp_project.ingest(_write_ris(tmp_path / "full.ris", [_RIS_A]), source_database="test")
        assert result.imported == 0 and result.deduplicated == 1
        [kept] = tmp_project.db.list_sources(tmp_project.project_id)
        # the complete incoming record took over the SAME row (id preserved for attached work)
        assert kept.id == bare.id
        assert kept.doi == "10.1/dyad" and kept.authors
        # the replaced bare content is logged as the dropped duplicate, restorable
        dups = tmp_project.db.list_duplicates(tmp_project.project_id)
        assert len(dups) == 1 and dups[0]["reason"] == "title"

    def test_title_match_drops_the_less_complete_incoming(self, tmp_project, tmp_path):
        tmp_project.ingest(_write_ris(tmp_path / "full.ris", [_RIS_A]), source_database="test")
        [kept_before] = tmp_project.db.list_sources(tmp_project.project_id)
        result = tmp_project.ingest(_write_ris(tmp_path / "bare.ris", [_RIS_A_BARE]), source_database="test")
        assert result.imported == 0 and result.deduplicated == 1
        [kept] = tmp_project.db.list_sources(tmp_project.project_id)
        assert kept.id == kept_before.id and kept.doi == "10.1/dyad"  # existing row untouched
