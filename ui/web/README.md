# Веб-интерфейс

Клиент [REST API](../../docs/api.md) на React, TypeScript и Vite. Вид — по макету из [`../design/`](../design/README.md):
цвета, шрифты, радиусы и анимации берутся из `tokens.css` и `motion.css`, стили компонентов — CSS Modules рядом с
компонентом.

Нужен Node.js 20 или новее. Команды — из корня репозитория:

```bash
make web-install   # зависимости по package-lock.json
make api           # API на http://127.0.0.1:8000, в соседнем терминале
make web-dev       # интерфейс на http://127.0.0.1:5173; запросы /v1 уходят на API
make web-lint      # ESLint, Prettier, tsc
make web-test      # Vitest
make web-build     # сборка в ui/web/dist
```

Типы API генерируются из `schemas/openapi.json`: после изменения моделей API — `make schema`, затем
`npm --prefix ui/web run gen:types`; CI проверяет, что `src/api/schema.ts` совпадает со схемой. Запросы идут на тот
же адрес, что у страницы; другой API можно указать в `VITE_API_URL`.

```
src/
├── main.tsx       точка входа: токены, анимации, общие стили
├── App.tsx        оболочка: колонка разделов и экран
├── api/           клиент API: schema.ts (генерируется), types.ts (короткие имена), client.ts (запросы, ApiError),
│                  hooks.ts (запросы через TanStack Query)
├── shell/         колонка разделов, иконки, список разделов
└── test/setup.ts  настройка тестов (jest-dom)
```

Тексты интерфейса — на русском, прямо в компонентах.
