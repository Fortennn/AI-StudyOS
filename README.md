# AI StudyOS — Smart Flashcards & Knowledge Hub

Веб-застосунок на FastAPI для перетворення конспектів і лекцій (текст, PDF, DOCX, TXT) у структуроване резюме, 3D флеш-картки, квіз, режим «Правда чи Пастка», бліц-тренажер та AI-чат за матеріалом лекції.

---

## 1. Швидкий запуск без ключа (`LLM_PROVIDER=mock`)

Підходить для локальної перевірки інтерфейсу, API та тестів без інтернету і без API-ключа.

### Клонування та залежності
```bash
git clone https://github.com/Fortennn/AI-StudyOS.git
cd AI-StudyOS
python -m venv .venv
```

**Windows (PowerShell):**
```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

**Linux / macOS (bash):**
```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

### Ініціалізація даних та запуск сервера
```bash
python -m app.init_data
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Після запуску:
- Інтерфейс: `http://127.0.0.1:8000/`
- Перевірка стану: `http://127.0.0.1:8000/api/health` (`"status": "ok"`, `"provider": "mock"`)
- OpenAPI / Swagger: `http://127.0.0.1:8000/docs`

---

## 2. Запуск зі справжнім провайдером (`gemini`)

У файлі `.env` змініть:
```env
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3-flash-preview
LLM_API_KEY=ваш_ключ_gemini
```
Запустіть сервер тією ж командою:
```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## 3. Команди розробки та перевірки

| Дія | Команда |
| :--- | :--- |
| Ініціалізація БД (безпечна для повтору) | `python -m app.init_data` |
| Усі офлайн-тести (`mock` / `recorded`) | `python -m pytest -v` |
| Живий smoke-тест Gemini (`LIVE_TESTS=1`) | `LIVE_TESTS=1 python -m pytest -m live -v` *(PowerShell: `$env:LIVE_TESTS="1"; python -m pytest -m live -v`)* |
| Eval-прогін на 20 прикладах (`k=3`) | `python -m eval.run --label base_mock_v1 --k 3 --provider mock` |
| Аналіз JSONL-журналу та кешу | `python -m eval.analyze --compare-cache` |
| Димова перевірка запущеного URL | `python scripts/smoke.py http://127.0.0.1:8000` |

---

## 4. Основні змінні середовища (`.env.example`)

| Змінна | За замовчуванням | Секрет | Призначення |
| :--- | :--- | :---: | :--- |
| `APP_ENV` | `development` | ні | Середовище (`development` / `production`) |
| `PORT` / `APP_PORT` | `8000` | ні | Порт HTTP-сервера |
| `LLM_PROVIDER` | `mock` | ні | Провайдер (`mock`, `gemini`, `recorded`) |
| `LLM_MODEL` | `mock-study-flash` | ні | Назва моделі (`gemini-3-flash-preview` для `gemini`) |
| `LLM_API_KEY` | — | **так** | Ключ Gemini API (обов'язковий при `LLM_PROVIDER=gemini`) |
| `AI_ENABLED` | `1` | ні | Вимикач AI-генерації (`0` вимикає виклики моделі) |
| `DEMO_TOKEN` | — | **так** | Якщо задано, вимагає `X-Demo-Token` для AI-маршрутів |
| `DATA_DIR` | `data` | ні | Каталог для `study.db` та `budget.json` |
| `USER_RATE_LIMIT_RPM` | `20` | ні | Ліміт запитів на користувача за хвилину |
| `BUDGET_DAILY_USD` | `5.0` | ні | Денний ліміт оціночних витрат |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | — | **так** | Опційна авторизація через Google OAuth |

---

## 5. Docker та розгортання

### Локальний запуск у Docker
```bash
docker build -t ai-studyos .
docker run --rm --env-file .env -p 8000:8000 ai-studyos
```

### Розгортання на Render / хмарній платформі
1. Підключити репозиторій та вказати Python `3.11.9` ([.python-version](.python-version)) і команду запуску з [Procfile](Procfile): `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
2. Задати змінні середовища у панелі платформи (`LLM_PROVIDER=gemini`, окремий `LLM_API_KEY`, `DEMO_TOKEN`, `USER_RATE_LIMIT_RPM=10`, `BUDGET_DAILY_USD=1.0`).
3. Перевірити розгорнутий URL димовим скриптом: `python scripts/smoke.py https://<your-app>.onrender.com`.
4. **Відкат:** зафіксовані релізи позначаються тегом (`git tag -a v1.0 -m "release"`), відкат виконується повторним деплоєм попереднього коміту/тегу.

---

## 6. Документація та відомі обмеження

- Архітектура та API: [docs/architecture.md](docs/architecture.md), [docs/api.md](docs/api.md)
- Стани інтерфейсу (ПР17): [docs/ui-states.md](docs/ui-states.md)
- Надійність, кеш та каталог помилок (ПР18): [docs/reliability.md](docs/reliability.md)
- Оцінювання, план перевірок та безпека (ПР19): [docs/evaluation.md](docs/evaluation.md), [docs/measurements.md](docs/measurements.md)
- Розгортання (ПР20): [docs/deployment.md](docs/deployment.md)
- Облік використання AI: [AI_USAGE.md](AI_USAGE.md)
- **Відомі обмеження:** живий прогін 20×3 кейсів на безкоштовному рівні Gemini обмежений хвилинною квотою провайдера; при використанні тимчасового диска без підключеного Persistent Disk локальна БД SQLite скидається при редеплої.
