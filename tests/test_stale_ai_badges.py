"""Extraction staleness, and the outdated badge on the full-text conflicts card.

TestStaleDetection (test_ai_run_flows.py) covers the same rule on the screening side. This file
covers the extraction side, which the full-text conflict cards read: their AI vote is the
flag_check verdict an extraction run produced, so an extraction made under a prompt you have
since edited is a stale vote to adjudicate against.
"""

import pytest

from ailr.core.source import Source
from ailr.reviewers import ExtractionResult, ScreeningDecision


def _source(project, title="Paper"):
    return project.db.insert_source(Source(title=title, project_id=project.project_id))


def _ai_extracted(project, sid, prompt_version):
    project.db.insert_extraction(ExtractionResult(
        extractor_type="ai", extractor_id="gpt", field_name="design",
        value="observational", source_id=sid, prompt_version=prompt_version,
    ))


def _version(project, composed):
    return project.db.save_prompt_version(
        project.project_id, "extraction", "template", composed=composed
    )


@pytest.mark.parametrize("stored,current,expected_stale", [
    (None, "new", False),   # never tagged with a version
    ("", "new", False),     # tagged, but the version stored no composed prompt
    ("old", "new", True),
    ("same", "same", False),
])
def test_extraction_staleness(tmp_project, stored, current, expected_stale):
    sid = _source(tmp_project)
    version = None if stored is None else _version(tmp_project, stored)
    _ai_extracted(tmp_project, sid, version)
    got = tmp_project.db.stale_ai_extraction_source_ids(tmp_project.project_id, current)
    assert got == ({sid} if expected_stale else set())


def test_source_ids_narrows_the_scan(tmp_project):
    db = tmp_project.db
    version = _version(tmp_project, "old")
    a, b = _source(tmp_project, "A"), _source(tmp_project, "B")
    _ai_extracted(tmp_project, a, version)
    _ai_extracted(tmp_project, b, version)
    assert db.stale_ai_extraction_source_ids(tmp_project.project_id, "new") == {a, b}
    assert db.stale_ai_extraction_source_ids(tmp_project.project_id, "new", source_ids=[b]) == {b}
    assert db.stale_ai_extraction_source_ids(tmp_project.project_id, "new", source_ids=[]) == set()


def _card_text(node):
    def walk(n):
        yield n
        children = getattr(n, "children", None)
        if children is None:
            return
        for c in (children if isinstance(children, (list, tuple)) else [children]):
            yield from walk(c)
    return " ".join(n for n in walk(node) if isinstance(n, str))


def test_full_text_conflict_card_carries_the_extraction_badge(tmp_project, monkeypatch):
    from ailr.ui import ft_conflicts_view

    db = tmp_project.db
    sid = _source(tmp_project, "Conflicted paper")
    db.insert_screening_decision(ScreeningDecision(
        decision="include", reasoning="in", reviewer_type="human",
        reviewer_id="amber", source_id=sid, stage="full_text",
    ))
    db.insert_screening_decision(ScreeningDecision(
        decision="exclude", reasoning="out", reviewer_type="ai",
        reviewer_id="gpt", source_id=sid, stage="full_text",
    ))
    _ai_extracted(tmp_project, sid, _version(tmp_project, "the old prompt"))

    monkeypatch.setattr("ailr.ui.ai_runner.current_extraction_composed", lambda _p: "the new prompt")
    text = _card_text(ft_conflicts_view.layout())
    assert "AI extraction outdated" in text
    assert "AI screening outdated" not in text  # the abstract wording must not leak over

    monkeypatch.setattr("ailr.ui.ai_runner.current_extraction_composed", lambda _p: "the old prompt")
    assert "AI extraction outdated" not in _card_text(ft_conflicts_view.layout())
