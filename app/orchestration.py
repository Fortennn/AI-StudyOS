import re
from typing import Dict, Any, List, Optional, Tuple
from app.models import StudySummary, FlashcardItem, QuizItem, TrueFalseItem
from app.prompts import render_study_prompt
from app.telemetry import logger
from app.providers.base import ProviderError

_PLACEHOLDER_PATTERNS = re.compile(
    r"^(todo|tbd|n/a|none|null|string|\.\.\.|lorem ipsum.*)$",
    re.IGNORECASE,
)
_SECRET_LEAK_PATTERN = re.compile(r"(AIza[0-9A-Za-z_\-]{20,}|sk-[0-9A-Za-z]{20,}|ref-7f3a91)")

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

    def build_prompt_bundle(
        self,
        text: str,
        topic: str,
        card_count: int,
        quiz_count: int,
        validation_feedback: Optional[str] = None,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """Condenses material and renders versioned prompt template with untrusted delimiters."""
        condensed = self.condense_text_for_llm(text)
        return render_study_prompt(
            material=condensed,
            topic=topic,
            card_count=card_count,
            quiz_count=quiz_count,
            validation_feedback=validation_feedback,
        )

    @staticmethod
    def _is_placeholder(value: str) -> bool:
        cleaned = (value or "").strip()
        return not cleaned or bool(_PLACEHOLDER_PATTERNS.match(cleaned))

    def validate_and_parse_output(
        self,
        raw_data: Dict[str, Any],
        expected_card_count: int = 0,
        expected_quiz_count: int = 0,
    ) -> Dict[str, Any]:
        """3-layer validation (Schema -> Rules -> Content) on provider output."""
        logger.info("Orchestrator running 3-layer validation on structured output")

        if not isinstance(raw_data, dict):
            raise ProviderError(
                kind="invalid_output",
                message="[schema] Відповідь моделі має бути JSON-об'єктом.",
                retryable=False,
            )

        # Layer 1: Schema validation
        try:
            summary = StudySummary(**raw_data.get("summary", {}))
            cards = [FlashcardItem(**item) for item in raw_data.get("flashcards", [])]
            quiz = [QuizItem(**item) for item in raw_data.get("quiz", [])]
            true_false = [TrueFalseItem(**item) for item in raw_data.get("true_false", [])]
        except Exception as e:
            logger.error(f"Layer 1 (schema) validation failed: {e}")
            raise ProviderError(
                kind="invalid_output",
                message=f"[schema] Вихідні дані моделі не відповідають схемі: {e}",
                retryable=False,
            )

        # Layer 2: Business & structural rules
        if summary.insufficient_data:
            if not summary.missing_info or len(summary.missing_info.strip()) < 5:
                raise ProviderError(
                    kind="invalid_output",
                    message="[rules] При insufficient_data=true необхідно вказати summary.missing_info.",
                    retryable=False,
                )
        else:
            if len(summary.tldr.strip()) < 15:
                raise ProviderError(
                    kind="invalid_output",
                    message="[rules] Поле summary.tldr занадто коротке (< 15 символів).",
                    retryable=False,
                )
            if len(summary.key_takeaways) < 1:
                raise ProviderError(
                    kind="invalid_output",
                    message="[rules] Поле summary.key_takeaways не може бути порожнім.",
                    retryable=False,
                )
            if expected_card_count > 0 and len(cards) != expected_card_count:
                raise ProviderError(
                    kind="invalid_output",
                    message=f"[rules] Очікувалось рівно {expected_card_count} карток, отримано {len(cards)}.",
                    retryable=False,
                )
            if expected_quiz_count > 0 and len(quiz) != expected_quiz_count:
                raise ProviderError(
                    kind="invalid_output",
                    message=f"[rules] Очікувалось рівно {expected_quiz_count} питань тесту, отримано {len(quiz)}.",
                    retryable=False,
                )
            if len({c.id for c in cards}) != len(cards):
                raise ProviderError(
                    kind="invalid_output",
                    message="[rules] Знайдено дублікати id у flashcards.",
                    retryable=False,
                )
            if len({q.id for q in quiz}) != len(quiz):
                raise ProviderError(
                    kind="invalid_output",
                    message="[rules] Знайдено дублікати id у quiz.",
                    retryable=False,
                )
            for q in quiz:
                normalized_opts = [o.strip().lower() for o in q.options if o and o.strip()]
                if len(normalized_opts) != 4 or len(set(normalized_opts)) < 4:
                    raise ProviderError(
                        kind="invalid_output",
                        message=f"[rules] Питання id={q.id} має містити 4 різні непорожні варіанти відповіді.",
                        retryable=False,
                    )

        # Layer 3: Content sanity & secret leak guard
        texts_to_check = [summary.tldr] + list(summary.key_takeaways)
        if not summary.insufficient_data:
            if self._is_placeholder(summary.tldr):
                raise ProviderError(
                    kind="invalid_output",
                    message="[content] summary.tldr містить шаблонну заглушку замість змісту.",
                    retryable=False,
                )
            for c in cards:
                if self._is_placeholder(c.front) or self._is_placeholder(c.back):
                    raise ProviderError(
                        kind="invalid_output",
                        message=f"[content] Картка id={c.id} містить порожній або шаблонний текст.",
                        retryable=False,
                    )
                texts_to_check.extend([c.front, c.back])
            for q in quiz:
                if self._is_placeholder(q.question) or self._is_placeholder(q.explanation):
                    raise ProviderError(
                        kind="invalid_output",
                        message=f"[content] Питання id={q.id} містить порожній або шаблонний текст.",
                        retryable=False,
                    )
                texts_to_check.extend([q.question, q.explanation] + list(q.options))

        joined_text = "\n".join(texts_to_check)
        if _SECRET_LEAK_PATTERN.search(joined_text):
            raise ProviderError(
                kind="invalid_output",
                message="[content] Виявлено потенційний витік API-ключа у відповіді моделі.",
                retryable=False,
            )

        return {
            "summary": summary,
            "flashcards": cards,
            "quiz": quiz,
            "true_false": true_false,
        }
