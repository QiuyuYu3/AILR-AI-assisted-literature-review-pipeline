"""strip_references: only a bibliography heading in the latter half of a document cuts; an early
'References' mention (a JSTOR cover page) must not delete the body.
"""

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
