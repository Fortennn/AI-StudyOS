import uuid
from typing import Optional
from app.models import StudyRequest, StudyKitResponse, ChatRequest, ChatResponse
from app.gateway import LLMGateway
from app.orchestration import StudyOrchestrator
from app.storage import Storage
from app.telemetry import logger, get_current_request_id

class StudyService:
    def __init__(self, gateway: LLMGateway, orchestrator: StudyOrchestrator, storage: Storage):
        self.gateway = gateway
        self.orchestrator = orchestrator
        self.storage = storage

    def _resolve_gateway(self, provider: Optional[str], api_key: Optional[str] = None) -> LLMGateway:
        if not provider and not api_key:
            return self.gateway
        from app.settings import Settings
        override_settings = Settings(
            app_env=self.gateway.settings.app_env,
            app_version=self.gateway.settings.app_version,
            app_port=self.gateway.settings.app_port,
            llm_provider=provider or self.gateway.settings.llm_provider,
            llm_model="gemini-3-flash-preview" if (provider == "gemini" and self.gateway.settings.llm_provider != "gemini") else self.gateway.settings.llm_model,
            llm_api_key=api_key or self.gateway.settings.llm_api_key,
            llm_timeout_s=self.gateway.settings.llm_timeout_s,
            mock_failure_rate=self.gateway.settings.mock_failure_rate,
            mock_failure_kind=self.gateway.settings.mock_failure_kind,
            mock_delay_ms=self.gateway.settings.mock_delay_ms,
            db_path=self.gateway.settings.db_path
        )
        return LLMGateway(settings=override_settings)

    async def process_study_material(self, req: StudyRequest, req_id: str = None, user_id: Optional[str] = None) -> StudyKitResponse:
        current_id = req_id or get_current_request_id() or f"req-{uuid.uuid4().hex[:8]}"
        logger.info(f"StudyService executing business scenario for request {current_id}")

        active_gateway = self._resolve_gateway(req.provider, req.api_key)

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
            quiz=validated_parts["quiz"],
            true_false=validated_parts.get("true_false", [])
        )

        # 5. Persist to storage
        try:
            self.storage.save_session(response, raw_text=req.text, user_id=user_id)
        except Exception as e:
            logger.warning(f"Could not persist session to storage: {e}")

        logger.info(f"StudyService finished successfully for {current_id}")
        return response

    async def answer_lecture_question(self, req: ChatRequest, req_id: str = None, user_id: Optional[str] = None) -> ChatResponse:
        current_id = req_id or get_current_request_id() or f"req-{uuid.uuid4().hex[:8]}"
        active_gateway = self._resolve_gateway(req.provider)

        context_text = (req.context_text or "").strip()
        if not context_text and req.session_id:
            context_text = self.storage.get_session_text(req.session_id, user_id=user_id)

        condensed_ctx = self.orchestrator.condense_text_for_llm(context_text) if context_text else ""
        history_dicts = [{"role": m.role, "text": m.text} for m in req.history]

        answer = await active_gateway.answer_chat(
            question=req.question,
            topic=req.topic,
            context_text=condensed_ctx,
            history=history_dicts
        )
        if req.session_id:
            try:
                self.storage.append_chat_exchange(req.session_id, req.question, answer, user_id=user_id)
            except Exception as e:
                logger.warning(f"Could not persist chat exchange for {req.session_id}: {e}")
        return ChatResponse(request_id=current_id, answer=answer)
