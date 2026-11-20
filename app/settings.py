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
    llm_provider: Literal["mock", "gemini"] = Field(default="mock")
    llm_model: str = Field(default="mock-study-flash")
    llm_api_key: str = Field(default="")
    llm_timeout_s: int = Field(default=15)

    # Mock testing options
    mock_failure_rate: float = Field(default=0.0)
    mock_failure_kind: Literal["none", "timeout", "rate_limit", "unavailable", "invalid_json"] = Field(default="none")
    mock_delay_ms: int = Field(default=50)

    # Storage and logs
    db_path: str = Field(default="data/study.db")
    log_file: str = Field(default="logs/app.log")
    log_level: str = Field(default="INFO")

    @field_validator("llm_api_key")
    @classmethod
    def validate_api_key_when_real(cls, v: str, info):
        provider = info.data.get("llm_provider", "mock")
        if provider == "gemini" and not v.strip():
            raise ValueError("LLM_API_KEY is required and cannot be empty when LLM_PROVIDER is 'gemini'")
        return v

def get_settings() -> Settings:
    """Reads settings from environment variables with sensible defaults."""
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        app_version=os.getenv("APP_VERSION", "0.1.0"),
        app_port=int(os.getenv("APP_PORT", "8000")),
        llm_provider=os.getenv("LLM_PROVIDER", "mock").lower(),
        llm_model=os.getenv("LLM_MODEL", "mock-study-flash"),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_timeout_s=int(os.getenv("LLM_TIMEOUT_S", "15")),
        mock_failure_rate=float(os.getenv("MOCK_FAILURE_RATE", "0.0")),
        mock_failure_kind=os.getenv("MOCK_FAILURE_KIND", "none").lower(),
        mock_delay_ms=int(os.getenv("MOCK_DELAY_MS", "50")),
        db_path=os.getenv("DB_PATH", "data/study.db"),
        log_file=os.getenv("LOG_FILE", "logs/app.log"),
        log_level=os.getenv("LOG_LEVEL", "INFO")
    )
