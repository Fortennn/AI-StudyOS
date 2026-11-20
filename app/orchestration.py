from typing import Dict, Any
from app.models import StudySummary, FlashcardItem, QuizItem
from app.telemetry import logger
from app.providers.base import ProviderError

class StudyOrchestrator:
    def __init__(self):
        pass

    def build_prompt(self, text: str, topic: str, card_count: int, quiz_count: int) -> str:
        """Constructs the system prompt instructing the model to generate structured JSON."""
        return (
            "Ти — висококваліфікований академічний тьютор та експерт з підготовки до іспитів.\n"
            f"Тема матеріалу: {topic}\n"
            f"Вимоги до обсягу: сформувати коротке резюме (TL;DR та 4 тези), {card_count} інтерактивних карток та {quiz_count} тестових питань.\n"
            "Вхідний конспект лекції:\n"
            f"\"\"\"\n{text}\n\"\"\"\n"
            "Поверни строго валідний JSON за вказаною схемою без додаткових тегів Markdown."
        )

    def validate_and_parse_output(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Validates that raw provider dictionary matches required structured fields."""
        logger.info("Orchestrator validating structured output on boundary")
        
        try:
            summary = StudySummary(**raw_data.get("summary", {}))
            
            cards = [
                FlashcardItem(**item)
                for item in raw_data.get("flashcards", [])
            ]
            
            quiz = [
                QuizItem(**item)
                for item in raw_data.get("quiz", [])
            ]
            
            return {
                "summary": summary,
                "flashcards": cards,
                "quiz": quiz
            }
        except Exception as e:
            logger.error(f"Orchestration boundary validation failed: {str(e)}")
            raise ProviderError(
                kind="invalid_schema",
                message=f"Вихідні дані моделі не відповідають схемі: {str(e)}",
                retryable=False
            )
