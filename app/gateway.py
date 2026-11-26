import asyncio
import random
import time
from typing import Any, Dict, List, Optional

from app.budget import DailyBudgetTracker
from app.providers.base import LLMProvider, ProviderError
from app.providers.gemini import GeminiProvider
from app.providers.mock import MockProvider
from app.providers.recorded import RecordedProvider
from app.resilience import CircuitBreaker
from app.settings import Settings
from app.telemetry import logger, log_event


class LLMGateway:
    def __init__(
        self,
        settings: Settings,
        provider: LLMProvider = None,
        budget_tracker: Optional[DailyBudgetTracker] = None,
        breaker: Optional[CircuitBreaker] = None,
    ):
        self.settings = settings
        self.timeout_s = settings.llm_timeout_s
        self.total_deadline_s = getattr(settings, "llm_total_deadline_s", 45)
        self.max_retries = max(1, getattr(settings, "llm_max_retries", 2))
        self.max_concurrency = max(1, getattr(settings, "llm_max_concurrency", 5))
        self._semaphores: Dict[int, asyncio.Semaphore] = {}

        self.budget_tracker = budget_tracker or DailyBudgetTracker(
            budget_file=settings.budget_file,
            max_daily_usd=settings.budget_daily_usd,
            max_daily_calls=settings.budget_daily_calls,
        )
        self.breaker = breaker or CircuitBreaker(
            name=settings.llm_provider,
            failure_threshold=getattr(settings, "breaker_threshold", 3),
            window_seconds=60.0,
            recovery_seconds=float(getattr(settings, "breaker_recovery_s", 30)),
        )

        if provider:
            self.provider = provider
        else:
            if settings.llm_provider == "mock":
                self.provider = MockProvider(
                    failure_kind=settings.mock_failure_kind,
                    failure_rate=settings.mock_failure_rate,
                    delay_ms=settings.mock_delay_ms,
                )
            elif settings.llm_provider == "gemini":
                self.provider = GeminiProvider(
                    api_key=settings.llm_api_key,
                    model=settings.llm_model,
                    timeout_s=settings.llm_timeout_s,
                )
            elif settings.llm_provider == "recorded":
                inner: Optional[LLMProvider] = None
                if settings.record_mode in ("record", "passthrough"):
                    inner = (
                        GeminiProvider(
                            api_key=settings.llm_api_key,
                            model=settings.llm_model,
                            timeout_s=settings.llm_timeout_s,
                        )
                        if settings.llm_api_key.strip()
                        else MockProvider(delay_ms=settings.mock_delay_ms)
                    )
                self.provider = RecordedProvider(
                    inner_provider=inner,
                    mode=settings.record_mode,
                    recordings_dir=settings.recordings_dir,
                )
            else:
                raise NotImplementedError(
                    f"Provider '{settings.llm_provider}' is not supported. Use 'mock', 'gemini', or 'recorded'."
                )

    def _get_semaphore(self) -> asyncio.Semaphore:
        loop_id = id(asyncio.get_running_loop())
        sem = self._semaphores.get(loop_id)
        if sem is None:
            sem = asyncio.Semaphore(self.max_concurrency)
            self._semaphores[loop_id] = sem
        return sem

    def _check_enabled_and_breaker(self) -> None:
        if not getattr(self.settings, "ai_enabled", True):
            log_event(level="WARNING", stage="llm_gateway", provider=self.settings.llm_provider, status="disabled", kind="ai_disabled")
            raise ProviderError(
                kind="ai_disabled",
                message="AI-функцію тимчасово вимкнено конфігурацією (AI_ENABLED=0).",
                retryable=False,
            )
        if not self.breaker.allow_request():
            log_event(level="WARNING", stage="llm_gateway", provider=self.settings.llm_provider, status="circuit_open", kind="circuit_open")
            raise ProviderError(
                kind="circuit_open",
                message="Запобіжник провайдера розімкнено через повторні збої. Спробуйте пізніше.",
                retryable=True,
                retry_after=max(1, int(self.breaker.recovery_seconds)),
            )

    async def generate_study_kit(
        self,
        prompt: str,
        topic: str,
        card_count: int,
        quiz_count: int,
        system_instruction: str = "",
        temperature: float = 0.2,
        max_tokens: int = 8192,
        prompt_version: str = "v1",
        schema_version: str = "StudyKitResponse",
    ) -> Dict[str, Any]:
        logger.info(f"Gateway dispatching request to provider '{self.settings.llm_provider}' (model: {self.settings.llm_model})")
        self._check_enabled_and_breaker()

        deadline = time.monotonic() + self.total_deadline_s
        sem = self._get_semaphore()
        last_error: Optional[ProviderError] = None

        for attempt in range(1, self.max_retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0.05:
                raise last_error or ProviderError(
                    kind="timeout",
                    message="Вичерпано загальний дедлайн запиту.",
                    retryable=True,
                )

            attempt_timeout = min(float(self.timeout_s), remaining)
            t0 = time.perf_counter()
            try:
                self.budget_tracker.check_and_reserve(
                    estimated_cost_usd=0.002 if self.settings.llm_provider == "gemini" else 0.0
                )
                try:
                    await asyncio.wait_for(sem.acquire(), timeout=min(5.0, remaining))
                except asyncio.TimeoutError:
                    raise ProviderError(
                        kind="rate_limit",
                        message="Черга одночасних запитів до провайдера переповнена. Спробуйте через кілька секунд.",
                        retryable=True,
                        retry_after=2,
                    )

                try:
                    result = await asyncio.wait_for(
                        self.provider.generate_structured(
                            prompt=prompt,
                            topic=topic,
                            card_count=card_count,
                            quiz_count=quiz_count,
                            system_instruction=system_instruction,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        ),
                        timeout=attempt_timeout,
                    )
                finally:
                    sem.release()

                duration_ms = round((time.perf_counter() - t0) * 1000, 2)
                self.breaker.record_success()
                log_event(
                    stage="llm_gateway",
                    provider=self.settings.llm_provider,
                    model=self.settings.llm_model,
                    prompt_version=prompt_version,
                    schema_version=schema_version,
                    attempt=attempt,
                    duration_ms=duration_ms,
                    status="ok",
                    cached=False,
                    fallback=attempt > 1,
                    cost=0.002 if self.settings.llm_provider == "gemini" else 0.0,
                )
                return result

            except asyncio.TimeoutError:
                pe = ProviderError(
                    kind="timeout",
                    message=f"Час очікування провайдера перевищено ({self.timeout_s} с)",
                    retryable=True,
                )
            except ProviderError as pe_caught:
                pe = pe_caught
            except Exception as e:
                pe = ProviderError(
                    kind="provider_unavailable",
                    message=f"Збій комунікації з AI провайдером: {e}",
                    retryable=True,
                )

            duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            self.breaker.record_failure(pe.kind)
            log_event(
                level="WARNING" if pe.retryable and attempt < self.max_retries else "ERROR",
                stage="llm_gateway",
                provider=self.settings.llm_provider,
                model=self.settings.llm_model,
                prompt_version=prompt_version,
                schema_version=schema_version,
                attempt=attempt,
                duration_ms=duration_ms,
                status="error",
                kind=pe.kind,
                cached=False,
                fallback=attempt > 1,
            )
            last_error = pe

            if not pe.retryable or attempt >= self.max_retries:
                raise pe

            # Bounded exponential backoff with jitter, respecting Retry-After and remaining deadline
            base_delay = float(pe.retry_after) if pe.retry_after else (0.15 * (2 ** (attempt - 1)) + random.uniform(0.0, 0.1))
            remaining_after = deadline - time.monotonic()
            if base_delay >= remaining_after:
                raise pe
            await asyncio.sleep(base_delay)

        raise last_error or ProviderError(kind="provider_unavailable", message="Провайдер недоступний", retryable=True)

    async def answer_chat(self, question: str, topic: str, context_text: str, history: List[Dict[str, str]]) -> str:
        logger.info(f"Gateway dispatching chat to provider '{self.settings.llm_provider}' (model: {self.settings.llm_model})")
        self._check_enabled_and_breaker()
        t0 = time.perf_counter()
        try:
            self.budget_tracker.check_and_reserve(estimated_cost_usd=0.001 if self.settings.llm_provider == "gemini" else 0.0)
            answer = await asyncio.wait_for(
                self.provider.answer_question(
                    question=question,
                    topic=topic,
                    context_text=context_text,
                    history=history,
                ),
                timeout=self.timeout_s,
            )
            self.breaker.record_success()
            log_event(
                stage="chat_gateway",
                provider=self.settings.llm_provider,
                model=self.settings.llm_model,
                attempt=1,
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
                status="ok",
                cached=False,
            )
            return answer
        except asyncio.TimeoutError:
            self.breaker.record_failure("timeout")
            raise ProviderError(
                kind="timeout",
                message=f"Час очікування провайдера перевищено ({self.timeout_s} с)",
                retryable=True,
            )
        except ProviderError as pe:
            self.breaker.record_failure(pe.kind)
            raise
        except Exception as e:
            self.breaker.record_failure("provider_unavailable")
            raise ProviderError(
                kind="provider_unavailable",
                message=f"Збій комунікації з AI провайдером: {e}",
                retryable=True,
            )
