import asyncio
from typing import Dict, Any, List
from app.providers.base import LLMProvider, ProviderError
from app.providers.mock import MockProvider
from app.providers.gemini import GeminiProvider
from app.settings import Settings
from app.telemetry import logger

class LLMGateway:
    def __init__(self, settings: Settings, provider: LLMProvider = None):
        self.settings = settings
        self.timeout_s = settings.llm_timeout_s
        
        if provider:
            self.provider = provider
        else:
            if settings.llm_provider == "mock":
                self.provider = MockProvider(
                    failure_kind=settings.mock_failure_kind,
                    failure_rate=settings.mock_failure_rate,
                    delay_ms=settings.mock_delay_ms
                )
            elif settings.llm_provider == "gemini":
                self.provider = GeminiProvider(
                    api_key=settings.llm_api_key,
                    model=settings.llm_model,
                    timeout_s=settings.llm_timeout_s
                )
            else:
                raise NotImplementedError(f"Provider '{settings.llm_provider}' is not supported. Use 'mock' or 'gemini'.")

    async def generate_study_kit(self, prompt: str, topic: str, card_count: int, quiz_count: int) -> Dict[str, Any]:
        logger.info(f"Gateway dispatching request to provider '{self.settings.llm_provider}' (model: {self.settings.llm_model})")
        
        try:
            # Enforce timeout boundary
            result = await asyncio.wait_for(
                self.provider.generate_structured(
                    prompt=prompt,
                    topic=topic,
                    card_count=card_count,
                    quiz_count=quiz_count
                ),
                timeout=self.timeout_s
            )
            return result
        except asyncio.TimeoutError:
            logger.error(f"Gateway timeout exceeded ({self.timeout_s}s)")
            raise ProviderError(
                kind="timeout",
                message=f"Час очікування провайдера перевищено ({self.timeout_s} с)",
                retryable=True
            )
        except ProviderError as pe:
            logger.error(f"Gateway caught provider error: {pe.kind} - {pe.message}")
            raise pe
        except Exception as e:
            logger.error(f"Gateway unexpected failure: {str(e)}")
            raise ProviderError(
                kind="provider_unavailable",
                message=f"Збій комунікації з AI провайдером: {str(e)}",
                retryable=True
            )

    async def answer_chat(self, question: str, topic: str, context_text: str, history: List[Dict[str, str]]) -> str:
        logger.info(f"Gateway dispatching chat to provider '{self.settings.llm_provider}' (model: {self.settings.llm_model})")
        try:
            return await asyncio.wait_for(
                self.provider.answer_question(
                    question=question,
                    topic=topic,
                    context_text=context_text,
                    history=history
                ),
                timeout=self.timeout_s
            )
        except asyncio.TimeoutError:
            raise ProviderError(
                kind="timeout",
                message=f"Час очікування провайдера перевищено ({self.timeout_s} с)",
                retryable=True
            )
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(
                kind="provider_unavailable",
                message=f"Збій комунікації з AI провайдером: {str(e)}",
                retryable=True
            )
