"""Contract 2b: the CMS adapter interface. Connectors implement this; the tool gateway exposes it over MCP."""
from __future__ import annotations
import time
from datetime import datetime
from typing import Callable, Protocol, TypeVar
from pydantic import BaseModel
from .audit import ApprovalToken
from .errors import AdapterError
from .page_model import Change, Page, PageRef


class DraftHandle(BaseModel):
    draft_id: str
    ref: PageRef
    base_version: str
    content_hash: str


class PageList(BaseModel):
    refs: list[PageRef]
    next_cursor: str | None = None


class PublishResult(BaseModel):
    ref: PageRef
    draft_id: str
    new_version: str
    published_at: datetime


class CMSAdapter(Protocol):
    """Rules every implementation must follow (enforced by tests/adapter_conformance.py):

    R1  Never write live content except in publish().
    R2  create_draft fails with VERSION_CONFLICT if base_version is not the live version.
    R3  create_draft and publish are idempotent per idempotency_key (replay returns the original result).
    R4  publish verifies the approval token (audit.verify_token) BEFORE any write; the token is bound to the
        draft's content hash, so a changed draft cannot ride an old approval.
    R5  publish fails with VERSION_CONFLICT if the live version moved since the draft's base_version.
    R6  Operations outside the adapter's configured path scope fail with PERMISSION_DENIED.
    R7  list_pages is complete and duplicate-free across cursors.
    R8  Throttling and timeouts surface as retryable AdapterError (RATE_LIMITED / TRANSIENT).
    """

    def list_pages(self, path_prefix: str, cursor: str | None = None, limit: int = 100) -> PageList: ...
    def read_page(self, ref: PageRef) -> Page: ...
    def create_draft(self, ref: PageRef, base_version: str, page: Page, idempotency_key: str) -> DraftHandle: ...
    def diff(self, draft_id: str) -> list[Change]: ...
    def preview_url(self, draft_id: str) -> str: ...
    def publish(self, draft_id: str, approval: ApprovalToken | None, idempotency_key: str) -> PublishResult: ...


T = TypeVar("T")


def call_with_retry(fn: Callable[[], T], max_attempts: int = 4, sleep: Callable[[float], None] = time.sleep) -> T:
    """Retry only retryable AdapterErrors, honouring retry_after; exponential backoff otherwise. Never retries other errors."""
    delay = 0.5
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except AdapterError as e:
            if not e.retryable or attempt == max_attempts:
                raise
            sleep(e.retry_after if e.retry_after is not None else delay)
            delay *= 2
    raise RuntimeError("unreachable")
