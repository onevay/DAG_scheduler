"""Канонический JSON и его хеш: одинаковые данные дают одинаковые байты на любой машине.

Ключи отсортированы, пробелов нет, UTF-8 без экранирования. Для целых, строк, bool, None, списков и словарей
это совпадает с RFC 8785; числа с плавающей точкой так не гарантируются, поэтому в данных их нет.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    # allow_nan=False и отсутствие default: неподдерживаемое значение должно падать, а не искажать хеш.
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical_hash(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
