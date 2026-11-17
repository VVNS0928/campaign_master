"""Reusable conformance suite. Any real adapter (Strapi, WordPress, AEM...) must pass this unchanged.
Subclass ConformanceSuite and provide an `env` fixture returning an Env."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import pytest
from gradial_contracts.adapter import call_with_retry
from gradial_contracts.audit import issue_token
from gradial_contracts.errors import AdapterError, ErrorCode
from gradial_contracts.page_model import Component, Page, PageRef

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)


@dataclass
class Env:
    adapter: object
    secret: bytes
    refs: list            # seeded in-scope pages (at least 3), all at their live version
    outside_ref: PageRef  # a page path outside the adapter's scope
    scope: str            # the adapter's configured path prefix
    edit_live: object     # callable(ref) that changes the live page behind the adapter's back (a human edit)
    inject: object        # callable(code, times, retry_after=None) making the next calls fail
    now: datetime = NOW


def edited(page: Page, text: str) -> Page:
    comps = [Component(id="banner", type="text", props={"text": text})]
    return page.model_copy(update={"components": comps})


def approve(env: Env, draft, reviewer="rev@x", ttl=timedelta(hours=1), content_hash=None):
    return issue_token(env.secret, task_id="t1", draft_id=draft.draft_id,
                       content_hash=content_hash or draft.content_hash, reviewer=reviewer, expires_at=env.now + ttl)


def code_of(excinfo) -> ErrorCode:
    return excinfo.value.code


class ConformanceSuite:
    def _draft(self, env, text="new", key="k1"):
        ref = env.refs[0]
        live = env.adapter.read_page(ref)
        return live, env.adapter.create_draft(ref, live.version, edited(live, text), key)

    # R1: drafts never touch live content
    def test_draft_does_not_change_live(self, env):
        live, _ = self._draft(env)
        assert env.adapter.read_page(env.refs[0]).content_hash() == live.content_hash()

    def test_diff_reports_the_change(self, env):
        _, d = self._draft(env, "hello")
        paths = [c.path for c in env.adapter.diff(d.draft_id)]
        assert "components/banner/props/text" in paths or "components/banner" in paths

    # R2
    def test_stale_base_version_rejected_at_draft(self, env):
        ref = env.refs[0]
        live = env.adapter.read_page(ref)
        env.edit_live(ref)
        with pytest.raises(AdapterError) as e:
            env.adapter.create_draft(ref, live.version, edited(live, "x"), "kstale")
        assert code_of(e) == ErrorCode.VERSION_CONFLICT

    # R3
    def test_create_draft_is_idempotent(self, env):
        _, d1 = self._draft(env, "a", "same")
        live = env.adapter.read_page(env.refs[0])
        d2 = env.adapter.create_draft(env.refs[0], live.version, edited(live, "a"), "same")
        assert d1.draft_id == d2.draft_id

    def test_publish_is_idempotent_and_does_not_double_bump(self, env):
        _, d = self._draft(env)
        r1 = env.adapter.publish(d.draft_id, approve(env, d), "pub1")
        r2 = env.adapter.publish(d.draft_id, approve(env, d), "pub1")
        assert r1.new_version == r2.new_version
        assert env.adapter.read_page(env.refs[0]).version == r1.new_version

    def test_publish_key_reuse_for_other_draft_rejected(self, env):
        _, d1 = self._draft(env, "a", "k1")
        ref2 = env.refs[1]
        live2 = env.adapter.read_page(ref2)
        d2 = env.adapter.create_draft(ref2, live2.version, edited(live2, "b"), "k2")
        env.adapter.publish(d1.draft_id, approve(env, d1), "pubX")
        with pytest.raises(AdapterError) as e:
            env.adapter.publish(d2.draft_id, approve(env, d2), "pubX")
        assert code_of(e) == ErrorCode.INVALID_REQUEST

    # R4
    def test_publish_without_approval_rejected_and_writes_nothing(self, env):
        live, d = self._draft(env)
        with pytest.raises(AdapterError) as e:
            env.adapter.publish(d.draft_id, None, "p")
        assert code_of(e) == ErrorCode.APPROVAL_INVALID
        assert env.adapter.read_page(env.refs[0]).version == live.version

    def test_expired_approval_rejected(self, env):
        _, d = self._draft(env)
        with pytest.raises(AdapterError) as e:
            env.adapter.publish(d.draft_id, approve(env, d, ttl=timedelta(seconds=-1)), "p")
        assert code_of(e) == ErrorCode.APPROVAL_INVALID

    def test_approval_for_other_content_rejected(self, env):
        _, d = self._draft(env)
        with pytest.raises(AdapterError) as e:
            env.adapter.publish(d.draft_id, approve(env, d, content_hash="0" * 64), "p")
        assert code_of(e) == ErrorCode.APPROVAL_INVALID

    def test_forged_signature_rejected(self, env):
        _, d = self._draft(env)
        tok = approve(env, d).model_copy(update={"signature": "f" * 64})
        with pytest.raises(AdapterError) as e:
            env.adapter.publish(d.draft_id, tok, "p")
        assert code_of(e) == ErrorCode.APPROVAL_INVALID

    # R5: a human edited the live page while the agent drafted
    def test_publish_conflicts_if_live_moved_after_draft(self, env):
        live, d = self._draft(env)
        env.edit_live(env.refs[0])
        with pytest.raises(AdapterError) as e:
            env.adapter.publish(d.draft_id, approve(env, d), "p")
        assert code_of(e) == ErrorCode.VERSION_CONFLICT

    # R6
    def test_out_of_scope_denied(self, env):
        with pytest.raises(AdapterError) as e:
            env.adapter.read_page(env.outside_ref)
        assert code_of(e) == ErrorCode.PERMISSION_DENIED

    # R6 also covers listing: a prefix wider than the scope is denied, not silently filtered
    def test_list_wider_than_scope_denied(self, env):
        with pytest.raises(AdapterError) as e:
            env.adapter.list_pages("/")
        assert code_of(e) == ErrorCode.PERMISSION_DENIED

    # R7
    def test_list_pages_complete_without_duplicates(self, env):
        seen, cursor = [], None
        while True:
            page = env.adapter.list_pages(env.scope, cursor, limit=1)
            seen += [r.path for r in page.refs]
            cursor = page.next_cursor
            if cursor is None:
                break
        assert sorted(seen) == sorted(r.path for r in env.refs)
        assert len(seen) == len(set(seen))

    # R8
    def test_throttling_is_retryable_and_recovers(self, env):
        env.inject(ErrorCode.RATE_LIMITED, 2, 0.0)
        slept = []
        out = call_with_retry(lambda: env.adapter.read_page(env.refs[0]), sleep=slept.append)
        assert out.ref == env.refs[0] and slept == [0.0, 0.0]

    def test_non_retryable_errors_are_not_retried(self, env):
        calls = []

        def fn():
            calls.append(1)
            return env.adapter.read_page(env.outside_ref)

        with pytest.raises(AdapterError):
            call_with_retry(fn, sleep=lambda s: None)
        assert len(calls) == 1
