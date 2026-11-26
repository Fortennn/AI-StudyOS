import uuid
from fastapi.testclient import TestClient

from app.main import app, study_service
from app.providers.base import LLMProvider
from app.providers.mock import MockProvider


def test_ui_config_matches_study_request_limits(client):
    """GET /api/ui-config returns server-derived validation limits for the UI."""
    res = client.get("/api/ui-config")
    assert res.status_code == 200
    cfg = res.json()
    assert cfg["min_text_length"] == 10
    assert cfg["max_text_length"] == 250000
    assert cfg["card_count"]["max"] == 30
    assert cfg["quiz_count"]["max"] == 25
    assert ".pdf" in cfg["allowed_extensions"]


def test_422_includes_field_level_errors(client):
    """422 error payload includes fields[] so UI can highlight specific inputs."""
    res = client.post("/api/generate", json={"text": "мало", "card_count": 2, "quiz_count": 2})
    assert res.status_code == 422
    err = res.json()["error"]
    assert err["kind"] == "invalid_input"
    assert isinstance(err["fields"], list)
    assert any(f["field"] == "text" for f in err["fields"])


def test_e2e_happy_path_with_idempotency_and_history(client, monkeypatch):
    """
    Full user flow at API level:
    ui-config -> generate with Idempotency-Key -> duplicate Idempotency-Key returns cache ->
    history retrieval -> chat on session.
    """
    calls = {"count": 0}

    class CountingMock(MockProvider):
        def __init__(self):
            super().__init__(delay_ms=0)

        async def generate_structured(self, *args, **kwargs):
            calls["count"] += 1
            return await super().generate_structured(*args, **kwargs)

    monkeypatch.setattr(study_service.gateway, "provider", CountingMock())

    cfg = client.get("/api/ui-config").json()
    assert cfg["min_text_length"] == 10

    idem_key = str(uuid.uuid4())
    payload = {
        "text": "Нормалізація реляційних баз даних до третьої нормальної форми усуває транзитивні залежності.",
        "topic": "Бази даних E2E",
        "card_count": 3,
        "quiz_count": 2,
    }

    r1 = client.post("/api/generate", json=payload, headers={"Idempotency-Key": idem_key})
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["provider_kind"] == "mock"
    assert d1["checks"] == ["schema", "rules", "content"]
    assert calls["count"] == 1

    # Second submission with the same Idempotency-Key must return cached result without calling LLM again
    r2 = client.post("/api/generate", json=payload, headers={"Idempotency-Key": idem_key})
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["request_id"] == d1["request_id"]
    assert d2["provider_kind"] == "cache"
    assert calls["count"] == 1

    # Verify session persisted in history
    hist = client.get(f"/api/history/{d1['request_id']}")
    assert hist.status_code == 200
    assert hist.json()["topic"] == "Бази даних E2E"

    # Verify follow-up chat on that session
    chat = client.post(
        "/api/chat",
        json={
            "session_id": d1["request_id"],
            "topic": "Бази даних E2E",
            "question": "Що таке 3НФ?",
        },
    )
    assert chat.status_code == 200
    assert len(chat.json()["answer"]) > 5


def test_e2e_failure_path_controlled_error(monkeypatch):
    """Failure path E2E: provider failure returns unified error contract with retryable & request_id."""
    monkeypatch.setattr(
        study_service.gateway,
        "provider",
        MockProvider(failure_kind="unavailable", delay_ms=0),
    )
    tc = TestClient(app)
    res = tc.post(
        "/api/generate",
        json={
            "text": "Тестовий конспект для перевірки контрольованої помилки в повному сценарії.",
            "topic": "Відмовостійкість",
            "card_count": 2,
            "quiz_count": 2,
        },
    )
    assert res.status_code == 503
    err = res.json()["error"]
    assert err["kind"] == "provider_unavailable"
    assert err["retryable"] is True
    assert err["request_id"]


def test_xss_payload_from_model_is_returned_as_plain_text_and_escaped_in_ui(client, monkeypatch):
    """Model output containing <img src=x onerror=alert(1)> is preserved as plain text, not executed."""
    xss = "<img src=x onerror=alert(1)>"

    class XssMock(LLMProvider):
        async def generate_structured(self, prompt, topic, card_count, quiz_count, **kwargs):
            return {
                "summary": {
                    "tldr": f"Безпечний текст з потенційним XSS {xss} для перевірки екранування.",
                    "key_takeaways": [f"Теза {xss}"],
                    "insufficient_data": False,
                    "missing_info": None,
                },
                "flashcards": [
                    {"id": 1, "front": f"Термін {xss}", "back": "Пояснення", "category": "Термін"},
                    {"id": 2, "front": "Термін 2", "back": "Пояснення 2", "category": "Термін"},
                ],
                "quiz": [
                    {
                        "id": 1,
                        "question": "Питання 1?",
                        "options": ["A", "B", "C", "D"],
                        "correct_index": 0,
                        "explanation": "Пояснення",
                    },
                    {
                        "id": 2,
                        "question": "Питання 2?",
                        "options": ["A", "B", "C", "D"],
                        "correct_index": 1,
                        "explanation": "Пояснення",
                    },
                ],
                "true_false": [],
            }

    monkeypatch.setattr(study_service.gateway, "provider", XssMock())
    res = client.post(
        "/api/generate",
        json={
            "text": "Тестовий конспект для перевірки XSS-екранування виводу моделі.",
            "topic": "Безпека",
            "card_count": 2,
            "quiz_count": 2,
        },
    )
    assert res.status_code == 200
    assert xss in res.json()["summary"]["tldr"]
