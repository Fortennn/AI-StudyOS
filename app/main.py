import time
import uuid
from typing import List
from fastapi import FastAPI, Request, status, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError

from app.settings import get_settings
from app.models import StudyRequest, StudyKitResponse, HealthResponse, ErrorResponse, ErrorDetail, SessionRecord
from app.telemetry import logger, request_id_ctx
from app.storage import Storage
from app.gateway import LLMGateway
from app.orchestration import StudyOrchestrator
from app.service import StudyService
from app.providers.base import ProviderError
from app.extractor import extract_text_from_file

# Initialize core dependencies
settings = get_settings()
storage = Storage(db_path=settings.db_path)
gateway = LLMGateway(settings=settings)
orchestrator = StudyOrchestrator()
study_service = StudyService(gateway=gateway, orchestrator=orchestrator, storage=storage)

START_TIME = time.time()

app = FastAPI(
    title="AI Study Copilot API",
    version=settings.app_version,
    description="Інтелектуальний помічник для навчання (Конспект -> Самарі, 3D Картки, Тести)"
)

# Request ID & Logging Middleware
@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:8]}"
    token = request_id_ctx.set(req_id)
    
    start = time.time()
    logger.info(f"Incoming HTTP {request.method} {request.url.path}")
    
    try:
        response = await call_next(request)
        duration = round((time.time() - start) * 1000, 2)
        response.headers["X-Request-ID"] = req_id
        logger.info(f"Completed HTTP {request.method} {request.url.path} with status {response.status_code} in {duration}ms")
        return response
    finally:
        request_id_ctx.reset(token)

# Unified Error Handlers
@app.exception_handler(ProviderError)
async def provider_error_handler(request: Request, exc: ProviderError):
    req_id = request_id_ctx.get()
    
    status_map = {
        "rate_limit": status.HTTP_429_TOO_MANY_REQUESTS,
        "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
        "provider_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
        "invalid_json": status.HTTP_502_BAD_GATEWAY,
        "invalid_schema": status.HTTP_502_BAD_GATEWAY
    }
    http_status = status_map.get(exc.kind, status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    err = ErrorResponse(
        error=ErrorDetail(
            kind=exc.kind,
            message=exc.message,
            retryable=exc.retryable,
            request_id=req_id
        )
    )
    return JSONResponse(status_code=http_status, content=err.model_dump())

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    req_id = request_id_ctx.get()
    err = ErrorResponse(
        error=ErrorDetail(
            kind="invalid_input",
            message=f"Помилка валідації вхідних даних: {str(exc.errors()[0]['msg'])}",
            retryable=False,
            request_id=req_id
        )
    )
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=err.model_dump())

@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    req_id = request_id_ctx.get()
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    err = ErrorResponse(
        error=ErrorDetail(
            kind="internal_error",
            message=f"Внутрішня помилка сервера: {str(exc)}",
            retryable=False,
            request_id=req_id
        )
    )
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=err.model_dump())

# --- Routes ---

@app.get("/api/health", response_model=HealthResponse)
async def get_health():
    """Health check endpoint: verifies system state without calling paid models."""
    uptime = int(time.time() - START_TIME)
    storage_ok = storage.is_healthy()
    
    return HealthResponse(
        status="ok" if storage_ok else "degraded",
        version=settings.app_version,
        provider=settings.llm_provider,
        model=settings.llm_model,
        mode=settings.llm_provider,
        storage="ok" if storage_ok else "error",
        extra={
            "vector_index": "not_loaded_planned"
        },
        uptime_s=uptime
    )

@app.post("/api/upload")
async def upload_document(file: UploadFile = File(...)):
    """Extracts text from uploaded PDF, DOCX, or TXT file."""
    req_id = request_id_ctx.get()
    logger.info(f"Received file upload: '{file.filename}' (content_type: {file.content_type})")
    
    try:
        content = await file.read()
        if len(content) > 15 * 1024 * 1024: # 15 MB limit
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content=ErrorResponse(
                    error=ErrorDetail(
                        kind="file_too_large",
                        message="Розмір файлу перевищує ліміт 15 МБ.",
                        retryable=False,
                        request_id=req_id
                    )
                ).model_dump()
            )
        
        extracted_text, suggested_topic = extract_text_from_file(file.filename, content)
        logger.info(f"Successfully extracted {len(extracted_text)} characters from '{file.filename}'")
        
        return {
            "status": "success",
            "filename": file.filename,
            "char_count": len(extracted_text),
            "suggested_topic": suggested_topic,
            "text": extracted_text
        }
    except ValueError as ve:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=ErrorResponse(
                error=ErrorDetail(
                    kind="extraction_error",
                    message=str(ve),
                    retryable=False,
                    request_id=req_id
                )
            ).model_dump()
        )
    except Exception as e:
        logger.error(f"Failed to process file upload: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error=ErrorDetail(
                    kind="upload_failed",
                    message=f"Помилка при зчитуванні файлу: {str(e)}",
                    retryable=False,
                    request_id=req_id
                )
            ).model_dump()
        )

@app.post("/api/generate", response_model=StudyKitResponse)
async def generate_kit(payload: StudyRequest):
    """Main scenario: transforms study notes into summary, flashcards and quiz."""
    req_id = request_id_ctx.get()
    return await study_service.process_study_material(payload, req_id=req_id)

@app.get("/api/history", response_model=List[SessionRecord])
async def get_history():
    """Lists recent study sessions from storage."""
    return storage.list_recent_sessions(limit=10)

# Serve Web UI
import os
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def serve_index():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "AI Study Copilot API is running. Visit /docs for Swagger UI."}
