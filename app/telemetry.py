import contextvars
import json
import logging
import os
import re
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict

# Context variable for tracing request_id across async and sync layers
request_id_ctx = contextvars.ContextVar("request_id", default="system")

_SECRET_MASK_RE = re.compile(r"(AIza[0-9A-Za-z_\-]{16,}|sk-[0-9A-Za-z]{16,})")

ALLOWED_JSONL_FIELDS = frozenset(
    {
        "timestamp",
        "level",
        "request_id",
        "stage",
        "provider",
        "provider_kind",
        "model",
        "prompt_version",
        "schema_version",
        "attempt",
        "duration_ms",
        "ttft_ms",
        "status",
        "kind",
        "cached",
        "fallback",
        "tokens",
        "cost",
    }
)


def get_current_request_id() -> str:
    return request_id_ctx.get()


def _mask_secrets(value: Any) -> Any:
    if isinstance(value, str):
        return _SECRET_MASK_RE.sub("***REDACTED***", value)
    return value


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = get_current_request_id()
        return True


class JsonLinesFormatter(logging.Formatter):
    """Formats structured event records using only allowlisted fields."""

    def format(self, record: logging.LogRecord) -> str:
        raw_event: Dict[str, Any] = getattr(record, "event_data", {}) or {}
        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "request_id": get_current_request_id(),
        }
        for key, val in raw_event.items():
            if key in ALLOWED_JSONL_FIELDS and val is not None:
                payload[key] = _mask_secrets(val)
        return json.dumps(payload, ensure_ascii=False)


def setup_logger(log_file: str = "logs/app.log", log_level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("study_copilot")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [req:%(request_id)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        ch.addFilter(RequestIdFilter())
        logger.addHandler(ch)

        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            fh = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
            fh.setFormatter(formatter)
            fh.addFilter(RequestIdFilter())
            logger.addHandler(fh)
        except Exception:
            pass

    return logger


def setup_jsonl_logger(jsonl_file: str = "logs/app.jsonl") -> logging.Logger:
    jlogger = logging.getLogger("study_copilot_jsonl")
    jlogger.setLevel(logging.INFO)
    jlogger.propagate = False

    if not jlogger.handlers:
        try:
            os.makedirs(os.path.dirname(jsonl_file), exist_ok=True)
            rfh = RotatingFileHandler(jsonl_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
            rfh.setFormatter(JsonLinesFormatter())
            jlogger.addHandler(rfh)
        except Exception:
            pass

    return jlogger


logger = setup_logger()
jsonl_logger = setup_jsonl_logger()


def log_event(level: str = "INFO", **fields: Any) -> None:
    """Emits a structured JSONL event restricted strictly to ALLOWED_JSONL_FIELDS."""
    lvl = getattr(logging, level.upper(), logging.INFO)
    jsonl_logger.log(lvl, fields.get("stage", "event"), extra={"event_data": fields})
