"""Reference adapter: proves the contract is implementable and gives real adapters something to be tested against."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable
from ..audit import ApprovalToken, verify_token
from ..adapter import DraftHandle, PageList, PublishResult
from ..errors import AdapterError, ErrorCode
from ..page_model import Change, Page, PageRef, diff_pages


class InMemoryAdapter:
    def __init__(self, cms: str, scope_prefix: str, secret: bytes, clock: Callable[[], datetime] | None = None):
        self.cms, self.scope, self.secret = cms, scope_prefix, secret
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.live: dict[PageRef, Page] = {}
        self.drafts: dict[str, tuple[DraftHandle, Page]] = {}
        self._draft_keys: dict[str, str] = {}
        self._publish_keys: dict[str, PublishResult] = {}
        self._n = 0
        self._inject: list[AdapterError] = []

    # test helpers
    def seed(self, page: Page) -> None:
        self.live[page.ref] = page

    def inject_failure(self, code: ErrorCode, times: int = 1, retry_after: float | None = None) -> None:
        self._inject += [AdapterError(code, "injected", retry_after) for _ in range(times)]

    def human_edit(self, ref: PageRef, **updates) -> None:
        """Simulate someone editing the live page in the CMS directly."""
        cur = self.live[ref]
        self.live[ref] = cur.model_copy(update={**updates, "version": self._bump(cur.version)})

    # internals
    @staticmethod
    def _bump(v: str) -> str:
        return f"v{int(v[1:]) + 1}"

    def _gate(self, path: str) -> None:
        if self._inject:
            raise self._inject.pop(0)
        if not path.startswith(self.scope):
            raise AdapterError(ErrorCode.PERMISSION_DENIED, f"{path} outside scope {self.scope}")

    # contract
    def list_pages(self, path_prefix: str, cursor: str | None = None, limit: int = 100) -> PageList:
        self._gate(path_prefix)
        refs = sorted((r for r in self.live if r.path.startswith(path_prefix)), key=lambda r: r.path)
        start = int(cursor or 0)
        chunk = refs[start:start + limit]
        nxt = str(start + limit) if start + limit < len(refs) else None
        return PageList(refs=chunk, next_cursor=nxt)

    def read_page(self, ref: PageRef) -> Page:
        self._gate(ref.path)
        if ref not in self.live:
            raise AdapterError(ErrorCode.NOT_FOUND, ref.path)
        return self.live[ref].model_copy(deep=True)

    def create_draft(self, ref: PageRef, base_version: str, page: Page, idempotency_key: str) -> DraftHandle:
        self._gate(ref.path)
        if idempotency_key in self._draft_keys:
            return self.drafts[self._draft_keys[idempotency_key]][0]
        if ref not in self.live:
            raise AdapterError(ErrorCode.NOT_FOUND, ref.path)
        if self.live[ref].version != base_version:
            raise AdapterError(ErrorCode.VERSION_CONFLICT, f"live is {self.live[ref].version}, base was {base_version}")
        self._n += 1
        draft_page = page.model_copy(update={"ref": ref, "version": base_version}, deep=True)
        h = DraftHandle(draft_id=f"d{self._n}", ref=ref, base_version=base_version, content_hash=draft_page.content_hash())
        self.drafts[h.draft_id] = (h, draft_page)
        self._draft_keys[idempotency_key] = h.draft_id
        return h

    def diff(self, draft_id: str) -> list[Change]:
        h, p = self._draft(draft_id)
        return diff_pages(self.live[h.ref], p)

    def preview_url(self, draft_id: str) -> str:
        self._draft(draft_id)
        return f"https://preview.local/{draft_id}"

    def publish(self, draft_id: str, approval: ApprovalToken | None, idempotency_key: str) -> PublishResult:
        if idempotency_key in self._publish_keys:
            prior = self._publish_keys[idempotency_key]
            if prior.draft_id != draft_id:
                raise AdapterError(ErrorCode.INVALID_REQUEST, "idempotency key already used for another draft")
            return prior
        h, p = self._draft(draft_id)
        self._gate(h.ref.path)
        verify_token(self.secret, approval, draft_id=draft_id, content_hash=p.content_hash(), now=self.clock())   # R4: before any write
        live = self.live[h.ref]
        if live.version != h.base_version:
            raise AdapterError(ErrorCode.VERSION_CONFLICT, f"live moved to {live.version}")
        new_v = self._bump(live.version)
        self.live[h.ref] = p.model_copy(update={"version": new_v}, deep=True)
        res = PublishResult(ref=h.ref, draft_id=draft_id, new_version=new_v, published_at=self.clock())
        self._publish_keys[idempotency_key] = res
        return res

    def _draft(self, draft_id: str):
        if draft_id not in self.drafts:
            raise AdapterError(ErrorCode.NOT_FOUND, draft_id)
        return self.drafts[draft_id]
