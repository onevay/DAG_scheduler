"""Лимиты размера входа: защищают от слишком больших файлов до их разбора."""

MAX_BODY_BYTES = 20 << 20  # 20 МБ
MAX_TASKS = 100_000
MAX_EDGES = 1_000_000


class InputTooLarge(ValueError):
    """Вход больше MAX_BODY_BYTES. Это отказ на границе, а не диагностика."""
