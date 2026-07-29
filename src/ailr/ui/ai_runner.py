"""Background AI runners for the UI: run ScreeningTask / ExtractionTask in a thread, expose progress.

A single-user desktop app, so one global job per kind is enough. Progress is polled by a dcc.Interval.
"""

import threading
from typing import Any, Callable

from ailr.core.config import resolve_stage_llm
from ailr.llm.factory import make_llm_client
from ailr.prompt_versions import (
    extraction_composed,
    extraction_prompt_version,
    screening_composed,
    screening_prompt_version,
)
from ailr.reviewers import LLMReviewer
from ailr.tasks.extract import ExtractionTask
from ailr.tasks.screen import ScreeningTask

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


def _make_client(project: Any, stage: str, mock: bool, synth: bool = False):
    if mock:
        if stage == "extract" or synth:
            # Fabricate data shaped to the tool schema so the UI populates every field
            # (value/quote, groups, _flag_check per-criterion verdicts) — no API call.
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


def start_screening(project: Any, mock: bool, flag_check: Any = None) -> bool:
    return _start("screening", _run_screening, project, mock, flag_check)


def start_quick_test(project: Any, n: int, mock: bool, stage: str = "abstract", source_ids=None) -> bool:
    return _start(f"quicktest-{stage}", _run_quick_test, project, mock, n, stage, source_ids)


def start_calibration(project: Any, n: int, mock: bool, stage: str = "screening") -> bool:
    key = "calibration-abstract" if stage == "screening" else "calibration-extraction"
    return _start(key, _run_calibration, project, mock, n, stage)


def start_extraction(project: Any, mock: bool, all_sources: bool = False, force: bool = False) -> bool:
    return _start("extraction", _run_extraction, project, mock, all_sources, force)


def start_single_extraction(project: Any, source_id: int, mock: bool = False) -> bool:
    """Re-extract one paper. Shares the "extraction" job key with the batch run so the two can
    never write the same rows at once."""
    return _start("extraction", _run_single_extraction, project, mock, source_id)


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


# Kept as names because screen_view / full_text_view import them for the stale-run check.
current_screening_composed = screening_composed
current_extraction_composed = extraction_composed


def _run_screening(key: str, project: Any, mock: bool, flag_check: Any = None) -> None:
    try:
        # A real run supersedes earlier mock results: clear them first so they don't block re-screening.
        replaced = project.db.clear_mock_ai_decisions(project.project_id) if not mock else 0
        client = _make_client(project, "screen", mock)
        reviewer = LLMReviewer(client, prompt_version=screening_prompt_version(project))
        summary = ScreeningTask(project, reviewer).run(on_progress=_progress_cb(key), batch=mock, flag_check=flag_check)
        text = (
            f"Screened {summary.screened}/{summary.total} — "
            f"include {summary.include}, exclude {summary.exclude}, uncertain {summary.uncertain}."
        )
        if replaced:
            text += f" Replaced {replaced} earlier mock decision(s)."
        with _lock:
            _jobs[key].update({"running": False, "summary": text})
    except Exception as e:  # surface to the UI rather than dying silently in the thread
        with _lock:
            _jobs[key].update({"running": False, "error": str(e)})


def _run_quick_test(key: str, project: Any, mock: bool, n: int, stage: str, source_ids=None) -> None:
    try:
        if stage == "extraction":
            from ailr.tasks.calibrate import ExtractionQuickTestTask

            client = _make_client(project, "extract", mock)
            summary = ExtractionQuickTestTask(project, LLMReviewer(client)).run(n=n, source_ids=source_ids, on_progress=_progress_cb(key))
            c = summary.decision_counts
            text = (
                f"Tested {summary.sample_size} (of {summary.candidates_available} with markdown) — "
                f"full-text: include {c['include']}, exclude {c['exclude']}, uncertain {c['uncertain']}, "
                f"failed {summary.failed}."
            )
        else:
            from ailr.tasks.calibrate import QuickTestTask

            client = _make_client(project, "screen", mock, synth=True)
            summary = QuickTestTask(project, LLMReviewer(client)).run(n=n, source_ids=source_ids, on_progress=_progress_cb(key))
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


def _run_calibration(key: str, project: Any, mock: bool, n: int, stage: str = "screening") -> None:
    try:
        from ailr.tasks.calibrate import CalibrationTask

        if stage == "extraction":
            client = _make_client(project, "extract", mock)
            reviewer = LLMReviewer(client)
            verdict = "full-text"
        else:
            client = _make_client(project, "screen", mock)
            reviewer = LLMReviewer(client, prompt_version=screening_prompt_version(project))
            verdict = "abstract"
        summary = CalibrationTask(project, reviewer, stage=stage).run(
            n=n, on_progress=_progress_cb(key)
        )
        text = (
            f"Calibration round {summary.sample_round}: AI on {summary.sample_size} — "
            f"{verdict} include {summary.ai_counts['include']}, exclude {summary.ai_counts['exclude']}, "
            f"uncertain {summary.ai_counts['uncertain']}, failed {summary.failed}."
        )
        with _lock:
            _jobs[key].update(
                {"running": False, "summary": text, "result": {"sample_round": summary.sample_round}}
            )
    except Exception as e:
        with _lock:
            _jobs[key].update({"running": False, "error": str(e)})


def _extraction_summary_text(summary: Any) -> str:
    text = (
        f"Extracted {summary.extracted}/{summary.total_candidates} "
        f"(already done {summary.skipped_already_done}, failed {summary.failed}). "
        f"{summary.total_input_tokens + summary.total_output_tokens:,} tokens"
    )
    if summary.quote_values:
        text += (
            f" Quotes: {summary.quote_quoted}/{summary.quote_values} values quoted "
            f"({summary.quote_quoted / summary.quote_values:.0%}), "
            f"{summary.quote_verbatim}/{summary.quote_checked} verbatim."
        )
    if summary.archived:
        text += f" Kept {summary.archived} row(s) from the previous AI run as an earlier version."
    # Without this a failed paper reported only "failed 1", with the reason sitting unread in
    # summary.failures.
    for f in getattr(summary, "failures", [])[:3]:
        text += f" | #{f.get('source_id')} failed: {f.get('error')}"
    return text


def _run_single_extraction(key: str, project: Any, mock: bool, source_id: int) -> None:
    try:
        client = _make_client(project, "extract", mock)
        reviewer = LLMReviewer(client, prompt_version=extraction_prompt_version(project))
        # force=True: retiring the paper's previous AI run is ExtractionTask's job, so a single-paper
        # re-run and a forced batch run behave identically.
        summary = ExtractionTask(project, reviewer).run(
            only_includes=False, force=True, source_ids=[source_id],
            on_progress=_progress_cb(key), batch=False,
        )
        text = _extraction_summary_text(summary)
        if not summary.total_candidates:
            text = "This paper has no full-text markdown, so there was nothing to extract."
        elif not summary.extracted:
            text += " Previous AI extraction kept."
        with _lock:
            _jobs[key].update({"running": False, "summary": text})
    except Exception as e:
        with _lock:
            _jobs[key].update({"running": False, "error": str(e)})


def _run_extraction(key: str, project: Any, mock: bool, all_sources: bool = False, force: bool = False) -> None:
    try:
        # A real run supersedes earlier mock results: clear them first so they don't block re-extraction.
        replaced = project.db.clear_mock_ai_extractions(project.project_id) if not mock else 0
        client = _make_client(project, "extract", mock)
        reviewer = LLMReviewer(client, prompt_version=extraction_prompt_version(project))
        summary = ExtractionTask(project, reviewer).run(
            only_includes=not all_sources, force=force, on_progress=_progress_cb(key), batch=mock
        )
        text = _extraction_summary_text(summary)
        if replaced:
            text += f" Replaced {replaced} earlier mock extraction row(s)."
        with _lock:
            _jobs[key].update({"running": False, "summary": text})
    except Exception as e:
        with _lock:
            _jobs[key].update({"running": False, "error": str(e)})
