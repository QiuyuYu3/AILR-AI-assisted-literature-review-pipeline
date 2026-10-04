"""The composed screening/extraction prompt is cached on its input files' mtime.

The review queues call these on every render to mark stale AI runs, so the cache is what keeps a
page render off the disk. It is only correct if editing any input still takes effect immediately —
a prompt, the criteria, or the extraction schema — which is what these pin.
"""

import os

from ailr import prompt_versions
from ailr.prompt_versions import extraction_composed, screening_composed


def _write(project, rel: str, text: str) -> None:
    p = project.root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


class TestScreeningComposed:
    def test_repeated_calls_agree(self, tmp_project):
        _write(tmp_project, tmp_project.config.screening.prompt, "SCREEN {{criteria}}")
        assert screening_composed(tmp_project) == screening_composed(tmp_project)

    def test_editing_the_prompt_takes_effect(self, tmp_project):
        rel = tmp_project.config.screening.prompt
        _write(tmp_project, rel, "FIRST")
        assert "FIRST" in screening_composed(tmp_project)
        _write(tmp_project, rel, "SECOND")
        assert "SECOND" in screening_composed(tmp_project)

    def test_an_edit_that_keeps_the_size_takes_effect(self, tmp_project):
        """Swapping one year for another leaves the file the same size; only its mtime moves."""
        rel = tmp_project.config.screening.prompt
        _write(tmp_project, rel, "Studies published since 2010.")
        assert "2010" in screening_composed(tmp_project)
        path = tmp_project.root / rel
        later = path.stat().st_mtime_ns + 10**9
        _write(tmp_project, rel, "Studies published since 2015.")
        os.utime(path, ns=(later, later))     # saved a second later, whatever the clock resolution
        assert "2015" in screening_composed(tmp_project)

    def test_editing_the_additional_instructions_takes_effect(self, tmp_project):
        cfg = tmp_project.config.screening
        _write(tmp_project, cfg.prompt, "SCREEN {{criteria}}")
        _write(tmp_project, cfg.additional, "Prefer infant samples.")
        assert "Prefer infant samples." in screening_composed(tmp_project)
        _write(tmp_project, cfg.additional, "Prefer adolescent samples instead.")
        assert "Prefer adolescent samples instead." in screening_composed(tmp_project)

    def test_an_unchanged_prompt_is_not_composed_again(self, tmp_project, monkeypatch):
        _write(tmp_project, tmp_project.config.screening.prompt, "SCREEN {{criteria}}")
        builds = []
        real = prompt_versions.compose_screening_prompt
        monkeypatch.setattr(prompt_versions, "compose_screening_prompt", lambda *a, **k: builds.append(1) or real(*a, **k))
        screening_composed(tmp_project)
        screening_composed(tmp_project)
        assert len(builds) == 1

    def test_editing_the_criteria_takes_effect(self, tmp_project):
        _write(tmp_project, tmp_project.config.screening.prompt, "SCREEN {{criteria}}")
        rel = tmp_project.config.screening.criteria_structured
        _write(tmp_project, rel, "criteria:\n  - id: B1\n    name: dyadic\n")
        assert "dyadic" in screening_composed(tmp_project)
        _write(tmp_project, rel, "criteria:\n  - id: B1\n    name: triadic\n")
        composed = screening_composed(tmp_project)
        assert "triadic" in composed and "dyadic" not in composed


class TestExtractionComposed:
    def test_repeated_calls_agree(self, tmp_project):
        _write(tmp_project, tmp_project.config.extraction.prompt, "EXTRACT {{schema_md}}")
        assert extraction_composed(tmp_project) == extraction_composed(tmp_project)

    def test_editing_the_schema_takes_effect(self, tmp_project):
        _write(tmp_project, tmp_project.config.extraction.prompt, "EXTRACT {{schema_md}}")
        rel = tmp_project.config.extraction.schema_path
        _write(tmp_project, rel, "fields:\n  - name: design\n    type: string\n")
        assert "design" in extraction_composed(tmp_project)
        _write(tmp_project, rel, "fields:\n  - name: sample_size\n    type: string\n")
        composed = extraction_composed(tmp_project)
        assert "sample_size" in composed and "design" not in composed

    def test_the_two_stages_do_not_share_a_cache_entry(self, tmp_project):
        _write(tmp_project, tmp_project.config.screening.prompt, "SCREENING SIDE")
        _write(tmp_project, tmp_project.config.extraction.prompt, "EXTRACTION SIDE")
        assert "SCREENING SIDE" in screening_composed(tmp_project)
        assert "EXTRACTION SIDE" in extraction_composed(tmp_project)


def test_two_projects_do_not_share_a_cache_entry(tmp_project, tmp_path):
    """The cache is keyed on the project root: switching projects in the UI must not carry the
    previous project's prompt over."""
    from ailr.core.project import Project

    other = Project.init(tmp_path / "other")
    _write(tmp_project, tmp_project.config.screening.prompt, "PROJECT ONE")
    _write(other, other.config.screening.prompt, "PROJECT TWO")
    assert "PROJECT ONE" in screening_composed(tmp_project)
    assert "PROJECT TWO" in screening_composed(other)
