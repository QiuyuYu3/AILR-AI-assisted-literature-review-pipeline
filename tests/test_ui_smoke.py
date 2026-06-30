"""Smoke tests for the Dash UI.

build_app() exercises every view's callback registration in one shot; the parametrized
layout test renders each tab against a seeded project. No browser, no interaction logic --
this catches import breakage, layout-build errors, and callback-registration conflicts.
"""

import pytest

from ailr.ui import (
    calibration_view,
    conflicts_view,
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


_LAYOUTS = [
    ("project_manager", lambda: project_manager_view.layout()),
    ("dashboard", lambda: dashboard_view.layout("")),
    ("screen", lambda: screen_view.layout()),
    ("conflicts", lambda: conflicts_view.layout()),
    ("ft_conflicts", lambda: ft_conflicts_view.layout()),
    ("full_text", lambda: full_text_view.layout()),
    ("extract", lambda: extract_view.layout()),
    ("template_variables", lambda: template_view.variables_layout()),
    ("template_prompt", lambda: template_view.prompt_layout()),
    ("protocol", lambda: protocol_view.layout()),
    ("sources", lambda: sources_view.layout()),
    ("tags", lambda: tags_view.layout()),
    ("duplicates", lambda: duplicates_view.layout()),
    ("database", lambda: database_view.layout()),
    ("reports", lambda: reports_view.layout()),
    ("settings", lambda: settings_view.layout()),
    ("import", lambda: import_view.layout()),
    ("calibration_abstract", lambda: calibration_view.layout("abstract")),
    ("calibration_extraction", lambda: calibration_view.layout("extraction")),
    ("workflow_abstract", lambda: workflow_view.layout("abstract")),
    ("workflow_fulltext", lambda: workflow_view.layout("full_text")),
]


@pytest.mark.parametrize("build", [b for _, b in _LAYOUTS], ids=[n for n, _ in _LAYOUTS])
def test_layout_renders(seeded_project, build):
    assert build() is not None
