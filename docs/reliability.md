# Надійність, кешування та спостережуваність (`AI StudyOS`, ПР18)

## 1. Карта зовнішніх залежностей і збоїв

| Залежність | Способи відмови | Як код дізнається | Що робить код | Що бачить людина | `error.kind` | Як відтворити |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Gemini API** | Таймаут відповіді | `asyncio.TimeoutError` / `httpx.ReadTimeout` | 1 обмежений повтор (якщо вкладається в дедлайн), запис у `CircuitBreaker` | «Сервер не встиг відповісти» + кнопка `Повторити` + `request_id` | `timeout` (504) | `MOCK_FAILURE_KIND=timeout` |
| **Gemini API** | Ліміт частоти (429) | HTTP 429 від API | Повтор із паузою (`Retry-After`), облік у `CircuitBreaker` | «Забагато запитів» + `Повторити` | `rate_limit` (429) | `MOCK_FAILURE_KIND=rate_limit` |
| **Gemini API** | Недоступність / 5xx | HTTP 5xx / `ConnectError` | Перебір `FALLBACK_MODELS`, потім повтор, розмикання `CircuitBreaker` після порога | «AI-сервіс тимчасово недоступний» + `Повторити` | `provider_unavailable` (503) | `MOCK_FAILURE_KIND=unavailable` |
| **Gemini API** | Обрив потоку / відповіді | `RemoteProtocolError` / `mid_stream` | Позначка `retryable=True`, повтор у межах дедлайну | Повідомлення про збій + `Повторити` | `mid_stream` (502) | `MOCK_FAILURE_KIND=mid_stream` |
| **Gemini API** | Невірний ключ (401/403) | HTTP 401/403 | **Не повторюється**, `CircuitBreaker` **не** рахує | Помилка авторизації без автоповтору | `auth` (401) | `MOCK_FAILURE_KIND=auth` |
| **Gemini API** | Невалідний JSON / схема / правила | `ValidationError` / 3-шарова валідація | 1 повтор у `StudyService` з текстом помилки, далі відмова (`retryable=False`) | «Некоректна відповідь моделі» без кнопки `Повторити` | `invalid_output` (502) | `MOCK_FAILURE_KIND=invalid_json` або бита відповідь |
| **Парсер файлів (`pypdf`, `python-docx`)** | Порожній / пошкоджений / завеликий файл | Перевірка розміру > 15 МБ або `ValueError` | Повертає 413 / 400 без виклику LLM | Повідомлення біля форми завантаження | `file_too_large` (413) / `extraction_error` (400) | Завантажити `.exe` або порожній `.txt` |
| **SQLite (`data/study.db`)** | Помилка запису сесії | `sqlite3.Error` | Логує `WARNING`, повертає згенерований комплект без падіння | Згенеровані картки і квіз працюють | — (graceful degradation) | Недоступний шлях БД |

---

## 2. Таймаути, повтори, ліміти та запобіжник

- **Таймаути:**
  - HTTP-клієнт: `connect=10.0s`, `read=LLM_TIMEOUT_S` (за замовчуванням `15s`–`60s` для довгих конспектів із [docs/measurements.md](measurements.md)).
  - Спільний дедлайн запиту: `LLM_TOTAL_DEADLINE_S=45s`. Перед кожною повторною спробою перевіряється залишок дедлайну (`deadline - time.monotonic()`).
- **Обмежені повтори:**
  - На рівні шлюзу `LLM_MAX_RETRIES=2` (максимум 2 спроби лише для `retryable=True`) з експоненційною паузою + випадковим jitter та урахуванням `Retry-After`.
  - На рівні валідації `StudyService` робить максимум 1 повтор.
  - Сумарно в найгіршому разі на одне натискання виконується не більше `2 × 2 = 4` спроб.
- **Чотири рівні лімітів:**
  1. `429 rate_limit` від провайдера (`retryable=True`).
  2. `429 user_rate_limit` від нашого сервера (`USER_RATE_LIMIT_RPM=20` за хвилину на користувача + заголовок `Retry-After`).
  3. `asyncio.Semaphore(LLM_MAX_CONCURRENCY=5)` у шлюзі.
  4. Денний бюджет `BUDGET_DAILY_USD` / `BUDGET_DAILY_CALLS` (`kind="budget"`, `retryable=False`).
- **Запобіжник (`CircuitBreaker`):** стани `closed` → `open` → `half_open`, поріг `BREAKER_THRESHOLD=3` за 60 с, відновлення `BREAKER_RECOVERY_S=30` с. Рахує **лише** `timeout`, `provider_unavailable`, `rate_limit`. Стан видно у `GET /api/health` без виклику моделі.
- **Сходинки погіршення:** `main` (`gemini-3-flash-preview`) → `FALLBACK_MODELS` → `cache` (якщо є влучання) → чесна відмова з `request_id`. Вимикач AI: `AI_ENABLED=0` (`kind="ai_disabled"`, HTTP `503`).
  - *Відхилена сходинка:* генерація «із загальних знань» без опори на наданий конспект відхилена, оскільки суперечить ідеї навчального помічника за конкретною лекцією.

---

## 3. Закритий каталог помилок

| `kind` | HTTP | `retryable` | `Retry-After` | Дія інтерфейсу |
| :--- | :---: | :---: | :---: | :--- |
| `invalid_input` | `422` | `false` | — | Показати помилку біля поля |
| `auth` | `401` | `false` | — | Повідомити про невірний ключ, без повтору |
| `missing_api_key` | `400` | `false` | — | Запропонувати задати ключ / обрати Mock |
| `budget` | `429` | `false` | — | Повідомити про денний ліміт, без повтору |
| `user_rate_limit` | `429` | `true` | так | Показати таймер / кнопку `Повторити` |
| `rate_limit` | `429` | `true` | так | Показати кнопку `Повторити` |
| `invalid_output` | `502` | `false` | — | Запропонувати уточнити текст, без повтору |
| `mid_stream` | `502` | `true` | — | Показати кнопку `Повторити` |
| `provider_unavailable` | `503` | `true` | — | Показати кнопку `Повторити` |
| `circuit_open` | `503` | `true` | так | Повідомити про тимчасову паузу |
| `ai_disabled` | `503` | `false` | — | Повідомити, що AI вимкнено |
| `timeout` | `504` | `true` | — | Показати кнопку `Повторити` |
| `internal` | `500` | `false` | — | Загальне повідомлення з `request_id` без деталей винятку |

---

## 4. Кешування та журнал

- **TTL-кеш (`app/cache.py`):** ключ `sha256` від `{model, prompt_version, schema_version, topic, material, card_count, quiz_count, owner}`. Помилки та невалідні відповіді не кешуються; заголовок `X-Bypass-Cache: 1` дозволяє примусово обійти кеш.
- **Структурований журнал (`logs/app.jsonl`):** ротується через `RotatingFileHandler`, приймає лише поля з `ALLOWED_JSONL_FIELDS` та маскує патерни ключів (`AIza...`, `sk-...`).
