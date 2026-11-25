import asyncio
import random
from typing import Dict, Any
from app.providers.base import LLMProvider, ProviderError
from app.telemetry import logger

class MockProvider(LLMProvider):
    def __init__(self, failure_kind: str = "none", failure_rate: float = 0.0, delay_ms: int = 50):
        self.failure_kind = failure_kind
        self.failure_rate = failure_rate
        self.delay_ms = delay_ms

    async def generate_structured(self, prompt: str, topic: str, card_count: int, quiz_count: int) -> Dict[str, Any]:
        # Simulate network latency
        if self.delay_ms > 0:
            await asyncio.sleep(self.delay_ms / 1000.0)

        # Failure injection
        if self.failure_kind != "none" or (self.failure_rate > 0.0 and random.random() < self.failure_rate):
            kind = self.failure_kind if self.failure_kind != "none" else "provider_unavailable"
            logger.warning(f"MockProvider triggering simulated failure: {kind}")
            
            if kind == "timeout":
                raise ProviderError(kind="timeout", message="Час очікування відповіді провайдера вичерпано (Mock Timeout)", retryable=True)
            elif kind == "rate_limit":
                raise ProviderError(kind="rate_limit", message="Перевищено ліміт запитів провайдера (Mock 429)", retryable=True)
            elif kind == "invalid_json":
                raise ProviderError(kind="invalid_json", message="Провайдер повернув некоректний JSON (Mock Malformed)", retryable=False)
            else:
                raise ProviderError(kind="provider_unavailable", message="Провайдер тимчасово недоступний (Mock 503)", retryable=True)

        logger.info(f"MockProvider generating response for topic '{topic}' ({card_count} cards, {quiz_count} questions)")

        # Deterministic generation
        t_clean = topic if topic and topic.strip() else "Навчальний матеріал"
        effective_cards = card_count if card_count > 0 else 6
        effective_quiz = quiz_count if quiz_count > 0 else 5
        
        # Build Flashcards
        cards = []
        sample_cards = [
            ("Як досягається порядок точності $O(h^2)$?", "Центральна різницева формула $D_0 f(x) = \\frac{f(x+h) - f(x-h)}{2h}$ знищує парні степені $h$ для функцій $f \\in C^3$.", "Формула / Код"),
            ("Яка основна перевага цього підходу?", "Забезпечення високої продуктивності, масштабованості та мінімізація надлишковості даних.", "Принцип дії"),
            ("Як реалізується оптимізація в системі?", "Шляхом індексації, кешування та попередньої фільтрації обчислень на рівні сховища.", "Оптимізація"),
            ("Яке головне обмеження слід враховувати?", "Неможливість гарантувати повну узгодженість без додаткових витрат на синхронізацію.", "Обмеження"),
            ("Де на практиці застосовується цей алгоритм?", "У розподілених сервісах, обробці транзакцій та аналітичних інформаційних системах.", "Практика"),
            ("Який тип даних є найефективнішим для ідентифікаторів?", "Цілочисельні автоінкрементні ключі або компактні UUID версії 4/7.", "Архітектура"),
            ("Що відбувається при виникненні конфлікту оновлення?", "Система застосовує оптимістичне блокування (Optimistic Locking) або відкат транзакції.", "Надійність")
        ]
        
        for i in range(effective_cards):
            front, back, cat = sample_cards[i % len(sample_cards)]
            cards.append({
                "id": i + 1,
                "front": f"{front} [Тема: {t_clean}]",
                "back": back,
                "category": cat
            })

        # Build Quiz
        quiz = []
        sample_quiz = [
            (
                f"Яка головна мета вивчення теми '{t_clean}'?",
                ["Підвищення швидкодії та надійності", "Збільшення обсягу коду", "Повне відключення кешування", "Використання лише текстових файлів"],
                0,
                f"Тема '{t_clean}' спрямована на оптимізацію роботи системи та забезпечення її стабільності."
            ),
            (
                "Який із наведених компонентів відповідає за надійність збереження стану?",
                ["Транзакційний журнал (WAL)", "CSS стилі інтерфейсу", "Клієнтський браузер", "Таблиця стилів"],
                0,
                "Write-Ahead Logging (WAL) фіксує всі зміни до їх запису на диск, гарантуючи стійкість до збоїв."
            ),
            (
                "Що з наведеного НЕ є рекомендованою практикою для цього сценарію?",
                ["Зберігання секретних ключів у відкритому репозиторії", "Використання змінних оточення", "Покриття коду тестами", "Структурована валідація"],
                0,
                "Секрети та API ключі ніколи не повинні потрапляти у відкритий код чи систему контролю версій."
            ),
            (
                "Яка часова складність пошуку в збалансованому B-Tree індексі?",
                ["$O(\\log N)$", "$O(N^2)$", "$O(1)$", "$O(N!)$"],
                0,
                "Деревоподібні збалансовані індекси забезпечують логарифмічний час пошуку $O(\\log N)$."
            ),
            (
                "Яка основна перевага використання контракту замість вільного тексту?",
                ["Строга типізація та передбачуваність інтерфейсу", "Збільшення розміру трафіку", "Уповільнення запитів", "Неможливість тестування"],
                0,
                "Контракт API гарантує, що клієнт отримає відповідь очікуваного формату без галюцинацій структури."
            )
        ]

        for i in range(effective_quiz):
            q, opts, c_idx, expl = sample_quiz[i % len(sample_quiz)]
            quiz.append({
                "id": i + 1,
                "question": q,
                "options": opts,
                "correct_index": c_idx,
                "explanation": expl
            })

        return {
            "summary": {
                "tldr": (
                    f"У конспекті з теми «{t_clean}» розкрито математичну модель і принципи скінченно-різницевої апроксимації похідних. "
                    "Завдяки симетрії вузлів центральна формула $D_0 f(x) = \\frac{f(x+h) - f(x-h)}{2h}$ знищує парні степені кроку $h$ і досягає порядку точності $O(h^2)$ за умови $f \\in C^3$. "
                    "На межах відрізка застосовують односторонні різниці першого порядку $O(h)$, що критично враховувати при чисельному моделюванні."
                ),
                "key_takeaways": [
                    "Центральна різницева схема: $D_0 f(x) = \\frac{f(x+h) - f(x-h)}{2h}$ з похибкою $O(h^2)$",
                    "Умова збіжності другого порядку: функція повинна належати класу $f \\in C^3$ (тричі неперервно диференційовна)",
                    "Причина підвищення точності: взаємне знищення парних членів розкладу Тейлора через симетрію вузлів $x \\pm h$",
                    "Гранична пастка: на краях відрізка відсутні зовнішні вузли, тому використовують односторонні різниці з точністю $O(h)$",
                    "Індексний пошук у збалансованих структурах забезпечує складність $O(\\log N)$"
                ]
            },
            "flashcards": cards,
            "quiz": quiz
        }
