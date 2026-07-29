"""Full-text review sidebar filters.

The 'Full-text' checklist and the status list decide which papers a reviewer is shown, so a
wrong combination silently hides work rather than failing loudly.
"""

from types import SimpleNamespace

import pytest

from ailr.ui.full_text_view import (
    _RECONCILE_FILTER,
    _ft_avail_filter,
    _low_text_md,
    _status_filters,
)


@pytest.mark.parametrize(
    "checked,expected",
    [
        ([], (False, None)),
        (None, (False, None)),
        (["has"], (False, "has")),
        (["needs"], (False, "needs")),
        (["has", "needs"], (False, None)),          # both ticked = no availability filter
        (["low"], (True, None)),
        (["low", "has"], (True, None)),             # low wins: it is resolved into an id whitelist
        (["low", "has", "needs"], (True, None)),
    ],
)
def test_ft_avail_filter(checked, expected):
    assert _ft_avail_filter(checked) == expected


def _project(workflow: str):
    return SimpleNamespace(config=SimpleNamespace(extraction=SimpleNamespace(workflow=workflow)))


def test_to_reconcile_is_offered_only_for_independent_extraction():
    # Under `verify` only one person extracts, so nothing can ever need reconciling.
    for workflow in ("verify", "assisted"):
        assert _RECONCILE_FILTER not in _status_filters(_project(workflow))
    assert _RECONCILE_FILTER in _status_filters(_project("independent"))


def test_to_reconcile_sits_before_the_calibration_and_all_entries():
    values = [o["value"] for o in _status_filters(_project("independent"))]
    assert values.index("to_reconcile") < values.index("calibration") < values.index("all")


def _write_md(root, sid, text):
    p = root / "data" / "markdown"
    p.mkdir(parents=True, exist_ok=True)
    (p / f"{sid}.md").write_text(text, encoding="utf-8")


def test_low_text_md_flags_a_short_conversion(tmp_path):
    _write_md(tmp_path, 1, "x" * 50)
    assert _low_text_md(tmp_path, 1, 500) is True


def test_low_text_md_passes_a_full_conversion(tmp_path):
    _write_md(tmp_path, 1, "x" * 5000)
    assert _low_text_md(tmp_path, 1, 500) is False


def test_low_text_md_is_false_when_there_is_no_markdown(tmp_path):
    assert _low_text_md(tmp_path, 99, 500) is False
