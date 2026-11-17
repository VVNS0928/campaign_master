"""Contract 5: governance result and autonomy policy."""
from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field
from .context import CheckKind, Severity
from .page_model import Page


class Verdict(str, Enum):
    PASS = "pass"
    REPAIR = "repair"        # a repairable blocker failed and attempts remain
    ESCALATE = "escalate"    # blockers remain; a person must decide


class CheckResult(BaseModel):
    check_id: str
    kind: CheckKind
    passed: bool
    severity: Severity
    repairable: bool = False
    message: str = ""
    rule_pack: str                      # pin, e.g. "brand@3"
    evidence: dict[str, Any] = Field(default_factory=dict)


class GovernanceReport(BaseModel):
    page_content_hash: str              # report is valid only for this exact content
    results: list[CheckResult]
    repair_attempts_used: int = 0

    def is_current_for(self, page: Page) -> bool:
        return self.page_content_hash == page.content_hash()

    def failed_blockers(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and r.severity == Severity.BLOCKER]

    def verdict(self, max_repairs: int = 1) -> Verdict:
        fb = self.failed_blockers()
        if not fb:
            return Verdict.PASS
        if self.repair_attempts_used < max_repairs and all(r.repairable for r in fb):
            return Verdict.REPAIR
        return Verdict.ESCALATE


class AutonomyMode(str, Enum):
    FULL_REVIEW = "full_review"
    SPOT_CHECK = "spot_check"
    AUTO = "auto"


def requires_human(mode: AutonomyMode, report: GovernanceReport, page: Page, sampled: bool) -> bool:
    """True if a person must look before publish. Fails closed: a stale or non-passing report always needs a human."""
    if not report.is_current_for(page) or report.verdict() != Verdict.PASS:
        return True
    if mode == AutonomyMode.FULL_REVIEW:
        return True
    if mode == AutonomyMode.SPOT_CHECK:
        return sampled
    # AUTO: still escalate if any LLM-judge check failed, even at warning level
    return any(not r.passed and r.kind == CheckKind.LLM_JUDGE for r in report.results)
