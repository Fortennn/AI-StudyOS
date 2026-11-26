import time
import uuid
from typing import Optional
from app.cache import StudyResponseCache
from app.models import StudyRequest, StudyKitResponse, ChatRequest, ChatResponse
from app.gateway import LLMGateway
from app.orchestration import StudyOrchestrator
from app.providers.base import ProviderError
from app.storage import Storage
from app.telemetry import logger, log_event, get_current_request_id

class StudyService:
    def __init__(
        self,
        gateway: LLMGateway,
        orchestrator: StudyOrchestrator,
        storage: Storage,
        cache: Optional[StudyResponseCache] = None,
    ):
        self.gateway = gateway
        self.orchestrator = orchestrator
        self.storage = storage
        self.cache = cache or StudyResponseCache(
            ttl_seconds=getattr(gateway.settings, "cache_ttl_s", 3600)
        )

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
            db_path=self.gateway.settings.db_path,
            record_mode=self.gateway.settings.record_mode,
            recordings_dir=self.gateway.settings.recordings_dir,
            budget_daily_usd=self.gateway.settings.budget_daily_usd,
            budget_daily_calls=self.gateway.settings.budget_daily_calls,
            budget_file=self.gateway.settings.budget_file,
            ai_enabled=self.gateway.settings.ai_enabled,
            llm_total_deadline_s=self.gateway.settings.llm_total_deadline_s,
            llm_max_retries=self.gateway.settings.llm_max_retries,
            llm_max_concurrency=self.gateway.settings.llm_max_concurrency,
            breaker_threshold=self.gateway.settings.breaker_threshold,
            breaker_recovery_s=self.gateway.settings.breaker_recovery_s,
            cache_ttl_s=self.gateway.settings.cache_ttl_s,
        )
        return LLMGateway(
            settings=override_settings,
            budget_tracker=self.gateway.budget_tracker,
            breaker=self.gateway.breaker,
        )

    async def process_study_material(
        self,
        req: StudyRequest,
        req_id: str = None,
        user_id: Optional[str] = None,
        bypass_cache: bool = False,
    ) -> StudyKitResponse:
        current_id = req_id or get_current_request_id() or f"req-{uuid.uuid4().hex[:8]}"
        logger.info(f"StudyService executing business scenario for request {current_id}")

        active_gateway = self._resolve_gateway(req.provider, req.api_key)
        cache_key = self.cache.make_key(
            model=active_gateway.settings.llm_model,
            prompt_version="v1",
            schema_version="StudyKitResponse",
            topic=req.topic or "Загальна тема",
            material=req.text,
            card_count=req.card_count,
            quiz_count=req.quiz_count,
            user_id=user_id,
        )
        if not bypass_cache:
            cached_payload = self.cache.get(cache_key)
            if cached_payload is not None:
                log_event(
                    stage="cache",
                    provider=active_gateway.settings.llm_provider,
                    provider_kind="cache",
                    model=active_gateway.settings.llm_model,
                    prompt_version="v1",
                    schema_version="StudyKitResponse",
                    status="hit",
                    cached=True,
                    cost=0.0,
                )
                return StudyKitResponse(
                    **{**cached_payload, "request_id": current_id, "provider_kind": "cache"}
                )

        # 1. Build prompt & run gateway with 1 automatic validation retry
        validation_feedback: Optional[str] = None
        validated_parts = None
        for attempt in range(2):
            system_instruction, user_prompt, meta = self.orchestrator.build_prompt_bundle(
                text=req.text,
                topic=req.topic or "Загальна тема",
                card_count=req.card_count,
                quiz_count=req.quiz_count,
                validation_feedback=validation_feedback,
            )

            raw_output = await active_gateway.generate_study_kit(
                prompt=user_prompt,
                topic=req.topic or "Загальна тема",
                card_count=req.card_count,
                quiz_count=req.quiz_count,
                system_instruction=system_instruction,
                temperature=meta.get("temperature", 0.2),
                max_tokens=meta.get("max_tokens", 8192),
            )

            try:
                t_val = time.perf_counter()
                validated_parts = self.orchestrator.validate_and_parse_output(
                    raw_output,
                    expected_card_count=req.card_count,
                    expected_quiz_count=req.quiz_count,
                )
                log_event(
                    stage="validation",
                    attempt=attempt + 1,
                    duration_ms=round((time.perf_counter() - t_val) * 1000, 2),
                    status="ok",
                )
                break
            except ProviderError as pe:
                log_event(
                    level="WARNING" if attempt == 0 else "ERROR",
                    stage="validation",
                    attempt=attempt + 1,
                    status="error",
                    kind=pe.kind,
                )
                if pe.kind in ("invalid_output", "invalid_schema", "invalid_json") and attempt == 0:
                    logger.warning(f"Validation failed on attempt 1 for {current_id}; retrying once: {pe.message}")
                    validation_feedback = pe.message
                    continue
                raise ProviderError(
                    kind="invalid_output",
                    message=pe.message,
                    retryable=False,
                )

        # 4. Construct response model
        from app.providers.mock import MockProvider
        from app.providers.recorded import RecordedProvider
        from app.providers.gemini import GeminiProvider
        prov_obj = active_gateway.provider
        if isinstance(prov_obj, MockProvider):
            provider_kind = "mock"
        elif isinstance(prov_obj, RecordedProvider):
            provider_kind = "recorded"
        elif isinstance(prov_obj, GeminiProvider):
            provider_kind = "main"
        else:
            prov_name = active_gateway.settings.llm_provider
            provider_kind = "main" if prov_name == "gemini" else ("recorded" if prov_name == "recorded" else "mock")
        response = StudyKitResponse(
            request_id=current_id,
            status="success",
            topic=req.topic,
            summary=validated_parts["summary"],
            flashcards=validated_parts["flashcards"],
            quiz=validated_parts["quiz"],
            true_false=validated_parts.get("true_false", []),
            provider_kind=provider_kind,
            checks=["schema", "rules", "content"],
        )

        # 5. Cache valid response & persist to storage
        if not req.provider or req.provider == active_gateway.settings.llm_provider:
            self.cache.set(cache_key, response.model_dump(), owner=user_id)
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
