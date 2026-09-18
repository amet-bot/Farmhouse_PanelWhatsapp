import asyncio

from config import settings
from services import faq_bot


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        _FakeAsyncClient.last_call = (url, kwargs)
        return _FakeResponse(_FakeAsyncClient.next_payload)


def _enable_faq_bot(monkeypatch):
    monkeypatch.setattr(settings, "FAQ_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test-123")
    monkeypatch.setattr("services.faq_bot.httpx.AsyncClient", _FakeAsyncClient)


def test_answer_faq_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "FAQ_BOT_ENABLED", False)
    assert asyncio.run(faq_bot.answer_faq("¿Tienen wifi?")) is None


def test_answer_faq_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr(settings, "FAQ_BOT_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", None)
    assert asyncio.run(faq_bot.answer_faq("¿A qué hora abren?")) is None


def test_answer_faq_returns_model_text_and_sends_the_question(monkeypatch):
    _enable_faq_bot(monkeypatch)
    _FakeAsyncClient.next_payload = {
        "content": [{"type": "text", "text": "Abrimos todos los días de 10:30 AM a 9:30 PM."}]
    }

    result = asyncio.run(faq_bot.answer_faq("¿A qué hora abren?"))

    assert result == "Abrimos todos los días de 10:30 AM a 9:30 PM."
    url, kwargs = _FakeAsyncClient.last_call
    assert url == faq_bot.ANTHROPIC_API_URL
    assert kwargs["headers"]["x-api-key"] == "sk-test-123"
    assert kwargs["json"]["messages"] == [{"role": "user", "content": "¿A qué hora abren?"}]


def test_answer_faq_returns_none_when_model_is_not_sure(monkeypatch):
    _enable_faq_bot(monkeypatch)
    _FakeAsyncClient.next_payload = {"content": [{"type": "text", "text": faq_bot.NO_SE_SENTINEL}]}

    assert asyncio.run(faq_bot.answer_faq("¿Cuánto cuesta el catering para 200 personas?")) is None


def test_answer_faq_returns_none_on_request_failure(monkeypatch):
    _enable_faq_bot(monkeypatch)

    class _BrokenAsyncClient(_FakeAsyncClient):
        async def post(self, url, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr("services.faq_bot.httpx.AsyncClient", _BrokenAsyncClient)

    assert asyncio.run(faq_bot.answer_faq("¿Tienen delivery?")) is None
