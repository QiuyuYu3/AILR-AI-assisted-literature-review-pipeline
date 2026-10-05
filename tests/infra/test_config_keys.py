"""Retired settings: an older lit_review.yaml that still carries them must open, and they must not come back."""

import pytest
import yaml

from ailr.core.project import Project


def _with_retired_keys(root):
    cfg = root / "lit_review.yaml"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    for section in ("screening", "extraction"):
        data.setdefault(section, {}).update(target_kappa=0.9, calibration={"fraction": 0.2, "n": 12, "min": 4})
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@pytest.mark.parametrize("mode", ["assisted", "strict"])
def test_an_older_yaml_with_retired_calibration_settings_still_opens(tmp_path, mode):
    root = tmp_path / "proj"
    Project.init(root, mode=mode).db.close()
    _with_retired_keys(root)

    project = Project(root)
    try:
        settings = project.config.model_dump()
        for section in ("screening", "extraction"):
            assert "target_kappa" not in settings[section]
            assert settings[section]["calibration"] == {"min": 4}     # the one setting still read
    finally:
        project.db.close()
