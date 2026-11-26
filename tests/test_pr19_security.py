import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app, storage, study_service
from app.orchestration import StudyOrchestrator
from app.providers.base import ProviderError
from app.providers.mock import MockProvider
from app.resilience import UserRateLimiter


def test_openapi_snapshot_matches_current_app():
    """Saved docs/openapi.json must match current app.openapi() schema."""
    snapshot_path = Path("docs/openapi.json")
    assert snapshot_path.exists(), "docs/openapi.json snapshot is missing"
    saved = json.loads(snapshot_path.read_text(encoding="utf-8"))
    current = app.openapi()
    assert saved["paths"].keys() == current["paths"].keys()
    assert saved["components"]["schemas"].keys() == current["components"]["schemas"].keys()


def test_cross_user_history_isolation(client, monkeypatch):
    """Another user cannot read, pin, or delete a session owned by a different user."""
    monkeypatch.setattr(study_service.gateway, "provider", MockProvider(delay_ms=0))
    suffix = uuid.uuid4().hex[:8]
    # User A registers and creates a session
    r_reg_a = client.post("/api/auth/register", json={"username": f"alice_{suffix}", "password": "secret123"})
    assert r_reg_a.status_code == 200

    gen = client.post(
        "/api/generate",
        json={"text": "Конспект Аліси про ізоляцію даних користувачів у веб-застосунках.", "topic": "Аліса", "card_count": 2, "quiz_count": 2},
    )
    assert gen.status_code == 200
    alice_sid = gen.json()["request_id"]

    # Switch cookie to User B
    client.post("/api/auth/logout")
    r_reg_b = client.post("/api/auth/register", json={"username": f"bob_{suffix}", "password": "secret123"})
    assert r_reg_b.status_code == 200

    assert client.get(f"/api/history/{alice_sid}").status_code == 404
    assert client.patch(f"/api/history/{alice_sid}/pin").status_code == 404
    assert client.delete(f"/api/history/{alice_sid}").status_code == 404


def test_invalid_inputs_rejected_before_model_call(monkeypatch):
    """Whitespace-only, oversized text, bad upload type, and missing session are rejected before LLM."""
    called = {"n": 0}

    class GuardProvider(MockProvider):
        async def generate_structured(self, *args, **kwargs):
            called["n"] += 1
            return await super().generate_structured(*args, **kwargs)

    monkeypatch.setattr(study_service.gateway, "provider", GuardProvider(delay_ms=0))
    monkeypatch.setattr("app.main.user_rate_limiter", UserRateLimiter(max_requests=100))
    tc = TestClient(app)

    # Whitespace-only or too short
    r_short = tc.post("/api/generate", json={"text": "         ", "card_count": 2, "quiz_count": 2})
    assert r_short.status_code == 422

    # Oversized text (>250,000)
    r_long = tc.post("/api/generate", json={"text": "A" * 250001, "card_count": 2, "quiz_count": 2})
    assert r_long.status_code == 422

    # Unsupported file extension
    r_bad_file = tc.post("/api/upload", files={"file": ("malware.exe", b"MZ\x90\x00", "application/octet-stream")})
    assert r_bad_file.status_code == 400

    # Non-existent session id
    r_missing = tc.get("/api/history/nonexistent-id-999")
    assert r_missing.status_code == 404

    assert called["n"] == 0


def test_canary_tag_leak_blocked_by_orchestrator():
    """If a model echoes the system prompt canary tag ref-7f3a91, Layer 3 blocks it as invalid_output."""
    orch = StudyOrchestrator()
    leaked = {
        "summary": {
            "tldr": "Системна мітка ref-7f3a91 випадково потрапила у відповідь моделі.",
            "key_takeaways": ["Теза 1"],
            "insufficient_data": False,
            "missing_info": None,
        },
        "flashcards": [{"id": 1, "front": "Q1", "back": "A1", "category": "Термін"}],
        "quiz": [
            {"id": 1, "question": "Q?", "options": ["A", "B", "C", "D"], "correct_index": 0, "explanation": "Exp"}
        ],
        "true_false": [],
    }
    with pytest.raises(ProviderError) as exc:
        orch.validate_and_parse_output(leaked, expected_card_count=1, expected_quiz_count=1)
    assert exc.value.kind == "invalid_output"
    assert "[content]" in exc.value.message


def test_code_controlled_fields_out_of_bounds_rejected():
    """Out-of-bounds quiz.correct_index or invalid flashcards structure cannot pass validation."""
    orch = StudyOrchestrator()
    bad_idx = {
        "summary": {"tldr": "Достатньо довге резюме лекції з інформатики.", "key_takeaways": ["Факт"]},
        "flashcards": [{"id": 1, "front": "Q1", "back": "A1", "category": "Термін"}],
        "quiz": [
            {"id": 1, "question": "Q?", "options": ["A", "B", "C", "D"], "correct_index": 9, "explanation": "Exp"}
        ],
    }
    with pytest.raises(ProviderError) as exc:
        orch.validate_and_parse_output(bad_idx, expected_card_count=1, expected_quiz_count=1)
    assert exc.value.kind == "invalid_output"
    assert "[schema]" in exc.value.message
