"""BibTeX import: every entry in the file becomes one source, with its fields where the review reads them."""

import pytest

from ailr.exceptions import IngestError, InputNotFoundError
from ailr.ingest.bibtex import parse_bibtex


def _parse(tmp_path, text):
    path = tmp_path / "refs.bib"
    path.write_text(text, encoding="utf-8")
    return parse_bibtex(path, source_database="DB-A")


def test_the_fields_land_where_the_review_reads_them(tmp_path):
    [s] = _parse(tmp_path, (
        "@article{lee2020,\n"
        "  title = {Joint {Attention} in Toddlers},\n"
        "  author = {Lee, Jae and Park, Sun},\n"
        "  journal = {Infancy},\n"
        "  year = {2020},\n"
        "  doi = { 10.1/abc },\n"
        "  abstract = {We studied {gaze}.},\n"
        "  volume = {12},\n"
        "  keywords = {gaze; dyads}\n"
        "}\n"
    ))
    assert (s.title, s.authors, s.journal, s.year, s.doi, s.abstract, s.source_database) == (
        "Joint Attention in Toddlers", ["Lee, Jae", "Park, Sun"], "Infancy", 2020, "10.1/abc", "We studied gaze.", "DB-A")
    assert s.metadata == {"volume": "12", "keywords": "gaze; dyads"}


def test_every_entry_type_is_kept(tmp_path):
    sources = _parse(tmp_path, "".join(
        f"@{kind}{{k{i}, title={{T{i}}}}}\n"
        for i, kind in enumerate(["article", "book", "inproceedings", "incollection", "phdthesis", "misc", "online", "report"])
    ))
    assert [s.title for s in sources] == [f"T{i}" for i in range(8)]


@pytest.mark.parametrize(("fields", "journal"), [
    ("journal = {J}, booktitle = {B}", "J"),
    ("journaltitle = {JT}, booktitle = {B}", "JT"),
    ("booktitle = {Proceedings of X}", "Proceedings of X"),
    ("", None),
])
def test_the_venue_falls_back_through_journal_journaltitle_booktitle(tmp_path, fields, journal):
    [s] = _parse(tmp_path, f"@article{{a, title={{T}}, {fields}}}\n")
    assert s.journal == journal


def test_field_names_are_read_in_any_case(tmp_path):
    [s] = _parse(tmp_path, "@ARTICLE{a, TITLE = {Upper}, Author = {Kim, H}, YEAR = {2019}, Pages = {1--9}}\n")
    assert (s.title, s.authors, s.year) == ("Upper", ["Kim, H"], 2019)
    assert s.metadata == {"pages": "1--9"}


def test_quoted_values_numbers_and_concatenation(tmp_path):
    [s] = _parse(tmp_path, '@article{a, title = "Quoted " # {and braced}, year = 2018}\n')
    assert (s.title, s.year) == ("Quoted and braced", 2018)


def test_string_macros_are_expanded_whatever_their_case(tmp_path):
    [s] = _parse(tmp_path, "@string{Inf = {Infancy}}\n@article{a, title={T}, journal = inf}\n")
    assert s.journal == "Infancy"


def test_a_hash_inside_quotes_or_braces_is_text(tmp_path):
    [s] = _parse(tmp_path, '@article{a, title = "Learning C# early", journal = {F# Quarterly}}\n')
    assert (s.title, s.journal) == ("Learning C# early", "F# Quarterly")


def test_macros_can_build_on_macros(tmp_path):
    [s] = _parse(tmp_path, '@string{j = "Jour" # {nal}}\n@string{jx = j # " of X"}\n@article{a, title={T}, journal = jx}\n')
    assert s.journal == "Journal of X"


def test_an_undefined_macro_is_kept_as_written(tmp_path):
    [s] = _parse(tmp_path, "@article{a, title={T}, month = jan}\n")
    assert s.metadata == {"month": "jan"}


def test_the_year_comes_from_date_when_year_is_missing(tmp_path):
    [s] = _parse(tmp_path, "@article{a, title={T}, date={2021-05-04}}\n")
    assert s.year == 2021


def test_an_organisation_author_in_braces_stays_one_author(tmp_path):
    [s] = _parse(tmp_path, "@report{a, title={T}, author={{World Health Organization} and Lee, J}}\n")
    assert s.authors == ["World Health Organization", "Lee, J"]


def test_missing_optional_fields_are_empty(tmp_path):
    [s] = _parse(tmp_path, "@misc{a, title={Only a title}}\n")
    assert (s.authors, s.year, s.doi, s.abstract, s.journal, s.metadata) == ([], None, None, None, None, {})


def test_comments_and_preambles_are_not_sources(tmp_path):
    sources = _parse(tmp_path, (
        "% a line comment\n"
        "@comment{exported by a reference manager}\n"
        "@preamble{\"\\newcommand{\\noop}[1]{}\"}\n"
        "@article{a, title={The only paper}}\n"
    ))
    assert [s.title for s in sources] == ["The only paper"]


def test_two_entries_sharing_a_citation_key_are_both_kept(tmp_path):
    """Exports from two databases can reuse a key like smith2020 for different papers."""
    sources = _parse(tmp_path, "@article{smith2020, title={First}}\n@article{smith2020, title={Second}}\n@article{lee2021, title={Third}}\n")
    assert [s.title for s in sources] == ["First", "Second", "Third"]


def test_a_byte_order_mark_is_ignored(tmp_path):
    path = tmp_path / "bom.bib"
    path.write_bytes("﻿@article{a, title={After a BOM}}\n".encode())
    assert [s.title for s in parse_bibtex(path)] == ["After a BOM"]


def test_a_missing_file_is_an_error(tmp_path):
    with pytest.raises(InputNotFoundError):
        parse_bibtex(tmp_path / "none.bib")


def test_a_broken_entry_is_not_dropped_silently(tmp_path):
    with pytest.raises(IngestError):
        _parse(tmp_path, "@article{a, title={Fine}}\n@article{b, title={Unclosed, year={2020}\n@article{c, title={After}}\n")
