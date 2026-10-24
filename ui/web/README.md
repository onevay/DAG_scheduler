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

```
src/
├── main.tsx       точка входа: токены, анимации, общие стили
├── App.tsx        оболочка: колонка разделов и экран
├── shell/         колонка разделов, иконки, список разделов
└── test/setup.ts  настройка тестов (jest-dom)
```

Тексты интерфейса — на русском, прямо в компонентах.
