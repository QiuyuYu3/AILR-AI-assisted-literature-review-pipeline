"""Verbatim quote audit: normalization tolerances, elision fragments, nested collection."""

from ailr.quote_audit import audit_fields
from ailr.reviewers import QUOTE_SEPARATOR

_TEXT = """# A Paper

Forty 4-month-old ( _M_ age = 4.07 months, SD = 0.31) full-term infants participated.
We used **TO-ComboSAD** for speech activity detection, per Schröder and Trouvain (2003).
The dataset consists of spontaneous conversational speech recorded with LENA units in a
high quality childcare learning center. Coefficient of variation was calculated per channel.
"""


def _one(field="f", value="v", quote=None):
    return audit_fields([(field, value, quote)], _TEXT, source_id=1)


class TestMatching:
    def test_exact_quote_found(self):
        a = _one(quote="The dataset consists of spontaneous conversational speech")
        assert (a.checked, a.verbatim) == (1, 1)

    def test_markdown_emphasis_and_bold_ignored(self):
        # paper has "( _M_ age" and "**TO-ComboSAD**"; the model echoes plain text
        assert _one(quote="Forty 4-month-old (M age = 4.07 months, SD = 0.31)").verbatim == 1
        assert _one(quote="We used TO-ComboSAD for speech activity detection").verbatim == 1

    def test_diacritics_folded(self):
        assert _one(quote="per Schroder and Trouvain (2003)").verbatim == 1

    def test_ellipsis_fragments_each_match(self):
        a = _one(quote="Forty 4-month-old ... full-term infants participated")
        assert a.verbatim == 1
        assert _one(quote="Forty 4-month-old ... nonexistent claim here").verbatim == 0

    def test_fabricated_quote_lands_in_not_found(self):
        a = _one(quote="This sentence does not appear in the paper at all.")
        assert a.verbatim == 0
        assert a.not_found[0]["source_id"] == 1 and a.not_found[0]["field"] == "f"

    def test_stacked_quotes_checked_individually(self):
        q = f"Coefficient of variation was calculated per channel{QUOTE_SEPARATOR}made-up second quote"
        a = _one(quote=q)
        assert (a.checked, a.verbatim) == (2, 1)


class TestAccounting:
    def test_null_value_not_counted(self):
        a = audit_fields([("f", None, None), ("g", "", None), ("h", [], None)], _TEXT)
        assert a.values == 0

    def test_value_without_quote_counts_against_coverage(self):
        a = audit_fields([("f", "Journal", None)], _TEXT)
        assert (a.values, a.quoted, a.checked) == (1, 0, 0)

    def test_reserved_fields_skipped(self):
        assert audit_fields([("_flag_check", [{"verdict": "PASS"}], None)], _TEXT).values == 0

    def test_nested_quotes_collected_from_list_of_objects(self):
        value = [{"tool": {"value": "TO-ComboSAD",
                           "quote": "We used TO-ComboSAD for speech activity detection"}}]
        a = audit_fields([("tools", value, None)], _TEXT)
        assert (a.values, a.quoted, a.checked, a.verbatim) == (1, 1, 1, 1)

    def test_merge_aggregates(self):
        a = audit_fields([("f", "x", "made-up quote one two")], _TEXT, source_id=1)
        b = audit_fields([("f", "y", "Coefficient of variation was calculated per channel")], _TEXT, source_id=2)
        a.merge(b)
        assert (a.values, a.quoted, a.checked, a.verbatim) == (2, 2, 2, 1)
        assert a.per_field["f"] == [2, 2, 2, 1]
        assert len(a.not_found) == 1
