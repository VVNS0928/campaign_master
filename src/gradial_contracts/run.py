"""Contract 3: run state and step graph. Per-item state so bulk jobs resume and partial failure is first-class."""
from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field, model_validator


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"     # an upstream step failed permanently


class Step(BaseModel):
    id: str
    agent: str                              # "author", "tagger", "qa", ...
    item_ref: str | None = None             # the page or asset this step works on; None for task-level steps
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    max_attempts: int = 2
    error: str | None = None

    @property
    def permanently_failed(self) -> bool:
        return self.status == StepStatus.FAILED and self.attempts >= self.max_attempts

    @property
    def terminal(self) -> bool:
        return self.status in {StepStatus.SUCCEEDED, StepStatus.SKIPPED} or self.permanently_failed


class StepGraph(BaseModel):
    steps: list[Step]

    @model_validator(mode="after")
    def _validate(self) -> "StepGraph":
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate step id")
        known = set(ids)
        indeg = {s.id: 0 for s in self.steps}
        for s in self.steps:
            for d in s.depends_on:
                if d not in known:
                    raise ValueError(f"step {s.id} depends on unknown step {d}")
                indeg[s.id] += 1
        queue = [i for i, n in indeg.items() if n == 0]
        seen = 0
        by_dep: dict[str, list[str]] = {}
        for s in self.steps:
            for d in s.depends_on:
                by_dep.setdefault(d, []).append(s.id)
        while queue:
            cur = queue.pop()
            seen += 1
            for nxt in by_dep.get(cur, []):
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    queue.append(nxt)
        if seen != len(ids):
            raise ValueError("step graph has a cycle")
        return self

    def get(self, step_id: str) -> Step:
        return next(s for s in self.steps if s.id == step_id)

    def ready(self) -> list[Step]:
        """PENDING steps whose dependencies all SUCCEEDED, or a FAILED step with retries left."""
        out = []
        for s in self.steps:
            if s.status == StepStatus.FAILED and s.attempts < s.max_attempts:
                out.append(s)
            elif s.status == StepStatus.PENDING and all(self.get(d).status == StepStatus.SUCCEEDED for d in s.depends_on):
                out.append(s)
        return out

    def propagate_failures(self) -> None:
        """Skip every step downstream of a permanent failure. Other items are untouched."""
        changed = True
        while changed:
            changed = False
            for s in self.steps:
                if s.status == StepStatus.PENDING and any(
                    self.get(d).permanently_failed or self.get(d).status == StepStatus.SKIPPED for d in s.depends_on
                ):
                    s.status = StepStatus.SKIPPED
                    changed = True


class RunPins(BaseModel):
    """Versions in force for this run, recorded so results are reproducible and auditable."""
    rule_packs: dict[str, str] = Field(default_factory=dict)      # pack id -> version
    golden_pages: dict[str, str] = Field(default_factory=dict)
    models: dict[str, str] = Field(default_factory=dict)          # role ("router","author","judge") -> exact model id


class Run(BaseModel):
    id: str
    task_id: str
    graph: StepGraph
    pins: RunPins = Field(default_factory=RunPins)

    def can_complete(self) -> bool:
        return all(s.terminal for s in self.graph.steps)

    def outcome(self) -> str:
        self.graph.propagate_failures()
        if not self.can_complete():
            return "in_progress"
        bad = [s for s in self.graph.steps if s.status != StepStatus.SUCCEEDED]
        if not bad:
            return "all_succeeded"
        return "failed" if all(s.status != StepStatus.SUCCEEDED for s in self.graph.steps) else "partial"
