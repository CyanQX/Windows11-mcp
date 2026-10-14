"""Concurrent window-scoped subscriptions, coalescing, overflow and expiry."""

import pytest

from windows_mcp.watchdog.subscription import SubscriptionHub


def test_two_subscriptions_are_independent_and_window_scoped():
    hub = SubscriptionHub()
    a = hub.subscribe("client", 100)
    b = hub.subscribe("client", 200)
    hub.publish("property", 100, property_id=30045)
    assert len(hub.drain(a)[0]) == 1
    assert hub.drain(b)[0] == []
    hub.cancel(a.subscription_id, "other-client")
    assert hub.active == 2
    hub.cancel(a.subscription_id, "client")
    assert hub.active == 1


def test_events_are_coalesced_and_queues_report_overflow():
    hub = SubscriptionHub()
    subscription = hub.subscribe("client")
    for _ in range(100):
        hub.publish("focus", 100, runtime_id=(1,))
    assert len(subscription.queue) == 1
    for index in range(100):
        hub.publish("property", 100, property_id=index)
    items, overflow = hub.drain(subscription)
    assert len(items) == 64 and overflow
    assert hub.drain(subscription) == ([], False)


def test_expiry_releases_subscription_without_new_event(monkeypatch):
    import windows_mcp.watchdog.subscription as module

    hub = SubscriptionHub()
    subscription = hub.subscribe("client", ttl=1)
    monkeypatch.setattr(module.time, "monotonic", lambda: subscription.expiry + 1)
    assert hub.active == 0
    assert subscription.signal.is_set()


@pytest.mark.parametrize("events", [["arbitrary"], []])
def test_invalid_event_filters_are_rejected(events):
    with pytest.raises(ValueError):
        SubscriptionHub().subscribe("client", events=events)


def test_subscription_limit_is_bounded():
    hub = SubscriptionHub()
    for _ in range(64):
        hub.subscribe("client")
    with pytest.raises(RuntimeError):
        hub.subscribe("client")
