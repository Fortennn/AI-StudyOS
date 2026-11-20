import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.settings import get_settings
from app.storage import Storage
from app.gateway import LLMGateway
from app.orchestration import StudyOrchestrator
from app.service import StudyService
from app.providers.mock import MockProvider

@pytest.fixture
def client():
    # TestClient ensures tests don't touch network ports
    return TestClient(app)

@pytest.fixture
def isolated_service():
    """Provides StudyService with in-memory SQLite and isolated mock provider."""
    storage = Storage(db_path=":memory:")
    mock_prov = MockProvider(failure_kind="none", delay_ms=0)
    settings = get_settings()
    gateway = LLMGateway(settings=settings, provider=mock_prov)
    orchestrator = StudyOrchestrator()
    return StudyService(gateway=gateway, orchestrator=orchestrator, storage=storage)
