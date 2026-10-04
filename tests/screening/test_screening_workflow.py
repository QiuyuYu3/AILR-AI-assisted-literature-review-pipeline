"""Screening workflow logic against a real SQLite project: the vote lock
(idempotent self-vote + team-size cap), conflict detection in both workflows
(assisted = AI vs human, independent = human vs human), and the reconciliation
closing the loop. These are the rules the Screen/Conflicts tabs rely on.
"""


from ailr.core.config import extractors_for, save_stage_workflow, team_size_for
from ailr.core.project import Project
from tests.helpers import add_source, vote


class TestVoteLock:
    def test_self_vote_is_flagged(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        i_voted, others = db.screening_lock_check(sid, "amber", "abstract")
        assert i_voted is True
        assert others == 0

    def test_other_reviewer_counted(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        i_voted, others = db.screening_lock_check(sid, "bob", "abstract")
        assert i_voted is False
        assert others == 1

    def test_others_are_distinct_reviewers_not_rows(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "amber", stage="abstract")  # same reviewer twice -> still 1 other
        _, others = db.screening_lock_check(sid, "bob", "abstract")
        assert others == 1

    def test_ai_votes_do_not_count_toward_lock(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "gpt", stage="abstract", reviewer_type="ai")
        i_voted, others = db.screening_lock_check(sid, "amber", "abstract")
        assert i_voted is False
        assert others == 0

    def test_stages_are_independent(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        i_voted, _ = db.screening_lock_check(sid, "amber", "full_text")
        assert i_voted is False

    def test_other_human_decided_names_the_blocker(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        assert db.other_human_decided(sid, "abstract", "bob") is None
        vote(db, sid, "include", "amber", stage="abstract")
        assert db.other_human_decided(sid, "abstract", "bob") == "amber"
        assert db.other_human_decided(sid, "abstract", "amber") is None  # my own vote doesn't block me


class TestAssistedConflicts:
    """Assisted mode: latest AI verdict vs latest human verdict; AI 'uncertain' always conflicts."""

    def test_disagreement_is_a_conflict(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "gpt", stage="abstract", reviewer_type="ai")
        vote(db, sid, "exclude", "amber", stage="abstract")
        assert [s.id for s in db.list_assisted_conflicts(tmp_project.project_id)] == [sid]
        assert db.count_unresolved_assisted_conflicts(tmp_project.project_id) == 1
        assert db.unresolved_conflict_ids(tmp_project.project_id, "assisted") == {sid}

    def test_agreement_is_not_a_conflict(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "gpt", stage="abstract", reviewer_type="ai")
        vote(db, sid, "include", "amber", stage="abstract")
        assert db.list_assisted_conflicts(tmp_project.project_id) == []

    def test_ai_uncertain_is_a_conflict_even_if_human_agrees(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "uncertain", "gpt", stage="abstract", reviewer_type="ai")
        vote(db, sid, "uncertain", "amber", stage="abstract")
        assert [s.id for s in db.list_assisted_conflicts(tmp_project.project_id)] == [sid]

    def test_only_latest_verdicts_compared(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "exclude", "gpt", stage="abstract", reviewer_type="ai")  # old AI run
        vote(db, sid, "include", "gpt", stage="abstract", reviewer_type="ai")  # re-run supersedes it
        vote(db, sid, "include", "amber", stage="abstract")
        assert db.list_assisted_conflicts(tmp_project.project_id) == []

    def test_only_the_humans_latest_verdict_is_compared(self, tmp_project):
        """The human side reads the latest vote too: the lock stops a re-vote in the UI, but two
        racing clicks can leave two rows, and the newer one is the vote."""
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "gpt", stage="abstract", reviewer_type="ai")
        vote(db, sid, "exclude", "amber", stage="abstract")
        vote(db, sid, "include", "amber", stage="abstract")
        assert db.list_assisted_conflicts(tmp_project.project_id) == []

    def test_ai_alone_is_not_a_conflict(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "uncertain", "gpt", stage="abstract", reviewer_type="ai")
        assert db.list_assisted_conflicts(tmp_project.project_id) == []

    def test_reconciliation_resolves_the_conflict(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "gpt", stage="abstract", reviewer_type="ai")
        vote(db, sid, "exclude", "amber", stage="abstract")
        db.insert_screening_reconciliation(sid, "exclude", adjudicator="amber", stage="abstract")
        assert db.list_assisted_conflicts(tmp_project.project_id) == []
        assert db.count_unresolved_assisted_conflicts(tmp_project.project_id) == 0
        assert db.unresolved_conflict_ids(tmp_project.project_id, "assisted") == set()

    def test_full_text_stage_tracked_separately(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "gpt", stage="full_text", reviewer_type="ai")
        vote(db, sid, "exclude", "amber", stage="full_text")
        assert db.list_assisted_conflicts(tmp_project.project_id, stage="abstract") == []
        assert [s.id for s in db.list_assisted_conflicts(tmp_project.project_id, stage="full_text")] == [sid]
        # resolving the abstract stage must NOT hide the full_text conflict
        db.insert_screening_reconciliation(sid, "include", adjudicator="amber", stage="abstract")
        assert [s.id for s in db.list_assisted_conflicts(tmp_project.project_id, stage="full_text")] == [sid]
        db.insert_screening_reconciliation(sid, "include", adjudicator="amber", stage="full_text")
        assert db.list_assisted_conflicts(tmp_project.project_id, stage="full_text") == []


class TestIndependentConflicts:
    """Independent mode: two humans, conflict = differing votes or any 'uncertain'."""

    def test_disagreement_is_a_conflict(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "bob", stage="abstract")
        assert [s.id for s in db.list_screening_conflicts(tmp_project.project_id)] == [sid]
        assert db.count_unresolved_screening_conflicts(tmp_project.project_id) == 1
        assert db.unresolved_conflict_ids(tmp_project.project_id, "independent") == {sid}

    def test_agreement_is_not_a_conflict(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "include", "bob", stage="abstract")
        assert db.list_screening_conflicts(tmp_project.project_id) == []

    def test_agreed_uncertain_still_needs_adjudication(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "uncertain", "amber", stage="abstract")
        vote(db, sid, "uncertain", "bob", stage="abstract")
        assert [s.id for s in db.list_screening_conflicts(tmp_project.project_id)] == [sid]

    def test_single_vote_is_not_a_conflict(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "uncertain", "amber", stage="abstract")
        assert db.list_screening_conflicts(tmp_project.project_id) == []

    def test_ai_vote_does_not_make_an_independent_conflict(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "gpt", stage="abstract", reviewer_type="ai")
        assert db.list_screening_conflicts(tmp_project.project_id) == []

    def test_an_ai_vote_does_not_split_two_agreeing_humans(self, tmp_project):
        """With two humans in, the reference AI's uncertain vote must not hold the paper back."""
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "include", "bob", stage="abstract")
        vote(db, sid, "uncertain", "gpt", stage="abstract", reviewer_type="ai")
        assert db.list_screening_conflicts(tmp_project.project_id) == []
        assert db.unresolved_conflict_ids(tmp_project.project_id, "independent") == set()

    def test_one_reviewer_voting_twice_is_still_one_reviewer(self, tmp_project):
        """Two rows from one human (a double click that slipped past the lock) neither finish an
        independent paper nor put it in conflict with itself."""
        db, pid = tmp_project.db, tmp_project.project_id
        twice = add_source(tmp_project, "voted twice alike")
        vote(db, twice, "include", "amber", stage="abstract")
        vote(db, twice, "include", "amber", stage="abstract")
        changed = add_source(tmp_project, "voted twice, changed mind")
        vote(db, changed, "include", "amber", stage="abstract")
        vote(db, changed, "exclude", "amber", stage="abstract")

        assert db.final_include_ids(pid, "abstract", workflow="independent") == set()
        assert db.full_text_candidate_ids(pid, workflow="independent") == []
        assert db.screening_lock_check_many([twice], "bob", "abstract") == {twice: (False, 1)}
        assert db.list_screening_conflicts(pid) == []

    def test_reconciliation_resolves_the_conflict(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "bob", stage="abstract")
        db.insert_screening_reconciliation(sid, "include", adjudicator="pi", stage="abstract")
        assert db.list_screening_conflicts(tmp_project.project_id) == []
        assert db.count_unresolved_screening_conflicts(tmp_project.project_id) == 0


class TestDisagreementsPairing:
    """screening_disagreements pairs the latest AI verdict with the latest human verdict at ONE
    stage. The plain join it replaced matched every AI row against every human row.
    """

    def test_stages_do_not_cross_pair(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "mock:mock", stage="abstract", reviewer_type="ai")
        vote(db, sid, "include", "amber", stage="abstract")
        vote(db, sid, "exclude", "amber", stage="full_text")

        assert db.screening_disagreements(tmp_project.project_id, stage="abstract") == []
        assert db.screening_disagreements(tmp_project.project_id, stage="full_text") == []

    def test_disagreement_is_reported_at_its_own_stage(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "mock:mock", stage="full_text", reviewer_type="ai")
        vote(db, sid, "exclude", "amber", stage="full_text")

        assert db.screening_disagreements(tmp_project.project_id, stage="abstract") == []
        rows = db.screening_disagreements(tmp_project.project_id, stage="full_text")
        assert [(r["source_id"], r["ai_decision"], r["human_decision"]) for r in rows] == [
            (sid, "include", "exclude")
        ]

    def test_a_re_run_does_not_multiply_the_rows(self, tmp_project):
        db = tmp_project.db
        sid = add_source(tmp_project)
        vote(db, sid, "include", "mock:mock", stage="abstract", reviewer_type="ai")
        vote(db, sid, "uncertain", "mock:mock", stage="abstract", reviewer_type="ai")  # AI re-run
        vote(db, sid, "exclude", "amber", stage="abstract")
        vote(db, sid, "include", "bob", stage="abstract")  # second human in an independent review

        rows = db.screening_disagreements(tmp_project.project_id)
        assert len(rows) == 1
        assert rows[0]["ai_decision"] == "uncertain"     # latest AI
        assert rows[0]["human_decision"] == "include"    # latest human
        assert rows[0]["human_reviewer_id"] == "bob"


class TestStageWorkflowResolution:
    """Each screening stage runs its own workflow: the common systematic-review design is
    AI-assisted at title/abstract, where the volume is, and two humans at full text."""

    def test_full_text_follows_abstract_when_unset(self, tmp_project):
        # Not the default 'assisted': a resolver that hard-coded it would pass on that.
        save_stage_workflow(tmp_project.root, "screening", "independent")
        cfg = Project(tmp_project.root).config
        assert cfg.screening.full_text_workflow is None
        assert cfg.screening_workflow("full_text") == "independent"

    def test_full_text_overrides_abstract_when_set(self, tmp_project):
        save_stage_workflow(tmp_project.root, "screening", "assisted")
        save_stage_workflow(tmp_project.root, "full_text_screening", "independent")
        cfg = Project(tmp_project.root).config

        assert cfg.screening_workflow("abstract") == "assisted"
        assert cfg.screening_workflow("full_text") == "independent"

    def test_abstract_is_unaffected_by_the_full_text_setting(self, tmp_project):
        save_stage_workflow(tmp_project.root, "screening", "independent")
        save_stage_workflow(tmp_project.root, "full_text_screening", "assisted")
        cfg = Project(tmp_project.root).config

        assert cfg.screening_workflow("abstract") == "independent"
        assert cfg.screening_workflow("full_text") == "assisted"
        assert team_size_for(cfg.screening_workflow("abstract")) == 2
        assert team_size_for(cfg.screening_workflow("full_text")) == 1

    def test_extractors_required_follows_the_extraction_workflow(self):
        assert extractors_for("independent") == 2
        assert extractors_for("verify") == 1
