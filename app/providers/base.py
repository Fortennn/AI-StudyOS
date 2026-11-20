from abc import ABC, abstractmethod
from typing import Dict, Any

class ProviderError(Exception):
    def __init__(self, kind: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retryable = retryable

class LLMProvider(ABC):
    @abstractmethod
    async def generate_structured(self, prompt: str, topic: str, card_count: int, quiz_count: int) -> Dict[str, Any]:
        """Generates structured dictionary output adhering to StudyKitResponse schema."""
        pass
