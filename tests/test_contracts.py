from datetime import datetime, timedelta, timezone
import pytest
from gradial_contracts.audit import AuditLog, issue_token, verify_token
from gradial_contracts.context import CheckKind, Severity
from gradial_contracts.errors import AdapterError
from gradial_contracts.governance import AutonomyMode, CheckResult, GovernanceReport, Verdict, requires_human
from gradial_contracts.page_model import Component, Page, PageRef
from gradial_contracts.run import Run, Step, StepGraph, StepStatus
from gradial_contracts.task import Budget, BudgetExceeded, IllegalTransition, Task, TaskSource, TaskStatus, TaskType

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def task():
    return Task(id="t1", tenant_id="ten", source=TaskSource.JIRA, source_ref="MKT-1", type=TaskType.UPDATE_COPY,
                instruction="update banner", idempotency_key="jira:MKT-1:evt9")


# ---- Task
def test_task_happy_path_and_illegal_jump():
    t = task().transition(TaskStatus.PLANNING).transition(TaskStatus.RUNNING).transition(TaskStatus.AWAITING_REVIEW)
    assert t.transition(TaskStatus.PUBLISHING).transition(TaskStatus.DONE).status == TaskStatus.DONE
    with pytest.raises(IllegalTransition):
        task().transition(TaskStatus.PUBLISHING)          # cannot publish a queued task


def test_publish_conflict_sends_task_back_to_review_and_done_is_final():
    t = task().transition(TaskStatus.PLANNING).transition(TaskStatus.RUNNING).transition(TaskStatus.AWAITING_REVIEW).transition(TaskStatus.PUBLISHING)
    assert t.transition(TaskStatus.AWAITING_REVIEW).status == TaskStatus.AWAITING_REVIEW
    with pytest.raises(IllegalTransition):
        t.transition(TaskStatus.DONE).transition(TaskStatus.RUNNING)


def test_budget_enforced():
    with pytest.raises(BudgetExceeded):
        Budget(max_usd=1.0).check(steps=1, tokens=1, usd=1.5)
    Budget().check(steps=10, tokens=1000, usd=0.1)


# ---- Run / StepGraph
def test_graph_rejects_cycle_unknown_dep_duplicate():
    with pytest.raises(ValueError):
        StepGraph(steps=[Step(id="a", agent="x", depends_on=["b"]), Step(id="b", agent="x", depends_on=["a"])])
    with pytest.raises(ValueError):
        StepGraph(steps=[Step(id="a", agent="x", depends_on=["zzz"])])
    with pytest.raises(ValueError):
        StepGraph(steps=[Step(id="a", agent="x"), Step(id="a", agent="x")])


def fanout(n=3):
    steps = []
    for i in range(n):
        steps += [Step(id=f"edit{i}", agent="author", item_ref=f"/p{i}"),
                  Step(id=f"qa{i}", agent="qa", item_ref=f"/p{i}", depends_on=[f"edit{i}"])]
    return Run(id="r1", task_id="t1", graph=StepGraph(steps=steps))


def test_partial_failure_isolates_one_item_and_reports_partial():
    r = fanout(3)
    g = r.graph
    for i in (0, 2):
        g.get(f"edit{i}").status = StepStatus.SUCCEEDED
        g.get(f"qa{i}").status = StepStatus.SUCCEEDED
    bad = g.get("edit1")
    bad.status, bad.attempts = StepStatus.FAILED, bad.max_attempts      # permanent failure
    assert r.outcome() == "partial"
    assert g.get("qa1").status == StepStatus.SKIPPED                   # downstream of the failure only
    assert g.get("qa0").status == StepStatus.SUCCEEDED
    assert r.can_complete()


def test_failed_step_with_retries_left_is_ready_again_and_run_not_complete():
    r = fanout(1)
    s = r.graph.get("edit0")
    s.status, s.attempts = StepStatus.FAILED, 1
    assert [x.id for x in r.graph.ready()] == ["edit0"]
    assert r.outcome() == "in_progress"


# ---- Governance
def res(passed, sev=Severity.BLOCKER, kind=CheckKind.DETERMINISTIC, repairable=False):
    return CheckResult(check_id="c", kind=kind, passed=passed, severity=sev, repairable=repairable, rule_pack="brand@1")


def page(text="x"):
    return Page(ref=PageRef(cms="d", path="/a"), version="v1", components=[Component(id="b", type="t", props={"text": text})])


def test_verdicts():
    p = page()
    h = p.content_hash()
    assert GovernanceReport(page_content_hash=h, results=[res(True)]).verdict() == Verdict.PASS
    assert GovernanceReport(page_content_hash=h, results=[res(False, Severity.WARNING)]).verdict() == Verdict.PASS
    assert GovernanceReport(page_content_hash=h, results=[res(False, repairable=True)]).verdict() == Verdict.REPAIR
    assert GovernanceReport(page_content_hash=h, results=[res(False, repairable=True)], repair_attempts_used=1).verdict() == Verdict.ESCALATE
    assert GovernanceReport(page_content_hash=h, results=[res(False, repairable=False)]).verdict() == Verdict.ESCALATE


def test_requires_human_fails_closed():
    p = page()
    ok = GovernanceReport(page_content_hash=p.content_hash(), results=[res(True)])
    assert requires_human(AutonomyMode.FULL_REVIEW, ok, p, sampled=False)
    assert not requires_human(AutonomyMode.SPOT_CHECK, ok, p, sampled=False)
    assert requires_human(AutonomyMode.SPOT_CHECK, ok, p, sampled=True)
    assert not requires_human(AutonomyMode.AUTO, ok, p, sampled=False)
    # stale report (page changed after checks) -> human, even in AUTO
    assert requires_human(AutonomyMode.AUTO, ok, page("changed"), sampled=False)
    # failed blocker -> human in every mode
    bad = GovernanceReport(page_content_hash=p.content_hash(), results=[res(False)])
    assert requires_human(AutonomyMode.AUTO, bad, p, sampled=False)
    # AUTO + a failed LLM-judge warning -> human
    judged = GovernanceReport(page_content_hash=p.content_hash(), results=[res(False, Severity.WARNING, CheckKind.LLM_JUDGE)])
    assert requires_human(AutonomyMode.AUTO, judged, p, sampled=False)


# ---- Audit and approval
def test_audit_chain_detects_tampering():
    log = AuditLog()
    for i in range(4):
        log.append(tenant_id="ten", task_id="t1", actor="system", action=f"a{i}", payload={"i": i}, ts=NOW)
    assert log.first_bad_seq() is None
    log.events[2] = log.events[2].model_copy(update={"action": "publish.succeeded"})
    assert log.first_bad_seq() == 2


def test_audit_chain_detects_deleted_event():
    log = AuditLog()
    for i in range(3):
        log.append(tenant_id="ten", task_id="t1", actor="system", action=f"a{i}", payload={}, ts=NOW)
    del log.events[1]
    assert log.first_bad_seq() is not None


def test_token_roundtrip_and_failures():
    s = b"k"
    t = issue_token(s, task_id="t1", draft_id="d1", content_hash="h", reviewer="r", expires_at=NOW + timedelta(hours=1))
    verify_token(s, t, draft_id="d1", content_hash="h", now=NOW)
    with pytest.raises(AdapterError):
        verify_token(b"other", t, draft_id="d1", content_hash="h", now=NOW)
    with pytest.raises(AdapterError):
        verify_token(s, t, draft_id="d1", content_hash="h2", now=NOW)
    with pytest.raises(AdapterError):
        verify_token(s, t, draft_id="d1", content_hash="h", now=NOW + timedelta(hours=2))


def test_content_hash_ignores_version_but_not_content():
    p = page("a")
    assert p.content_hash() == p.model_copy(update={"version": "v9"}).content_hash()
    assert p.content_hash() != page("b").content_hash()
