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

        system_instruction = (
            "You are a master academic tutor and educational content generator. "
            "Analyze the given lecture/study notes and return a strictly valid JSON object matching this schema:\n"
            "{\n"
            '  "summary": {\n'
            '    "tldr": "string (concise 3-4 sentence overview in Ukrainian)",\n'
            '    "key_takeaways": ["string (4-6 key principles or formulas in Ukrainian)"]\n'
            "  },\n"
            '  "flashcards": [\n'
            '    {\n'
            '      "id": 1,\n'
            '      "front": "string (clear question or term in Ukrainian)",\n'
            '      "back": "string (detailed explanation and definition in Ukrainian)",\n'
            '      "category": "string (e.g. Теорія, Практика, Оптимізація)"\n'
            '    }\n'
            "  ],\n"
            '  "quiz": [\n'
            '    {\n'
            '      "id": 1,\n'
            '      "question": "string (multiple choice question in Ukrainian)",\n'
            '      "options": ["string", "string", "string", "string"],\n'
            '      "correct_index": 0,\n'
            '      "explanation": "string (why this answer is correct in Ukrainian)"\n'
            '    }\n'
            "  ]\n"
            "}\n"
            "Do not include markdown fences like ```json. Return raw JSON."
        )

        user_content = (
            f"Тема: {topic}\n"
            f"Кількість карток: {card_count}\n"
            f"Кількість тестових запитань: {quiz_count}\n\n"
            f"Вхідний матеріал:\n{prompt}"
        )

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{system_instruction}\n\n{user_content}"}]
                }
            ],
            "generationConfig": {
                "temperature": 0.3,
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
