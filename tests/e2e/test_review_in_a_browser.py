"""A review clicked through in a real browser against a real `ailr ui` server, with the Mock AI.

The callback tests call functions directly. These check what only a browser shows: that buttons
reach their callbacks in the order a page fires them. Every step checks the database, not the page.
"""

import json
import os
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from ailr.core.project import Project
from ailr.core.source import Source
from ailr.criteria import save_criteria
from ailr.exports.prisma import prisma_counts
from ailr.extraction import compose_schema
from ailr.reviewers import ScreeningDecision

playwright = pytest.importorskip("playwright.sync_api")
expect = playwright.expect

_RECORD = "TY  - JOUR\nTI  - {}\nAU  - Lee, J\nPY  - 2021\nDO  - {}\nAB  - An abstract about dyads and gaze.\nER  - \n\n"
_RECORDS = (
    ("Joint attention in toddlers", "10.1/a"),
    ("Gaze following across cultures", "10.1/b"),
    ("Shared reading and vocabulary growth", "10.1/c"),
    ("Turn taking in parent infant play", "10.1/d"),
    ("Joint attention in toddlers, a second copy", "10.1/a"),
)
_REASON = "Wrong population"
_TIMEOUT_MS = 20_000
expect.set_options(timeout=_TIMEOUT_MS)  # a CI runner can be several times slower than a laptop


def _pattern_id(**parts) -> str:
    """The CSS selector of a Dash pattern-matching id, which the DOM holds as sorted compact JSON."""
    return f"[id={json.dumps(json.dumps(parts, sort_keys=True, separators=(',', ':')))}]"


def _until(check, what: str, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for: {what}")


def _go(page, tab: str) -> None:
    page.locator(_pattern_id(tab=tab, type="nav-link")).click()


def _votes(db, sid: int, stage: str) -> list[str]:
    return [d["decision"] for d in db.get_human_decisions(sid, stage)]


def _serve(root: Path, home: Path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home)}  # keeps ~/.ailr/recent.json out of it
    log = (home / "ui.log").open("w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-m", "ailr.cli", "ui", str(root), "--port", str(port)],
        env=env, stdout=log, stderr=subprocess.STDOUT,
    )

    def listening() -> bool:
        if server.poll() is not None:
            raise AssertionError(f"ailr ui exited: {(home / 'ui.log').read_text(encoding='utf-8')}")
        with socket.socket() as client:
            return client.connect_ex(("127.0.0.1", port)) == 0

    _until(listening, "the UI server")
    return server, log, f"http://127.0.0.1:{port}/"


@pytest.fixture
def review(tmp_path):
    """A new project with criteria and variables, served by `ailr ui`."""
    root = tmp_path / "review"
    project = Project.init(root)
    save_criteria(root / project.config.screening.criteria_structured, [{"id": "I1", "name": "Population", "pass_if": "Dyads"}])
    (root / project.config.extraction.schema_path).write_text("include_suggested:\n  - sample_size\n", encoding="utf-8")
    assert compose_schema(root / project.config.extraction.schema_path)
    server, log, url = _serve(root, tmp_path)
    yield SimpleNamespace(project=project, db=project.db, pid=project.project_id, root=root, url=url, tmp=tmp_path)
    server.terminate()
    server.wait(timeout=15)
    log.close()
    project.db.close()


@pytest.fixture
def browser():
    with playwright.sync_playwright() as driver:
        instance = driver.chromium.launch()
        yield instance
        instance.close()


def _open_page(context, url: str, problems: list[str]):
    page = context.new_page()
    page.set_default_timeout(_TIMEOUT_MS)
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.on("console", lambda m: problems.append(m.text) if m.type == "error" else None)
    page.goto(url)
    return page


def _import_references(page, review) -> list[int]:
    ris = review.tmp / "scopus.ris"
    ris.write_text("".join(_RECORD.format(*record) for record in _RECORDS), encoding="utf-8")
    _go(page, "import")
    page.locator("#import-ref-db").click()
    page.get_by_text("Scopus", exact=True).click()
    page.locator("#import-ref-upload input[type=file]").set_input_files(str(ris))
    _until(lambda: review.db.count_sources(review.pid) == 4, "the import")
    assert review.db.count_duplicates(review.pid) == 1
    return sorted(s.id for s in review.db.list_sources(project_id=review.pid))


def _screen_with_mock_ai(page, review) -> None:
    _go(page, "workflow_abstract")
    page.get_by_role("tab", name="AI screening").click()
    page.click("#screen-ai-run")
    _until(lambda: review.db.screening_summary(review.pid, "ai")["uncertain"] == 4, "the mock screening run")


def _screen_by_hand(page, review, included, excluded, undone) -> None:
    db = review.db
    _go(page, "screen")
    for sid, decision in ((included, "include"), (excluded, "exclude"), (undone, "exclude")):
        page.click(_pattern_id(decision=decision, source=sid, type="screen-decide"))
        _until(lambda sid=sid, decision=decision: _votes(db, sid, "abstract") == [decision], "the vote")

    page.click("#screen-banner-undo")
    _until(lambda: _votes(db, undone, "abstract") == [], "the banner Undo")
    page.click(_pattern_id(decision="include", source=undone, type="screen-decide"))
    _until(lambda: _votes(db, undone, "abstract") == ["include"], "the second vote")


def _adjudicate_abstracts(page, review, included, excluded, undone) -> None:
    db, pid = review.db, review.pid

    def still_open():
        return db.unresolved_conflict_ids(pid, "assisted", stage="abstract")

    _until(lambda: still_open() == {included, excluded, undone}, "the conflicts")  # the mock AI said uncertain
    _go(page, "conflicts")
    for sid, decision in ((included, "include"), (excluded, "exclude"), (undone, "include")):
        page.click(_pattern_id(decision=decision, source=sid, type="conflict-decide"))
        _until(lambda sid=sid: sid not in still_open(), "the ruling")

    [ruling] = [r for r in db.list_reconciliations(pid, "abstract_screening") if r["source_id"] == undone]
    page.click(_pattern_id(rec_id=ruling["id"], type="conflict-undo"))
    _until(lambda: undone in still_open(), "the undone ruling")
    page.click(_pattern_id(decision="include", source=undone, type="conflict-decide"))
    _until(lambda: undone not in still_open(), "the second ruling")


def _add_full_text(review, sids) -> None:
    folder = review.root / "data" / "markdown"
    folder.mkdir(parents=True, exist_ok=True)
    for sid in sids:
        (folder / f"{sid}.md").write_text("# Paper\n\n" + "Forty-eight dyads took part. " * 40, encoding="utf-8")
        review.db.update_markdown_path(sid, Path("data/markdown") / f"{sid}.md")


def _extract_with_mock_ai(page, review, sids) -> None:
    _go(page, "workflow_fulltext")
    page.get_by_role("tab", name="AI extraction").click()
    page.click("#extract-ai-run")
    _until(lambda: all(review.db.get_latest_ai_decision(sid, "full_text") for sid in sids), "the mock extraction run")


def _pick_a_reason_and_confirm(page) -> None:
    page.get_by_label(_REASON).check()
    page.click("#ft-exclude-confirm")


def _review_full_texts(page, review, included, excluded) -> None:
    db, pid = review.db, review.pid
    _go(page, "full_text")
    page.click(_pattern_id(decision="include", source=included, type="ft-decide"))
    _until(lambda: _votes(db, included, "full_text") == ["include"], "the full-text include")
    page.click(_pattern_id(source=excluded, type="ft-exclude-open"))
    _pick_a_reason_and_confirm(page)
    _until(lambda: _votes(db, excluded, "full_text") == ["exclude"], "the full-text exclude")

    _go(page, "ft_conflicts")     # the mock AI passed it, so the exclusion is a conflict
    page.click(_pattern_id(source=excluded, type="ft-conflict-exclude-open"))
    _pick_a_reason_and_confirm(page)
    _until(lambda: db.final_exclude_ids(pid, "full_text", workflow="assisted") == {excluded}, "the full-text ruling")


def _verify_extraction(page, review, sid) -> None:
    _go(page, "full_text")
    page.locator("#ft-status-all input").click()
    page.click(_pattern_id(source=sid, type="ft-open-extract"))
    expect(page.locator("[id*=ex-value], [id*=ex-grid]").first).to_be_attached()  # Submit is enabled before the form lands
    page.click("#extract-submit")
    _until(lambda: review.db.human_extractors_for_sources([sid]), "the submitted extraction")


def _download(page, button: str) -> Path:
    with page.expect_download() as download:
        page.click(button)
    return Path(download.value.path())


def test_a_review_from_import_to_reports(review, browser):
    review.db.create_exclusion_reason(review.pid, _REASON)
    problems: list[str] = []
    page = _open_page(browser.new_context(accept_downloads=True), review.url, problems)
    page.fill("#shared-reviewer", "amber")

    included, excluded_at_abstract, excluded_at_full_text, unread = _import_references(page, review)
    _screen_with_mock_ai(page, review)
    _screen_by_hand(page, review, included, excluded_at_abstract, excluded_at_full_text)
    _adjudicate_abstracts(page, review, included, excluded_at_abstract, excluded_at_full_text)
    _add_full_text(review, (included, excluded_at_full_text))
    _extract_with_mock_ai(page, review, (included, excluded_at_full_text))
    _review_full_texts(page, review, included, excluded_at_full_text)
    _verify_extraction(page, review, included)

    counts = prisma_counts(review.project)
    assert (counts["records_identified"], counts["duplicates_removed"], counts["abstract_screened"]) == (5, 1, 3)
    assert (counts["abstract_excluded"], counts["reports_sought"], counts["full_text_excluded_reports"]) == (1, 2, 1)
    assert (counts["reports_included"], counts["studies_extracted"]) == (1, 1)
    assert counts["by_source_database"] == [{"source_database": "Scopus", "n": 5}]
    assert [_votes(review.db, unread, stage) for stage in ("abstract", "full_text")] == [[], []]

    _go(page, "reports")
    report = _download(page, "#report-dl-prisma").read_text(encoding="utf-8")
    assert "**Total records identified:** 5" in report and "- Scopus: 5" in report
    page.get_by_role("tab", name="Data exports").click()
    bundle = _download(page, "#report-dl-extraction")
    assert any(name.startswith("included/") for name in zipfile.ZipFile(bundle).namelist())

    assert problems == []


def test_a_tab_left_on_an_earlier_project_cannot_write_into_the_new_one(review, browser):
    """Each project numbers its papers from 1, so an id alone would land on the wrong paper."""
    sid = review.db.insert_source(Source(title="A paper of the first project", abstract="x", project_id=review.pid))
    review.db.insert_screening_decision(ScreeningDecision(
        decision="include", reasoning="r", reviewer_type="ai", reviewer_id="m", source_id=sid,
    ))
    other_root = review.tmp / "other"
    other = Project.init(other_root)
    assert other.db.insert_source(Source(title="A paper of the second project", abstract="y", project_id=other.project_id)) == sid

    context = browser.new_context()
    stale_problems: list[str] = []
    stale = _open_page(context, review.url, stale_problems)
    stale.fill("#shared-reviewer", "amber")
    _go(stale, "screen")
    vote = stale.locator(_pattern_id(decision="exclude", source=sid, type="screen-decide"))
    expect(vote).to_be_visible()

    switcher = _open_page(context, review.url, [])
    _go(switcher, "projects")
    switcher.fill("#pm-open-path", str(other_root))
    switcher.click("#pm-open-btn")
    expect(switcher.locator("#app-header")).to_contain_text(other.config.project.name)

    vote.click()
    expect(stale.locator("#stale-project-notice")).to_be_visible()
    assert _votes(review.db, sid, "abstract") == []
    assert _votes(other.db, sid, "abstract") == []
    other.db.close()
