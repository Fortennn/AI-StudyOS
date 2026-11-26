import os
from typing import Literal
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

# Load environment from .env file
load_dotenv()

class Settings(BaseModel):
    app_env: str = Field(default="development")
    app_version: str = Field(default="0.1.0")
    app_port: int = Field(default=8000)

    # Provider config
    llm_provider: Literal["mock", "gemini", "recorded"] = Field(default="mock")
    llm_model: str = Field(default="gemini-3.5-flash")
    llm_api_key: str = Field(default="")
    llm_timeout_s: int = Field(default=60)

    # Recorded provider config
    record_mode: Literal["replay", "record", "passthrough"] = Field(default="replay")
    recordings_dir: str = Field(default="tests/recordings")

    # Daily budget limits
    budget_daily_usd: float = Field(default=5.0)
    budget_daily_calls: int = Field(default=500)
    budget_file: str = Field(default="data/budget.json")

    # Mock testing options
    mock_failure_rate: float = Field(default=0.0)
    mock_failure_kind: Literal["none", "timeout", "rate_limit", "unavailable", "invalid_json", "auth", "mid_stream"] = Field(default="none")
    mock_delay_ms: int = Field(default=50)

    # Reliability & Caching (PR18)
    ai_enabled: bool = Field(default=True)
    llm_total_deadline_s: int = Field(default=45)
    llm_max_retries: int = Field(default=2)
    llm_max_concurrency: int = Field(default=5)
    user_rate_limit_rpm: int = Field(default=20)
    breaker_threshold: int = Field(default=3)
    breaker_recovery_s: int = Field(default=30)
    cache_ttl_s: int = Field(default=3600)

    # Storage and logs
    data_dir: str = Field(default="data")
    db_path: str = Field(default="data/study.db")
    log_file: str = Field(default="logs/app.log")
    log_level: str = Field(default="INFO")

    # Deployment & Public Access Guardrails (PR20)
    demo_token: str = Field(default="")
    allowed_origins: str = Field(default="")
    git_commit: str = Field(default="local")

    @field_validator("llm_api_key")
    @classmethod
    def validate_api_key_when_real(cls, v: str, info):
        provider = info.data.get("llm_provider", "mock")
        if provider == "gemini" and not v.strip():
            raise ValueError("LLM_API_KEY is required and cannot be empty when LLM_PROVIDER is 'gemini'")
        return v

def get_settings() -> Settings:
    """Reads settings from environment variables with sensible defaults."""
    data_dir = os.getenv("DATA_DIR", "data")
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        app_version=os.getenv("APP_VERSION", "0.1.0"),
        app_port=int(os.getenv("PORT", os.getenv("APP_PORT", "8000"))),
        llm_provider=os.getenv("LLM_PROVIDER", "mock").strip().lower(),
        llm_model=os.getenv("LLM_MODEL", "mock-study-flash").strip(),
        llm_api_key=os.getenv("LLM_API_KEY", "").strip(),
        llm_timeout_s=int(os.getenv("LLM_TIMEOUT_S", "15")),
        mock_failure_rate=float(os.getenv("MOCK_FAILURE_RATE", "0.0")),
        mock_failure_kind=os.getenv("MOCK_FAILURE_KIND", "none").lower(),
        mock_delay_ms=int(os.getenv("MOCK_DELAY_MS", "50")),
        data_dir=data_dir,
        db_path=os.getenv("DB_PATH", f"{data_dir}/study.db"),
        log_file=os.getenv("LOG_FILE", "logs/app.log"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        record_mode=os.getenv("RECORD_MODE", "replay").lower(),
        recordings_dir=os.getenv("RECORDINGS_DIR", "tests/recordings"),
        budget_daily_usd=float(os.getenv("BUDGET_DAILY_USD", "5.0")),
        budget_daily_calls=int(os.getenv("BUDGET_DAILY_CALLS", "500")),
        budget_file=os.getenv("BUDGET_FILE", f"{data_dir}/budget.json"),
        ai_enabled=os.getenv("AI_ENABLED", "1") not in ("0", "false", "False", "no"),
        llm_total_deadline_s=int(os.getenv("LLM_TOTAL_DEADLINE_S", "45")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
        llm_max_concurrency=int(os.getenv("LLM_MAX_CONCURRENCY", "5")),
        user_rate_limit_rpm=int(os.getenv("USER_RATE_LIMIT_RPM", "20")),
        breaker_threshold=int(os.getenv("BREAKER_THRESHOLD", "3")),
        breaker_recovery_s=int(os.getenv("BREAKER_RECOVERY_S", "30")),
        cache_ttl_s=int(os.getenv("CACHE_TTL_S", "3600")),
        demo_token=os.getenv("DEMO_TOKEN", ""),
        allowed_origins=os.getenv("ALLOWED_ORIGINS", ""),
        git_commit=os.getenv("RENDER_GIT_COMMIT", os.getenv("GIT_COMMIT", "local")),
    )
