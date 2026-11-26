import pytest
from fastapi.testclient import TestClient

from app.init_data import init_data
from app.main import app, settings as app_settings, study_service
from app.providers.mock import MockProvider
from app.resilience import UserRateLimiter
from app.settings import Settings


def test_settings_fail_fast_without_api_key_for_gemini():
    """Real provider without LLM_API_KEY fails immediately at startup."""
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        Settings(llm_provider="gemini", llm_api_key="")


def test_health_exposes_git_commit_and_prompt_version(client):
    """Health endpoint reports deployed git_commit and prompt_version without calling LLM."""
    res = client.get("/api/health")
    assert res.status_code == 200
    extra = res.json()["extra"]
    assert "git_commit" in extra
    assert extra["prompt_version"] == "v1"


def test_demo_token_protects_ai_routes_while_health_stays_open(monkeypatch):
    """When DEMO_TOKEN is set, AI routes require X-Demo-Token/Bearer while /api/health and / remain open."""
    monkeypatch.setattr(app_settings, "demo_token", "secret-demo-token-123")
    monkeypatch.setattr(study_service.gateway, "provider", MockProvider(delay_ms=0))
    monkeypatch.setattr("app.main.user_rate_limiter", UserRateLimiter(max_requests=100))
    tc = TestClient(app)

    assert tc.get("/api/health").status_code == 200
    assert tc.get("/").status_code == 200

    payload = {"text": "Тестовий конспект для перевірки DEMO_TOKEN захисту.", "card_count": 2, "quiz_count": 2}
    r_no_tok = tc.post("/api/generate", json=payload)
    assert r_no_tok.status_code == 401
    assert r_no_tok.json()["error"]["kind"] == "auth"

    r_bad_tok = tc.post("/api/generate", json=payload, headers={"X-Demo-Token": "wrong"})
    assert r_bad_tok.status_code == 401

    r_ok = tc.post("/api/generate", json=payload, headers={"X-Demo-Token": "secret-demo-token-123"})
    assert r_ok.status_code == 200

    r_bearer = tc.post(
        "/api/chat",
        json={"topic": "T", "question": "Що таке стек?"},
        headers={"Authorization": "Bearer secret-demo-token-123"},
    )
    assert r_bearer.status_code == 200


def test_init_data_is_idempotent(monkeypatch, tmp_path):
    """init_data() creates demo data once and safely detects existing data on repeat runs."""
    db_file = tmp_path / "init_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    assert init_data() == "initialized"
    assert init_data() == "already_initialized"
