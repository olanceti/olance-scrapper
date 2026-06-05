"""Configuração lida de variáveis de ambiente (.env localmente, secrets no CI)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Config:
    olance_url: str
    cron_secret: str
    batch_size: int
    headless: bool
    detail_min_delay: float
    detail_max_delay: float


def load_config() -> Config:
    olance_url = (os.getenv("OLANCE_URL") or "").rstrip("/")
    cron_secret = os.getenv("CRON_SECRET") or ""

    if not olance_url or not cron_secret:
        raise SystemExit("❌ OLANCE_URL e CRON_SECRET são obrigatórios (configure no .env ou secrets)")

    return Config(
        olance_url=olance_url,
        cron_secret=cron_secret,
        batch_size=_get_int("BATCH_SIZE", 300),
        headless=_get_bool("HEADLESS", True),
        detail_min_delay=_get_float("DETAIL_MIN_DELAY", 2.0),
        detail_max_delay=_get_float("DETAIL_MAX_DELAY", 4.0),
    )
