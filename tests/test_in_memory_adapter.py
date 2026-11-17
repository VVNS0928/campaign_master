import pytest
from adapter_conformance import ConformanceSuite, Env, NOW
from gradial_contracts.page_model import Component, Page, PageRef
from gradial_contracts.reference.in_memory import InMemoryAdapter

SECRET = b"test-secret"


class TestInMemoryAdapter(ConformanceSuite):
    @pytest.fixture
    def env(self):
        a = InMemoryAdapter("demo", "/site", SECRET, clock=lambda: NOW)
        refs = [PageRef(cms="demo", path=f"/site/p{i}") for i in range(3)]
        for r in refs:
            a.seed(Page(ref=r, version="v1", title=r.path, components=[Component(id="banner", type="text", props={"text": "old"})]))
        return Env(adapter=a, secret=SECRET, refs=refs, outside_ref=PageRef(cms="demo", path="/admin/secret"), scope="/site",
                   edit_live=lambda ref: a.human_edit(ref, title="edited by a human"),
                   inject=lambda code, times, retry_after=None: a.inject_failure(code, times, retry_after))
