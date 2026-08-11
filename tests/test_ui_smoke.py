"""Smoke tests for the Dash UI.

build_app() exercises every view's callback registration in one shot; the parametrized
layout test renders each tab against a seeded project. No browser, no interaction logic --
this catches import breakage, layout-build errors, and callback-registration conflicts.
"""

import os
import socket

import pytest

from ailr.ui import (
    calibration_view,
    conflicts_view,
    consensus_view,
    dashboard_view,
    database_view,
    duplicates_view,
    extract_view,
    ft_conflicts_view,
    full_text_view,
    import_view,
    project_manager_view,
    protocol_view,
    reports_view,
    screen_view,
    settings_view,
    sources_view,
    tags_view,
    template_view,
    workflow_view,
)


def test_build_app(seeded_project):
    from ailr.ui.app import build_app

    app = build_app()
    assert app.layout is not None
    # Every view registers callbacks; a view whose registration silently no-ops would still leave
    # callback_map non-empty, so check the outputs actually span the tabs rather than one of them.
    outputs = " ".join(app.callback_map)
    for owner in ("screen-", "ft-", "extract-", "cons-", "bulk-", "tmpl-", "crit-", "report-"):
        assert owner in outputs, f"no callback registered for {owner}*"


# Each layout must reach the widget its tab is actually for, not merely build some component tree.
# The id is the one the tab's own callbacks write into, so if the page degrades to a placeholder
# or an error alert, it goes missing.
_LAYOUTS = [
    ("project_manager", lambda: project_manager_view.layout(), ["pm-new-create", "pm-recent-list"]),
    ("dashboard", lambda: dashboard_view.layout(""), ["dashboard-content"]),
    ("screen", lambda: screen_view.layout(), ["screen-cards", "screen-filter-status", "screen-counts"]),
    ("conflicts", lambda: conflicts_view.layout(), ["conflicts-cards", "conflicts-counts"]),
    ("ft_conflicts", lambda: ft_conflicts_view.layout(), ["ft-conflicts-cards", "ft-conflicts-counts"]),
    ("full_text", lambda: full_text_view.layout(), ["ft-cards", "ft-filter-status", "ft-counts"]),
    ("extract", lambda: extract_view.layout(), ["extract-form-container", "extract-ai-panel", "extract-submit"]),
    ("consensus", lambda: consensus_view.layout(), ["cons-body", "cons-save"]),
    ("template_variables", lambda: template_view.variables_layout(), ["tmpl-add", "tmpl-f-name"]),
    ("template_prompt", lambda: template_view.prompt_layout(), ["tmpl-additional"]),
    ("protocol", lambda: protocol_view.layout(), ["crit-list", "tmpl-add", "workflow-select"]),
    ("sources", lambda: sources_view.layout(), ["sources-grid", "bulk-apply"]),
    ("tags", lambda: tags_view.layout(), ["tags-list", "tags-create-btn"]),
    ("duplicates", lambda: duplicates_view.layout(), ["dup-manual-grid", "dup-ingest-grid"]),
    ("database", lambda: database_view.layout(), ["db-grid", "db-table"]),
    ("reports", lambda: reports_view.layout(), ["report-dl-prisma", "report-irr-body", "report-dl-csv"]),
    ("settings", lambda: settings_view.layout(), ["settings-clear-btn", "settings-screen-model"]),
    ("import", lambda: import_view.layout(), ["import-ref-upload", "import-ref-db"]),
    ("calibration_abstract", lambda: calibration_view.layout("abstract"), ["cal-abs-run", "cal-abs-status"]),
    ("calibration_extraction", lambda: calibration_view.layout("extraction"), ["cal-ext-run", "cal-ext-status"]),
    ("workflow_abstract", lambda: workflow_view.layout("abstract"), ["cal-abs-run", "screen-ai-run", "screen-prompt"]),
    ("workflow_fulltext", lambda: workflow_view.layout("full_text"), ["cal-ext-run", "extract-ai-run", "extract-runprompt"]),
]


def _walk(x):
    yield x
    children = getattr(x, "children", None)
    for kid in (children if isinstance(children, (list, tuple)) else [children] if children is not None else []):
        yield from _walk(kid)


def _ids(component) -> set[str]:
    """Every string id in the tree. Pattern-matching (dict) ids are skipped: they are built per
    row from live data, so they say nothing about whether the page shell rendered."""
    return {node.id for node in _walk(component)
            if isinstance(getattr(node, "id", None), str)}


@pytest.mark.parametrize(
    "build,required",
    [(b, r) for _, b, r in _LAYOUTS],
    ids=[n for n, _, _ in _LAYOUTS],
)
def test_layout_renders(seeded_project, build, required):
    ids = _ids(build())
    assert set(required) <= ids, f"missing: {sorted(set(required) - ids)}"


class TestPortSelection:
    """`ailr ui` must not die on a port it cannot have — on Windows, Hyper-V reserves a block
    that moves on every boot, so the default port works one day and not the next."""

    def test_returns_the_requested_port_when_it_is_free(self):
        from ailr.ui.app import _bindable_port

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free = s.getsockname()[1]
        assert _bindable_port(free) == free

    def test_skips_a_port_already_listening(self):
        from ailr.ui.app import _bindable_port

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen(1)
            port = taken.getsockname()[1]
            assert _bindable_port(port) > port

    def test_hops_to_the_next_hundred_when_a_whole_block_is_reserved(self):
        """A Hyper-V block is 100 ports wide, so a contiguous scan can sit entirely inside one."""
        from ailr.ui.app import _port_candidates

        candidates = list(_port_candidates(8050, tries=25, hops=8))
        assert candidates[:25] == list(range(8050, 8075))
        assert 8104 in candidates

    def test_exhausting_the_range_explains_where_to_look(self):
        """The OS error names no port and no cause; the message has to carry the diagnosis."""
        from ailr.ui.app import _bindable_port

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen(1)
            port = taken.getsockname()[1]
            with pytest.raises(SystemExit) as exc:
                _bindable_port(port, tries=1, hops=0)
        expected = "excludedportrange" if os.name == "nt" else "lsof"
        assert expected in str(exc.value)
