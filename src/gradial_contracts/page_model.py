"""Contract 2a: canonical page model. Every CMS adapter translates to and from this."""
from __future__ import annotations
import hashlib, json
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class PageRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    cms: str          # adapter id, e.g. "strapi-demo"
    path: str         # stable path inside that CMS


class Component(BaseModel):
    id: str           # stable within the page; diffs and edits address components by id
    type: str
    props: dict[str, Any] = Field(default_factory=dict)
    children: list["Component"] = Field(default_factory=list)


Component.model_rebuild()


class Page(BaseModel):
    ref: PageRef
    version: str                  # opaque CMS version token; compared for equality only
    template: str | None = None   # golden-page / template id
    locale: str = "en"
    title: str = ""
    components: list[Component] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    def content_hash(self) -> str:
        """Hash of everything except the version token. Approval is bound to this value."""
        body = self.model_dump(mode="json", exclude={"version"})
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Change(BaseModel):
    path: str
    before: Any = None
    after: Any = None


def _flat(components: list[Component], out: dict[str, Component] | None = None) -> dict[str, Component]:
    out = {} if out is None else out
    for c in components:
        out[c.id] = c
        _flat(c.children, out)
    return out


def diff_pages(before: Page, after: Page) -> list[Change]:
    changes: list[Change] = []
    if before.title != after.title:
        changes.append(Change(path="title", before=before.title, after=after.title))
    b, a = _flat(before.components), _flat(after.components)
    for cid in sorted(b.keys() | a.keys()):
        if cid not in a:
            changes.append(Change(path=f"components/{cid}", before=b[cid].type, after=None))
        elif cid not in b:
            changes.append(Change(path=f"components/{cid}", before=None, after=a[cid].type))
        else:
            for k in sorted(b[cid].props.keys() | a[cid].props.keys()):
                if b[cid].props.get(k) != a[cid].props.get(k):
                    changes.append(Change(path=f"components/{cid}/props/{k}", before=b[cid].props.get(k), after=a[cid].props.get(k)))
    return changes
