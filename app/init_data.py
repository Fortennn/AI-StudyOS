from app.models import FlashcardItem, QuizItem, StudyKitResponse, StudySummary
from app.settings import get_settings
from app.storage import Storage


def init_data() -> str:
    """Idempotently initializes database tables and a demo study session if empty."""
    settings = get_settings()
    storage = Storage(db_path=settings.db_path)
    demo_id = "demo-seed-001"
    if storage.get_session(demo_id, user_id=None):
        print(f"Database already initialized at {settings.db_path} (demo session present).")
        return "already_initialized"

    demo_kit = StudyKitResponse(
        request_id=demo_id,
        status="success",
        topic="Демонстраційний конспект: Алгоритми",
        summary=StudySummary(
            tldr="Базовий демонстраційний комплект: бінарний пошук працює за $O(\\log n)$ на відсортованому масиві.",
            key_takeaways=[
                "Передумова бінарного пошуку — відсортований масив.",
                "Часова складність пошуку: $O(\\log n)$.",
            ],
        ),
        flashcards=[
            FlashcardItem(
                id=1,
                front="Яка часова складність бінарного пошуку?",
                back="$O(\\log n)$ у найгіршому випадку за рахунок поділу діапазону навпіл.",
                category="Формула / Код",
            )
        ],
        quiz=[
            QuizItem(
                id=1,
                question="Яка головна умова для застосування бінарного пошуку?",
                options=[
                    "Масив має бути відсортованим",
                    "Масив має містити лише додатні числа",
                    "Розмір масиву має бути степенем двійки",
                    "Елементи мають бути унікальними",
                ],
                correct_index=0,
                explanation="Без впорядкованості неможливо відкинути половину діапазону.",
            )
        ],
        provider_kind="mock",
    )
    storage.save_session(demo_kit, raw_text="Бінарний пошук працює на відсортованому масиві за O(log n).", user_id=None)
    print(f"Initialized database at {settings.db_path} with demo session '{demo_id}'.")
    return "initialized"


if __name__ == "__main__":
    init_data()
