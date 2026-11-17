"""Contract 6: approval token and audit log. The only way a publish is authorized, and the record of it."""
from __future__ import annotations
import hashlib, hmac, json
from datetime import datetime, timezone
from pydantic import BaseModel
from .errors import AdapterError, ErrorCode


class ApprovalToken(BaseModel):
    task_id: str
    draft_id: str
    content_hash: str      # approval is for exactly this content; any later edit invalidates it
    reviewer: str
    expires_at: datetime
    signature: str


def _msg(task_id, draft_id, content_hash, reviewer, expires_at) -> bytes:
    return f"{task_id}|{draft_id}|{content_hash}|{reviewer}|{expires_at.isoformat()}".encode()


def issue_token(secret: bytes, *, task_id: str, draft_id: str, content_hash: str, reviewer: str, expires_at: datetime) -> ApprovalToken:
    sig = hmac.new(secret, _msg(task_id, draft_id, content_hash, reviewer, expires_at), hashlib.sha256).hexdigest()
    return ApprovalToken(task_id=task_id, draft_id=draft_id, content_hash=content_hash, reviewer=reviewer, expires_at=expires_at, signature=sig)


def verify_token(secret: bytes, token: ApprovalToken | None, *, draft_id: str, content_hash: str, now: datetime) -> None:
    """Raises AdapterError(APPROVAL_INVALID). Adapters MUST call this before any write in publish()."""
    if token is None:
        raise AdapterError(ErrorCode.APPROVAL_INVALID, "missing approval token")
    good = hmac.new(secret, _msg(token.task_id, token.draft_id, token.content_hash, token.reviewer, token.expires_at), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, token.signature):
        raise AdapterError(ErrorCode.APPROVAL_INVALID, "bad signature")
    if token.expires_at <= now:
        raise AdapterError(ErrorCode.APPROVAL_INVALID, "expired")
    if token.draft_id != draft_id:
        raise AdapterError(ErrorCode.APPROVAL_INVALID, "token is for a different draft")
    if token.content_hash != content_hash:
        raise AdapterError(ErrorCode.APPROVAL_INVALID, "content changed after approval")


class AuditEvent(BaseModel):
    seq: int
    ts: datetime
    tenant_id: str
    task_id: str
    actor: str             # "agent:author", "user:reviewer@..." , "system"
    action: str            # "draft.created", "governance.passed", "approval.issued", "publish.succeeded", ...
    payload_hash: str
    prev_hash: str
    hash: str


GENESIS = "0" * 64


def _h(seq, ts, tenant_id, task_id, actor, action, payload_hash, prev_hash) -> str:
    return hashlib.sha256(f"{seq}|{ts.isoformat()}|{tenant_id}|{task_id}|{actor}|{action}|{payload_hash}|{prev_hash}".encode()).hexdigest()


class AuditLog:
    """Append-only hash chain. Storage layer must forbid UPDATE/DELETE; this detects tampering if it does not."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, *, tenant_id: str, task_id: str, actor: str, action: str, payload: dict, ts: datetime | None = None) -> AuditEvent:
        ts = ts or datetime.now(timezone.utc)
        ph = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        prev = self.events[-1].hash if self.events else GENESIS
        seq = len(self.events)
        ev = AuditEvent(seq=seq, ts=ts, tenant_id=tenant_id, task_id=task_id, actor=actor, action=action,
                        payload_hash=ph, prev_hash=prev, hash=_h(seq, ts, tenant_id, task_id, actor, action, ph, prev))
        self.events.append(ev)
        return ev

    def first_bad_seq(self) -> int | None:
        prev = GENESIS
        for ev in self.events:
            if ev.prev_hash != prev or ev.hash != _h(ev.seq, ev.ts, ev.tenant_id, ev.task_id, ev.actor, ev.action, ev.payload_hash, ev.prev_hash):
                return ev.seq
            prev = ev.hash
        return None
