from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional

class ProviderError(Exception):
    def __init__(self, kind: str, message: str, retryable: bool = False, retry_after: Optional[int] = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after

class LLMProvider(ABC):
    @abstractmethod
    async def generate_structured(
        self,
        prompt: str,
        topic: str,
        card_count: int,
        quiz_count: int,
        system_instruction: str = "",
        temperature: float = 0.2,
        max_tokens: int = 8192,
    ) -> Dict[str, Any]:
        """Generates structured dictionary output adhering to StudyKitResponse schema."""
        pass

    async def answer_question(self, question: str, topic: str, context_text: str, history: List[Dict[str, str]]) -> str:
        """Answers a follow-up question grounded in the lecture material."""
        raise NotImplementedError("Chat is not implemented for this provider.")
