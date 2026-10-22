# REST API

```bash
uv sync --extra api
make api            # uvicorn на http://127.0.0.1:8000, документация OpenAPI — /docs
```

Прогон синхронный, результаты не хранятся: ответ на `POST /v1/runs` — единственная копия. Тело запроса
не больше 20 МБ, иначе 413.

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

| Код | Когда |
|---|---|
| 200 | вход разобран; ошибки графа, ресурсов и дедлайнов — в `diagnostics` |
| 422 | вход не соответствует схеме (E001, E070) — остальные проверки не выполнялись |
| 413 | тело больше лимита |

## `POST /v1/runs`

```json
{"problem": {"...": "problem.json"}, "policy": {"name": "fifo"}}
```

`policy` необязателен и целиком заменяет политику из входа.

| Код | Тело |
|---|---|
| 200 | `{"result": {...}, "events": [...]}` |
| 400 | `{"error": "..."}` — политика не реализована |
| 413 | `{"error": "..."}` — тело больше лимита |
| 422 | `{"diagnostics": [...]}` — запрос не соответствует схеме |

Ошибки самого входа (цикл, неизвестная зависимость) не делают запрос ошибочным: прогон возвращает 200
со статусом PARTIAL или INFEASIBLE и диагностиками в `result.diagnostics`.

`events` — журнал прогона ([формат](scheduling.md#журнал)). Схемы ответа: `result` —
[`schemas/result.schema.json`](../schemas/result.schema.json), запись журнала —
[`schemas/event.schema.json`](../schemas/event.schema.json). `result`:

| Поле | |
|---|---|
| `schema_version` | `"1.0"` |
| `input_hash` | хеш канонического JSON входа вместе с действующей политикой |
| `result_hash` | хеш журнала; совпадает у повторных прогонов того же входа |
| `policy` | имя политики |
| `status` | `OK` — выполнены все задачи; `PARTIAL` — не все; `INFEASIBLE` — `strict` нашёл ошибки, симуляции не было |
| `attempts` | попытки: `task`, `attempt`, `worker`, `start`, `end`, `resume_from`, `outcome` (`DONE`, `INT`), `saved` |
| `tasks` | по id: `status`, `start` (первой попытки), `finish`, `deadline`, `tardiness`, `waits` |
| `metrics` | [метрики](scheduling.md#метрики); `null` при INFEASIBLE |
| `replans` | записи `REPLAN` журнала: как внешние события сдвинули план ([формат](scheduling.md#перепланирование)); `null`, если задач больше `max_forecast_tasks` |
| `diagnostics` | ошибки и предупреждения входа |
| `violations` | нарушения, найденные проверкой расписания; пустой список — расписание допустимо |
