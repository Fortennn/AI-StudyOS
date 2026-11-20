# Контракти API (API Specifications)

## 1. GET /api/health
Перевірка стану працездатності сервісу (Liveness & Readiness). Не викликає платну модель.

### Відповідь (200 OK):
```json
{
  "status": "ok",
  "version": "0.1.0",
  "provider": "mock",
  "model": "mock-study-flash",
  "mode": "mock",
  "storage": "ok",
  "extra": {
    "vector_index": "not_loaded_planned"
  },
  "uptime_s": 42
}
```

---

## 2. POST /api/generate
Трансформація тексту конспекту у структурований навчальний комплект.

### Тіло запиту (Request Body):
```json
{
  "text": "Текст навчальної лекції або конспекту...",
  "topic": "Бази даних",
  "card_count": 5,
  "quiz_count": 5
}
```

### Успішна відповідь (200 OK):
```json
{
  "request_id": "req-9b8c-4a3d",
  "status": "success",
  "summary": {
    "tldr": "Стисле резюме навчального матеріалу...",
    "key_takeaways": [
      "Ключова теза 1",
      "Ключова теза 2"
    ]
  },
  "flashcards": [
    {
      "id": 1,
      "front": "Що таке індекс у БД?",
      "back": "Структура даних для прискорення пошуку...",
      "category": "Оптимізація"
    }
  ],
  "quiz": [
    {
      "id": 1,
      "question": "Яка структура найчастіше використовується для індексів?",
      "options": ["B-Tree", "Черга", "Стек", "Граф"],
      "correct_index": 0,
      "explanation": "B-Tree забезпечує логарифмічний час пошуку O(log N)."
    }
  ]
}
```

---

## 3. Єдиний формат помилки (Error Response)
Повертається при виникненні будь-якої клієнтської або серверної помилки:

```json
{
  "error": {
    "kind": "provider_unavailable",
    "message": "Провайдер тимчасово недоступний (Mock Simulated Failure)",
    "retryable": true,
    "request_id": "req-9b8c-4a3d"
  }
}
```

| HTTP Status | Вид (`kind`) | Опис |
| :--- | :--- | :--- |
| **400 / 422** | `invalid_input` | Помилка валідації вхідних даних (відсутній текст або некоректні параметри) |
| **429** | `rate_limit` | Перевищено ліміт запитів (можна повторити пізніше, `retryable: true`) |
| **503 / 504** | `provider_unavailable` | Збій зв'язку з провайдером моделей (`retryable: true`) |
| **500** | `internal_error` | Непередбачувана внутрішня помилка сервера |
