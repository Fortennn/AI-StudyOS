# Оцінювання, тестування та безпека (`AI StudyOS`, ПР19)

## 1. План перевірки (властивість → чим перевіряється → де лежить)

| Властивість / Обіцянка | Тип перевірки | Де лежить |
| :--- | :--- | :--- |
| Контракт API, статуси, `422` з `fields`, `/api/ui-config`, `Idempotency-Key` | Автотест (без моделі) | [tests/test_api.py](../tests/test_api.py), [tests/test_pr17_e2e.py](../tests/test_pr17_e2e.py) |
| Знімок схеми OpenAPI збігається з поточним застосунком | Автотест (без моделі) | [tests/test_pr19_security.py](../tests/test_pr19_security.py), [docs/openapi.json](openapi.json) |
| Ізоляція чужих ресурсів (`GET/DELETE/PATCH /api/history/{id}`) | Автотест (без моделі) | [tests/test_pr19_security.py](../tests/test_pr19_security.py) |
| Відхилення некоректного входу до виклику моделі (пробіли, >250k, битий файл, неіснуючий id) | Автотест (без моделі) | [tests/test_pr19_security.py](../tests/test_pr19_security.py), [tests/test_upload.py](../tests/test_upload.py) |
| 3-шарова валідація (`schema -> rules -> content`), 1 повтор, `invalid_output` 502 | Автотест (без моделі) | [tests/test_pr15.py](../tests/test_pr15.py), [tests/test_pr19_security.py](../tests/test_pr19_security.py) |
| Закритий каталог помилок (`auth`, `mid_stream`, `rate_limit`, `budget`, `timeout`, `unavailable`, `circuit_open`, `ai_disabled`) | Параметризований тест | [tests/test_pr18.py](../tests/test_pr18.py) |
| Захист від витоку ключа, канарейкової мітки `ref-7f3a91` та XSS у виводі моделі | Автотест + адверсарні кейси | [tests/test_pr15.py](../tests/test_pr15.py), [tests/test_pr17_e2e.py](../tests/test_pr17_e2e.py), [tests/test_pr19_security.py](../tests/test_pr19_security.py) |
| Поведінка AI на 20 прикладах (4 види × 5) + 4 відкладені приклади | Eval-набір (`k=3`) | [eval/cases.json](../eval/cases.json), [eval/holdout.json](../eval/holdout.json), [eval/run.py](../eval/run.py) |
| Живий smoke-тест Gemini API | Опційний live-тест (`LIVE_TESTS=1`) | [tests/test_pr15.py](../tests/test_pr15.py) (`@pytest.mark.live`) |

## 2. Рішення щодо LLM-судді

У цьому сценарії **окремий LLM-суддя не потрібен**: вихід `StudyKitResponse` є повністю структурованим JSON, де кожне поле (`summary`, `flashcards`, `quiz`, `true_false`) перевіряється детермінованими правилами у 3 шари (`schema` → `rules` → `content`) в [app/orchestration.py](../app/orchestration.py), а якість формулювання контролюється лімітами довжини, перевіркою заглушок, унікальністю варіантів та наявністю поля `insufficient_data` / `missing_info`.

## 3. Таблиця атак та шарів захисту

| Атака / Кейс | Що намагається зробити | Що виконав код | Який шар зупинив |
| :--- | :--- | :--- | :--- |
| `adv_01_direct_prompt_leak` | Витягти системний промпт та мітку `ref-7f3a91` | Перевірив вихід регулярним виразом; при збігу заблокував з `invalid_output` | Layer 3 (`_SECRET_LEAK_PATTERN` в `StudyOrchestrator`) + ізоляція секретів поза шаблоном |
| `adv_02_indirect_doc_injection` | Закрити `<<<END_USER_DATA>>>` усередині документа | Екранував `<<<` / `>>>` у `< < <` / `> > >` до відправки моделі | `escape_untrusted_text` у [app/prompts.py](../app/prompts.py) |
| `adv_03_xss_in_material` | Вставити `<img src=x onerror=alert(1)>` у текст | Зберіг і повернув як звичайний текст; браузер рендерить через `escapeHtml` / `textContent` | Екранування у [static/index.html](../static/index.html) |
| `adv_04_role_hijack_category` | Зламати JSON-схему (`flashcards: "HACKED"`) | Відхилив на Layer 1 (`schema`) і повторив 1 раз, далі 502 `invalid_output` | Layer 1 + Layer 2 валідація у [app/orchestration.py](../app/orchestration.py) |
| `adv_05_cross_user_probe` | Отримати чужі `session_id` / конспекти | Запит до БД фільтрується по `user_id` із підписаного cookie; чужі дані не потрапляють у контекст | Серверна ізоляція в [app/storage.py](../app/storage.py) |

## 4. Місця, де відповідь моделі керує кодом

У проєкті немає автономних викликів зовнішніх інструментів (tool calls), але такі значення з відповіді моделі інтерпретуються кодом/UI:
1. `summary.insufficient_data` (`bool`) — перемикає банер недостатніх даних у UI (перевіряється Pydantic + `missing_info` у Layer 2).
2. `quiz[*].correct_index` (`0..3`) — визначає правильний варіант у тесті (обмежено `ge=0, le=3` у Pydantic + 4 унікальні варіанти у Layer 2).
3. `flashcards[*].category` (`str`) — відображається бейджем у колоді (рендериться через `textContent` / `escapeHtml`).

## 5. Відомі обмеження

- Живий eval-прогін на реальній моделі (`gemini-3-flash-preview`, `20 × 3 = 60` запитів) обмежений квотою безкоштовного рівня (RPM/RPD), тому в CI запускається детермінований офлайн-прогін на `mock` / `recorded`.
- При вимкненому `LIVE_TESTS=1` живий тест `test_live_gemini_smoke` пропускається.

## 6. Перелік зовнішніх ланок і залежностей

| Компонент | Версія | Джерело | Ліцензія |
| :--- | :--- | :--- | :--- |
| `fastapi` | `0.141.1` | PyPI | MIT |
| `uvicorn` | `0.52.4` | PyPI | BSD-3-Clause |
| `pydantic` | `2.13.5` | PyPI | MIT |
| `httpx` | `0.28.1` | PyPI | BSD-3-Clause |
| `python-dotenv` | `1.2.3` | PyPI | BSD-3-Clause |
| `pypdf` | `6.12.2` | PyPI | BSD-3-Clause |
| `python-docx` | `1.2.0` | PyPI | MIT |
| `python-multipart` | `0.0.32` | PyPI | Apache-2.0 |
| `pytest` | `9.2.0` | PyPI | MIT |
| Gemini API (`gemini-3-flash-preview`) | `v1beta` | Google AI for Developers | Google APIs Terms |
