import re
from typing import Dict, Any, List
from app.models import StudySummary, FlashcardItem, QuizItem
from app.telemetry import logger
from app.providers.base import ProviderError

class StudyOrchestrator:
    def __init__(self):
        pass

    @staticmethod
    def condense_text_for_llm(text: str, max_chars: int = 14000) -> str:
        """Normalizes whitespace and condenses oversized documents while preserving key structure."""
        cleaned = re.sub(r"[ \t]+", " ", text.strip())
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

        if len(cleaned) <= max_chars:
            return cleaned

        paragraphs: List[str] = [p.strip() for p in cleaned.split("\n\n") if p.strip()]
        if len(paragraphs) <= 3:
            head = cleaned[: int(max_chars * 0.65)]
            tail = cleaned[-int(max_chars * 0.30) :]
            return f"{head}\n\n[...]\n\n{tail}"

        # Keep intro and conclusion, then pick highest-signal middle paragraphs in original order
        selected_idx = {0, len(paragraphs) - 1}
        total_len = len(paragraphs[0]) + len(paragraphs[-1])

        def score_paragraph(p: str) -> float:
            s = 0.0
            if re.search(r"[=+\-*/^$\\]|O\(", p):
                s += 3.0
            if re.search(r"(^|\n)\s*([•\-*]|\d+\.)", p):
                s += 2.0
            if re.search(r"\b(визначення|теорема|формула|алгоритм|принцип|правило|приклад|висновок|основн)\b", p, re.I):
                s += 2.5
            if len(p) < 120 and p.endswith(":"):
                s += 1.5
            return s + min(len(p), 600) / 600.0

        middle_indices = list(range(1, len(paragraphs) - 1))
        middle_indices.sort(key=lambda idx: score_paragraph(paragraphs[idx]), reverse=True)

        for idx in middle_indices:
            p_len = len(paragraphs[idx]) + 2
            if total_len + p_len <= max_chars:
                selected_idx.add(idx)
                total_len += p_len

        ordered = [paragraphs[i] for i in sorted(selected_idx)]
        condensed = "\n\n".join(ordered)
        logger.info(f"Condensed large document from {len(cleaned)} to {len(condensed)} chars")
        return condensed

    def build_prompt(self, text: str, topic: str, card_count: int, quiz_count: int) -> str:
        """Prepares normalized/condensed study material for the LLM provider."""
        return self.condense_text_for_llm(text)

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
