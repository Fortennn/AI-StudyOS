import uuid
from app.models import StudyRequest, StudyKitResponse
from app.gateway import LLMGateway
from app.orchestration import StudyOrchestrator
from app.storage import Storage
from app.telemetry import logger, get_current_request_id

class StudyService:
    def __init__(self, gateway: LLMGateway, orchestrator: StudyOrchestrator, storage: Storage):
        self.gateway = gateway
        self.orchestrator = orchestrator
        self.storage = storage

    async def process_study_material(self, req: StudyRequest, req_id: str = None) -> StudyKitResponse:
        current_id = req_id or get_current_request_id() or f"req-{uuid.uuid4().hex[:8]}"
        logger.info(f"StudyService executing business scenario for request {current_id}")

        # 1. Build prompt via orchestration
        prompt = self.orchestrator.build_prompt(
            text=req.text,
            topic=req.topic,
            card_count=req.card_count,
            quiz_count=req.quiz_count
        )

        # 2. Call gateway
        raw_output = await self.gateway.generate_study_kit(
            prompt=prompt,
            topic=req.topic,
            card_count=req.card_count,
            quiz_count=req.quiz_count
        )

        # 3. Validate boundary schema
        validated_parts = self.orchestrator.validate_and_parse_output(raw_output)

        # 4. Construct response model
        response = StudyKitResponse(
            request_id=current_id,
            status="success",
            topic=req.topic,
            summary=validated_parts["summary"],
            flashcards=validated_parts["flashcards"],
            quiz=validated_parts["quiz"]
        )

        # 5. Persist to storage
        try:
            self.storage.save_session(response, raw_text=req.text)
        except Exception as e:
            logger.warning(f"Could not persist session to storage: {e}")

        logger.info(f"StudyService finished successfully for {current_id}")
        return response
