"""Общие утилиты (логирование, env) — расширяйте под корпоративные стандарты."""

from __future__ import annotations

import os


def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")
