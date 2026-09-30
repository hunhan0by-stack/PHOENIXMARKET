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
