import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

from app.providers.base import ProviderError
from app.telemetry import logger


class DailyBudgetTracker:
    """Tracks daily LLM call count and estimated USD spend in a local JSON file."""

    def __init__(
        self,
        budget_file: str = "data/budget.json",
        max_daily_usd: float = 5.0,
        max_daily_calls: int = 500,
    ):
        self.budget_file = Path(budget_file)
        self.max_daily_usd = max_daily_usd
        self.max_daily_calls = max_daily_calls
        self._lock = threading.Lock()

    @staticmethod
    def _today_utc() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load_state(self) -> Dict[str, Any]:
        today = self._today_utc()
        if not self.budget_file.exists():
            return {"date": today, "calls": 0, "estimated_usd": 0.0}
        try:
            data = json.loads(self.budget_file.read_text(encoding="utf-8"))
            if data.get("date") != today:
                return {"date": today, "calls": 0, "estimated_usd": 0.0}
            return {
                "date": today,
                "calls": int(data.get("calls", 0)),
                "estimated_usd": float(data.get("estimated_usd", 0.0)),
            }
        except Exception:
            return {"date": today, "calls": 0, "estimated_usd": 0.0}

    def _save_state(self, state: Dict[str, Any]) -> None:
        self.budget_file.parent.mkdir(parents=True, exist_ok=True)
        self.budget_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def get_usage(self) -> Dict[str, Any]:
        with self._lock:
            return self._load_state()

    def check_and_reserve(self, estimated_cost_usd: float = 0.002) -> Dict[str, Any]:
        """Checks daily call and USD limits and reserves one call if within budget."""
        with self._lock:
            state = self._load_state()
            if self.max_daily_calls > 0 and state["calls"] >= self.max_daily_calls:
                logger.warning(f"Daily call budget exceeded: {state['calls']}/{self.max_daily_calls}")
                raise ProviderError(
                    kind="budget",
                    message=f"Денний ліміт викликів AI вичерпано ({state['calls']}/{self.max_daily_calls}).",
                    retryable=False,
                )
            if self.max_daily_usd > 0 and (state["estimated_usd"] + estimated_cost_usd) > self.max_daily_usd:
                logger.warning(
                    f"Daily USD budget exceeded: ${state['estimated_usd']:.4f}/${self.max_daily_usd:.2f}"
                )
                raise ProviderError(
                    kind="budget",
                    message=f"Денний бюджет витрат на AI вичерпано (${self.max_daily_usd:.2f}).",
                    retryable=False,
                )

            state["calls"] += 1
            state["estimated_usd"] = round(state["estimated_usd"] + estimated_cost_usd, 6)
            self._save_state(state)
            return state
