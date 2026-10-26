# REST API

```bash
uv sync --extra api
make api            # uvicorn на http://127.0.0.1:8000, документация OpenAPI — /docs
```

Каждый прогон идёт в отдельном процессе и сохраняется в `EXPSCHED_RUNS_DIR` (по умолчанию `runs/`). Тело запроса
не больше 20 МБ, иначе 413. Все тела запросов и ответов описаны в `/openapi.json`.

Пределы, чтобы сервер не повесил машину:

| Переменная | По умолчанию | |
|---|---|---|
| `EXPSCHED_RUN_WORKERS` | 2 | сколько прогонов идёт одновременно |
| `EXPSCHED_RUN_QUEUE` | 32 | сколько прогонов может идти и ждать всего; сверх этого — 503 |
| `EXPSCHED_RUN_TIMEOUT` | 60 | секунд на прогон, дальше — 504 |
| `EXPSCHED_RUN_MEMORY_MB` | 8192 | память рабочего процесса, дальше прогон падает с 500 `RUN_FAILED`; 0 — без предела; на Windows предела нет |
| `EXPSCHED_MAX_RUNS` | 100 | сколько прогонов хранится; лишние старые удаляются |
| `EXPSCHED_MAX_RUNS_MB` | 2048 | сколько места на диске занимают прогоны вместе; лишние старые удаляются |

What-if занимает места в очереди сразу под все ещё не посчитанные варианты: принимается целиком или получает 503.
Проверка входа, разбор тел и чтение хранилища идут вне цикла событий — большой вход не задерживает остальные
запросы.

| Метод и путь | Что делает |
|---|---|
| `GET /v1/policies` | реализованные политики и параметры по умолчанию |
| `GET /v1/scenarios`, `GET /v1/scenarios/{name}` | примеры входа |
| `POST /v1/validate` | проверка входа: диагностики, нижняя граница, критический путь |
| `POST /v1/runs` | прогон входа политикой; тот же вход отдаётся из хранилища |
| `GET /v1/runs` | последние прогоны |
| `GET /v1/runs/{run_id}`, `/problem`, `/events` | результат, вход, журнал страницами |
| `GET /v1/runs/{run_id}/explain` | цепочка, ожидания, простой, суженные до задачи, узла, момента |
| `POST /v1/runs/{run_id}/events` | новый прогон: тот же вход и ещё одно событие |
| `POST /v1/whatif` | варианты входа с одним изменением и разница метрик |

Обычный путь клиента: выбрать пример или собрать вход → `validate` → `runs` → журнал страницами и `explain` →
`events` или `whatif`, чтобы посмотреть, что изменится.

## Ошибки

Любая ошибка приходит в одном формате; `diagnostics` есть, только если вход не соответствует схеме.

```json
{"error": {"code": "SCHEMA_INVALID", "message": "input does not match the schema"},
 "diagnostics": [{"code": "E001", "level": "error", "detail": {"loc": "tasks.0.duration", "...": "..."}, "hint": "..."}]}
```

| Статус | `code` | Когда |
|---|---|---|
| 400 | `POLICY_NOT_IMPLEMENTED` | политика ещё не реализована |
| 404 | `NOT_FOUND` | нет такого пути или прогона (никогда не было или уже удалён как старый) |
| 405 | `METHOD_NOT_ALLOWED` | путь есть, метод не тот |
| 413 | `INPUT_TOO_LARGE` | тело больше лимита |
| 422 | `SCHEMA_INVALID` | тело не соответствует схеме; подробности в `diagnostics` |
| 422 | `INVALID_REQUEST` | неверный параметр запроса, например `mode` |
| 503 | `RUN_QUEUE_FULL` | в очереди уже `EXPSCHED_RUN_QUEUE` прогонов; повторить позже |
| 504 | `RUN_TIMEOUT` | прогон не уложился в `EXPSCHED_RUN_TIMEOUT`; он не сохраняется |
| 500 | `RUN_FAILED` | прогон упал в рабочем процессе (например, не хватило памяти); трассировка только в журнале сервера |
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
                  "hint": "Цикл: a → b → c → a. ..."}],
 "analysis": {"lower_bound": 4, "critical_path": ["free", "report"]}}
```

`analysis` — нижняя граница makespan и критический путь той части входа, которую можно планировать: задачи
с ошибками и их потомки не учитываются, поданные событиями задачи входят с моментом подачи как release_time.
`null`, если планировать нечего или режим strict нашёл ошибки.

Код 200 — вход разобран; ошибки графа, ресурсов и дедлайнов приходят в `diagnostics`. Если вход не
соответствует схеме (E001, E070), остальные проверки не выполняются и ответ — ошибка 422 `SCHEMA_INVALID`.

## `GET /v1/scenarios`, `GET /v1/scenarios/{name}`

Примеры входа из каталога `EXPSCHED_SCENARIOS_DIR` (по умолчанию `scenarios/` репозитория). Список — имя, число
задач (вместе с подаваемыми), узлов и событий, политика, единица времени. Пример целиком — `problem.json`;
без собственного `name` он получает имя файла. Имя — `[a-z0-9_-]`, неизвестное — 404.

## `GET /v1/runs?limit=50`

Последние сохранённые прогоны, новые первыми (`limit` до 500):

```json
{"runs": [{"run_id": "dc2f0ef68b28536c", "name": "urgent_failure", "policy": "priority", "status": "OK",
           "tasks": 11, "makespan": 22, "created_at": "2026-10-24T10:15:02.120+00:00"}]}
```

## `POST /v1/runs`

```json
{"problem": {"...": "problem.json"}, "policy": {"name": "fifo"}}
```

`policy` необязателен и целиком заменяет политику из входа. Ответ 201:

```json
{"run_id": "dc2f0ef68b28536c", "result": {"status": "OK", "...": "..."}}
```

`run_id` — первые 16 символов `input_hash`, поэтому тот же вход с той же политикой не пересчитывается: API
отдаёт сохранённый прогон. Ошибки — 400, 413, 422, 500, 503, 504 в [общем формате](#ошибки).

## `GET /v1/runs/{run_id}`

Тот же ответ, что у `POST /v1/runs`. `GET /v1/runs/{run_id}/problem` — вход прогона вместе с политикой, которой
он посчитан.

## `GET /v1/runs/{run_id}/events?from_seq=0&limit=1000`

Журнал страницами: записи с номерами `[from_seq, from_seq + limit)`, `limit` от 1 до 10 000.

```json
{"events": [{"seq": 0, "t": 0, "type": "WAIT", "...": "..."}], "total": 52, "next_seq": 1000}
```

`next_seq` — откуда читать следующую страницу, `null` — журнал прочитан.

## `POST /v1/runs/{run_id}/events`

Новый прогон: вход прогона `run_id` с его политикой и ещё одним событием в конце списка `events`. Исходный
прогон не меняется. Так проверяют «а если node-B упадёт в 12» или «а если подать срочную задачу».

```json
{"event": {"time": 12, "type": "worker_down", "worker": "node-B"}}
```

Ответ 201 — как у `POST /v1/runs`; что сдвинуло событие, видно в `result.replans`. Тот же вход с тем же
событием отдаёт сохранённый прогон. Событие для неизвестного узла — диагностика E040 в результате (в режиме
lenient событие отброшено); неизвестный `run_id` — 404, неверное событие — 422.

## `POST /v1/whatif`

Что если: исходный вход и до 16 его вариантов, в каждом — одно изменение. Все считаются той же политикой.

```json
{"problem": {"...": "problem.json"}, "policy": {"name": "priority"},
 "interventions": [{"kind": "set_checkpoint", "task": "pretrain_vit", "checkpoint_interval": 1},
                   {"kind": "remove_failures", "worker": "node-A"}]}
```

| `kind` | Поля | Что меняется |
|---|---|---|
| `add_worker` | `worker` — как в `cluster.workers` | в кластере на один узел больше |
| `remove_failures` | `worker` | у узла нет отказов и возвратов |
| `set_checkpoint` | `task`, `checkpoint_interval` (`null` — без чекпоинтов) | интервал чекпоинта |
| `set_preemptible` | `task`, `preemptible` (по умолчанию `true`) | можно ли вытеснять задачу |
| `set_priority` | `task`, `priority` | приоритет |
| `set_deadline` | `task`, `deadline` (`null` — снять) | дедлайн |

Задача может быть и во входе, и поданной событием. Ответ:

```json
{"baseline": {"run_id": "...", "status": "OK", "metrics": {"makespan": 22, "...": "..."}},
 "variants": [{"intervention": {"kind": "set_checkpoint", "...": "..."}, "run_id": "...", "status": "OK",
               "metrics": {"makespan": 21, "...": "..."},
               "delta": {"makespan": -1, "lost_work_gpu": -4, "utilization_gpu": 0.0265, "...": "..."}}]}
```

`delta` — вариант минус исходный по `makespan`, сумме `deadline_misses`, `weighted_tardiness`,
`utilization_gpu`, `lost_work_gpu`, `mean_wait`; `null`, если один из прогонов не симулировался. Каждый вариант —
сохранённый прогон: по `run_id` открываются его расписание и объяснения. Ссылка на неизвестные задачу или узел,
повтор id узла — 422 `INVALID_REQUEST`.

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
