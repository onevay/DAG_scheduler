# Разработка

Нужны Python 3.12 и [uv](https://docs.astral.sh/uv/). Команды `make` работают в Linux и в Git Bash под Windows.

```bash
make setup     # зависимости со всеми extras и pre-commit хук
make lint      # ruff check, ruff format --check, lint-imports
make types     # mypy --strict
make test      # pytest без docker и slow
make perf      # замеры по времени (маркер slow); на нагруженной машине могут не уложиться в границы
make test-all  # все тесты
make schema    # пересобрать JSON Schema входа, результата и записи журнала в schemas/
make web-lint web-test web-build   # проверки JS-клиента (нужен Node.js 20)
make web-e2e                       # сквозные проверки клиента в Chromium (Playwright, axe)
make audit                         # известные уязвимости зависимостей: pip-audit по uv.lock, npm audit
```

CI выполняет `make lint types test perf`, проверяет, что `schemas/` и `ui/web/src/api/schema.ts` совпадают со
сгенерированными, гоняет `make web-lint web-test web-build`, `make audit-py`, `make audit-web` и отдельной задачей `make web-e2e`. Перед отправкой
изменений все эти цели должны проходить.

## Тесты

- `tests/unit/` — ядро. В `conftest.py` есть фабрики `make_task` и `make_problem`, тестовая политика `greedy`,
  генератор случайных DAG `random_problem(seed, failures=False)` и проверка ёмкости `check_capacity`.
- `tests/unit/test_scenarios.py` — прогоны `scenarios/*.json` с числами, посчитанными вручную. Если число
  разошлось, сначала нужно понять, что изменилось в поведении, а не обновлять ожидание.
- `tests/e2e/` — путь клиента по живому серверу: uvicorn в отдельном процессе, запросы по HTTP.
- Маркеры: `slow` — долгие тесты и замеры по времени, `docker` — тесты, которым нужен Docker. В быстром
  `make test` нет проверок по настенным часам: такие тесты помечаются `slow`.

## Соглашения

- Строка до 110 символов, форматирование — `ruff format`.
- Новый пакет `expsched.*` добавляется в `[tool.mypy] packages` и в слои `.importlinter`.
- Docstring объясняет, что делает функция и какие у неё неочевидные условия; комментарий — почему код
  написан именно так. Пересказывать код не нужно.
- Поведение, видимое снаружи (поле входа, запись журнала, поле результата, код диагностики), меняется вместе
  с документацией в `docs/`.
- Время и ресурсы — целые числа. Во входе и журнале нет float: по ним считаются `input_hash` и `result_hash`,
  а канонический JSON для float не гарантирован. Дробные метрики округляются.
- Файлы в репозитории — UTF-8 с LF (`.gitattributes`).
