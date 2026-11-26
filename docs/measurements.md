# Вимірювання AI-сценарію (`StudyKitResponse`)

Нижче зафіксовані результати прогону контрольного набору [eval/cases.json](../eval/cases.json) через [eval/run.py](../eval/run.py) (`k=3`, сумарно `18` запусків, збережено у [eval/runs/mock_baseline.json](../eval/runs/mock_baseline.json)).

## Базовий офлайн-прогін (`provider=mock`, `k=3`)

| Кейс | Тема | Вхід (симв.) | Карток / Тестів | Середній час (мс) | Pass rate | Вартість |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| `case_01_db_normalization` | Реляційні бази даних | 292 | 4 / 3 | 33.85 | 1.00 | \$0.00 |
| `case_02_os_processes` | Операційні системи | 319 | 5 / 4 | 33.63 | 1.00 | \$0.00 |
| `case_03_numerical_diff` | Чисельні методи | 272 | 4 / 3 | 37.60 | 1.00 | \$0.00 |
| `case_04_graphs_bfs_dfs` | Алгоритми на графах | 252 | 4 / 3 | 30.55 | 1.00 | \$0.00 |
| `case_05_http_rest` | Веб-архітектура та HTTP | 308 | 3 / 3 | 37.98 | 1.00 | \$0.00 |
| `case_06_noisy_fragment` | Фрагмент без контексту | 78 | 3 / 2 | 27.45 | 1.00 | \$0.00 |

- **Загальний результат:** `18/18` (`overall_pass_rate = 1.0`), усі відповіді пройшли 3-шарову валідацію (`schema -> rules -> content`).
- **Для живих замірів Gemini** (за наявності `LLM_API_KEY` у `.env`):
  ```bash
  python -m eval.run --label gemini_live --k 3 --provider gemini
  ```
