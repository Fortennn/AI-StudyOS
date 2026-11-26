from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

# --- Input Models ---
class StudyRequest(BaseModel):
    text: str = Field(..., min_length=10, max_length=250000, description="Текст конспекту або лекції для обробки")
    topic: Optional[str] = Field(default="Загальна тема", description="Назва теми або предмету")
    card_count: int = Field(default=5, ge=0, le=30, description="Кількість флеш-карток (0 = авто)")
    quiz_count: int = Field(default=5, ge=0, le=25, description="Кількість питань тесту (0 = авто)")
    provider: Optional[str] = Field(default=None, description="Опціональне перекриття провайдера (mock або gemini)")
    api_key: Optional[str] = Field(default=None, description="Опціональний API ключ для Gemini")

# --- Structured Output / Domain Models ---
class StudySummary(BaseModel):
    tldr: str = Field(..., description="Стисле резюме конспекту (3-4 речення)")
    key_takeaways: List[str] = Field(..., description="Ключові тези або формули")

class FlashcardItem(BaseModel):
    id: int = Field(..., description="Унікальний порядковий номер картки")
    front: str = Field(..., description="Питання або термін на лицьовій стороні")
    back: str = Field(..., description="Визначення, відповідь або пояснення на зворотній стороні")
    category: str = Field(default="Основне", description="Категорія складності або підтема")

class QuizItem(BaseModel):
    id: int = Field(..., description="Порядковий номер питання")
    question: str = Field(..., description="Текст питання")
    options: List[str] = Field(..., min_length=4, max_length=4, description="Чотири варіанти відповіді (A, B, C, D)")
    correct_index: int = Field(..., ge=0, le=3, description="Індекс правильної відповіді (0..3)")
    explanation: str = Field(..., description="Коротке пояснення правильної відповіді")

class TrueFalseItem(BaseModel):
    id: int = Field(..., description="Порядковий номер твердження")
    statement: str = Field(..., description="Твердження за матеріалом лекції (істинне або з пасткою)")
    is_true: bool = Field(..., description="Чи є твердження істинним")
    correction: str = Field(..., description="Коротке пояснення: чому це правда або в чому саме пастка")

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(..., min_length=1, max_length=16000)

class StudyKitResponse(BaseModel):
    request_id: str
    status: str = "success"
    topic: str
    summary: StudySummary
    flashcards: List[FlashcardItem]
    quiz: List[QuizItem]
    true_false: List[TrueFalseItem] = Field(default_factory=list)
    chat_history: List[ChatMessage] = Field(default_factory=list)

# --- Error Contract ---
class ErrorDetail(BaseModel):
    kind: str
    message: str
    retryable: bool
    request_id: str

class ErrorResponse(BaseModel):
    error: ErrorDetail

# --- Health Contract ---
class HealthResponse(BaseModel):
    status: str
    version: str
    provider: str
    model: str
    mode: str
    storage: str
    extra: Dict[str, Any]
    uptime_s: int

# --- Storage Entity ---
class SessionRecord(BaseModel):
    id: str
    created_at: str
    topic: str
    text_length: int
    card_count: int
    quiz_count: int
    pinned: bool = False

# --- Lecture Chat Models ---
class ChatRequest(BaseModel):
    session_id: Optional[str] = Field(default=None)
    topic: str = Field(default="Загальна тема")
    context_text: str = Field(default="")
    question: str = Field(..., min_length=2, max_length=2000)
    history: List[ChatMessage] = Field(default_factory=list)
    provider: Optional[str] = Field(default=None)

class ChatResponse(BaseModel):
    request_id: str
    answer: str
