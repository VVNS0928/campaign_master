"""Tiny deterministic rule pack standing in for axe/SEO/brand checks."""
from __future__ import annotations
from ..context import CheckKind, Severity
from ..governance import CheckResult, GovernanceReport
from ..page_model import Page, Component

PACK = "demo@1"
MAX_TITLE = 60
BANNED = {"guaranteed", "miracle"}


def _walk(cs: list[Component]):
    for c in cs:
        yield c
        yield from _walk(c.children)


def run_checks(page: Page, repair_attempts_used: int = 0) -> GovernanceReport:
    res = [
        CheckResult(check_id="title_length", kind=CheckKind.DETERMINISTIC, passed=len(page.title) <= MAX_TITLE,
                    severity=Severity.BLOCKER, repairable=True, rule_pack=PACK,
                    message=f"title is {len(page.title)} chars, max {MAX_TITLE}"),
    ]
    texts = " ".join(str(c.props.get("text", "")) for c in _walk(page.components)).lower() + " " + page.title.lower()
    hit = sorted(w for w in BANNED if w in texts)
    res.append(CheckResult(check_id="banned_words", kind=CheckKind.DETERMINISTIC, passed=not hit,
                           severity=Severity.BLOCKER, repairable=False, rule_pack=PACK,
                           message=f"banned: {hit}" if hit else "", evidence={"hits": hit}))
    res.append(CheckResult(check_id="has_title", kind=CheckKind.DETERMINISTIC, passed=bool(page.title.strip()),
                           severity=Severity.WARNING, rule_pack=PACK, message="empty title"))
    return GovernanceReport(page_content_hash=page.content_hash(), results=res, repair_attempts_used=repair_attempts_used)
