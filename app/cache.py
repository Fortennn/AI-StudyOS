import copy
import hashlib
import json
import threading
import time
from typing import Any, Dict, Optional, Tuple


class StudyResponseCache:
    """In-memory TTL cache keyed by model, prompt/schema versions, parameters, content, and owner."""

    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 256):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._store: Dict[str, Tuple[float, Dict[str, Any], str]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def make_key(
        model: str,
        prompt_version: str,
        schema_version: str,
        topic: str,
        material: str,
        card_count: int,
        quiz_count: int,
        user_id: Optional[str],
    ) -> str:
        payload = json.dumps(
            {
                "model": model,
                "prompt_version": prompt_version,
                "schema_version": schema_version,
                "topic": (topic or "").strip(),
                "material": material.strip(),
                "card_count": card_count,
                "quiz_count": quiz_count,
                "owner": user_id or "anon",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            expires_at, value, _ = entry
            if now >= expires_at:
                del self._store[key]
                return None
            return copy.deepcopy(value)

    def set(self, key: str, value: Dict[str, Any], owner: Optional[str] = None) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._store) >= self.max_entries:
                oldest_key = min(self._store, key=lambda k: self._store[k][0])
                del self._store[oldest_key]
            self._store[key] = (now + self.ttl_seconds, copy.deepcopy(value), owner or "anon")

    def invalidate_owner(self, owner: Optional[str]) -> int:
        target = owner or "anon"
        with self._lock:
            keys = [k for k, (_, _, o) in self._store.items() if o == target]
            for k in keys:
                del self._store[k]
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
