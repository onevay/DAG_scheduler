"""OpenAPI-описание API; из него генерируются типы клиентов. `make schema` пишет schemas/openapi.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from expsched.api.app import create_app


def dump_openapi() -> str:
    return json.dumps(create_app().openapi(), ensure_ascii=False, indent=2, sort_keys=True)


if __name__ == "__main__":
    # LF на любой ОС: CI сравнивает файл побайтово
    Path(sys.argv[1]).write_text(dump_openapi() + "\n", encoding="utf-8", newline="\n")
