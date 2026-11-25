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

        # Dynamic provider override if specified
        active_gateway = self.gateway
        if req.provider or req.api_key:
            from app.settings import Settings
            override_settings = Settings(
                app_env=self.gateway.settings.app_env,
                app_version=self.gateway.settings.app_version,
                app_port=self.gateway.settings.app_port,
                llm_provider=req.provider or self.gateway.settings.llm_provider,
                llm_model="gemini-3-flash-preview" if (req.provider == "gemini" and self.gateway.settings.llm_provider != "gemini") else self.gateway.settings.llm_model,
                llm_api_key=req.api_key or self.gateway.settings.llm_api_key,
                llm_timeout_s=self.gateway.settings.llm_timeout_s,
                mock_failure_rate=self.gateway.settings.mock_failure_rate,
                mock_failure_kind=self.gateway.settings.mock_failure_kind,
                mock_delay_ms=self.gateway.settings.mock_delay_ms,
                db_path=self.gateway.settings.db_path
            )
            active_gateway = LLMGateway(settings=override_settings)

        # 1. Build prompt via orchestration
        prompt = self.orchestrator.build_prompt(
            text=req.text,
            topic=req.topic,
            card_count=req.card_count,
            quiz_count=req.quiz_count
        )

        # 2. Call gateway
        raw_output = await active_gateway.generate_study_kit(
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
