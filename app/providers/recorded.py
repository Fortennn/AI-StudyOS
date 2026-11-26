import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from app.providers.base import LLMProvider, ProviderError
from app.telemetry import logger


class RecordedProvider(LLMProvider):
    """
    Deterministic provider wrapper supporting:
    - replay: reads saved JSON response from recordings_dir/<scenario>/<sha256>.json
    - record: calls inner_provider and writes response to recordings_dir/<scenario>/<sha256>.json
    - passthrough: delegates directly to inner_provider without disk I/O
    """

    def __init__(
        self,
        inner_provider: Optional[LLMProvider] = None,
        mode: Literal["replay", "record", "passthrough"] = "replay",
        recordings_dir: str = "tests/recordings",
        scenario: str = "study_kit",
    ):
        self.inner_provider = inner_provider
        self.mode = mode
        self.recordings_dir = Path(recordings_dir)
        self.scenario = scenario

    @staticmethod
    def compute_cache_key(
        scenario: str,
        prompt: str,
        topic: str,
        card_count: int,
        quiz_count: int,
    ) -> str:
        payload = json.dumps(
            {
                "scenario": scenario,
                "topic": (topic or "").strip(),
                "card_count": card_count,
                "quiz_count": quiz_count,
                "prompt": prompt.strip(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _recording_path(self, scenario: str, key: str) -> Path:
        return self.recordings_dir / scenario / f"{key}.json"

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
        key = self.compute_cache_key(self.scenario, prompt, topic, card_count, quiz_count)
        rec_path = self._recording_path(self.scenario, key)

        if self.mode == "replay":
            if not rec_path.exists():
                raise ProviderError(
                    kind="recording_missing",
                    message=f"Записана відповідь для ключа {key} відсутня ({rec_path}).",
                    retryable=False,
                )
            data = json.loads(rec_path.read_text(encoding="utf-8"))
            logger.info(f"RecordedProvider replay hit: {self.scenario}/{key}")
            return data["response"]

        if self.inner_provider is None:
            raise ProviderError(
                kind="provider_unavailable",
                message=f"RecordedProvider в режимі '{self.mode}' потребує внутрішнього провайдера.",
                retryable=False,
            )

        result = await self.inner_provider.generate_structured(
            prompt=prompt,
            topic=topic,
            card_count=card_count,
            quiz_count=quiz_count,
            system_instruction=system_instruction,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        if self.mode == "record":
            rec_path.parent.mkdir(parents=True, exist_ok=True)
            envelope = {
                "key": key,
                "scenario": self.scenario,
                "request": {
                    "topic": topic,
                    "card_count": card_count,
                    "quiz_count": quiz_count,
                },
                "response": result,
            }
            rec_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"RecordedProvider saved recording: {self.scenario}/{key}")

        return result

    async def answer_question(
        self,
        question: str,
        topic: str,
        context_text: str,
        history: List[Dict[str, str]],
    ) -> str:
        key = self.compute_cache_key(
            "chat",
            f"{context_text[:2000]}\nQ:{question}",
            topic,
            0,
            0,
        )
        rec_path = self._recording_path("chat", key)

        if self.mode == "replay":
            if not rec_path.exists():
                raise ProviderError(
                    kind="recording_missing",
                    message=f"Записана відповідь чату для ключа {key} відсутня.",
                    retryable=False,
                )
            data = json.loads(rec_path.read_text(encoding="utf-8"))
            return data["response"]

        if self.inner_provider is None:
            raise ProviderError(
                kind="provider_unavailable",
                message=f"RecordedProvider в режимі '{self.mode}' потребує внутрішнього провайдера.",
                retryable=False,
            )

        answer = await self.inner_provider.answer_question(
            question=question,
            topic=topic,
            context_text=context_text,
            history=history,
        )
        if self.mode == "record":
            rec_path.parent.mkdir(parents=True, exist_ok=True)
            rec_path.write_text(
                json.dumps({"key": key, "scenario": "chat", "response": answer}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return answer
