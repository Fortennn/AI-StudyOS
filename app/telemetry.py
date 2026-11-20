import logging
import os
import contextvars
from datetime import datetime

# Context variable for tracing request_id across async and sync layers
request_id_ctx = contextvars.ContextVar("request_id", default="system")

def get_current_request_id() -> str:
    return request_id_ctx.get()

class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = get_current_request_id()
        return True

def setup_logger(log_file: str = "logs/app.log", log_level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("study_copilot")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    
    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [req:%(request_id)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        
        # Console handler
        ch = logging.StreamHandler()
        ch.setFormatter(formatter)
        ch.addFilter(RequestIdFilter())
        logger.addHandler(ch)
        
        # File handler
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(formatter)
            fh.addFilter(RequestIdFilter())
            logger.addHandler(fh)
        except Exception:
            pass # fallback to console only if file creation fails
            
    return logger

logger = setup_logger()
