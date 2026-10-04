"""Card identity: every piece of data shown on a card belongs to that card's paper.

The conflict cards are assembled from ~7 batch queries whose results are looked up by
source id ([_conflicts_base.initial_payload]). A grouping bug in one of those queries, or a
switch to zipping results positionally, would pair paper A's card with paper B's AI verdict,
tags or flag check. Nothing about that looks broken on screen: the card still renders, the
reasoning is just quietly about a different paper.

Every seeded row therefore carries a marker naming the paper it belongs to, so a mix-up is
an assertion failure rather than something a reviewer has to notice.
"""

import json

import pytest

from ailr.core.source import Source
from ailr.reviewers import ExtractionResult, ScreeningDecision
from ailr.ui import ft_conflicts_view
from tests.helpers import component_text, find_by_id, walk

_N = 5


def _marker(i: int) -> str:
    return f"M{i}"


@pytest.fixture
def conflicted(tmp_project):
    """_N papers in full-text conflict (AI exclude vs human include), each carrying its own
    marker in every field a card reads, plus one paper with no side data at all."""
    db = tmp_project.db
    sids = {}
    for i in range(1, _N + 1):
        m = _marker(i)
        sid = db.insert_source(Source(title=f"Paper {m}", project_id=tmp_project.project_id))
        sids[m] = sid

        db.insert_screening_decision(ScreeningDecision(
            decision="include", reasoning=f"human-{m}", reviewer_type="human",
            reviewer_id=f"hum-{m}", source_id=sid, stage="full_text",
        ))
        # A superseded AI verdict first: the card must show the newer one.
        db.insert_screening_decision(ScreeningDecision(
            decision="include", reasoning=f"stale-ai-{m}", reviewer_type="ai",
            reviewer_id="gpt", source_id=sid, stage="full_text",
        ))
        db.insert_screening_decision(ScreeningDecision(
            decision="exclude", reasoning=f"ai-{m}", reviewer_type="ai",
            reviewer_id="gpt", source_id=sid, stage="full_text",
        ))
        db.insert_extraction(ExtractionResult(
            extractor_type="ai", extractor_id="gpt", field_name="_flag_check",
            value=[{"criterion_id": f"crit-{m}", "verdict": "fail", "reason": f"flag-{m}"}],
            source_id=sid,
        ))
        tag_id = db.create_tag(tmp_project.project_id, f"tag-{m}")
        db.tag_source(sid, tag_id)
        for n in range(i):  # a different note count per paper
            db.add_note(sid, f"hum-{m}", f"note-{m}-{n}")

    bare = db.insert_source(Source(title="Paper BARE", project_id=tmp_project.project_id))
    db.insert_screening_decision(ScreeningDecision(
        decision="include", reasoning="human-BARE", reviewer_type="human",
        reviewer_id="hum-bare", source_id=bare, stage="full_text",
    ))
    return tmp_project, sids, bare


# ----- Batch lookups -----

def test_human_decisions_belong_to_their_own_paper(conflicted):
    project, sids, _ = conflicted
    got = project.db.get_human_decisions_for_sources(list(sids.values()), stage="full_text")
    for m, sid in sids.items():
        assert [d["reasoning"] for d in got[sid]] == [f"human-{m}"]


def test_latest_ai_row_is_the_right_paper_and_the_newest_verdict(conflicted):
    project, sids, _ = conflicted
    got = project.db.get_latest_ai_decision_rows(list(sids.values()), stage="full_text")
    for m, sid in sids.items():
        assert got[sid]["reasoning"] == f"ai-{m}"
        assert got[sid]["decision"] == "exclude"


def test_flag_checks_belong_to_their_own_paper(conflicted):
    project, sids, _ = conflicted
    got = project.db.get_flag_checks(list(sids.values()))
    for m, sid in sids.items():
        assert [f["criterion_id"] for f in got[sid]] == [f"crit-{m}"]


def test_tags_belong_to_their_own_paper(conflicted):
    project, sids, _ = conflicted
    got = project.db.get_tags_for_sources(list(sids.values()))
    for m, sid in sids.items():
        assert [t["name"] for t in got[sid]] == [f"tag-{m}"]


def test_note_counts_belong_to_their_own_paper(conflicted):
    project, sids, _ = conflicted
    got = project.db.count_notes(list(sids.values()))
    for i in range(1, _N + 1):
        assert got[sids[_marker(i)]] == i


def test_companions_belong_to_their_own_paper(conflicted):
    project, sids, _ = conflicted
    db = project.db
    db.set_study_group(sids["M2"], sids["M1"])
    got = db.list_study_companions(list(sids.values()))
    assert [c["id"] for c in got[sids["M1"]]] == [sids["M2"]]
    assert [c["id"] for c in got[sids["M2"]]] == [sids["M1"]]
    for m in ("M3", "M4", "M5"):
        assert got.get(sids[m], []) == []


@pytest.mark.parametrize("method,stage_kw", [
    ("get_human_decisions_for_sources", {"stage": "full_text"}),
    ("get_latest_ai_decision_rows", {"stage": "full_text"}),
    ("get_flag_checks", {}),
    ("get_tags_for_sources", {}),
    ("count_notes", {}),
])
def test_the_order_of_the_ids_asked_for_does_not_change_the_mapping(conflicted, method, stage_kw):
    """A positional zip would pass the tests above and still fail here."""
    project, sids, _ = conflicted
    ids = list(sids.values())
    fn = getattr(project.db, method)
    assert fn(list(reversed(ids)), **stage_kw) == fn(ids, **stage_kw)


def test_a_paper_without_side_data_gets_nothing_from_its_neighbours(conflicted):
    project, sids, bare = conflicted
    db = project.db
    ids = [*sids.values(), bare]
    assert db.get_latest_ai_decision_rows(ids, stage="full_text").get(bare) is None
    assert db.get_flag_checks(ids).get(bare) is None
    assert db.get_tags_for_sources(ids)[bare] == []
    assert db.count_notes(ids).get(bare, 0) == 0
    assert [d["reasoning"] for d in db.get_human_decisions_for_sources(ids, stage="full_text")[bare]] == ["human-BARE"]


# ----- Rendered cards -----


def _source_ids_targeted(card) -> set:
    """Every source id the card's buttons and inputs would act on when clicked."""
    out = set()
    for n in walk(card):
        comp_id = getattr(n, "id", None)
        if isinstance(comp_id, dict) and "source" in comp_id:
            out.add(comp_id["source"])
    return out


def test_every_card_shows_one_papers_data_and_acts_on_that_paper(conflicted):
    project, sids, _ = conflicted
    assert project.config.screening_workflow("full_text") == "assisted"
    cards = find_by_id(ft_conflicts_view.layout(), "ft-conflicts-cards").children
    assert len(cards) == _N

    by_sid = {sid: m for m, sid in sids.items()}
    seen = set()
    for card in cards:
        targeted = _source_ids_targeted(card)
        assert len(targeted) == 1, f"one card wires its controls to several papers: {targeted}"
        sid = targeted.pop()
        mine = by_sid[sid]
        seen.add(mine)

        text = component_text(card)
        for marker in (f"Paper {mine}", f"human-{mine}", f"ai-{mine}", f"crit-{mine}", f"tag-{mine}"):
            assert marker in text, f"card for {mine} is missing its own {marker!r}"
        assert f"Note ({int(mine[1:])})" in text
        assert f"stale-ai-{mine}" not in text  # the superseded verdict stays off the card

        for other in (m for m in sids if m != mine):
            assert other not in text.replace(mine, ""), f"card for {mine} carries data of {other}"

    assert seen == set(sids)


def _ft_conflict(project, marker: str, *, side_data: bool) -> int:
    """One paper in full-text conflict, every row it carries tagged with `marker`."""
    db = project.db
    sid = db.insert_source(Source(title=f"Paper {marker}", project_id=project.project_id))
    for decision, rtype, rid in (("include", "human", f"hum-{marker}"), ("exclude", "ai", "gpt")):
        db.insert_screening_decision(ScreeningDecision(
            decision=decision, reasoning=f"{rtype}-{marker}", reviewer_type=rtype,
            reviewer_id=rid, source_id=sid, stage="full_text",
        ))
    if side_data:
        db.insert_extraction(ExtractionResult(
            extractor_type="ai", extractor_id="gpt", field_name="_flag_check",
            value=[{"criterion_id": f"crit-{marker}", "verdict": "fail", "reason": "x"}], source_id=sid,
        ))
        db.tag_source(sid, db.create_tag(project.project_id, f"tag-{marker}"))
        db.add_note(sid, f"hum-{marker}", "a note")
    return sid


def test_a_conflicted_paper_with_no_side_data_does_not_shift_its_neighbours(tmp_project):
    """In the fixture every conflicted paper has side data, so a positional zip would still line
    up. Here the middle one has none, and pairing by position would hand its flag check slot to
    the next paper's card."""
    sids = {m: _ft_conflict(tmp_project, m, side_data=(m != "Y")) for m in ("X", "Y", "Z")}
    by_sid = {sid: m for m, sid in sids.items()}
    cards = find_by_id(ft_conflicts_view.layout(), "ft-conflicts-cards").children
    assert len(cards) == 3
    for card in cards:
        [sid] = _source_ids_targeted(card)
        mine = by_sid[sid]
        text = component_text(card).replace(f"Paper {mine}", "")
        for other in (m for m in sids if m != mine):
            assert f"crit-{other}" not in text and f"tag-{other}" not in text, (mine, other)


def test_a_full_text_card_shows_no_abstract_votes(tmp_project):
    db = tmp_project.db
    sid = _ft_conflict(tmp_project, "W", side_data=False)
    for rtype, rid in (("human", "carol"), ("ai", "gpt")):   # the AI row is a later abstract re-run
        db.insert_screening_decision(ScreeningDecision(
            decision="include", reasoning=f"abstract-{rtype}", reviewer_type=rtype,
            reviewer_id=rid, source_id=sid, stage="abstract",
        ))
    [card] = find_by_id(ft_conflicts_view.layout(), "ft-conflicts-cards").children
    text = component_text(card)
    assert "human-W" in text and "ai-W" in text
    assert "abstract-human" not in text and "abstract-ai" not in text


def test_the_reader_button_opens_the_paper_whose_card_it_is_on(conflicted):
    """The 'Read full text' button is what turns a card mix-up into reading the wrong PDF."""
    project, sids, _ = conflicted
    cards = find_by_id(ft_conflicts_view.layout(), "ft-conflicts-cards").children
    by_sid = {sid: m for m, sid in sids.items()}
    for card in cards:
        read_btn = next(
            n for n in walk(card)
            if isinstance(getattr(n, "id", None), dict) and n.id.get("type") == "ft-read-btn"
        )
        assert f"Paper {by_sid[read_btn.id['source']]}" in component_text(card)


def test_the_flag_check_shown_is_the_one_stored_for_that_paper(conflicted):
    """flag_check comes from the extractions table, the other votes from screening_decisions;
    a card joining the two by position rather than by id would cross them here."""
    project, sids, _ = conflicted
    stored = project.db.get_flag_checks(list(sids.values()))
    for m, sid in sids.items():
        raw = json.dumps(stored[sid])
        assert f"crit-{m}" in raw and all(f"crit-{o}" not in raw for o in sids if o != m)
