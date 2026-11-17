"""Phase 0 service: the contracts behind HTTP. No LLM, no external deps beyond FastAPI. State is in memory."""
from __future__ import annotations
import os, threading
from datetime import datetime, timedelta, timezone
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
from ..adapter import call_with_retry
from ..audit import AuditLog, issue_token
from ..errors import AdapterError, ErrorCode
from ..governance import AutonomyMode, Verdict, requires_human
from ..page_model import Component, Page, PageRef
from ..reference.in_memory import InMemoryAdapter
from ..task import IllegalTransition, Task, TaskSource, TaskStatus, TaskType
from . import author_stub
from .checks import MAX_TITLE, run_checks

CMS, SCOPE = "demo", "/site"
_HTTP = {ErrorCode.VERSION_CONFLICT: 409, ErrorCode.NOT_FOUND: 404, ErrorCode.PERMISSION_DENIED: 403,
         ErrorCode.APPROVAL_INVALID: 403, ErrorCode.INVALID_REQUEST: 400, ErrorCode.RATE_LIMITED: 429, ErrorCode.TRANSIENT: 503}


class NewTask(BaseModel):
    tenant_id: str = "t1"
    instruction: str
    target_path: str
    type: TaskType = TaskType.UPDATE_COPY
    idempotency_key: str
    source: TaskSource | None = None


class Review(BaseModel):
    reviewer: str


def create_app(api_key: str | None = None, secret: bytes | None = None, hooks: bool | None = None,
               mode: AutonomyMode = AutonomyMode.FULL_REVIEW) -> FastAPI:
    api_key = api_key or os.environ.get("API_KEY", "dev-key")
    secret = secret or os.environ.get("APPROVAL_SECRET", "dev-secret").encode()
    hooks = (os.environ.get("E2E_HOOKS") == "1") if hooks is None else hooks
    app = FastAPI(title="gradial phase0")
    lock = threading.RLock()
    adapter = InMemoryAdapter(CMS, SCOPE, secret)
    audit = AuditLog()
    tasks: dict[str, Task] = {}
    by_key: dict[str, str] = {}
    info: dict[str, dict] = {}     # task_id -> draft/report/etc.

    def auth(x_api_key: str | None = Header(default=None)):
        if x_api_key != api_key:
            raise HTTPException(401, "bad api key")

    def log(t: Task, actor: str, action: str, payload: dict | None = None):
        audit.append(tenant_id=t.tenant_id, task_id=t.id, actor=actor, action=action, payload=payload or {})

    def move(t: Task, new: TaskStatus) -> Task:
        try:
            t = t.transition(new)
        except IllegalTransition as e:
            raise HTTPException(409, str(e))
        tasks[t.id] = t
        log(t, "system", f"status:{new.value}")
        return t

    def view(t: Task) -> dict:
        i = info.get(t.id, {})
        return {"id": t.id, "status": t.status.value, "type": t.type.value, "target": i.get("path"),
                "verdict": i.get("verdict"), "draft_id": i.get("draft_id"), "error": i.get("error")}

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/tasks", dependencies=[Depends(auth)])
    def submit(body: NewTask):
        with lock:
            if body.idempotency_key in by_key:                       # I-idempotent intake: webhook redelivery -> same task
                return view(tasks[by_key[body.idempotency_key]])
            tid = f"task{len(tasks) + 1}"
            t = Task(id=tid, tenant_id=body.tenant_id, source=body.source or TaskSource.PROMPT, type=body.type,
                     instruction=body.instruction, target_refs=[body.target_path], idempotency_key=body.idempotency_key)
            tasks[tid], by_key[body.idempotency_key] = t, tid
            info[tid] = {"path": body.target_path}
            log(t, "intake", "created", {"instruction": body.instruction})
            t = move(t, TaskStatus.PLANNING)
            t = move(t, TaskStatus.RUNNING)
            try:
                ref = PageRef(cms=CMS, path=body.target_path)
                live = call_with_retry(lambda: adapter.read_page(ref), sleep=lambda s: None)
                page = author_stub.author(live, body.instruction)
                rep = run_checks(page)
                if rep.verdict() == Verdict.REPAIR:
                    page = author_stub.repair_title(page, MAX_TITLE)
                    rep = run_checks(page, repair_attempts_used=1)
                h = call_with_retry(lambda: adapter.create_draft(ref, live.version, page, f"{tid}-draft"), sleep=lambda s: None)
            except author_stub.UnsupportedInstruction as e:
                info[tid]["error"] = str(e)
                t = move(t, TaskStatus.FAILED)
                return view(t)
            except AdapterError as e:
                info[tid]["error"] = f"{e.code.value}: {e}"
                t = move(t, TaskStatus.FAILED)
                return view(t)
            info[tid].update(draft_id=h.draft_id, page=page, report=rep, verdict=rep.verdict().value, ref=ref)
            log(t, "agent", "draft_created", {"draft_id": h.draft_id, "content_hash": h.content_hash, "verdict": rep.verdict().value})
            t = move(t, TaskStatus.AWAITING_REVIEW)
            return view(t)

    @app.get("/tasks/{tid}", dependencies=[Depends(auth)])
    def get_task(tid: str):
        if tid not in tasks:
            raise HTTPException(404, "no such task")
        return view(tasks[tid])

    @app.get("/tasks/{tid}/draft", dependencies=[Depends(auth)])
    def get_draft(tid: str):
        i = info.get(tid)
        if not i or "draft_id" not in i:
            raise HTTPException(404, "no draft")
        return {"draft_id": i["draft_id"], "verdict": i["verdict"],
                "changes": [c.model_dump() for c in adapter.diff(i["draft_id"])],
                "failed_blockers": [r.check_id for r in i["report"].failed_blockers()],
                "preview_url": adapter.preview_url(i["draft_id"])}

    @app.post("/tasks/{tid}/approve", dependencies=[Depends(auth)])
    def approve(tid: str, body: Review):
        with lock:
            t, i = tasks.get(tid), info.get(tid)
            if not t or "draft_id" not in (i or {}):
                raise HTTPException(404, "no such task/draft")
            if t.status != TaskStatus.AWAITING_REVIEW:
                raise HTTPException(409, f"task is {t.status.value}")
            page, rep = i["page"], i["report"]
            if not rep.is_current_for(page) or rep.verdict() != Verdict.PASS:
                raise HTTPException(409, "governance report not passing for this content; cannot approve")
            t = move(t, TaskStatus.PUBLISHING)
            tok = issue_token(secret, task_id=tid, draft_id=i["draft_id"], content_hash=page.content_hash(),
                              reviewer=body.reviewer, expires_at=datetime.now(timezone.utc) + timedelta(minutes=15))
            log(t, body.reviewer, "approved", {"draft_id": i["draft_id"], "content_hash": page.content_hash()})
            try:
                res = call_with_retry(lambda: adapter.publish(i["draft_id"], tok, f"{tid}-publish"), sleep=lambda s: None)
            except AdapterError as e:
                back = TaskStatus.AWAITING_REVIEW if e.code == ErrorCode.VERSION_CONFLICT else TaskStatus.FAILED
                t = move(t, back)
                log(t, "system", "publish_failed", {"code": e.code.value})
                raise HTTPException(_HTTP.get(e.code, 500), f"{e.code.value}: {e}")
            t = move(t, TaskStatus.DONE)
            log(t, "system", "published", {"new_version": res.new_version})
            return {**view(t), "new_version": res.new_version}

    @app.post("/tasks/{tid}/reject", dependencies=[Depends(auth)])
    def reject(tid: str, body: Review):
        with lock:
            t = tasks.get(tid)
            if not t:
                raise HTTPException(404, "no such task")
            log(t, body.reviewer, "rejected")
            return view(move(t, TaskStatus.RUNNING))

    @app.get("/pages", dependencies=[Depends(auth)])
    def get_page(path: str):
        try:
            return adapter.read_page(PageRef(cms=CMS, path=path)).model_dump()
        except AdapterError as e:
            raise HTTPException(_HTTP.get(e.code, 500), str(e))

    @app.get("/audit", dependencies=[Depends(auth)])
    def get_audit(task_id: str | None = None):
        evs = [e for e in audit.events if task_id in (None, e.task_id)]
        return {"events": [{"seq": e.seq, "actor": e.actor, "action": e.action, "task_id": e.task_id} for e in evs],
                "chain_ok": audit.first_bad_seq() is None}

    if hooks:   # test-only endpoints; never enabled in a real deployment
        @app.post("/_test/seed", dependencies=[Depends(auth)])
        def seed(path: str, title: str = "Home"):
            adapter.seed(Page(ref=PageRef(cms=CMS, path=path), version="v1", title=title,
                              components=[Component(id="p1", type="text", props={"text": "hello"})]))
            return {"seeded": path}

        @app.post("/_test/human_edit", dependencies=[Depends(auth)])
        def human_edit(path: str, title: str):
            adapter.human_edit(PageRef(cms=CMS, path=path), title=title)
            return {"ok": True}

        @app.post("/_test/inject", dependencies=[Depends(auth)])
        def inject(code: str, times: int = 1):
            adapter.inject_failure(ErrorCode(code), times)
            return {"ok": True}

        @app.post("/_test/tamper_audit", dependencies=[Depends(auth)])
        def tamper():
            audit.events[1] = audit.events[1].model_copy(update={"actor": "mallory"})
            return {"ok": True}

    return app


app = create_app() if os.environ.get("GRADIAL_AUTOSTART", "1") == "1" else None
