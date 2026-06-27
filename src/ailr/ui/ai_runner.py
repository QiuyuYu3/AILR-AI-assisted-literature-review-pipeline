"""Background AI runners for the UI: run ScreeningTask / ExtractionTask in a thread, expose progress.

A single-user desktop app, so one global job per kind is enough. Progress is polled by a dcc.Interval.
"""

import threading
from typing import Any, Callable

from ailr.core.config import resolve_stage_llm
from ailr.llm.factory import make_llm_client
from ailr.reviewers import LLMReviewer
from ailr.tasks.extract import ExtractionTask
from ailr.tasks.screen import ScreeningTask
from ailr.ui._common import read_screening_prompt

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _default() -> dict:
    return {"running": False, "started": False, "done": 0, "total": 0, "error": None, "summary": None}


def get_status(key: str) -> dict:
    with _lock:
        return dict(_jobs.get(key, _default()))


def is_running(key: str) -> bool:
    with _lock:
        return bool(_jobs.get(key, {}).get("running"))


def _make_client(project: Any, stage: str, mock: bool):
    if mock:
        if stage == "extract":
            # Mock extraction fabricates data shaped to the schema so the extraction UI
            # populates every field (value/quote, groups, _flag_check) — no API call.
            from ailr.llm.mock import MockLLMClient, synth_from_tool_schema

            return MockLLMClient(
                model=f"mock-{stage}",
                response_fn=lambda _s, _u, ts: synth_from_tool_schema(ts),
            )
        return make_llm_client("mock", model=f"mock-{stage}")
    cfg = project.config
    stage_cfg = cfg.screening.llm if stage == "screen" else cfg.extraction.llm
    llm = resolve_stage_llm(cfg.llm, stage_cfg)
    return make_llm_client(
        provider=llm.provider,
        model=llm.model,
        temperature=llm.temperature,
        seed=llm.seed,
        max_retries=llm.max_retries,
    )


def _progress_cb(key: str) -> Callable:
    def cb(idx, total, *_):
        with _lock:
            if key in _jobs:
                _jobs[key]["done"] = idx
                _jobs[key]["total"] = total
    return cb


def _start(key: str, runner: Callable, *args: Any) -> bool:
    with _lock:
        if _jobs.get(key, {}).get("running"):
            return False
        _jobs[key] = {**_default(), "running": True, "started": True}
    threading.Thread(target=runner, args=(key, *args), daemon=True).start()
    return True


def start_screening(project: Any, mock: bool) -> bool:
    return _start("screening", _run_screening, project, mock)


def start_quick_test(project: Any, n: int, mock: bool, stage: str = "abstract") -> bool:
    return _start(f"quicktest-{stage}", _run_quick_test, project, mock, n, stage)


def start_calibration(project: Any, n: int, mock: bool) -> bool:
    return _start("calibration-abstract", _run_calibration, project, mock, n)


def start_extraction(project: Any, mock: bool, all_sources: bool = False, force: bool = False) -> bool:
    return _start("extraction", _run_extraction, project, mock, all_sources, force)


def start_preprocess(project: Any, force: bool = False, only_ids=None) -> bool:
    return _start("preprocess", _run_preprocess, project, force, only_ids)


def _run_preprocess(key: str, project: Any, force: bool, only_ids) -> None:
    try:
        from ailr.tasks.preprocess import PreprocessTask

        summary = PreprocessTask(project).run(force=force, only_ids=only_ids, on_progress=_progress_cb(key))
        text = (
            f"Converted {summary.converted}, already done {summary.skipped_already_done}, "
            f"failed {summary.failed}, no PDF for {len(summary.missing_pdfs)} source(s)."
        )
        if summary.low_quality:
            ids = ", ".join(f"#{q['source_id']}" for q in summary.low_quality)
            text += (
                f" Low-text (likely scanned/failed): {len(summary.low_quality)} — {ids}. "
                "Consider re-converting these with the marker backend."
            )
        with _lock:
            _jobs[key].update({"running": False, "summary": text})
    except Exception as e:
        with _lock:
            _jobs[key].update({"running": False, "error": str(e)})


def _screening_prompt_version(project: Any) -> str:
    db = project.db
    pid = project.project_id
    content = read_screening_prompt("")
    latest = db.latest_prompt_version(pid, "screening")
    if latest is not None:
        prev = db.get_prompt_version(pid, "screening", latest)
        if prev and prev["content"] == content:
            return latest
    if not content.strip() and latest is None:
        return "unversioned"
    return db.save_prompt_version(pid, "screening", content, None)


def _run_screening(key: str, project: Any, mock: bool) -> None:
    try:
        client = _make_client(project, "screen", mock)
        reviewer = LLMReviewer(client, prompt_version=_screening_prompt_version(project))
        summary = ScreeningTask(project, reviewer).run(on_progress=_progress_cb(key))
        text = (
            f"Screened {summary.screened}/{summary.total} — "
            f"include {summary.include}, exclude {summary.exclude}, uncertain {summary.uncertain}."
        )
        with _lock:
            _jobs[key].update({"running": False, "summary": text})
    except Exception as e:  # surface to the UI rather than dying silently in the thread
        with _lock:
            _jobs[key].update({"running": False, "error": str(e)})


def _run_quick_test(key: str, project: Any, mock: bool, n: int, stage: str) -> None:
    try:
        if stage == "extraction":
            from ailr.tasks.calibrate import ExtractionQuickTestTask

            client = _make_client(project, "extract", mock)
            summary = ExtractionQuickTestTask(project, LLMReviewer(client)).run(n=n, on_progress=_progress_cb(key))
            c = summary.decision_counts
            text = (
                f"Tested {summary.sample_size} (of {summary.candidates_available} with markdown) — "
                f"full-text: include {c['include']}, exclude {c['exclude']}, uncertain {c['uncertain']}, "
                f"failed {summary.failed}."
            )
        else:
            from ailr.tasks.calibrate import QuickTestTask

            client = _make_client(project, "screen", mock)
            summary = QuickTestTask(project, LLMReviewer(client)).run(n=n, on_progress=_progress_cb(key))
            text = (
                f"Tested {summary.sample_size} (of {summary.candidates_available} available) — "
                f"include {summary.ai_counts['include']}, exclude {summary.ai_counts['exclude']}, "
                f"uncertain {summary.ai_counts['uncertain']}, failed {summary.failed}."
            )
        with _lock:
            _jobs[key].update({"running": False, "summary": text, "result": {"run_id": summary.run_id}})
    except Exception as e:
        with _lock:
            _jobs[key].update({"running": False, "error": str(e)})


def _run_calibration(key: str, project: Any, mock: bool, n: int) -> None:
    try:
        from ailr.tasks.calibrate import CalibrationTask

        client = _make_client(project, "screen", mock)
        reviewer = LLMReviewer(client, prompt_version=_screening_prompt_version(project))
        summary = CalibrationTask(project, reviewer, stage="screening").run(
            n=n, on_progress=_progress_cb(key)
        )
        text = (
            f"Calibration round {summary.sample_round}: AI on {summary.sample_size} — "
            f"include {summary.ai_counts['include']}, exclude {summary.ai_counts['exclude']}, "
            f"uncertain {summary.ai_counts['uncertain']}, failed {summary.failed}."
        )
        with _lock:
            _jobs[key].update(
                {"running": False, "summary": text, "result": {"sample_round": summary.sample_round}}
            )
    except Exception as e:
        with _lock:
            _jobs[key].update({"running": False, "error": str(e)})


def _run_extraction(key: str, project: Any, mock: bool, all_sources: bool = False, force: bool = False) -> None:
    try:
        client = _make_client(project, "extract", mock)
        summary = ExtractionTask(project, LLMReviewer(client)).run(
            only_includes=not all_sources, force=force, on_progress=_progress_cb(key)
        )
        text = (
            f"Extracted {summary.extracted}/{summary.total_candidates} "
            f"(already done {summary.skipped_already_done}, failed {summary.failed}). "
            f"{summary.total_input_tokens + summary.total_output_tokens:,} tokens"
        )
        with _lock:
            _jobs[key].update({"running": False, "summary": text})
    except Exception as e:
        with _lock:
            _jobs[key].update({"running": False, "error": str(e)})
