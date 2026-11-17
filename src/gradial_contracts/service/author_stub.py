"""Deterministic stand-in for the LLM author agent. Zero model cost; lets the plumbing be tested end to end.
Instruction grammar (everything else is treated as data and rejected, never executed):
    set title: <text>
    append paragraph: <text>
"""
from __future__ import annotations
from ..page_model import Component, Page


class UnsupportedInstruction(Exception):
    pass


def author(page: Page, instruction: str) -> Page:
    text = instruction.strip()
    low = text.lower()
    if low.startswith("set title:"):
        return page.model_copy(update={"title": text.split(":", 1)[1].strip()}, deep=True)
    if low.startswith("append paragraph:"):
        body = text.split(":", 1)[1].strip()
        n = len(page.components) + 1
        comp = Component(id=f"p{n}", type="text", props={"text": body})
        return page.model_copy(update={"components": [*page.components, comp]}, deep=True)
    raise UnsupportedInstruction("instruction not in supported grammar")


def repair_title(page: Page, max_chars: int) -> Page:
    return page.model_copy(update={"title": page.title[:max_chars].rstrip()}, deep=True)
