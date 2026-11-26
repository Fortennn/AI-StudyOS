import os
import time
import uuid
from urllib.parse import urlencode
from typing import List
import httpx
from fastapi import FastAPI, Request, status, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError

from app.settings import get_settings
from app.models import (
    StudyRequest, StudyKitResponse, HealthResponse, ErrorResponse, ErrorDetail,
    SessionRecord, ChatRequest, ChatResponse, AuthRequest, AuthResponse, UserPublic
)
from app.auth import (
    COOKIE_NAME, TOKEN_TTL_SECONDS, hash_password, verify_password,
    create_session_token, verify_session_token
)
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

def _get_current_user_id(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    uid = verify_session_token(token)
    if not uid:
        return None
    return uid if storage.get_user_by_id(uid) else None

def _set_auth_cookie(response: JSONResponse, user_id: str):
    token = create_session_token(user_id)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=TOKEN_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )

@app.post("/api/auth/register", response_model=AuthResponse)
async def register_user(payload: AuthRequest):
    req_id = request_id_ctx.get()
    if storage.get_user_by_username(payload.username):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=ErrorResponse(
                error=ErrorDetail(
                    kind="username_taken",
                    message="Користувач з таким логіном вже існує.",
                    retryable=False,
                    request_id=req_id
                )
            ).model_dump()
        )
    uid = f"usr-{uuid.uuid4().hex[:12]}"
    pw_hash, salt = hash_password(payload.password)
    user = storage.create_user(uid, payload.username, pw_hash, salt)
    res = JSONResponse(content=AuthResponse(user=UserPublic(**user)).model_dump())
    _set_auth_cookie(res, uid)
    return res

@app.post("/api/auth/login", response_model=AuthResponse)
async def login_user(payload: AuthRequest):
    req_id = request_id_ctx.get()
    user = storage.get_user_by_username(payload.username)
    if not user or not user["password_hash"] or not verify_password(payload.password, user["password_hash"], user["salt"]):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=ErrorResponse(
                error=ErrorDetail(
                    kind="invalid_credentials",
                    message="Невірний логін або пароль.",
                    retryable=False,
                    request_id=req_id
                )
            ).model_dump()
        )
    res = JSONResponse(content=AuthResponse(user=UserPublic(id=user["id"], username=user["username"])).model_dump())
    _set_auth_cookie(res, user["id"])
    return res

@app.post("/api/auth/logout")
async def logout_user():
    res = JSONResponse(content={"status": "ok"})
    res.delete_cookie(COOKIE_NAME)
    return res

def _google_oauth_config(request: Request):
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "").strip() or f"{str(request.base_url).rstrip('/')}/api/auth/google/callback"
    return client_id, client_secret, redirect_uri

@app.get("/api/auth/me", response_model=AuthResponse)
async def get_auth_me(request: Request):
    client_id, client_secret, _ = _google_oauth_config(request)
    google_ok = bool(client_id and client_secret)
    uid = _get_current_user_id(request)
    if not uid:
        return AuthResponse(user=None, google_enabled=google_ok)
    user = storage.get_user_by_id(uid)
    return AuthResponse(user=UserPublic(**user) if user else None, google_enabled=google_ok)

@app.get("/api/auth/google/login")
async def google_oauth_login(request: Request):
    client_id, client_secret, redirect_uri = _google_oauth_config(request)
    if not client_id or not client_secret:
        return RedirectResponse(url="/?auth_error=google_not_configured")
    state = create_session_token("google_oauth_state", ttl=600)
    params = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    })
    return RedirectResponse(url=f"https://accounts.google.com/o/oauth2/v2/auth?{params}")

@app.get("/api/auth/google/callback")
async def google_oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error or not code or verify_session_token(state) != "google_oauth_state":
        return RedirectResponse(url="/?auth_error=google_cancelled")

    client_id, client_secret, redirect_uri = _google_oauth_config(request)
    if not client_id or not client_secret:
        return RedirectResponse(url="/?auth_error=google_not_configured")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            tok_res = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            if tok_res.status_code != 200:
                return RedirectResponse(url="/?auth_error=google_token_failed")
            access_token = tok_res.json().get("access_token")
            info_res = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if info_res.status_code != 200:
                return RedirectResponse(url="/?auth_error=google_userinfo_failed")
            profile = info_res.json()
            google_id = str(profile.get("id") or "")
            email = profile.get("email") or ""
            name = profile.get("name") or ""
            if not google_id:
                return RedirectResponse(url="/?auth_error=google_userinfo_failed")

            user = storage.upsert_google_user(google_id=google_id, email=email, name=name)
            resp = RedirectResponse(url="/")
            token = create_session_token(user["id"])
            resp.set_cookie(
                key=COOKIE_NAME,
                value=token,
                max_age=TOKEN_TTL_SECONDS,
                httponly=True,
                samesite="lax",
            )
            return resp
    except Exception as e:
        logger.error(f"Google OAuth callback error: {e}")
        return RedirectResponse(url="/?auth_error=google_failed")

@app.post("/api/generate", response_model=StudyKitResponse)
async def generate_kit(payload: StudyRequest, request: Request):
    """Main scenario: transforms study notes into summary, flashcards and quiz."""
    req_id = request_id_ctx.get()
    uid = _get_current_user_id(request)
    return await study_service.process_study_material(payload, req_id=req_id, user_id=uid)

@app.get("/api/history", response_model=List[SessionRecord])
async def get_history(request: Request):
    """Lists recent study sessions from storage."""
    uid = _get_current_user_id(request)
    return storage.list_recent_sessions(limit=30, user_id=uid)

@app.delete("/api/history/{session_id}")
async def delete_history_session(session_id: str, request: Request):
    """Deletes a saved study session by ID."""
    req_id = request_id_ctx.get()
    uid = _get_current_user_id(request)
    if not storage.delete_session(session_id, user_id=uid):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(
                error=ErrorDetail(
                    kind="not_found",
                    message="Сесію не знайдено.",
                    retryable=False,
                    request_id=req_id
                )
            ).model_dump()
        )
    return {"status": "ok", "id": session_id}

@app.patch("/api/history/{session_id}/pin")
async def pin_history_session(session_id: str, request: Request):
    """Toggles pinned state of a saved study session."""
    req_id = request_id_ctx.get()
    uid = _get_current_user_id(request)
    pinned = storage.toggle_pin_session(session_id, user_id=uid)
    if pinned is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(
                error=ErrorDetail(
                    kind="not_found",
                    message="Сесію не знайдено.",
                    retryable=False,
                    request_id=req_id
                )
            ).model_dump()
        )
    return {"status": "ok", "id": session_id, "pinned": pinned}

@app.get("/api/history/{session_id}", response_model=StudyKitResponse)
async def get_history_session(session_id: str, request: Request):
    """Restores a previously generated study kit by session ID."""
    req_id = request_id_ctx.get()
    uid = _get_current_user_id(request)
    kit = storage.get_session(session_id, user_id=uid)
    if not kit:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=ErrorResponse(
                error=ErrorDetail(
                    kind="not_found",
                    message="Сесію не знайдено в історії.",
                    retryable=False,
                    request_id=req_id
                )
            ).model_dump()
        )
    return kit

@app.post("/api/chat", response_model=ChatResponse)
async def chat_with_lecture(payload: ChatRequest, request: Request):
    """Follow-up Q&A grounded in the loaded lecture material."""
    req_id = request_id_ctx.get()
    uid = _get_current_user_id(request)
    return await study_service.answer_lecture_question(payload, req_id=req_id, user_id=uid)

@app.delete("/api/chat/{session_id}")
async def clear_lecture_chat(session_id: str, request: Request):
    """Clears persisted chat history for a study session."""
    uid = _get_current_user_id(request)
    storage.clear_chat_history(session_id, user_id=uid)
    return {"status": "ok"}

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
