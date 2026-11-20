import json
import re
from typing import Dict, Any
import httpx
from app.providers.base import LLMProvider, ProviderError
from app.telemetry import logger

class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gemini-2.5-flash", timeout_s: int = 30):
        self.api_key = api_key
        self.model = model or "gemini-2.5-flash"
        self.timeout_s = timeout_s

    async def generate_structured(self, prompt: str, topic: str, card_count: int, quiz_count: int) -> Dict[str, Any]:
        if not self.api_key:
            raise ProviderError(
                kind="missing_api_key",
                message="Не задано API ключ для Gemini (перевірте LLM_API_KEY у .env файлі)",
                retryable=False
            )

        # Standardize model name
        clean_model = self.model
        if clean_model.startswith("models/"):
            clean_model = clean_model.replace("models/", "")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={self.api_key}"

        # Determine counts if auto
        c_req = f"{card_count} карток" if card_count > 0 else "оптимальну кількість карток (від 5 до 20 залежно від насиченості тексту)"
        q_req = f"{quiz_count} питань" if quiz_count > 0 else "оптимальну кількість тестових питань (від 4 до 10)"

        system_instruction = (
            "Ти — елітний академічний тьютор та експерт зі швидкого запам'ятовування (Spaced Repetition & Flashcard Mastery).\n"
            "Твоя мета: глибоко проаналізувати навчальний матеріал і трансформувати його у високоефективний інтерактивний навчальний комплект.\n\n"
            "КРИТИЧНІ ПРАВИЛА ПЕДАГОГІКИ:\n"
            "1. Мова: відповідай тією ж мовою, якою написаний вхідний текст (якщо українська — строго українською).\n"
            "2. Принцип атомарності флеш-карток: кожна картка повинна перевіряти ОДИН конкретний концепт. Уникай розмитих запитань типу 'Розкажи все про...'.\n"
            "3. Структура зворотного боку картки (Back):\n"
            "   - Чітка суть: 1-2 концентрованих речення без 'води'.\n"
            "   - Приклад / Практика: життєва аналогія, фрагмент коду або формула.\n"
            "   - Порада / Пастка: на чому найчастіше помиляються на іспиті.\n"
            "4. Реалістичні категорії для карток (Category): 'Термін', 'Принцип дії', 'Формула / Код', 'Пастка на іспиті', 'Порівняння'.\n"
            "5. Розумні тести (Quiz): варіанти відповідей повинні бути правдоподібними, відображаючи типові помилки студентів (без очевидно безглуздих дистракторів).\n"
            "6. Фактична точність: спирайся виключно на факти та термінологію з наданого тексту, без галюцинацій.\n\n"
            "Поверни строго валідний JSON за такою схемою:\n"
            "{\n"
            '  "summary": {\n'
            '    "tldr": "string (глибоке концентроване резюме теми на 3-4 речення)",\n'
            '    "key_takeaways": ["string (4-6 ключових законів, правил або принципів)"]\n'
            "  },\n"
            '  "flashcards": [\n'
            "    {\n"
            '      "id": 1,\n'
            '      "front": "string (лаконічне, чітке запитання або назва явища)",\n'
            '      "back": "string (визначення + приклад + підводний камінь)",\n'
            '      "category": "string (Термін | Принцип дії | Формула / Код | Пастка на іспиті | Порівняння)"\n'
            "    }\n"
            "  ],\n"
            '  "quiz": [\n'
            "    {\n"
            '      "id": 1,\n'
            '      "question": "string (каверзне концептуальне питання)",\n'
            '      "options": ["string", "string", "string", "string"],\n'
            '      "correct_index": 0,\n'
            '      "explanation": "string (чому ця відповідь правильна і чому інші помилкові)"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Не додавай markdown-теги ```json. Поверни лише сирий JSON."
        )

        user_content = (
            f"Тема: {topic}\n"
            f"Вимога до обсягу: сформувати {c_req} та {q_req}.\n\n"
            f"Вхідний навчальний матеріал:\n{prompt}"
        )

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{system_instruction}\n\n{user_content}"}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json"
            }
        }

        logger.info(f"GeminiProvider calling model '{clean_model}' via Google AI API")

        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                res = await client.post(url, json=payload)

            if res.status_code == 429:
                raise ProviderError(
                    kind="rate_limit",
                    message="Перевищено ліміт запитів Gemini API (Rate Limit 429). Спробуйте знову через кілька секунд.",
                    retryable=True
                )
            elif res.status_code == 403 or res.status_code == 401:
                raise ProviderError(
                    kind="auth_error",
                    message="Помилка авторизації Gemini API. Перевірте правильність вашого LLM_API_KEY.",
                    retryable=False
                )
            elif res.status_code != 200:
                err_text = res.text
                try:
                    err_json = res.json()
                    err_text = err_json.get("error", {}).get("message", err_text)
                except Exception:
                    pass
                raise ProviderError(
                    kind="provider_unavailable",
                    message=f"Gemini API повернув помилку (HTTP {res.status_code}): {err_text}",
                    retryable=res.status_code in [500, 502, 503, 504]
                )

            data = res.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ProviderError(
                    kind="empty_response",
                    message="Модель Gemini не повернула варіантів відповіді (можливо спрацював Safety Filter).",
                    retryable=False
                )

            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise ProviderError(
                    kind="empty_response",
                    message="Порожня відповідь від моделі Gemini.",
                    retryable=False
                )

            raw_text = parts[0].get("text", "").strip()

            # Clean markdown code fences if model enclosed it
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            elif raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            raw_text = raw_text.strip()

            parsed = json.loads(raw_text)
            
            # Log token usage if available
            usage = data.get("usageMetadata", {})
            if usage:
                logger.info(f"Gemini tokens used: prompt={usage.get('promptTokenCount')}, candidates={usage.get('candidatesTokenCount')}, total={usage.get('totalTokenCount')}")

            return parsed

        except httpx.TimeoutException:
            logger.error("Gemini API request timed out")
            raise ProviderError(
                kind="timeout",
                message=f"Час очікування Gemini API перевищено ({self.timeout_s} с).",
                retryable=True
            )
        except json.JSONDecodeError as je:
            logger.error(f"Failed to parse Gemini output as JSON: {je}")
            raise ProviderError(
                kind="invalid_json",
                message=f"Модель повернула некоректний формат JSON: {str(je)}",
                retryable=False
            )
        except ProviderError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in GeminiProvider: {e}")
            raise ProviderError(
                kind="provider_unavailable",
                message=f"Неочікувана помилка при виклику Gemini: {str(e)}",
                retryable=True
            )
