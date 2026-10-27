"""Cached traversal preserves breadth/depth bounds and reduces provider calls on a tree."""

from types import SimpleNamespace

import pytest

from tests.test_elements import make
from windows_mcp.desktop import elements as el


class Array:
    def __init__(self, items):
        self.items, self.Length = items, len(items)

    def GetElement(self, index):
        return self.items[index]


class Raw:
    def __init__(self, index, children=(), cached=False):
        self.index, self.children, self.cached = index, list(children), cached

    def FindAllBuildCache(self, scope, condition, cache):
        return Array([Raw(child.index, child.children, bool(cache.TreeScope & el.TreeScope.TreeScope_Children)) for child in self.children])

    def GetCachedChildren(self):
        if not self.cached:
            return None  # Real UIA may return a null array rather than an error.
        return Array([Raw(child.index, child.children) for child in self.children])


@pytest.fixture
def tree(monkeypatch):
    root = Raw(0, [Raw(1, [Raw(3), Raw(4)]), Raw(2, [Raw(5), Raw(6)])])
    client = SimpleNamespace(ElementFromHandle=lambda h: root, CreateTrueCondition=lambda: object())
    monkeypatch.setattr(el, "_client", lambda: client)
    monkeypatch.setattr(el, "_cache_request", lambda: SimpleNamespace(TreeScope=el.TreeScope.TreeScope_Element))
    monkeypatch.setattr(el, "element_from_raw", lambda raw, **kwargs: make(raw=raw, runtime_id=(raw.index,)))
    return el.TopWindow(100, "Test", 1000, "test", False)


def test_cached_and_bounded_return_same_order_with_fewer_roundtrips(tree):
    baseline, optimized = {}, {}
    a = el.walk_bounded(tree, limit=100, max_depth=8, metrics=baseline)
    b = el.walk_bounded(tree, limit=100, max_depth=8, strategy="cached", metrics=optimized)
    assert [node.runtime_id for node in a[0]] == [node.runtime_id for node in b[0]]
    assert a[1] == b[1] is False
    assert optimized["provider_calls"] < baseline["provider_calls"]
    assert optimized["cached_expansions"] > 0


@pytest.mark.parametrize("strategy", ["bounded", "cached"])
def test_limits_and_depth_are_preserved(tree, strategy):
    nodes, truncated, _ = el.walk_bounded(tree, limit=3, max_depth=8, strategy=strategy)
    assert len(nodes) == 3 and truncated
    nodes, truncated, _ = el.walk_bounded(tree, limit=100, max_depth=1, strategy=strategy)
    assert [node.runtime_id for node in nodes] == [(1,), (2,)] and truncated
