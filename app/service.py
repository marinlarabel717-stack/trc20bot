from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from .config import Settings
from .models import TransferEvent, WatchAddress
from .store import Store
from .trongrid import TronGridClient, TronGridRateLimitError, normalize_transfer
from .ui import rich_text


logger = logging.getLogger(__name__)


def format_amount(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def short_hash(tx_hash: str) -> str:
    if len(tx_hash) <= 12:
        return tx_hash
    return f"{tx_hash[:4]}…{tx_hash[-4:]}"


class MonitorService:
    def __init__(self, settings: Settings, store: Store, client: TronGridClient) -> None:
        self.settings = settings
        self.store = store
        self.client = client
        self.timezone = ZoneInfo(settings.timezone_name)
        self._poll_lock = asyncio.Lock()

    async def poll_once(self, bot: Bot | None = None) -> int:
        if self._poll_lock.locked():
            return 0
        async with self._poll_lock:
            total_inserted = 0
            for watch in self.store.list_watches():
                total_inserted += await self._poll_watch(watch, bot)
            return total_inserted

    async def _poll_watch(self, watch: WatchAddress, bot: Bot | None) -> int:
        try:
            items = await self.client.fetch_usdt_transfers(watch)
        except Exception:
            logger.exception("Failed to fetch transfers for %s", watch.address)
            return 0

        max_ts = watch.last_scan_ts
        inserted_count = 0
        for item in sorted(items, key=lambda row: int(row.get("block_timestamp") or row.get("block_ts") or 0)):
            event = normalize_transfer(item, watch, self.settings.trc20_usdt_contract)
            if event is None:
                continue
            max_ts = max(max_ts, event.block_timestamp)
            if not self.store.insert_event(event):
                continue
            inserted_count += 1
            if bot is not None:
                await self._send_notification(bot, event)

        if max_ts > watch.last_scan_ts:
            self.store.update_watch_scan_ts(watch.id, max_ts)
        return inserted_count

    async def _send_notification(self, bot: Bot, event: TransferEvent) -> None:
        block_time = datetime.fromtimestamp(event.block_timestamp / 1000, tz=self.timezone).strftime("%Y-%m-%d %H:%M:%S")
        today_start = datetime.now(self.timezone).replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_ms = int(today_start.timestamp() * 1000)
        today_stats = self.store.get_stats(today_start_ms)
        try:
            _, usdt_balance = await self.client.fetch_account_balance(event.owner_address)
            balance_text = format_amount(usdt_balance)
        except TronGridRateLimitError:
            balance_text = "限流中"
        trade_type = "收入" if event.direction == "in" else "支出"

        text, entities = rich_text(
            [
                ("📢", "trade_type"),
                (f"交易类型：{trade_type}\n", None),
                ("🪙", "trade_amount"),
                (f"交易金额：{format_amount(event.amount)} USDT\n", None),
                ("⬆️", "addr_out"),
                ("出账地址：\n", None),
                (f"{event.from_address}\n", None),
                ("⬇️", "addr_in"),
                ("入账地址：\n", None),
                (f"{event.to_address}\n", None),
                ("✏️", "trade_time"),
                (f"交易时间：{block_time}\n", None),
                ("💊", "trade_hash"),
                (f"交易哈希：{short_hash(event.tx_hash)}\n\n", None),
                ("➕", "today_income"),
                (f"今日收入：{format_amount(today_stats.amount_in)}\n", None),
                ("🚫", "today_expense"),
                (f"今日支出：{format_amount(today_stats.amount_out)}\n", None),
                ("💰", "today_profit"),
                (f"今日利润：{format_amount(today_stats.net)}\n", None),
                ("🪙", "usdt_balance"),
                (f"USDT余额：{balance_text}", None),
            ]
        )
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📷 查看交易", url=f"https://tronscan.org/#/transaction/{event.tx_hash}")],
                [InlineKeyboardButton("🏠 打开后台", callback_data="menu:home")],
            ]
        )
        for chat_id in self.settings.notify_chat_ids:
            try:
                await bot.send_message(chat_id=chat_id, text=text, entities=entities, reply_markup=keyboard)
            except Exception:
                logger.exception("Failed to send notification to %s", chat_id)
