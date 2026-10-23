# REST API

```bash
uv sync --extra api
make api            # uvicorn на http://127.0.0.1:8000, документация OpenAPI — /docs
```

Каждый прогон идёт в отдельном процессе и сохраняется в `EXPSCHED_RUNS_DIR` (по умолчанию `runs/`), хранятся
последние `EXPSCHED_MAX_RUNS` (100). Прогон дольше `EXPSCHED_RUN_TIMEOUT` секунд (60) прерывается с ошибкой 504;
одновременно идёт не больше `EXPSCHED_RUN_WORKERS` (2) прогонов, остальные ждут очереди, а остальные запросы
обслуживаются как обычно. Тело запроса не больше 20 МБ, иначе 413. Все тела запросов и ответов
описаны в `/openapi.json`.

## Ошибки

Любая ошибка приходит в одном формате; `diagnostics` есть, только если вход не соответствует схеме.

```json
{"error": {"code": "SCHEMA_INVALID", "message": "input does not match the schema"},
 "diagnostics": [{"code": "E001", "level": "error", "detail": {"loc": "tasks.0.duration", "...": "..."}, "hint": "..."}]}
```

| Статус | `code` | Когда |
|---|---|---|
| 400 | `POLICY_NOT_IMPLEMENTED` | политика или режим вытеснения ещё не реализованы |
| 404 | `NOT_FOUND` | нет такого пути или прогона (никогда не было или уже удалён как старый) |
| 405 | `METHOD_NOT_ALLOWED` | путь есть, метод не тот |
| 413 | `INPUT_TOO_LARGE` | тело больше лимита |
| 422 | `SCHEMA_INVALID` | тело не соответствует схеме; подробности в `diagnostics` |
| 422 | `INVALID_REQUEST` | неверный параметр запроса, например `mode` |
| 504 | `RUN_TIMEOUT` | прогон не уложился в `EXPSCHED_RUN_TIMEOUT`; он не сохраняется |
| 500 | `INTERNAL` | ошибка сервера; подробности только в журнале сервера |

## CORS

Браузерному клиенту с другого адреса (например, dev-сервер JS-приложения) API отвечает, только если этот адрес
указан в `EXPSCHED_CORS_ORIGINS`, через запятую:

```bash
EXPSCHED_CORS_ORIGINS=http://localhost:5173 make api
```

По умолчанию список пуст, и CORS-заголовки не отдаются.

## `GET /v1/policies`

Реализованные политики и параметры политики по умолчанию.

```json
{"policies": ["fifo", "fifo_skip", "priority", "priority_backfill"],
 "defaults": {"name": "priority_backfill", "aging": 360, "validation_mode": "lenient", "...": "..."}}
```

## `POST /v1/validate?mode=lenient|strict`

Тело — `problem.json`. Ответ:

```json
{"valid": false,
 "diagnostics": [{"code": "E012", "level": "error", "detail": {"cycle": ["a", "b", "c", "a"], "component_size": 3},
                  "hint": "Цикл: a → b → c → a. ..."}]}
```

Код 200 — вход разобран; ошибки графа, ресурсов и дедлайнов приходят в `diagnostics`. Если вход не
соответствует схеме (E001, E070), остальные проверки не выполняются и ответ — ошибка 422 `SCHEMA_INVALID`.

## `POST /v1/runs`

```json
{"problem": {"...": "problem.json"}, "policy": {"name": "fifo"}}
```

`policy` необязателен и целиком заменяет политику из входа. Ответ 201:

```json
{"run_id": "dc2f0ef68b28536c", "result": {"status": "OK", "...": "..."}}
```

`run_id` — первые 16 символов `input_hash`, поэтому тот же вход с той же политикой не пересчитывается: API
отдаёт сохранённый прогон. Ошибки — 400, 413, 422, 504 в [общем формате](#ошибки).

## `GET /v1/runs/{run_id}`

Тот же ответ, что у `POST /v1/runs`. `GET /v1/runs/{run_id}/problem` — вход прогона вместе с политикой, которой
он посчитан.

## `GET /v1/runs/{run_id}/events?from_seq=0&limit=1000`

Журнал страницами: записи с номерами `[from_seq, from_seq + limit)`, `limit` от 1 до 10 000.

```json
{"events": [{"seq": 0, "t": 0, "type": "WAIT", "...": "..."}], "total": 52, "next_seq": 1000}
```

`next_seq` — откуда читать следующую страницу, `null` — журнал прочитан.

## `GET /v1/runs/{run_id}/explain?task=&worker=&t=`

Почему прогон такой: [критическая цепочка](scheduling.md#почему-makespan-такой), попытки, ожидания задач и
[простой узлов](scheduling.md#почему-простаивают-gpu). Каждый фильтр сужает все списки сразу: `task` — эта
задача, `worker` — этот узел, `t` — то, что идёт в этот момент.

```json
{"chain": [...], "decomposition": {"dep": 21, "res": 0, "lost": 1, "release": 0, "wait": 0},
 "attempts": [...], "waits": [{"task": "urgent_eval", "code": "WAIT_DEPS", "from": 9, "to": 10, "detail": {}}],
 "idle": [{"worker": "node-C", "from": 13, "to": 15, "code": "IDLE_NO_READY", "gpu": 2, "detail": {}}]}
```

Ожидание — свойство задачи, простой — узла: с `worker` ожиданий в ответе нет, с `task` нет простоя. Неизвестные
задача или узел и прогон без симуляции (INFEASIBLE) — 404.

Ошибки самого входа (цикл, неизвестная зависимость) не делают запрос ошибочным: прогон возвращает 200
со статусом PARTIAL или INFEASIBLE и диагностиками в `result.diagnostics`.

Формат записей журнала — в [описании журнала](scheduling.md#журнал). Схемы ответа: `result` —
[`schemas/result.schema.json`](../schemas/result.schema.json), запись журнала —
[`schemas/event.schema.json`](../schemas/event.schema.json). `result`:

| Поле | |
|---|---|
| `schema_version` | `"1.0"` |
| `input_hash` | хеш канонического JSON входа вместе с действующей политикой |
| `result_hash` | хеш журнала; совпадает у повторных прогонов того же входа |
| `policy` | имя политики |
| `status` | `OK` — выполнены все задачи; `PARTIAL` — не все; `INFEASIBLE` — `strict` нашёл ошибки, симуляции не было |
| `attempts` | попытки: `task`, `attempt`, `worker`, `start`, `end`, `resume_from`, `outcome` (`DONE`, `INT`, `PRE`), `saved` |
| `tasks` | по id: `status`, `start` (первой попытки), `finish`, `deadline`, `tardiness`, `waits` |
| `metrics` | [метрики](scheduling.md#метрики); `null` при INFEASIBLE |
| `explain` | критическая цепочка, разложение makespan ([поля](scheduling.md#почему-makespan-такой)) и простой узлов (`idle`, [коды](scheduling.md#почему-простаивают-gpu)); `null` при INFEASIBLE |
| `replans` | записи `REPLAN` журнала: как внешние события сдвинули план ([формат](scheduling.md#перепланирование)); `null`, если задач больше `max_forecast_tasks` |
| `diagnostics` | ошибки и предупреждения входа |
| `violations` | нарушения, найденные проверкой расписания; пустой список — расписание допустимо |
