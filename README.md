# expsched

Планировщик вычислительных экспериментов. На вход — DAG задач (обучение, оценка, инференс) с требованиями
к ресурсам, приоритетами и дедлайнами и кластер узлов; на выходе — расписание, полученное детерминированной
дискретно-событийной симуляцией, его независимая проверка, метрики и объяснение, почему каждая задача ждала.

- Политики: `fifo`, `fifo_skip`, `priority` (класс приоритета, запас до дедлайна, длина цепочки, aging)
  и `priority_backfill` — та же очередь с резервом для крупной задачи, которой сейчас нет места.
- Отказы узлов: прерванная задача продолжает с последнего чекпоинта на другом узле, после исчерпания
  ретраев — FAILED, потомки — BLOCKED.
- Задачи, поданные во время прогона, и вытеснение ради дедлайна.
- Перепланирование: на каждом внешнем событии видно, чьи прогнозные старты оно сдвинуло.
- Проверка входа с кодами ошибок (циклы, недостижимые дедлайны, задачи, которым не хватит ни одного узла)
  и режимом lenient, в котором планируется всё, что можно.
- Один и тот же вход и политика дают побайтово одинаковый журнал.

## Быстрый старт

Нужны Python 3.12 и [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras
make web-install   # зависимости JS-клиента, нужен Node.js 20
make run           # клиент и REST API на http://127.0.0.1:8000
```

Или в Docker, без Python и Node на машине:

```bash
docker compose up --build   # http://127.0.0.1:8000, прогоны — в томе expsched-runs
```

Образ ([`Dockerfile`](Dockerfile)) собирает клиент на Node и окружение через uv, а работает на `python:3.12-slim`
от непривилегированного пользователя. [`compose.yaml`](compose.yaml) открывает порт только на 127.0.0.1
(авторизации в сервисе нет), монтирует корень только для чтения, сбрасывает capabilities и ограничивает память.
Состояние — `docker compose ps` (HEALTHCHECK опрашивает `/v1/policies`).

Из Python:

```python
from pathlib import Path

from expsched.contract.io_models import PolicyIn, load
from expsched.pipeline import run

problem = load(Path("scenarios/failure.json").read_bytes())
out = run(problem, PolicyIn(name="priority"))

assert out.result["status"] == "OK"
assert out.result["metrics"]["makespan"] == 22
assert out.result["metrics"]["lost_work_gpu"] == 4  # час работы на 4 GPU после последнего чекпоинта
assert out.result["violations"] == []
```

Через API (ответ — `run_id` и результат; по `run_id` читаются журнал, объяснения и варианты what-if, см.
[REST API](docs/api.md)):

```bash
curl -s localhost:8000/v1/runs -H 'content-type: application/json' \
  -d "{\"problem\": $(cat scenarios/demo.json)}"
```

## Примеры входа

| Файл | Что показывает |
|---|---|
| `scenarios/demo.json` | 10 задач на 3 узлах и срочная задача, поданная в t = 9 |
| `scenarios/failure.json` | тот же DAG, node-A недоступен с 5 до 15 |
| `scenarios/urgent_failure.json` | отказ и срочная задача вместе |
| `scenarios/backfill.json` | `priority_backfill`: крупная задача получает резерв, мелкая заполняет дыру |
| `scenarios/preemption.json` | срочная задача вытесняет фоновую, чтобы успеть к дедлайну |
| `scenarios/cycle.json` | ошибки входа: цикл, неизвестная зависимость, повтор ребра |

## Документация

- [Формат входа](docs/input.md) — `problem.json`, поля и значения по умолчанию.
- [Как строится расписание](docs/scheduling.md) — симуляция, политики, отказы, причины ожидания, проверка
  и метрики.
- [REST API](docs/api.md)
- [Архитектура](docs/architecture.md) — пакеты, слои и правила зависимостей.
- [Интерфейс](ui/README.md)
- [Разработка](CONTRIBUTING.md)

## Ограничения

- Политика `cpsat` пока не реализована.
- Вытеснение — только ради дедлайна (`preemption: deadline_only`); режим `priority` и поле `objective`
  пока не поддерживаются.
- Поддерживается только режим `simulate`.
- На широких DAG (10⁴ задач, тысячи готовых одновременно) прогон занимает до 20 с: очередь политики
  пересчитывается каждую эпоху.
