"""Contract 4: context formats. What agents are given besides the task."""
from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field
from .page_model import Page


class CheckKind(str, Enum):
    DETERMINISTIC = "deterministic"   # axe-core, SEO, link and schema checks
    LLM_JUDGE = "llm_judge"           # brand, tone, claims, compliance, scored against a written rubric


class Severity(str, Enum):
    BLOCKER = "blocker"
    WARNING = "warning"
    INFO = "info"


class Rule(BaseModel):
    id: str
    check_kind: CheckKind
    severity: Severity
    repairable: bool = False          # may the QA agent attempt one automatic fix
    params: dict[str, Any] = Field(default_factory=dict)   # e.g. {"max_title_chars": 60} or {"rubric": "..."}


class RulePack(BaseModel):
    id: str
    version: str
    rules: list[Rule]

    @property
    def pin(self) -> str:
        return f"{self.id}@{self.version}"


class GoldenPage(BaseModel):
    """Approved structural reference for new pages. The skeleton's components hold placeholder props."""
    id: str
    version: str
    template: str
    skeleton: Page


class AssetRecord(BaseModel):
    id: str
    source_system: str                # "s3", "dam-x"
    uri: str
    sha256: str
    mime: str
    tags: list[str] = Field(default_factory=list)
    alt_text: str | None = None
