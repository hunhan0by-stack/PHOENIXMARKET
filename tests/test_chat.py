"""Support chat endpoint: disabled without a key, prompt contents, limits."""
import pytest

import views.api as api


class FakeOpenAI:
    """Stands in for the OpenAI client and records the request."""

    calls = []

    def __init__(self, api_key=None, timeout=None):
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        FakeOpenAI.calls.append(kwargs)

        class Msg:
            content = " Merhaba! "

        class Choice:
            message = Msg()

        class Completion:
            choices = [Choice()]

        return Completion()


@pytest.fixture
def ai_enabled(monkeypatch):
    FakeOpenAI.calls = []
    monkeypatch.setattr(api, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


def test_chat_disabled_without_key(client):
    assert 'data-chat-api-enabled="false"' in client.html("/")
    assert client.post_json("/api/chat", {"message": "hello"}).status_code == 503


def test_chat_answers_with_catalog_and_language(ai_enabled, make_client):
    c = make_client(locale="tr", ip="10.50.0.1")
    assert 'data-chat-api-enabled="true"' in c.html("/")
    r = c.post_json("/api/chat", {"message": "Neler satıyorsunuz?"})
    assert r.status_code == 200 and r.get_json()["answer"] == "Merhaba!"
    kwargs = FakeOpenAI.calls[-1]
    system = kwargs["messages"][0]["content"]
    assert "Reply in Turkish" in system and "TikTok Views" in system
    assert kwargs["max_tokens"] == 400


def test_chat_rejects_long_messages_and_rate_limits(ai_enabled, make_client):
    c = make_client(ip="10.50.0.2")
    assert c.post_json("/api/chat", {"message": "x" * 1001}).status_code == 400
    codes = [c.post_json("/api/chat", {"message": "hi"}).status_code for _ in range(20)]
    assert codes == [200] * 20
    assert c.post_json("/api/chat", {"message": "hi"}).status_code == 429


@pytest.mark.parametrize("payload", [{"message": 5}, {"message": ["hi"]}, {"message": None}, ["hi"], "hi"])
def test_chat_rejects_malformed_payloads(ai_enabled, make_client, payload):
    r = make_client(ip="10.50.0.3").post_json("/api/chat", payload)
    assert r.status_code == 400 and "error" in r.get_json()
    assert FakeOpenAI.calls == []


def test_chat_catalog_lists_packages_with_their_prices(ai_enabled, make_client):
    import services.store as store
    from tests.helpers import in_app

    in_app(store.create_variant, 2, "1K", 4.99, 10, 0, True)
    in_app(store.create_variant, 2, "5K", 19.99, 0, 1, True)
    make_client(ip="10.50.0.4").post_json("/api/chat", {"message": "prices?"})
    system = FakeOpenAI.calls[-1]["messages"][0]["content"]
    assert "Instagram Followers (Social Media): packages: 1K 4.99 $, 5K 19.99 $ (out of stock)" in system


def test_chat_provider_failure_is_reported_without_details(monkeypatch, make_client):
    class Exploding(FakeOpenAI):
        def create(self, **kwargs):
            raise RuntimeError("upstream secret")

    monkeypatch.setattr(api, "OpenAI", Exploding)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    r = make_client(locale="tr", ip="10.50.0.5").post_json("/api/chat", {"message": "hi"})
    assert r.status_code == 502 and "upstream secret" not in r.get_data(as_text=True)
    assert "asistanına" in r.get_json()["error"]
