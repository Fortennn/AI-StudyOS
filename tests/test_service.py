import asyncio
from app.models import StudyRequest

def test_service_isolated_call(isolated_service):
    """Test 2: Direct invocation of StudyService without HTTP layer proves clean architectural boundaries."""
    request = StudyRequest(
        text="Операційна система управляє ресурсами процесора, оперативної пам'яті та пристроїв вводу-виводу.",
        topic="Операційні системи",
        card_count=4,
        quiz_count=4
    )
    
    result = asyncio.run(isolated_service.process_study_material(request, req_id="test-service-123"))
    
    assert result.request_id == "test-service-123"
    assert result.topic == "Операційні системи"
    assert len(result.flashcards) == 4
    assert len(result.quiz) == 4
    assert result.summary.tldr != ""
