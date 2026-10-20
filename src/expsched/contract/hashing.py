"""Канонический JSON и хеш: основа теста детерминизма и кеша API.

Правила совпадают с RFC 8785 (JCS) для наших типов: ключи отсортированы, пробелов нет,
UTF-8 без \\u-экранирования. Рассчитано на целые числа, строки, bool, None, списки и словари;
для чисел с плавающей точкой запись Python не гарантирует побайтового совпадения с JCS,
поэтому в журнале и входе времена и ресурсы — только целые.
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
