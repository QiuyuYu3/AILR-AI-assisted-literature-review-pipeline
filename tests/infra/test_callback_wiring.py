"""A callback a global component can fire must not name an absent tab's components, or Dash throws."""

from ailr.ui.app import build_app
from tests.helpers import walk


def _output_ids(spec: str) -> list[str]:
    parts = spec.strip(".").split("...") if spec.startswith("..") else [spec]
    return [p.split("@")[0].rpartition(".")[0] for p in parts]


def test_global_triggers_never_reach_into_an_absent_tab(tmp_project):
    app = build_app()
    global_ids = {n.id for n in walk(app.layout) if isinstance(getattr(n, "id", None), str)}

    def tab_local(cid: str) -> bool:
        return not cid.startswith("{") and cid not in global_ids  # pattern ids match nothing harmlessly

    offenders = []
    for cb in app._callback_list:
        outs = _output_ids(cb["output"])
        ins = [d["id"] for d in cb["inputs"]]
        states = [d["id"] for d in cb.get("state", [])]
        if not any(i in global_ids for i in ins) or all(tab_local(o) for o in outs):
            continue
        stray = [i for i in outs + ins + states if tab_local(i)]
        if stray:
            offenders.append((outs, stray))

    assert offenders == []


def test_a_reviewer_change_resets_both_queue_pages(tmp_project):
    """Wired to the reviewer field alone, so it fires from whichever tab is showing."""
    app = build_app()
    resets = {
        out for cb in app._callback_list if [d["id"] for d in cb["inputs"]] == ["shared-reviewer"]
        for out in _output_ids(cb["output"])
    }
    assert {"screen-page", "ft-page"} <= resets
