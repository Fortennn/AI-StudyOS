import asyncio
from typing import Dict, Any
from app.providers.base import LLMProvider, ProviderError
from app.providers.mock import MockProvider
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
            else:
                # Real provider will be added in future releases
                raise NotImplementedError(f"Provider '{settings.llm_provider}' will be implemented in future releases")

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
