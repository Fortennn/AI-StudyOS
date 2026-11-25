import asyncio
import json
import re
from typing import Dict, Any, Optional
import httpx
from app.providers.base import LLMProvider, ProviderError
from app.telemetry import logger

_SHARED_CLIENT: Optional[httpx.AsyncClient] = None
_SHARED_LOOP: Optional[asyncio.AbstractEventLoop] = None

def _get_client(timeout_s: int) -> httpx.AsyncClient:
    global _SHARED_CLIENT, _SHARED_LOOP
    loop = asyncio.get_running_loop()
    if _SHARED_CLIENT is None or _SHARED_CLIENT.is_closed or _SHARED_LOOP is not loop:
        _SHARED_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
        _SHARED_LOOP = loop
    return _SHARED_CLIENT

class GeminiProvider(LLMProvider):
    FALLBACK_MODELS = [
        "gemini-3-flash-preview",
        "gemini-3.5-flash-lite",
        "gemini-flash-latest",
    ]

    def __init__(self, api_key: str, model: str = "gemini-3-flash-preview", timeout_s: int = 35):
        self.api_key = api_key
        # Map deprecated or chronically slow/hanging models to fast stable ones
        m = (model or "gemini-3-flash-preview").replace("models/", "")
        if m in ["gemini-2.5-flash", "gemini-2.5-flash-preview", "gemini-1.5-flash", "gemini-3.5-flash"]:
            m = "gemini-3-flash-preview"
        self.model = m
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
            "Ти — академічний тьютор. Трансформуй конспект у структурований навчальний комплект (JSON).\n"
            "Правила:\n"
            "1. Мова відповіді = мова тексту.\n"
            "2. Summary (адаптуй структуру й обсяг під складність та щільність лекції, без води та шаблонних фраз!):\n"
            "   - tldr: обери оптимальну глибину (для короткої нотатки 2-3 концентровані речення, для насиченої лекції — розгорнуте концептуальне резюме з головними означеннями, умовами та формулою у LaTeX).\n"
            "   - key_takeaways: самостійно визнач оптимальну кількість тез (від 3 до 12+ залежно від обсягу матеріалу) так, щоб охопити всі ключові формули ($...$ / $$...$$), теореми, алгоритми, граничні випадки та пастки без повторів.\n"
            "3. Картки: атомарні запитання; back = суть (1-2 речення) + короткий приклад/формула + типова пастка.\n"
            "4. Категорії карток: 'Термін' | 'Принцип дії' | 'Формула / Код' | 'Пастка на іспиті' | 'Порівняння'.\n"
            "5. Квіз: 4 правдоподібні варіанти та коротке пояснення.\n"
            "6. Усі математичні формули, степені та символи оформлюй у LaTeX: $...$ або $$...$$.\n"
            "7. Лише факти з тексту, без вигадок.\n\n"
            "Схема JSON (без markdown):\n"
            '{"summary":{"tldr":"string (адаптивне змістовне резюме з формулами)","key_takeaways":["string (оптимальна кількість конкретних фактів, формул і правил)"]},'
            '"flashcards":[{"id":1,"front":"string","back":"string","category":"Термін"}],'
            '"quiz":[{"id":1,"question":"string","options":["A","B","C","D"],"correct_index":0,"explanation":"string"}]}'
        )

        user_content = (
            f"Тема: {topic}. Обсяг: {c_req}, {q_req}.\n"
            f"Матеріал:\n{prompt}"
        )

        payload = {
            "systemInstruction": {
                "parts": [{"text": system_instruction}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_content}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 6144,
                "responseMimeType": "application/json",
                "thinkingConfig": {
                    "thinkingBudget": 0
                }
            }
        }

        # Build unique candidate model list (primary + fallbacks)
        candidate_models = []
        for m in [clean_model, *self.FALLBACK_MODELS]:
            if m and m not in candidate_models:
                candidate_models.append(m)

        client = _get_client(self.timeout_s)
        per_attempt_timeout = min(float(self.timeout_s), 14.0)
        last_error: Optional[ProviderError] = None

        for idx, target_model in enumerate(candidate_models):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={self.api_key}"
            req_payload = json.loads(json.dumps(payload))
            if "lite" in target_model:
                req_payload["generationConfig"].pop("thinkingConfig", None)

            logger.info(f"GeminiProvider calling model '{target_model}' (attempt {idx + 1}/{len(candidate_models)}, timeout {per_attempt_timeout}s)")

            try:
                res = await client.post(url, json=req_payload, timeout=per_attempt_timeout)

                if res.status_code == 400 and "thinkingConfig" in req_payload.get("generationConfig", {}):
                    logger.warning(f"Model '{target_model}' rejected thinkingConfig (HTTP 400), retrying without it")
                    req_payload["generationConfig"].pop("thinkingConfig", None)
                    res = await client.post(url, json=req_payload, timeout=per_attempt_timeout)

                if res.status_code in (401, 403):
                    raise ProviderError(
                        kind="auth_error",
                        message="Помилка авторизації Gemini API. Перевірте правильність вашого LLM_API_KEY.",
                        retryable=False
                    )
                if res.status_code == 429:
                    last_error = ProviderError(
                        kind="rate_limit",
                        message="Перевищено ліміт запитів Gemini API (Rate Limit 429). Спробуйте знову через кілька секунд.",
                        retryable=True
                    )
                    continue
                if res.status_code != 200:
                    err_text = res.text
                    try:
                        err_json = res.json()
                        err_text = err_json.get("error", {}).get("message", err_text)
                    except Exception:
                        pass
                    last_error = ProviderError(
                        kind="provider_unavailable",
                        message=f"Gemini API повернув помилку (HTTP {res.status_code}): {err_text}",
                        retryable=res.status_code in [500, 502, 503, 504]
                    )
                    if res.status_code in (404, 500, 502, 503, 504):
                        logger.warning(f"Model '{target_model}' failed with HTTP {res.status_code}, trying fallback...")
                        continue
                    raise last_error

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
                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:]
                elif raw_text.startswith("```"):
                    raw_text = raw_text[3:]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]
                raw_text = raw_text.strip()

                parsed = json.loads(raw_text)

                usage = data.get("usageMetadata", {})
                if usage:
                    logger.info(f"Gemini '{target_model}' tokens: prompt={usage.get('promptTokenCount')}, candidates={usage.get('candidatesTokenCount')}, total={usage.get('totalTokenCount')}")
                return parsed

            except httpx.TimeoutException:
                logger.warning(f"Model '{target_model}' timed out after {per_attempt_timeout}s, trying fallback...")
                last_error = ProviderError(
                    kind="timeout",
                    message=f"Час очікування Gemini API перевищено ({self.timeout_s} с).",
                    retryable=True
                )
                continue
            except json.JSONDecodeError as je:
                logger.error(f"Failed to parse Gemini output as JSON ({target_model}): {je}")
                last_error = ProviderError(
                    kind="invalid_json",
                    message=f"Модель повернула некоректний формат JSON: {str(je)}",
                    retryable=True
                )
                continue
            except ProviderError:
                raise
            except Exception as e:
                logger.error(f"Unexpected error in GeminiProvider ({target_model}): {e}")
                last_error = ProviderError(
                    kind="provider_unavailable",
                    message=f"Неочікувана помилка при виклику Gemini: {str(e)}",
                    retryable=True
                )
                continue

        if last_error:
            raise last_error
        raise ProviderError(
            kind="provider_unavailable",
            message="Усі доступні моделі Gemini тимчасово недоступні.",
            retryable=True
        )
