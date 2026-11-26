import math
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, Optional

from app.providers.base import ProviderError
from app.telemetry import logger, log_event


class CircuitBreaker:
    """
    Per-dependency circuit breaker (closed -> open -> half_open).
    Counts only availability/transient faults (timeout, provider_unavailable, rate_limit).
    Ignores auth, invalid_input, invalid_output, and budget errors.
    """

    COUNTED_KINDS = frozenset({"timeout", "provider_unavailable", "rate_limit"})

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        window_seconds: float = 60.0,
        recovery_seconds: float = 30.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.recovery_seconds = recovery_seconds
        self._state = "closed"
        self._opened_at: Optional[float] = None
        self._since_iso: Optional[str] = None
        self._failures: Deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()

    def allow_request(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if self._state == "closed":
                return True
            if self._state == "open":
                if self._opened_at is not None and (now - self._opened_at) >= self.recovery_seconds:
                    self._state = "half_open"
                    logger.warning(f"CircuitBreaker '{self.name}' transitioned open -> half_open")
                    log_event(level="WARNING", stage="circuit_breaker", provider=self.name, status="half_open")
                    return True
                return False
            # half_open allows single probe
            return True

    def record_success(self) -> None:
        with self._lock:
            prev = self._state
            self._failures.clear()
            self._state = "closed"
            self._opened_at = None
            self._since_iso = None
            if prev != "closed":
                logger.warning(f"CircuitBreaker '{self.name}' transitioned {prev} -> closed")
                log_event(level="WARNING", stage="circuit_breaker", provider=self.name, status="closed")

    def record_failure(self, kind: str) -> None:
        if kind not in self.COUNTED_KINDS:
            return
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._failures.append(now)
            if self._state == "half_open" or len(self._failures) >= self.failure_threshold:
                prev = self._state
                self._state = "open"
                self._opened_at = now
                self._since_iso = datetime.now(timezone.utc).isoformat()
                if prev != "open":
                    logger.warning(f"CircuitBreaker '{self.name}' opened after {len(self._failures)} failures ({kind})")
                    log_event(level="WARNING", stage="circuit_breaker", provider=self.name, status="open", kind=kind)

    def status(self) -> Dict[str, object]:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            return {
                "name": self.name,
                "breaker": self._state,
                "since": self._since_iso,
                "failures_in_window": len(self._failures),
            }


class UserRateLimiter:
    """Sliding-window per-user rate limiter returning 429 + Retry-After."""

    def __init__(self, max_requests: int = 20, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client_key: str) -> None:
        if self.max_requests <= 0:
            return
        now = time.monotonic()
        with self._lock:
            q = self._buckets.setdefault(client_key, deque())
            cutoff = now - self.window_seconds
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_requests:
                retry_after = max(1, int(math.ceil(q[0] + self.window_seconds - now)))
                raise ProviderError(
                    kind="user_rate_limit",
                    message=f"Забагато запитів від користувача. Спробуйте через {retry_after} с.",
                    retryable=True,
                    retry_after=retry_after,
                )
            q.append(now)
