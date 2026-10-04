"""Import + deduplication rules:
- blank DOIs are stored as NULL so they never collide on the (project_id, doi) unique key (0.19)
- a title match keeps the MORE COMPLETE record (DOI first, then authors) and logs the drop (0.19)
- DOI dedup within an import and against existing rows
"""

import json

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

    def test_a_title_that_is_a_word_subset_of_another_is_not_a_duplicate(self):
        """Short or generic titles must not swallow longer ones that merely contain their words."""
        existing = [Source(id=1, title="Joint attention", year=1995),
                    Source(id=2, title="Editorial", year=2020),
                    Source(id=3, title="Effects of joint attention training on language", year=2018)]
        new = [Source(title="Joint attention in autism: a meta-analysis", year=2015),
               Source(title="Editorial: special issue on infant cognition", year=2020),
               Source(title="Correction to: Effects of joint attention training on language", year=2018)]
        kept, matched = dedup.dedup_by_title(new, existing)
        assert matched == [] and kept == new

    def test_title_dedup_still_catches_typos_case_and_word_order(self):
        existing = [Source(id=1, title="Neural synchrony during parent-child interaction"),
                    Source(id=2, title="Infant gaze and maternal speech")]
        new = [Source(title="Neural synchrony during parent-child interation"),
               Source(title="MATERNAL SPEECH AND INFANT GAZE")]
        kept, matched = dedup.dedup_by_title(new, existing)
        assert kept == [] and [e.id for _, e in matched] == [1, 2]

    def test_publication_years_more_than_a_year_apart_veto_a_title_match(self):
        existing = [Source(id=1, title="Dyadic gaze coordination in infancy", year=2010)]
        years_later = Source(title="Dyadic gaze coordination in infancy", year=2016)
        online_first = Source(title="Dyadic gaze coordination in infancy", year=2011)
        undated = Source(title="Dyadic gaze coordination in infancy")
        kept, matched = dedup.dedup_by_title([years_later, online_first, undated], existing)
        assert kept == [years_later]
        assert [n for n, _ in matched] == [online_first, undated]

    def test_a_vetoed_title_falls_through_to_the_next_candidate(self):
        """A same-titled paper from another year must not hide the real match behind it."""
        existing = [Source(id=1, title="Dyadic gaze coordination in infancy", year=2001),
                    Source(id=2, title="Dyadic gaze coordination in infancy.", year=2019)]
        incoming = Source(title="Dyadic gaze coordination in infancy", year=2019)
        _kept, matched = dedup.dedup_by_title([incoming], existing)
        assert [e.id for _, e in matched] == [2]

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

    def test_a_dropped_duplicate_remembers_its_identification_arm(self, tmp_project, tmp_path):
        """PRISMA counts each arm's records before deduplication, so the drop keeps its route."""
        tmp_project.ingest(_write_ris(tmp_path / "db.ris", [_RIS_A]), source_database="test")
        tmp_project.ingest(_write_ris(tmp_path / "cit.ris", [_RIS_A]), source_database="Citation searching",
                           identification_route="other")
        [dup] = tmp_project.db.list_duplicates(tmp_project.project_id)
        assert json.loads(tmp_project.db.get_duplicate_record(dup["id"]))["identification_route"] == "other"
        assert tmp_project.db.count_duplicates(tmp_project.project_id, route="other") == 1
        assert tmp_project.db.count_duplicates(tmp_project.project_id, route="database") == 0

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
