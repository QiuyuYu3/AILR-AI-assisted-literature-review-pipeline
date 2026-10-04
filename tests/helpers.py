"""Plain helpers the test modules share; fixtures live in conftest.py."""

from pathlib import Path

import dash
import yaml

import ailr.ui._project as ui_project
from ailr.core.source import Source
from ailr.llm.base import CallMetadata
from ailr.llm.mock import MockLLMClient, synth_from_tool_schema
from ailr.reviewers import LLMReviewer, ScreeningDecision

DECISIONS = ("include", "exclude", "uncertain")
STAGES = ("abstract", "full_text")


# ----- Records -----


def add_source(project, title="Paper", *, with_md=False, md_on_disk=False, **fields) -> int:
    """with_md records a markdown path; md_on_disk also writes the file there."""
    sid = project.db.insert_source(Source(title=title, project_id=project.project_id, **fields))
    if with_md or md_on_disk:
        rel = Path("data/markdown") / f"{sid}.md"
        if md_on_disk:
            target = project.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Paper\n\nFull text about dyadic interaction.", encoding="utf-8")
        project.db.update_markdown_path(sid, rel)
    return sid


def vote(db, sid, decision, reviewer_id, *, stage, reviewer_type="human", reasoning="test") -> None:
    # ScreeningDecision is a plain dataclass, so nothing else stops a swapped argument
    assert decision in DECISIONS and stage in STAGES, (decision, stage)
    db.insert_screening_decision(ScreeningDecision(
        decision=decision, reasoning=reasoning, reviewer_type=reviewer_type,
        reviewer_id=reviewer_id, source_id=sid, stage=stage,
    ))


def set_config(project, section, **values):
    """Rewrite one section of lit_review.yaml and return the project as the UI now loads it."""
    cfg = project.root / "lit_review.yaml"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    data.setdefault(section, {}).update(values)
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    ui_project._project = None
    return ui_project.get_project()


# ----- Dash -----


def walk(node):
    yield node
    children = getattr(node, "children", None)
    for child in children if isinstance(children, (list, tuple)) else [children]:
        if child is not None:
            yield from walk(child)


def component_text(node) -> str:
    """Visible text of a Dash component tree, whitespace-normalised."""
    return " ".join(" ".join(n for n in walk(node) if isinstance(n, str)).split())


def find_by_id(node, comp_id):
    for n in walk(node):
        if getattr(n, "id", None) == comp_id:
            return n
    raise AssertionError(f"no component with id {comp_id!r} in the layout")


def callbacks_of(view) -> dict:
    """The server-side callbacks a view module registers, unwrapped and keyed by function name."""
    app = dash.Dash(suppress_callback_exceptions=True)
    view.register_callbacks(app)
    fns = [c["callback"].__wrapped__ for c in app.callback_map.values() if "callback" in c]  # clientside ones have none
    by_name = {f.__name__: f for f in fns}
    assert len(by_name) == len(fns), "two callbacks share a name"
    return by_name


# ----- LLM stand-ins -----

INCLUDE_RESPONSE = {
    "decision": "include",
    "reasoning": "mock says fits",
    "matched_criteria": [],
    "evidence_quotes": [],
    "confidence": 8,
}


def screen_reviewer(response=INCLUDE_RESPONSE):
    return LLMReviewer(MockLLMClient(response=response))


def extract_reviewer():
    # Same shape as the UI's mock path: fabricate a schema-shaped response per call.
    return LLMReviewer(MockLLMClient(
        model="mock-extract", response_fn=lambda _s, _u, ts: synth_from_tool_schema(ts),
    ))


class StubClient:
    """Records what it was asked and returns a canned tool payload."""

    provider_name = "stub"
    model_name = "checker-1"
    temperature = 0.0
    effective_seed = None

    def __init__(self, output):
        self.output = output
        self.calls: list[dict] = []

    def complete_structured(self, *, system, user_message, tool_schema, max_tokens, cache_system=False):
        self.calls.append({"system": system, "user": user_message, "schema": tool_schema})
        return self.output, CallMetadata(provider="stub", model="checker-1", input_tokens=10, output_tokens=5)


def codes(issues):
    return sorted(i.issue_code for i in issues)
