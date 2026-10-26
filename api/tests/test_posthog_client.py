from api.services import posthog_client


class FakePostHog:
    def __init__(self):
        self.group_identify_calls = []

    def group_identify(self, *args, **kwargs):
        self.group_identify_calls.append((args, kwargs))


def test_group_identify_uses_stable_server_distinct_id(monkeypatch):
    fake_posthog = FakePostHog()
    monkeypatch.setattr(posthog_client, "get_posthog", lambda: fake_posthog)

    posthog_client.group_identify("organization", "42", {})

    _, kwargs = fake_posthog.group_identify_calls[0]
    assert kwargs["distinct_id"] == "server-group-identify"


def test_group_identify_preserves_real_distinct_id(monkeypatch):
    fake_posthog = FakePostHog()
    monkeypatch.setattr(posthog_client, "get_posthog", lambda: fake_posthog)

    posthog_client.group_identify(
        "organization",
        "42",
        {},
        distinct_id="stack-user-1",
    )

    _, kwargs = fake_posthog.group_identify_calls[0]
    assert kwargs["distinct_id"] == "stack-user-1"


def test_group_identify_reports_whether_it_was_queued(monkeypatch):
    fake_posthog = FakePostHog()
    monkeypatch.setattr(posthog_client, "get_posthog", lambda: fake_posthog)
    assert posthog_client.group_identify("account", "team-1", {}) is True

    def broken(*args, **kwargs):
        raise RuntimeError("queue full")

    fake_posthog.group_identify = broken
    assert posthog_client.group_identify("account", "team-1", {}) is False

    monkeypatch.setattr(posthog_client, "get_posthog", lambda: None)
    assert posthog_client.group_identify("account", "team-1", {}) is False
