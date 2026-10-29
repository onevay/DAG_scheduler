# Контейнеры

- [`workload/`](workload/Containerfile) — образ встроенной нагрузки агента (`make workload-image`).
- [`gravesentinel/`](gravesentinel/README.md) — гипервизор чекпоинтов: привилегированный демон над rootful
  Podman и CRIU, который регистрирует контейнеры на парах CPU, снимает чекпоинты и восстанавливает контейнер из
  последнего поколения, сначала убедившись, что прежнего экземпляра нет.

## gravesentinel

Перенесён из [github.com/crystarm/gravesentinel](https://github.com/crystarm/gravesentinel), коммит
`3e9c7484f7ed0d5a84b23d630692a7be3f1a3637` (2026-10-26, протокол v3), без `.git` и `.github`: его сборка и
проверки — задачи CI этого репозитория. Дальше код меняется здесь, вместе с планировщиком и агентом; обратно
в исходный репозиторий изменения не переносятся.

C++23, Bazel 8.8.2 (`.bazelversion`, Bzlmod); корень сборки — `containers/gravesentinel/`:

```bash
cd containers/gravesentinel
bazel build --config=release //...
bazel test //:unit_tests                      # и с --config=asan | ubsan | tsan
```

Сборка и unit-тесты идут на любом Linux x86-64 с C++23-компилятором. Rootful-наборы
(`//tests/integration:hypervisor_*_e2e`) разрушительны и идут только на квалифицированном стенде
([`docs/testbed.md`](gravesentinel/docs/testbed.md)): Debian 13, ядро 6.12, cgroup v2 под systemd, свободные
CPU 0–7, rootful Podman 5.4.2, crun 1.21, CRIU 4.1.1. Перед ними — `scripts/check_testbed.sh` от root; Bazel
через `sudo` не запускается.

Запуск демона, его параметры и systemd-юнит — [`docs/operations.md`](gravesentinel/docs/operations.md);
протокол для клиента — [`docs/architecture.md`](gravesentinel/docs/architecture.md) и
[`docs/scheduler-client.md`](gravesentinel/docs/scheduler-client.md).

### CI

Workflow `ci-gravesentinel` (`.github/workflows/gravesentinel.yml`):

- `unit` (ubuntu-24.04, при изменениях в `containers/gravesentinel/`): release-сборка и unit-тесты, они же под
  asan, ubsan и tsan;
- `e2e` — только при ручном запуске (Actions → ci-gravesentinel → Run workflow), после `unit`: self-hosted
  runner с метками `self-hosted, linux, x64, gravesentinel-e2e` от uid 0, проверка стенда и rootful-наборы;
- `agent-smoke` — после `e2e`, там же: агент expsched на этом гипервизоре проходит прогон с отказом узла и
  восстановлением из снимка CRIU ([`docs/agent.md`](../docs/agent.md#сквозной-прогон)).

Shell-скрипты должны быть исполняемыми (`scripts/*.sh`, `tests/**/*.sh`). Git под Windows этот бит не хранит,
поэтому при первом добавлении в индекс:

```bash
git add containers/gravesentinel
git ls-files 'containers/gravesentinel/*.sh' | xargs git update-index --chmod=+x
```
