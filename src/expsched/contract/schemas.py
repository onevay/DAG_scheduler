"""JSON Schema входа, результата, журнала и протокола агента; `make schema` пересобирает их в schemas/."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from expsched.contract.agent import dump_agent_schema
from expsched.contract.io_models import dump_schema
from expsched.contract.output_models import dump_event_schema, dump_result_schema

SCHEMAS: dict[str, Callable[[], str]] = {
    "problem.schema.json": dump_schema,
    "result.schema.json": dump_result_schema,
    "event.schema.json": dump_event_schema,
    "agent.schema.json": dump_agent_schema,
}


def write_all(directory: Path) -> None:
    for name, dump in SCHEMAS.items():
        # LF на любой ОС: CI сравнивает файлы побайтово
        (directory / name).write_text(dump() + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write_all(Path(sys.argv[1]))
