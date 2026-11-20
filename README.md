# 🎓 AI Study Copilot — Smart Flashcards & Knowledge Hub

Інтелектуальний веб-застосунок для трансформації неструктурованих конспектів, лекцій та наукових статей у структуроване резюме, інтерактивні 3D флеш-картки та тести для самоперевірки.

Проєкт реалізовано як **Архітектурний технічний прототип (Walking Skeleton) інтелектуальної освітньої платформи**.

---

## 🚀 Швидкий старт (Quick Start)

### 1. Клонування репозиторію та перехід у папку:
```bash
git clone <URL_РЕПОЗИТОРІЮ>
cd "AI StudyOS"
```

### 2. Створення віртуального оточення та встановлення залежностей:
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Налаштування змінних оточення:
```bash
# Windows PowerShell:
Copy-Item .env.example .env

# Bash:
# cp .env.example .env
```
*(За замовчуванням увімкнено безкоштовний режим `LLM_PROVIDER=mock`, тому API-ключ не потрібен).*

### 4. Запуск сервера:
```bash
python -m uvicorn app.main:app --reload --port 8000
```
Відкрийте браузер за адресою: **[http://localhost:8000](http://localhost:8000)**  
Swagger-документація API доступна за адресою: **[http://localhost:8000/docs](http://localhost:8000/docs)**

---

## 🧪 Запуск тестів (Pytest)

Проєкт містить 3 обов'язкові групи тестів архітектурного каркаса:
1. **Тест API потоку** через контролер, шлюз та підставний провайдер.
2. **Ізольований тест прикладного сервісу** без HTTP.
3. **Тест контрольованого збою** та єдиного формату помилки.

```bash
python -m pytest -v
```

---

## 📂 Структура проєкту

```
AI StudyOS/
├── .env.example          # Зразок конфігурації без секретів
├── requirements.txt      # Залежності проєкту
├── README.md             # Інструкція з розгортання
├── AI_USAGE.md           # Журнал використання ШІ під час розробки
├── docs/                 # Архітектурна документація
│   ├── architecture.md   # Дворівнева C4 схема та опис 6-ти шарів
│   ├── api.md            # Специфікація маршрутів та помилок
│   └── adr/              # Записи архітектурних рішень (ADR-001..003)
├── app/                  # Вихідний код бекенду
│   ├── main.py           # Контролер FastAPI, middleware, /api/health
│   ├── settings.py       # Валідація конфігурації при старті
│   ├── models.py         # Pydantic моделі (вхід, вихід, помилки)
│   ├── telemetry.py      # Наскрізне логування з request_id
│   ├── storage.py        # Сховище SQLite (історія та сесії)
│   ├── gateway.py        # Шлюз до провайдерів з таймаутами
│   ├── orchestration.py  # Промпти та перевірка вихідних схем
│   ├── service.py        # Прикладний сервіс (сценарій)
│   └── providers/
│       ├── base.py       # Абстрактний інтерфейс LLMProvider
│       └── mock.py       # Детермінований MockProvider з емуляцією збоїв
├── static/               # Веб-інтерфейс
│   ├── index.html        # Інтерфейс у стилі Dark Glassmorphism
│   └── hero.jpg          # 3D концепт-ілюстрація
└── tests/                # Тести pytest
    ├── conftest.py
    ├── test_api.py
    ├── test_service.py
    └── test_errors.py
```
