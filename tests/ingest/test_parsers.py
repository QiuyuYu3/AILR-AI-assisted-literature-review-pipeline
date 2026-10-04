"""Reference-file parsers: where each database's export keeps a field, and no record dropped silently."""

import pytest

from ailr.ingest import csv as csv_ingest
from ailr.ingest.bibtex import parse_bibtex
from ailr.ingest.csv import parse_csv
from ailr.ingest.ris import parse_ris


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestRis:
    def test_a_last_record_without_its_end_tag_is_kept(self, tmp_path):
        path = _write(tmp_path, "cut.ris", "TY  - JOUR\nTI  - First\nER  - \n\nTY  - JOUR\nTI  - Second, file cut short\n")
        assert [s.title for s in parse_ris(path)] == ["First", "Second, file cut short"]


class TestBibtex:
    def test_biblatex_entry_types_and_fields_are_read(self, tmp_path):
        path = _write(tmp_path, "a.bib", (
            "@online{b, title={A web page}, date={2021-05-04}}\n"
            "@thesis{c, title={A thesis}, author={Kim, H}, year={2019}}\n"
            "@article{d, title={An article}, journaltitle={Infancy}, date={2020}}\n"
        ))
        assert [(s.title, s.year, s.journal) for s in parse_bibtex(path)] == [
            ("A web page", 2021, None), ("A thesis", 2019, None), ("An article", 2020, "Infancy")]

    def test_an_author_list_broken_across_lines_is_still_split(self, tmp_path):
        path = _write(tmp_path, "a.bib", "@article{a, title={An article}, author={Lee, Jae and\n  Park, Sun}, year={2020}}\n")
        assert parse_bibtex(path)[0].authors == ["Lee, Jae", "Park, Sun"]


class TestCsv:
    def test_ieee_xplore(self, tmp_path):
        path = _write(tmp_path, "ieee.csv",
                      '"Document Title","Authors","Publication Title","Publication Year","DOI"\n'
                      '"A paper","J. Lee; S. Park","IEEE Trans","2020","10.1/i"\n')
        [s] = parse_csv(path)
        assert (s.title, s.journal, s.year, s.doi, s.authors) == ("A paper", "IEEE Trans", 2020, "10.1/i", ["J. Lee", "S. Park"])

    def test_scopus_journal_is_the_source_title_not_the_database(self, tmp_path):
        path = _write(tmp_path, "scopus.csv",
                      '"Authors","Title","Year","Source title","DOI","Source"\n'
                      '"Lee J.; Park S.","A paper","2020","Infancy","10.1/s","Scopus"\n')
        assert parse_csv(path)[0].journal == "Infancy"

    def test_web_of_science_tags(self, tmp_path):
        path = _write(tmp_path, "wos.txt", "PT\tAU\tTI\tSO\tPY\tDI\tPM\nJ\tLee, J; Park, S\tA paper\tINFANCY\t2020\t10.1/w\t12345\n")
        [s] = parse_csv(path)
        assert (s.title, s.journal, s.doi, s.pmid, s.authors) == ("A paper", "INFANCY", "10.1/w", "12345", ["Lee, J", "Park, S"])

    def test_pubmed(self, tmp_path):
        path = _write(tmp_path, "pubmed.csv",
                      '"PMID","Title","Authors","Journal/Book","Publication Year","DOI"\n'
                      '"111","Two authors","Lee J, Park S.","Infancy","2020","10.1/p"\n'
                      '"222","Three authors","Lee J, Park S, Kim H.","Infancy","2021","10.1/q"\n')
        assert [(s.journal, s.authors) for s in parse_csv(path)] == [
            ("Infancy", ["Lee J", "Park S."]), ("Infancy", ["Lee J", "Park S", "Kim H."])]

    def test_one_author_written_surname_comma_initials_stays_one_author(self, tmp_path):
        path = _write(tmp_path, "one.csv", '"Title","Authors"\n"A paper","Lee, J."\n')
        assert parse_csv(path)[0].authors == ["Lee, J."]

    @pytest.mark.parametrize("header", ['"Title","Journal","Source"', '"Title","Source","Journal"'])
    def test_the_more_specific_journal_column_wins_whatever_the_column_order(self, tmp_path, header):
        values = '"A paper","Infancy","Scopus"' if header.endswith('"Source"') else '"A paper","Scopus","Infancy"'
        path = _write(tmp_path, "both.csv", f"{header}\n{values}\n")
        assert parse_csv(path)[0].journal == "Infancy"

    def test_alias_priority_is_an_ordered_list(self):
        """A set's iteration order changes from one run to the next, and with it which column won."""
        assert all(isinstance(aliases, tuple) for aliases in csv_ingest._COLUMN_ALIASES.values())
