import asyncio
import json
import re
from typing import Dict, Any, Optional, List
import httpx
from app.providers.base import LLMProvider, ProviderError
from app.prompts import render_study_prompt
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
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-flash-latest",
        "gemini-flash-lite-latest",
        "gemini-3.7-flash",
    ]

    def __init__(self, api_key: str, model: str = "gemini-3-flash-preview", timeout_s: int = 35):
        self.api_key = (api_key or "").strip()
        # Map deprecated or chronically slow/hanging models to fast stable ones
        m = (model or "gemini-3-flash-preview").strip().replace("models/", "")
        if m in ["gemini-2.5-flash", "gemini-2.5-flash-preview", "gemini-1.5-flash", "gemini-3.5-flash"]:
            m = "gemini-3-flash-preview"
        self.model = m
        self.timeout_s = timeout_s

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
        if not self.api_key:
            raise ProviderError(
                kind="missing_api_key",
                message="Не задано API ключ для Gemini (перевірте LLM_API_KEY у .env файлі)",
                retryable=False
            )

        clean_model = self.model.replace("models/", "")

        if not system_instruction:
            system_instruction, user_content, meta = render_study_prompt(
                material=prompt,
                topic=topic,
                card_count=card_count,
                quiz_count=quiz_count,
            )
            temperature = meta.get("temperature", temperature)
            max_tokens = meta.get("max_tokens", max_tokens)
        else:
            user_content = prompt

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
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
                "thinkingConfig": {
                    "thinkingBudget": 0
                }
            }
        }
        return await self._call_with_fallback(payload, clean_model=clean_model, parse_json=True)

    async def answer_question(self, question: str, topic: str, context_text: str, history: List[Dict[str, str]]) -> str:
        if not self.api_key:
            raise ProviderError(
                kind="missing_api_key",
                message="Не задано API ключ для Gemini (перевірте LLM_API_KEY у .env файлі)",
                retryable=False
            )

        clean_model = self.model.replace("models/", "")
        system_instruction = (
            "Ти — персональний академічний тьютор. Відповідай на запитання студента по наданому конспекту.\n"
            "Правила:\n"
            "1. Мова відповіді = мова запитання/конспекту.\n"
            "2. Відповідай чітко, по суті й зрозуміло (можна з короткими списками та прикладами).\n"
            "3. Формули оформлюй у LaTeX: $...$ або $$...$$.\n"
            "4. Спирайся на матеріал конспекту; якщо чогось у конспекті немає, чесно зазнач це."
        )

        contents = [
            {
                "role": "user",
                "parts": [{"text": f"Тема: {topic}\nКонтекст лекції:\n{context_text[:14000]}"}]
            },
            {
                "role": "model",
                "parts": [{"text": "Зрозумів, я ознайомився з конспектом. Яке запитання?"}]
            }
        ]
        for msg in history[-6:]:
            role = "model" if msg.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": msg.get("text", "")}]})
        contents.append({"role": "user", "parts": [{"text": question}]})

        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 8192,
                "thinkingConfig": {"thinkingBudget": 0}
            }
        }
        return await self._call_with_fallback(payload, clean_model=clean_model, parse_json=False)

    async def _call_with_fallback(self, payload: Dict[str, Any], clean_model: str, parse_json: bool = True) -> Any:
        candidate_models = []
        for m in [clean_model, *self.FALLBACK_MODELS]:
            if m and m not in candidate_models:
                candidate_models.append(m)

        client = _get_client(self.timeout_s)
        per_attempt_timeout = min(float(self.timeout_s), 20.0)
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
                        message=(
                            "Сервери Gemini тимчасово перевантажені (HTTP 503). Спробуйте ще раз через кілька секунд."
                            if res.status_code == 503
                            else f"Gemini API повернув помилку (HTTP {res.status_code}): {err_text}"
                        ),
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
                usage = data.get("usageMetadata", {})
                if usage:
                    logger.info(f"Gemini '{target_model}' tokens: prompt={usage.get('promptTokenCount')}, candidates={usage.get('candidatesTokenCount')}, total={usage.get('totalTokenCount')}")

                if not parse_json:
                    return raw_text

                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:]
                elif raw_text.startswith("```"):
                    raw_text = raw_text[3:]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]
                raw_text = raw_text.strip()

                return json.loads(raw_text)

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
