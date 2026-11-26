import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.cache import StudyResponseCache
from app.gateway import LLMGateway
from app.main import app, gateway as app_gateway, study_service, user_rate_limiter
from app.models import StudyRequest
from app.orchestration import StudyOrchestrator
from app.providers.base import LLMProvider, ProviderError
from app.providers.mock import MockProvider
from app.resilience import CircuitBreaker, UserRateLimiter
from app.service import StudyService
from app.settings import Settings
from app.storage import Storage
from app.telemetry import log_event


def test_gateway_retries_only_transient_errors(tmp_path):
    """Gateway retries transient errors up to llm_max_retries, but never retries non-retryable auth errors."""
    attempts = {"n": 0}
    ok_mock = MockProvider(delay_ms=0)

    class FlakyTimeoutProvider(LLMProvider):
        async def generate_structured(self, *args, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise ProviderError(kind="timeout", message="Timed out", retryable=True)
            return await ok_mock.generate_structured(*args, **kwargs)

    settings = Settings(llm_provider="mock", llm_max_retries=2, budget_file=str(tmp_path / "b1.json"))
    gw = LLMGateway(settings=settings, provider=FlakyTimeoutProvider())
    res = asyncio.run(gw.generate_study_kit("prompt", "Тема", 2, 2))
    assert attempts["n"] == 2
    assert len(res["flashcards"]) == 2

    # Non-retryable auth error must stop on attempt 1
    auth_attempts = {"n": 0}

    class AuthFailProvider(LLMProvider):
        async def generate_structured(self, *args, **kwargs):
            auth_attempts["n"] += 1
            raise ProviderError(kind="auth", message="Bad key", retryable=False)

    gw2 = LLMGateway(
        settings=Settings(llm_provider="mock", llm_max_retries=3, budget_file=str(tmp_path / "b2.json")),
        provider=AuthFailProvider(),
    )
    with pytest.raises(ProviderError) as exc:
        asyncio.run(gw2.generate_study_kit("prompt", "Тема", 2, 2))
    assert exc.value.kind == "auth"
    assert auth_attempts["n"] == 1


def test_circuit_breaker_opens_ignores_auth_and_shows_in_health(monkeypatch, tmp_path):
    """CircuitBreaker ignores auth/invalid_output, opens on availability failures, and appears in /api/health."""
    cb = CircuitBreaker(name="mock", failure_threshold=2, window_seconds=60.0, recovery_seconds=0.1)
    cb.record_failure("auth")
    cb.record_failure("invalid_output")
    assert cb.status()["breaker"] == "closed"
    assert cb.status()["failures_in_window"] == 0

    cb.record_failure("provider_unavailable")
    cb.record_failure("timeout")
    assert cb.status()["breaker"] == "open"
    assert cb.allow_request() is False

    monkeypatch.setattr(app_gateway, "breaker", cb)
    monkeypatch.setattr(study_service.gateway, "breaker", cb)
    tc = TestClient(app)

    health = tc.get("/api/health").json()
    assert health["extra"]["provider"]["breaker"] == "open"

    resp = tc.post(
        "/api/generate",
        json={"text": "Достатньо довгий конспект для перевірки запобіжника.", "topic": "CB", "card_count": 2, "quiz_count": 2},
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["kind"] == "circuit_open"
    assert "Retry-After" in resp.headers

    # After recovery window, breaker allows half_open probe and closes on success
    time.sleep(0.12)
    assert cb.allow_request() is True
    assert cb.status()["breaker"] == "half_open"
    cb.record_success()
    assert cb.status()["breaker"] == "closed"


def test_user_rate_limit_and_ai_kill_switch(monkeypatch, tmp_path):
    """Per-user rate limit returns 429 + Retry-After; AI_ENABLED=0 returns 503 ai_disabled."""
    limiter = UserRateLimiter(max_requests=1, window_seconds=60.0)
    monkeypatch.setattr("app.main.user_rate_limiter", limiter)

    tc = TestClient(app)
    payload = {"text": "Конспект про ліміти частоти користувачів і надійність.", "topic": "RL", "card_count": 2, "quiz_count": 2}
    r1 = tc.post("/api/generate", json=payload)
    assert r1.status_code == 200

    r2 = tc.post("/api/generate", json=payload)
    assert r2.status_code == 429
    assert r2.json()["error"]["kind"] == "user_rate_limit"
    assert "Retry-After" in r2.headers

    # AI kill switch
    monkeypatch.setattr("app.main.user_rate_limiter", UserRateLimiter(max_requests=100))
    monkeypatch.setattr(study_service.gateway.settings, "ai_enabled", False)
    r3 = tc.post("/api/generate", json=payload, headers={"X-Bypass-Cache": "1"})
    assert r3.status_code == 503
    assert r3.json()["error"]["kind"] == "ai_disabled"


def test_ttl_cache_hit_owner_isolation_and_bypass(tmp_path):
    """TTL cache hits save provider calls, isolate by user_id, and respect bypass_cache=True."""
    calls = {"n": 0}
    orig = MockProvider(delay_ms=0)

    class CountingMock(MockProvider):
        def __init__(self):
            super().__init__(delay_ms=0)

        async def generate_structured(self, *args, **kwargs):
            calls["n"] += 1
            return await orig.generate_structured(*args, **kwargs)

    settings = Settings(llm_provider="mock", budget_file=str(tmp_path / "b.json"))
    svc = StudyService(
        gateway=LLMGateway(settings=settings, provider=CountingMock()),
        orchestrator=StudyOrchestrator(),
        storage=Storage(":memory:"),
        cache=StudyResponseCache(ttl_seconds=60),
    )

    req = StudyRequest(text="Кешування результатів за хешем вмісту і власником.", topic="Кеш", card_count=2, quiz_count=2)
    r1 = asyncio.run(svc.process_study_material(req, req_id="c1", user_id="user-A"))
    assert r1.provider_kind == "mock"
    assert calls["n"] == 1

    # Same user hits cache
    r2 = asyncio.run(svc.process_study_material(req, req_id="c2", user_id="user-A"))
    assert r2.provider_kind == "cache"
    assert calls["n"] == 1

    # Different user does NOT get user-A's cache entry
    r3 = asyncio.run(svc.process_study_material(req, req_id="c3", user_id="user-B"))
    assert r3.provider_kind == "mock"
    assert calls["n"] == 2

    # Explicit bypass_cache skips cache
    r4 = asyncio.run(svc.process_study_material(req, req_id="c4", user_id="user-A", bypass_cache=True))
    assert r4.provider_kind == "mock"
    assert calls["n"] == 3


@pytest.mark.parametrize(
    "failure_kind,expected_status,expected_retryable",
    [
        ("auth", 401, False),
        ("mid_stream", 502, True),
        ("rate_limit", 429, True),
        ("unavailable", 503, True),
        ("timeout", 504, True),
    ],
)
def test_closed_error_catalog_statuses(monkeypatch, tmp_path, failure_kind, expected_status, expected_retryable):
    """Every failure kind maps to its documented HTTP status and retryable flag."""
    monkeypatch.setattr(study_service.gateway, "provider", MockProvider(failure_kind=failure_kind, delay_ms=0))
    monkeypatch.setattr(study_service.gateway, "breaker", CircuitBreaker("test", failure_threshold=99))
    monkeypatch.setattr("app.main.user_rate_limiter", UserRateLimiter(max_requests=100))

    tc = TestClient(app)
    res = tc.post(
        "/api/generate",
        headers={"X-Bypass-Cache": "1"},
        json={"text": "Конспект для перевірки каталогу контрольованих помилок.", "topic": "Errors", "card_count": 2, "quiz_count": 2},
    )
    assert res.status_code == expected_status
    err = res.json()["error"]
    assert err["retryable"] is expected_retryable
    assert err["request_id"]


def test_jsonl_allowlist_and_secret_masking():
    """Structured JSONL log keeps only allowlisted fields and masks secret patterns."""
    log_path = Path("logs/app.jsonl")
    log_event(
        stage="test_pr18",
        status="ok",
        provider="gemini",
        model="gemini-3-flash-preview",
        kind="AIzaSySecretPattern1234567890",
        raw_prompt="SENSITIVE_USER_TEXT_MUST_NOT_APPEAR",
        email="student@example.com",
    )
    assert log_path.exists()
    last_line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    record = json.loads(last_line)
    assert record["stage"] == "test_pr18"
    assert "raw_prompt" not in record
    assert "email" not in record
    assert "AIzaSySecretPattern" not in last_line
    assert "***REDACTED***" in last_line
