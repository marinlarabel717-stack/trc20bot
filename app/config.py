from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv


def _parse_int_set(raw: str) -> set[int]:
    values: set[int] = set()
    for part in (raw or "").split(","):
        text = part.strip()
        if not text:
            continue
        values.add(int(text))
    return values


def _parse_int_list(raw: str) -> list[int]:
    values: list[int] = []
    for part in (raw or "").split(","):
        text = part.strip()
        if not text:
            continue
        values.append(int(text))
    return values


def _parse_str_list(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


@dataclass(slots=True)
class Settings:
    bot_token: str
    admin_user_ids: set[int]
    notify_chat_ids: list[int]
    database_path: Path
    timezone_name: str
    trongrid_api_base: str
    trongrid_api_keys: list[str]
    trc20_usdt_contract: str
    request_timeout: int
    poll_seconds: int
    page_limit: int
    max_pages: int
    lookback_minutes: int
    only_confirmed: bool
    history_default_limit: int


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValueError("BOT_TOKEN not configured")

    admin_user_ids = _parse_int_set(os.getenv("ADMIN_USER_IDS", ""))
    notify_chat_ids = _parse_int_list(os.getenv("NOTIFY_CHAT_IDS", ""))
    if not notify_chat_ids and admin_user_ids:
        notify_chat_ids = sorted(admin_user_ids)

    database_path = Path(os.getenv("DATABASE_PATH", "data/trc20bot.db")).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    return Settings(
        bot_token=bot_token,
        admin_user_ids=admin_user_ids,
        notify_chat_ids=notify_chat_ids,
        database_path=database_path,
        timezone_name=os.getenv("TIMEZONE", "Asia/Shanghai").strip() or "Asia/Shanghai",
        trongrid_api_base=os.getenv("TRONGRID_API_BASE", "https://api.trongrid.io/v1").strip().rstrip("/"),
        trongrid_api_keys=_parse_str_list(os.getenv("TRONGRID_API_KEYS", "")),
        trc20_usdt_contract=os.getenv(
            "TRC20_USDT_CONTRACT",
            "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
        ).strip(),
        request_timeout=max(5, int(os.getenv("TRONGRID_REQUEST_TIMEOUT", "20"))),
        poll_seconds=max(3, int(os.getenv("TRONGRID_POLL_SECONDS", "6"))),
        page_limit=max(1, min(int(os.getenv("TRONGRID_PAGE_LIMIT", "100")), 200)),
        max_pages=max(1, int(os.getenv("TRONGRID_MAX_PAGES", "10"))),
        lookback_minutes=max(1, int(os.getenv("TRONGRID_LOOKBACK_MINUTES", "30"))),
        only_confirmed=os.getenv("TRONGRID_ONLY_CONFIRMED", "false").strip().lower() in {"1", "true", "yes", "on"},
        history_default_limit=max(1, int(os.getenv("HISTORY_DEFAULT_LIMIT", "15"))),
    )
