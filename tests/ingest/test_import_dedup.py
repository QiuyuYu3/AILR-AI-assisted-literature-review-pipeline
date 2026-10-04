"""Import + deduplication rules:
- blank DOIs are stored as NULL so they never collide on the (project_id, doi) unique key (0.19)
- a title match keeps the MORE COMPLETE record (DOI first, then authors) and logs the drop (0.19)
- DOI dedup within an import and against existing rows
- title dedup is conservative: a wrong merge hides a paper, while a missed one is screened twice
  and can still be flagged by hand
"""

import json

import pytest

from ailr.core.project import _record_score
from ailr.core.source import Source
from ailr.ingest import dedup


class TestDedupFunctions:
    def test_normalize_title(self):
        assert dedup.normalize_title("LAEO-Net++:  A   Deep Model!") == "laeo net a deep model"

    def test_a_doi_written_as_a_url_or_with_a_prefix_is_the_same_doi(self):
        a = Source(title="A", doi="10.1/abc")
        b = Source(title="A from another database", doi="https://doi.org/10.1/ABC")
        c = Source(title="A once more", doi="doi: 10.1/abc")
        unique, dups = dedup.dedup_by_doi([a, b, c])
        assert unique == [a] and dups == [b, c]

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
        kept, matched = dedup.dedup_by_title(new, existing)
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

    def test_case_punctuation_and_spacing_do_not_hide_a_duplicate(self):
        existing = [Source(id=1, title="Neural synchrony during parent-child interaction")]
        new = [Source(title="NEURAL SYNCHRONY  during parent child interaction.")]
        kept, matched = dedup.dedup_by_title(new, existing)
        assert kept == [] and [e.id for _, e in matched] == [1]

    def test_a_typo_or_reordered_words_are_left_for_screening(self):
        """Who gazes and who speaks is a different paper; a typo may be one too. Neither is merged."""
        existing = [Source(id=1, title="Neural synchrony during parent-child interaction"),
                    Source(id=2, title="Infant gaze and maternal speech")]
        new = [Source(title="Neural synchrony during parent-child interation"),
               Source(title="Maternal gaze and infant speech")]
        kept, matched = dedup.dedup_by_title(new, existing)
        assert kept == new and matched == []

    def test_one_changed_word_keeps_two_titles_apart(self):
        existing = [Source(id=1, title="Infant gaze and maternal speech", year=2019)]
        new = [Source(title="Infant gaze and paternal speech", year=2019)]
        kept, matched = dedup.dedup_by_title(new, existing)
        assert kept == new and matched == []

    def test_different_dois_mean_different_records_whatever_the_title(self):
        existing = [Source(id=1, title="Dyadic gaze coordination in infancy", doi="10.1/first")]
        other_doi = Source(title="Dyadic gaze coordination in infancy", doi="10.1/second")
        same_doi_other_notation = Source(title="Dyadic gaze coordination in infancy", doi="https://doi.org/10.1/FIRST")
        no_doi = Source(title="Dyadic gaze coordination in infancy")
        kept, matched = dedup.dedup_by_title([other_doi, same_doi_other_notation, no_doi], existing)
        assert kept == [other_doi]
        assert [n for n, _ in matched] == [same_doi_other_notation, no_doi]

    def test_first_authors_with_no_name_in_common_veto_a_title_match(self):
        existing = [Source(id=1, title="Dyadic gaze coordination in infancy", authors=["Lee, Jae-Hyun", "Park, S"])]
        other_author = Source(title="Dyadic gaze coordination in infancy", authors=["Garcia, M"])
        formats = [Source(title="Dyadic gaze coordination in infancy", authors=[a])
                   for a in ("Lee JH", "J.-H. Lee", "Lee, J.")]
        no_authors = Source(title="Dyadic gaze coordination in infancy")
        kept, matched = dedup.dedup_by_title([other_author, *formats, no_authors], existing)
        assert kept == [other_author]
        assert [n for n, _ in matched] == [*formats, no_authors]

    def test_short_titles_are_never_merged_on_title_alone(self):
        """Editorials, introductions and replies share their titles across journals and years."""
        existing = [Source(id=1, title="Editorial", year=2020), Source(id=2, title="Joint attention", year=2015)]
        new = [Source(title="Editorial", year=2020), Source(title="Joint attention", year=2015)]
        kept, matched = dedup.dedup_by_title(new, existing)
        assert kept == new and matched == []

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

    def test_one_import_is_deduplicated_on_title_by_the_same_rule(self):
        bare = Source(title="Dyadic gaze coordination in infancy", year=2019)
        complete = Source(title="Dyadic Gaze Coordination in Infancy.", year=2019, doi="10.1/x", authors=["Lee, J"])
        years_apart = Source(title="Dyadic gaze coordination in infancy", year=2001)
        short = [Source(title="Editorial", year=2020), Source(title="Editorial", year=2020)]
        kept, dropped = dedup.dedup_by_title_within([bare, complete, years_apart, *short], score=_record_score)
        assert kept == [complete, years_apart, *short]      # the more complete of the pair stays
        assert dropped == [bare]

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

    def test_a_doi_given_as_a_link_is_stored_bare(self, tmp_project, tmp_path):
        linked = _RIS_A.replace("DO  - 10.1/dyad", "DO  - https://doi.org/10.1/Dyad")
        tmp_project.ingest(_write_ris(tmp_path / "a.ris", [linked]), source_database="test")
        assert [s.doi for s in tmp_project.db.list_sources(tmp_project.project_id)] == ["10.1/Dyad"]

    def test_a_title_repeated_inside_one_file_is_imported_once(self, tmp_project, tmp_path):
        result = tmp_project.ingest(_write_ris(tmp_path / "twice.ris", [_RIS_A_BARE, _RIS_A]), source_database="test")
        assert (result.imported, result.deduplicated) == (1, 1)
        [kept] = tmp_project.db.list_sources(tmp_project.project_id)
        assert kept.doi == "10.1/dyad"
        [dup] = tmp_project.db.list_duplicates(tmp_project.project_id)
        assert dup["reason"] == "title (within import)"

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
        assert dups[0]["matched_source_id"] == bare.id
        logged = json.loads(tmp_project.db.get_duplicate_record(dups[0]["id"]))
        assert logged["doi"] is None and not logged["authors"]   # the bare record, not the incoming one

    def test_an_equally_complete_incoming_record_does_not_replace_the_existing_one(self, tmp_project, tmp_path):
        """A tie keeps what is already there: work may be attached to it, and nothing is gained."""
        first = _RIS_A_BARE.replace("ER  -", "AU  - Lee, J\nAB  - The first abstract.\nER  -")
        second = _RIS_A_BARE.replace("ER  -", "AU  - Lee, J.\nAB  - The second abstract.\nER  -")
        tmp_project.ingest(_write_ris(tmp_path / "first.ris", [first]), source_database="test")
        result = tmp_project.ingest(_write_ris(tmp_path / "second.ris", [second]), source_database="test")
        assert (result.imported, result.deduplicated) == (0, 1)
        [kept] = tmp_project.db.list_sources(tmp_project.project_id)
        assert kept.abstract == "The first abstract."
        [dup] = tmp_project.db.list_duplicates(tmp_project.project_id)
        assert json.loads(tmp_project.db.get_duplicate_record(dup["id"]))["abstract"] == "The second abstract."

    def test_title_match_drops_the_less_complete_incoming(self, tmp_project, tmp_path):
        tmp_project.ingest(_write_ris(tmp_path / "full.ris", [_RIS_A]), source_database="test")
        [kept_before] = tmp_project.db.list_sources(tmp_project.project_id)
        result = tmp_project.ingest(_write_ris(tmp_path / "bare.ris", [_RIS_A_BARE]), source_database="test")
        assert result.imported == 0 and result.deduplicated == 1
        [kept] = tmp_project.db.list_sources(tmp_project.project_id)
        assert kept.id == kept_before.id and kept.doi == "10.1/dyad"  # existing row untouched
        # the dropped incoming record is logged, so PRISMA counts it and it can be restored
        dups = tmp_project.db.list_duplicates(tmp_project.project_id)
        assert [(d["reason"], d["matched_source_id"]) for d in dups] == [("title", kept_before.id)]
        assert json.loads(tmp_project.db.get_duplicate_record(dups[0]["id"]))["doi"] is None

    @pytest.mark.parametrize("stored,incoming", [
        ("10.1/dyad", "https://doi.org/10.1/dyad"),
        ("https://doi.org/10.1/dyad", "10.1/DYAD"),     # a link kept from an earlier import
    ])
    def test_an_existing_doi_in_another_notation_blocks_a_reimport(self, tmp_project, tmp_path, stored, incoming):
        first = _RIS_A.replace("DO  - 10.1/dyad", f"DO  - {stored}")
        tmp_project.ingest(_write_ris(tmp_path / "a.ris", [first]), source_database="test")
        [kept] = tmp_project.db.list_sources(tmp_project.project_id)
        renamed = (_RIS_A.replace("TI  - Dyadic gaze coordination in infancy", "TI  - A different title altogether")
                   .replace("DO  - 10.1/dyad", f"DO  - {incoming}"))
        result = tmp_project.ingest(_write_ris(tmp_path / "b.ris", [renamed]), source_database="test")
        assert (result.imported, result.deduplicated) == (0, 1)
        [dup] = tmp_project.db.list_duplicates(tmp_project.project_id)
        assert (dup["reason"], dup["matched_source_id"]) == ("doi", kept.id)

    def test_an_existing_doi_blocks_a_reimport_under_another_title(self, tmp_project, tmp_path):
        """A different title takes title matching out of the picture, so only the DOI can catch it."""
        tmp_project.ingest(_write_ris(tmp_path / "a.ris", [_RIS_A]), source_database="test")
        [kept] = tmp_project.db.list_sources(tmp_project.project_id)
        renamed = (_RIS_A.replace("TI  - Dyadic gaze coordination in infancy", "TI  - A different title altogether")
                   .replace("DO  - 10.1/dyad", "DO  - 10.1/DYAD"))
        result = tmp_project.ingest(_write_ris(tmp_path / "b.ris", [renamed]), source_database="test")
        assert (result.imported, result.deduplicated) == (0, 1)
        [dup] = tmp_project.db.list_duplicates(tmp_project.project_id)
        assert (dup["reason"], dup["matched_source_id"]) == ("doi", kept.id)


class TestBulkInsert:
    def test_good_records_go_in_as_one_batch(self, tmp_project, monkeypatch):
        """Row by row is the fallback for a bad record. A broken batch would still import, only
        slowly against a remote database, so nothing else would notice it."""
        db = tmp_project.db
        one_by_one = []
        real = type(db)._insert_source_row
        monkeypatch.setattr(type(db), "_insert_source_row", lambda self, src: one_by_one.append(src.title) or real(self, src))
        sources = [Source(title=f"P{i}", project_id=tmp_project.project_id) for i in range(3)]
        assert db.insert_sources(sources) == (3, [])
        assert one_by_one == []
        assert sorted(x.title for x in db.list_sources(tmp_project.project_id)) == ["P0", "P1", "P2"]

    def test_a_bad_record_fails_alone(self, tmp_project):
        pid = tmp_project.project_id
        sources = [Source(title="first", doi="10.1/a", project_id=pid),
                   Source(title="same DOI again", doi="10.1/a", project_id=pid),
                   Source(title="second", project_id=pid)]
        inserted, failures = tmp_project.db.insert_sources(sources)
        assert inserted == 2 and [f["title"] for f in failures] == ["same DOI again"]
        assert sorted(x.title for x in tmp_project.db.list_sources(pid)) == ["first", "second"]
