import pytest
from app.providers.mock import MockProvider
from app.gateway import LLMGateway
from app.orchestration import StudyOrchestrator
from app.storage import Storage
from app.service import StudyService
from app.models import StudyRequest
from app.providers.base import ProviderError
from fastapi.testclient import TestClient
from app.main import app

def test_controlled_error_via_api(monkeypatch):
    """Test 3: Simulating provider failure yields unified error contract with appropriate HTTP status."""
    
    # Configure mock provider to trigger unavailable failure
    failing_mock = MockProvider(failure_kind="unavailable", delay_ms=0)
    from app.main import study_service
    monkeypatch.setattr(study_service.gateway, "provider", failing_mock)
    
    client = TestClient(app)
    payload = {
        "text": "Тестовий текст лекції для перевірки помилки відмовостійкості.",
        "topic": "Тестування помилок",
        "card_count": 2,
        "quiz_count": 2
    }
    
    response = client.post("/api/generate", json=payload)
    
    # Should not crash with 500 unhandled, but return controlled 503
    assert response.status_code == 503
    data = response.json()
    assert "error" in data
    assert data["error"]["kind"] == "provider_unavailable"
    assert data["error"]["retryable"] is True
    assert "request_id" in data["error"]
    assert "Mock 503" in data["error"]["message"]

def test_validation_error_format(client):
    """Test 3.2: Malformed input generates 422 with unified error format."""
    payload = {
        "text": "коротко",  # Too short (min_length=10)
        "card_count": 50    # Exceeds max limit 20
    }
    response = client.post("/api/generate", json=payload)
    assert response.status_code == 422
    data = response.json()
    assert "error" in data
    assert data["error"]["kind"] == "invalid_input"
    assert data["error"]["retryable"] is False
    assert "request_id" in data["error"]
