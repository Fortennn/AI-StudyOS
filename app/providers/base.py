from abc import ABC, abstractmethod
from typing import Dict, Any, List

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

    async def answer_question(self, question: str, topic: str, context_text: str, history: List[Dict[str, str]]) -> str:
        """Answers a follow-up question grounded in the lecture material."""
        raise NotImplementedError("Chat is not implemented for this provider.")
