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


def settle(db, sid, decision, reviewer_id="amber", *, stage) -> None:
    """Both votes an `assisted` stage waits for, alike: the AI's, then the human's."""
    vote(db, sid, decision, "gpt", stage=stage, reviewer_type="ai")
    vote(db, sid, decision, reviewer_id, stage=stage)


def count_decisions(db, project_id, reviewer_type=None) -> int:
    """Every screening_decisions row of the project, optionally of one reviewer type."""
    sql = "SELECT COUNT(*) AS n FROM screening_decisions d JOIN sources s ON s.id = d.source_id WHERE s.project_id = ?"
    params: list = [project_id]
    if reviewer_type:
        sql += " AND d.reviewer_type = ?"
        params.append(reviewer_type)
    return db._conn.execute(sql, params).fetchone()["n"]


def stash_duplicate(db, project_id, title, doi=None, reason="doi", full_record_json=None) -> int:
    """A record dropped at import, logged the way ingest logs it; returns its duplicates id."""
    db.insert_duplicates([(project_id, title, None, doi, reason, None, full_record_json)])
    return db._conn.execute("SELECT MAX(id) AS id FROM duplicates WHERE project_id = ?", (project_id,)).fetchone()["id"]


def clear_cross_checks(db, source_id) -> None:
    db._conn.execute("DELETE FROM cross_checks WHERE source_id = ?", (source_id,))


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
    if isinstance(node, (list, tuple)):
        for item in node:
            yield from walk(item)
    elif node is not None:
        yield node
        yield from walk(getattr(node, "children", None))


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
