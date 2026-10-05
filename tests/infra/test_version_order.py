"""Saved prompt and artifact versions in order, also when several land in the same second."""


def _same_second(db, table):
    db._conn.execute(f"UPDATE {table} SET created_at = '2026-01-01 12:00:00'")


def test_the_tenth_prompt_version_is_the_latest(db, tmp_project):
    pid = tmp_project.project_id
    for i in range(10):
        db.save_prompt_version(pid, "screening", f"prompt {i}")
    _same_second(db, "prompt_versions")

    assert db.latest_prompt_version(pid, "screening") == "v10"
    assert [v["version"] for v in db.list_prompt_versions(pid, "screening")][:3] == ["v10", "v9", "v8"]


def test_the_tenth_artifact_version_is_the_latest(db, tmp_project):
    """An unchanged save compares against the latest version, so reading v9 as latest saves v10's content again."""
    pid = tmp_project.project_id
    for i in range(10):
        db.save_artifact_version(pid, "criteria", f"criteria {i}")
    _same_second(db, "artifact_versions")

    assert db.save_artifact_version(pid, "criteria", "criteria 9") is None
    assert [v["version"] for v in db.list_artifact_versions(pid, "criteria")][:3] == ["v10", "v9", "v8"]


def test_amendments_follow_the_version_numbers(db, tmp_project):
    pid = tmp_project.project_id
    for i in range(10):
        db.save_artifact_version(pid, "criteria", f"criteria {i}")
    _same_second(db, "artifact_versions")

    rows = [a for a in db.list_amendments(pid) if a["part"] == "Eligibility criteria"]
    assert [a["version"] for a in rows] == [f"v{i}" for i in range(10, 0, -1)]     # newest first
    assert [a["version"] for a in rows if not a["is_amendment"]] == ["v1"]
