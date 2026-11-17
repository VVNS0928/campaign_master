"""Contract 1: Task. What intake produces and the orchestrator consumes."""
from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from pydantic import BaseModel, Field


class TaskSource(str, Enum):
    PROMPT = "prompt"
    JIRA = "jira"
    GITHUB_ISSUE = "github_issue"
    SLACK = "slack"
    MCP = "mcp"


class TaskType(str, Enum):
    """Key for the per-task-type autonomy policy."""
    UPDATE_COPY = "update_copy"
    TAG_ASSETS = "tag_assets"
    BUILD_PAGE = "build_page"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    PUBLISHING = "publishing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


S = TaskStatus
ALLOWED: dict[TaskStatus, set[TaskStatus]] = {
    S.QUEUED: {S.PLANNING, S.CANCELLED},
    S.PLANNING: {S.RUNNING, S.FAILED, S.CANCELLED},
    S.RUNNING: {S.AWAITING_REVIEW, S.FAILED, S.CANCELLED},
    S.AWAITING_REVIEW: {S.PUBLISHING, S.RUNNING, S.CANCELLED},   # RUNNING = rejected, rework
    S.PUBLISHING: {S.DONE, S.FAILED, S.AWAITING_REVIEW},          # AWAITING_REVIEW = conflict, re-review
    S.DONE: set(),
    S.FAILED: {S.QUEUED},                                         # explicit retry only
    S.CANCELLED: set(),
}


class IllegalTransition(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class Budget(BaseModel):
    max_steps: int = 200
    max_tokens: int = 2_000_000
    max_usd: float = 5.0

    def check(self, steps: int, tokens: int, usd: float) -> None:
        if steps > self.max_steps or tokens > self.max_tokens or usd > self.max_usd:
            raise BudgetExceeded(f"steps={steps}/{self.max_steps} tokens={tokens}/{self.max_tokens} usd={usd}/{self.max_usd}")


class Task(BaseModel):
    id: str
    tenant_id: str
    source: TaskSource
    source_ref: str | None = None          # e.g. Jira key; used for status write-back
    type: TaskType
    instruction: str                       # untrusted text: treated as data, never as tool authority
    target_refs: list[str] = Field(default_factory=list)
    idempotency_key: str                   # dedupes webhook redelivery: same key -> same task
    status: TaskStatus = TaskStatus.QUEUED
    budget: Budget = Field(default_factory=Budget)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def transition(self, new: TaskStatus) -> "Task":
        if new not in ALLOWED[self.status]:
            raise IllegalTransition(f"{self.status.value} -> {new.value}")
        return self.model_copy(update={"status": new})
