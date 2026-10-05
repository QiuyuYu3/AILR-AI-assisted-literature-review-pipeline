"""The UI's background runners, run inline: job-key guard, status reporting, and what each run
writes. Threads are replaced by a synchronous stand-in; real API calls by a non-mock client."""

from types import SimpleNamespace

import pytest

import ailr.ui.ai_runner as ai_runner
from ailr.reviewers import ExtractionResult
from ailr.ui import screen_view
from tests.helpers import ApiClient, add_source, callbacks_of, count_decisions, settle, vote


class _InlineThread:
    def __init__(self, target, args, daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


class _HeldThread(_InlineThread):
    """Never runs, so the job it belongs to stays 'running'."""

    def start(self):
        pass


@pytest.fixture(autouse=True)
def inline_jobs(monkeypatch):
    monkeypatch.setattr(ai_runner, "_jobs", {})
    # Only the runner's own reference: ThreadPoolExecutor inside the tasks needs real threads.
    monkeypatch.setattr(ai_runner, "threading", SimpleNamespace(Thread=_InlineThread))


@pytest.fixture
def api_client(monkeypatch):
    monkeypatch.setattr(ai_runner, "make_llm_client", lambda **_: ApiClient())


def _no_client(**_):
    raise AssertionError("no client should be built")


def _candidate(project, title="Paper") -> int:
    sid = add_source(project, title, md_on_disk=True)
    settle(project.db, sid, "include", stage="abstract")
    return sid


def _live_ai_rows(db, sid) -> list[dict]:
    rows = db._conn.execute(
        "SELECT id, field_name, extractor_id FROM extractions WHERE source_id = ? AND extractor_type = 'ai'", (sid,)
    ).fetchall()
    return [dict(r) for r in rows]


def _reviewers(db, stage) -> set[str]:
    rows = db._conn.execute("SELECT reviewer_id FROM screening_decisions WHERE stage = ?", (stage,)).fetchall()
    return {r["reviewer_id"] for r in rows}


class TestJobGuard:
    def test_a_running_key_refuses_a_second_start(self, tmp_project, monkeypatch):
        monkeypatch.setattr(ai_runner, "threading", SimpleNamespace(Thread=_HeldThread))

        assert ai_runner.start_extraction(tmp_project, mock=True) is True
        assert ai_runner.start_extraction(tmp_project, mock=True) is False
        # A one-paper re-run would write the same rows as the batch, so it shares the key.
        assert ai_runner.start_single_extraction(tmp_project, 1, mock=True) is False
        assert ai_runner.start_screening(tmp_project, mock=True) is True

    def test_a_finished_job_can_start_again(self, tmp_project):
        assert ai_runner.start_screening(tmp_project, mock=True) is True
        assert ai_runner.get_status("screening")["running"] is False
        assert ai_runner.start_screening(tmp_project, mock=True) is True

    def test_an_unknown_key_reads_as_never_started(self):
        assert ai_runner.get_status("nothing") == {
            "running": False, "started": False, "done": 0, "total": 0, "error": None, "summary": None,
        }

    def test_the_status_is_a_copy(self, tmp_project):
        ai_runner.start_screening(tmp_project, mock=True)
        ai_runner.get_status("screening")["summary"] = "edited"
        assert ai_runner.get_status("screening")["summary"] != "edited"


class TestScreening:
    def test_mock_run_reports_progress_and_counts(self, tmp_project):
        add_source(tmp_project, "A", abstract="About joint attention.")
        add_source(tmp_project, "B", abstract="About gaze.")

        ai_runner.start_screening(tmp_project, mock=True)

        status = ai_runner.get_status("screening")
        assert status["error"] is None
        assert (status["done"], status["total"]) == (2, 2)
        assert status["summary"] == "Screened 2/2 — include 0, exclude 0, uncertain 2."
        assert count_decisions(tmp_project.db, tmp_project.project_id, "ai") == 2

    def test_a_real_run_replaces_mock_decisions(self, tmp_project, api_client):
        sid = add_source(tmp_project, "A", abstract="About joint attention.")
        vote(tmp_project.db, sid, "uncertain", "mock:mock-screen", stage="abstract", reviewer_type="ai")

        ai_runner.start_screening(tmp_project, mock=False)

        assert "Replaced 1 earlier mock decision(s)." in ai_runner.get_status("screening")["summary"]
        assert _reviewers(tmp_project.db, "abstract") == {"stub:model-a"}

    def test_a_real_run_keeps_the_verdicts_of_a_mock_extraction(self, tmp_project, api_client):
        """They belong with the mock extraction rows, which only a real extraction run clears."""
        _candidate(tmp_project)
        ai_runner.start_extraction(tmp_project, mock=True)

        ai_runner.start_screening(tmp_project, mock=False)

        assert _reviewers(tmp_project.db, "full_text") == {"mock:mock-extract"}

    def test_a_real_run_that_cannot_start_keeps_the_mock_decisions(self, tmp_project):
        """No model configured: the run fails before any call, so nothing replaces what it cleared."""
        sid = add_source(tmp_project, "A", abstract="Text.")
        vote(tmp_project.db, sid, "uncertain", "mock:mock-screen", stage="abstract", reviewer_type="ai")

        ai_runner.start_screening(tmp_project, mock=False)

        status = ai_runner.get_status("screening")
        assert status["running"] is False and "No model set" in status["error"]
        assert _reviewers(tmp_project.db, "abstract") == {"mock:mock-screen"}

    def test_the_clear_mock_button_keeps_the_verdicts_of_a_mock_extraction(self, tmp_project):
        _candidate(tmp_project)
        other = add_source(tmp_project, "B", abstract="Text.")
        vote(tmp_project.db, other, "uncertain", "mock:mock-screen", stage="abstract", reviewer_type="ai")
        ai_runner.start_extraction(tmp_project, mock=True)

        callbacks_of(screen_view)["_clear_mock"](1)

        assert "mock:mock-screen" not in _reviewers(tmp_project.db, "abstract")
        assert _reviewers(tmp_project.db, "full_text") == {"mock:mock-extract"}


class TestExtraction:
    def test_mock_run_extracts_and_reports(self, tmp_project):
        sid = _candidate(tmp_project)

        ai_runner.start_extraction(tmp_project, mock=True)

        status = ai_runner.get_status("extraction")
        assert status["error"] is None
        assert status["summary"].startswith("Extracted 1/1 (already done 0, failed 0).")
        assert {r["extractor_id"] for r in _live_ai_rows(tmp_project.db, sid)} == {"mock:mock-extract"}

    def test_a_real_run_replaces_mock_extractions(self, tmp_project, api_client):
        sid = _candidate(tmp_project)
        ai_runner.start_extraction(tmp_project, mock=True)

        ai_runner.start_extraction(tmp_project, mock=False)

        assert "earlier mock extraction row(s)" in ai_runner.get_status("extraction")["summary"]
        assert {r["extractor_id"] for r in _live_ai_rows(tmp_project.db, sid)} == {"stub:model-a"}

    def test_a_real_run_that_cannot_start_keeps_the_mock_extractions(self, tmp_project):
        sid = _candidate(tmp_project)
        tmp_project.db.insert_extraction(ExtractionResult(
            extractor_type="ai", extractor_id="mock:mock-extract", field_name="sample_size", value=10, source_id=sid,
        ))

        ai_runner.start_extraction(tmp_project, mock=False)

        assert "No model set" in ai_runner.get_status("extraction")["error"]
        assert [r["extractor_id"] for r in _live_ai_rows(tmp_project.db, sid)] == ["mock:mock-extract"]

    def test_single_rerun_retires_the_previous_run(self, tmp_project, api_client):
        sid = _candidate(tmp_project)
        ai_runner.start_single_extraction(tmp_project, sid)
        first = {r["id"] for r in _live_ai_rows(tmp_project.db, sid)}

        ai_runner.start_single_extraction(tmp_project, sid)

        assert "from the previous AI run as an earlier version" in ai_runner.get_status("extraction")["summary"]
        assert first.isdisjoint(r["id"] for r in _live_ai_rows(tmp_project.db, sid))

    def test_a_failed_single_rerun_keeps_the_previous_run(self, tmp_project, monkeypatch, api_client):
        sid = _candidate(tmp_project)
        ai_runner.start_single_extraction(tmp_project, sid)
        first = _live_ai_rows(tmp_project.db, sid)
        broken = ApiClient()
        broken._response_fn = None  # canned screening-shaped answer: no _flag_check, so the paper fails
        monkeypatch.setattr(ai_runner, "make_llm_client", lambda **_: broken)

        ai_runner.start_single_extraction(tmp_project, sid)

        summary = ai_runner.get_status("extraction")["summary"]
        assert "failed 1" in summary and summary.endswith("Previous AI extraction kept.")
        assert _live_ai_rows(tmp_project.db, sid) == first

    def test_single_rerun_without_markdown_says_so(self, tmp_project, api_client):
        sid = add_source(tmp_project, "No text")

        ai_runner.start_single_extraction(tmp_project, sid)

        assert ai_runner.get_status("extraction")["summary"] == (
            "This paper has no full-text markdown, so there was nothing to extract."
        )


class TestCrossCheck:
    def test_deterministic_check_leaves_out_flagged_duplicates(self, tmp_project):
        sid = _candidate(tmp_project)
        copy = _candidate(tmp_project, "Copy")
        ai_runner.start_extraction(tmp_project, mock=True)
        tmp_project.db.mark_source_duplicate(copy, True)

        ai_runner.start_crosscheck(tmp_project)

        status = ai_runner.get_status("crosscheck")
        assert status["error"] is None and status["summary"]
        assert tmp_project.db.get_cross_checks(sid, stage="extraction")
        assert not tmp_project.db.get_cross_checks(copy)

    def test_a_blocked_llm_check_reports_why_and_calls_nothing(self, tmp_project, monkeypatch):
        monkeypatch.setattr(ai_runner, "make_llm_client", _no_client)

        ai_runner.start_llm_crosscheck(tmp_project, mock=False)

        status = ai_runner.get_status("crosscheck-llm")
        assert status["running"] is False
        assert status["error"] == "LLM cross-check is disabled (crosscheck.llm_enabled)."

    def test_a_mock_llm_check_skips_the_block(self, tmp_project, monkeypatch):
        sid = _candidate(tmp_project)
        ai_runner.start_extraction(tmp_project, mock=True)
        monkeypatch.setattr(ai_runner, "make_llm_client", _no_client)

        ai_runner.start_llm_crosscheck(tmp_project, mock=True, source_ids=[sid])

        assert ai_runner.get_status("crosscheck-llm")["error"] is None
        assert tmp_project.db.get_cross_checks(sid, stage="extraction")


class TestTrialRuns:
    def test_quick_test_reports_its_run_id(self, tmp_project):
        add_source(tmp_project, "A", abstract="Text.")

        ai_runner.start_quick_test(tmp_project, 1, mock=True)

        result = ai_runner.get_status("quicktest-abstract")["result"]
        assert tmp_project.db._conn.execute("SELECT id FROM test_runs").fetchone()["id"] == result["run_id"]
        assert count_decisions(tmp_project.db, tmp_project.project_id) == 0  # trial runs stay out of the record

    def test_extraction_quick_test_runs_on_its_own_key(self, tmp_project):
        _candidate(tmp_project)

        ai_runner.start_quick_test(tmp_project, 1, mock=True, stage="extraction")

        status = ai_runner.get_status("quicktest-extraction")
        assert status["error"] is None and "failed 0" in status["summary"]

    def test_calibration_reports_its_round(self, tmp_project):
        add_source(tmp_project, "A", abstract="Text.")

        ai_runner.start_calibration(tmp_project, 1, mock=True)

        assert ai_runner.get_status("calibration-abstract")["result"] == {"sample_round": 1}


class _ShortConverter:
    backend_name = "fake"

    def convert(self, pdf_path):
        return "Too short."


def test_preprocess_names_the_low_text_papers(tmp_project, monkeypatch):
    monkeypatch.setattr("ailr.tasks.preprocess.make_converter", lambda *_: _ShortConverter())
    sid = add_source(tmp_project, "A")
    pdfs = tmp_project.root / "data" / "pdfs"
    pdfs.mkdir(parents=True, exist_ok=True)
    (pdfs / f"{sid}.pdf").write_bytes(b"%PDF-1.4")

    ai_runner.start_preprocess(tmp_project)

    summary = ai_runner.get_status("preprocess")["summary"]
    assert summary.startswith("Converted 1, already done 0, failed 0")
    assert f"Low-text (likely scanned/failed): 1 — #{sid}." in summary
