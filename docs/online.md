# Online scheduler

`expsched.online` — первый вертикальный slice live-планирования поверх существующих доменных моделей и
политик. Он принимает реальный `Problem`, передаёт политикам обычный `Snapshot` и валидирует их `Decision`,
но, в отличие от `engine/simulator.py`, не создаёт событие завершения по длительности задачи.

## Быстрый пример

```python
from expsched.domain.models import Problem, Task, Worker
from expsched.domain.resources import Resources
from expsched.online import OnlineScheduler
from expsched.policies.fifo import FifoPolicy

worker = Worker("worker-1", Resources({"cpu": 8, "license": 1}))
task = Task("train", duration=30, resources=Resources({"cpu": 4, "license": 1}))
scheduler = OnlineScheduler(Problem({task.id: task}, {worker.id: worker}), FifoPolicy())

(intent,) = scheduler.plan(now=0)       # READY -> STARTING, ресурсы уже удерживаются
send_to_worker(intent)
scheduler.ack_start("ack/train/1", intent, now=1)  # STARTING -> RUNNING

# end_pred — только прогноз. Даже после него задача остаётся RUNNING.
scheduler.task_exited("exit/train/1", intent, success=True, now=28)
assert scheduler.statuses["train"] == "SUCCEEDED"
```

`send_to_worker` в примере — код интеграции с конкретной системой исполнения и не входит в пакет.

## Публичный API

Пакет экспортирует:

- `OnlineScheduler(problem, policy)` — coordinator с одним последовательным writer;
- `StartIntent` — неизменяемое описание попытки запуска;
- `TaskPhase` — `PENDING`, `READY`, `STARTING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `BLOCKED`;
- `OnlineError` и специализированные ошибки валидации.

Основные операции `OnlineScheduler`:

| Метод | Назначение |
|---|---|
| `plan(now)` | Получить и атомарно применить START-решения policy; вернуть упорядоченные intents |
| `ack_start(event_id, intent, now=...)` | Подтвердить реальный запуск |
| `reject_start(event_id, intent, now=...)` | Отклонить запуск, освободить intent и вернуть задачу в READY |
| `task_exited(event_id, intent, success=..., now=...)` | Зафиксировать фактический успешный/неуспешный exit |
| `submit_task(event_id, task, now=...)` | Добавить задачу, зависимости которой уже известны |
| `worker_down(...)` / `worker_up(...)` | Изменить доступность существующего worker |
| `snapshot()` | Получить текущий `Snapshot` для наблюдения |

`now` — неотрицательное целое логическое время; назад оно не переводится. `statuses`, `problem` и `now`
доступны как read-only свойства.

## Lifecycle и ресурсы

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> READY: release и все deps SUCCEEDED
    READY --> STARTING: plan / start intent
    STARTING --> RUNNING: ack_start
    STARTING --> READY: reject_start
    RUNNING --> SUCCEEDED: observed successful exit
    RUNNING --> FAILED: observed failed exit
    FAILED --> BLOCKED: descendants
```

Ресурсы вычитаются при создании `StartIntent`: это предотвращает двойную выдачу, пока start находится in
flight. Ack ресурсы не меняет. Они возвращаются только после `reject_start` или фактического `task_exited`.
Ключи ресурсов не фиксированы: используются операции существующего `Resources`, поэтому одинаково работают
`gpu`, `cpu`, `ram_gb`, лицензии и любые другие ключи.

`Task.duration` трактуется только как `duration_estimate`. `StartIntent.end_pred` прогнозирует окончание при
немедленном старте, а после ack `Snapshot.running[*].end_pred` пересчитывается от времени подтверждения.
Оба значения помогают policy оценивать будущую доступность, но coordinator **никогда** не переводит задачу в
terminal по наступлению прогноза. Если задача пережила прогноз, следующий snapshot оставляет ей положительный
остаток и сдвигает `end_pred` как минимум на следующий tick. Terminal-событие приходит только от executor через
`task_exited`.

Успешный exit освобождает прямых зависимых после выполнения всех их dependencies. Неуспешный exit терминален
для текущего slice и рекурсивно переводит незавершённых потомков в `BLOCKED`; `Task.max_retries` здесь пока не
интерпретируется.

## Детерминизм и защита от повторов

Policy видит те же `Problem`, `Task`, `Resources`, `Snapshot`, `Decision` и результат `analyze`, что и offline
ядро. Весь список решений сначала проверяется на текущем snapshot и временной копии свободных ресурсов. Если
хотя бы одно решение ошибочно, ни один intent из списка не создаётся. Валидные intents возвращаются в
каноническом порядке `(task, worker, attempt)` независимо от порядка словарей.

Поддерживается только `Decision(action="START", ...)`. `PREEMPT` и `RESERVE` завершаются явной
`UnsupportedDecisionError`; поэтому для первого slice подходят существующие `fifo`, `fifo_skip` и `priority`.
`priority_backfill` допустим только в эпохах, где он не выдаёт `RESERVE`.

Каждая внешняя мутация имеет непустой `event_id`. Повтор идентичного события — no-op даже после продвижения
логического времени; повтор того же id с другим типом, временем или payload даёт `EventConflictError`.
Неуспешно валидированное событие не запоминается и может быть исправлено с тем же id.

## Worker down/up и ограничения slice

`worker_down` запрещает новые старты и показывает нулевой `free`, но не считает процессы завершившимися и не
освобождает их allocations. После `worker_up` свободная ёмкость пересчитывается как capacity минус все intents
и runs, для которых ещё не было reject/exit. Intent на упавшем worker нельзя ack-нуть: его нужно явно reject-нуть.

Ограничения текущей версии:

- только один последовательный writer; встроенного durable event store нет;
- idempotency хранится в памяти процесса;
- workers нельзя добавлять или менять их capacity, можно только переключать up/down;
- входной `Problem` должен иметь пустой `events` и строгий DAG с известными dependencies и хотя бы одним
  eligible worker для каждой задачи;
- `submit_task` добавляет только новую вершину со ссылками на уже известные задачи;
- retry, cancellation, preemption и reservation пока не реализованы.
