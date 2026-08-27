from __future__ import annotations

import asyncio
import functools
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardMarkup, Message, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import Settings, load_settings
from .service import MonitorService, format_amount
from .store import Store
from .trongrid import TronGridClient, TronGridRateLimitError
from .ui import dashboard_keyboard, detail_keyboard, rich_text, watches_keyboard


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

ADDRESS_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
PENDING_ACTION_KEY = "pending_action"
DISPLAY_TIMEZONE_NAME = "Asia/Shanghai"


def get_services(context: ContextTypes.DEFAULT_TYPE) -> tuple[Settings, Store, TronGridClient, MonitorService]:
    settings = context.application.bot_data["settings"]
    store = context.application.bot_data["store"]
    client = context.application.bot_data["client"]
    monitor = context.application.bot_data["monitor"]
    return settings, store, client, monitor


async def run_blocking(func, *args, **kwargs):
    return await asyncio.to_thread(functools.partial(func, *args, **kwargs))


def is_admin(user_id: int, settings: Settings) -> bool:
    return not settings.admin_user_ids or user_id in settings.admin_user_ids


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if user is None:
        return False
    settings, _, _, _ = get_services(context)
    if is_admin(user.id, settings):
        return True
    if update.effective_message is not None:
        await update.effective_message.reply_text("无权限。")
    return False


def set_pending_action(context: ContextTypes.DEFAULT_TYPE, action: str | None) -> None:
    if action is None:
        context.user_data.pop(PENDING_ACTION_KEY, None)
    else:
        context.user_data[PENDING_ACTION_KEY] = action


def get_pending_action(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    value = context.user_data.get(PENDING_ACTION_KEY)
    return str(value) if value else None


def parse_period(period: str, timezone_name: str = DISPLAY_TIMEZONE_NAME) -> tuple[str, int | None]:
    tz = ZoneInfo(timezone_name)
    now = datetime.now(tz)
    normalized = period.strip().lower() or "all"
    if normalized == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return "今日", int(start.timestamp() * 1000)
    if normalized == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return "本月", int(start.timestamp() * 1000)
    return "全部", None


def format_watch_line(row) -> str:
    remark = row.remark or "未备注"
    return f"{row.id}. {remark}\n{row.address}"


def _build_dashboard_text_legacy(watch_count: int, today_count: int, month_net: float) -> tuple[str, list]:
    return rich_text(
        [
            ("🧠", "brain"),
            (" TRC20 管理后台\n\n", None),
            ("🖥", "screen"),
            (f" 监听地址：{watch_count} 个\n", None),
            ("⏱️", "clock"),
            (f" 今日记录：{today_count} 笔\n", None),
            ("💰", "money"),
            (f" 本月净额：{format_amount(month_net)} USDT\n", None),
            ("✨", "sparkle"),
            (" 点下面按钮直接操作", None),
        ]
    )


def build_dashboard_text(
    watch_count: int,
    today_count: int,
    today_income: float,
    yesterday_income: float,
    month_net: float,
) -> tuple[str, list]:
    return rich_text(
        [
            ("🧠", "brain"),
            (" TRC20 管理后台\n\n", None),
            ("🖥", "screen"),
            (f" 监听地址：{watch_count} 个\n", None),
            ("⏱️", "clock"),
            (f" 今日记录：{today_count} 笔\n", None),
            ("➕", "today_income"),
            (f" 今日收入：{format_amount(today_income)} USDT\n", None),
            ("➕", "today_income"),
            (f" 昨日收入：{format_amount(yesterday_income)} USDT\n", None),
            ("💰", "money"),
            (f" 本月净额：{format_amount(month_net)} USDT\n", None),
            ("✨", "sparkle"),
            (" 时间统一为北京时间，点下面按钮直接操作", None),
        ]
    )


def build_panel_text(title: str, body: str, emoji_key: str) -> tuple[str, list]:
    emoji_char = {
        "screen": "🖥",
        "money": "💰",
        "clock": "⏱️",
        "camera": "📷",
        "ok": "🆗",
        "warn": "⚠️",
        "plus": "➕",
        "light": "💡",
    }[emoji_key]
    return rich_text([(emoji_char, emoji_key), (f" {title}\n\n{body}", None)])


async def render_message(
    target: Message,
    text: str,
    entities,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    await target.reply_text(text=text, entities=entities, reply_markup=reply_markup)


async def render_callback(
    update: Update,
    text: str,
    entities,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    query = update.callback_query
    if query is None:
        return
    try:
        await query.edit_message_text(text=text, entities=entities, reply_markup=reply_markup)
    except BadRequest:
        if query.message is not None:
            await query.message.reply_text(text=text, entities=entities, reply_markup=reply_markup)


async def show_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, *, use_edit: bool) -> None:
    _, store, _, _ = get_services(context)
    _, today_since = parse_period("day")
    _, month_since = parse_period("month")
    beijing_tz = ZoneInfo(DISPLAY_TIMEZONE_NAME)
    today_start = datetime.now(beijing_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_since = int((today_start.timestamp() - 86400) * 1000)
    watches = await run_blocking(store.list_watches)
    today_count = await run_blocking(store.count_events, today_since)
    today_stats = await run_blocking(store.get_stats, today_since)
    yesterday_stats = await run_blocking(store.get_stats, yesterday_since, today_since)
    month_stats = await run_blocking(store.get_stats, month_since)
    text, entities = build_dashboard_text(
        len(watches),
        today_count,
        today_stats.amount_in,
        yesterday_stats.amount_in,
        month_stats.net,
    )
    if use_edit and update.callback_query is not None:
        await render_callback(update, text, entities, dashboard_keyboard())
    elif update.effective_message is not None:
        await render_message(update.effective_message, text, entities, dashboard_keyboard())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    set_pending_action(context, None)
    await show_dashboard(update, context, use_edit=False)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    set_pending_action(context, None)

    settings, store, client, monitor = get_services(context)
    data = str(query.data or "")
    parts = data.split(":")

    if data == "menu:home":
        await show_dashboard(update, context, use_edit=True)
        return

    if data == "menu:add":
        text, entities = rich_text(
            [
                ("➕", "plus"),
                (" 添加监听地址\n\n", None),
                ("💡", "light"),
                (" 直接发送：\n地址 备注\n\n示例：\nTXXX 收款主地址", None),
            ]
        )
        set_pending_action(context, "add_watch")
        await render_callback(update, text, entities, detail_keyboard())
        return

    if data == "menu:watches":
        watches = await run_blocking(store.list_watches)
        if not watches:
            text, entities = rich_text([("⚠️", "warn"), (" 当前还没有监听地址。", None)])
            await render_callback(update, text, entities, detail_keyboard())
            return
        body = "\n\n".join(format_watch_line(row) for row in watches)
        text, entities = build_panel_text("地址列表", body, "screen")
        await render_callback(update, text, entities, watches_keyboard([row.id for row in watches]))
        return

    if data == "menu:scan":
        inserted = await monitor.poll_once(context.application.bot)
        text, entities = rich_text([("🆗", "ok"), (f" 扫描完成\n\n新增记录：{inserted} 笔", None)])
        await render_callback(update, text, entities, detail_keyboard())
        return

    if data == "menu:balance_all":
        watches = await run_blocking(store.list_watches)
        if not watches:
            text, entities = rich_text([("⚠️", "warn"), (" 当前还没有监听地址。", None)])
            await render_callback(update, text, entities, detail_keyboard())
            return
        chunks: list[str] = []
        for row in watches:
            try:
                trx_balance, usdt_balance = await client.fetch_account_balance(row.address)
                trx_text = format_amount(trx_balance)
                usdt_text = format_amount(usdt_balance)
            except TronGridRateLimitError:
                trx_text = "限流中"
                usdt_text = "限流中"
            chunks.append(
                f"{row.remark or '未备注'}\n{row.address}\nTRX：{trx_text}\nUSDT：{usdt_text}"
            )
        text, entities = build_panel_text("全部余额", "\n\n".join(chunks), "money")
        await render_callback(update, text, entities, detail_keyboard())
        return

    if data.startswith("menu:history:"):
        period = parts[2]
        label, since_ts = parse_period(period)
        rows = await run_blocking(store.list_events, since_ts=since_ts, limit=settings.history_default_limit)
        if not rows:
            text, entities = rich_text([("⚠️", "warn"), (f" {label}暂无记录。", None)])
            await render_callback(update, text, entities, detail_keyboard())
            return
        lines = []
        tz = ZoneInfo(DISPLAY_TIMEZONE_NAME)
        for row in rows:
            when = datetime.fromtimestamp(int(row["block_timestamp"]) / 1000, tz=tz).strftime("%m-%d %H:%M")
            direction = "转入" if row["direction"] == "in" else "转出"
            lines.append(f"{when} {direction} {format_amount(float(row['amount']))} USDT\n{row['remark'] or '未备注'} | {row['tx_hash']}")
        text, entities = build_panel_text(f"{label}记录", "\n\n".join(lines), "clock")
        await render_callback(update, text, entities, detail_keyboard())
        return

    if data.startswith("menu:stats:"):
        period = parts[2]
        label, since_ts = parse_period(period)
        stats = await run_blocking(store.get_stats, since_ts)
        body = (
            f"转入笔数：{stats.count_in}\n"
            f"转出笔数：{stats.count_out}\n"
            f"转入总额：{format_amount(stats.amount_in)} USDT\n"
            f"转出总额：{format_amount(stats.amount_out)} USDT\n"
            f"净额：{format_amount(stats.net)} USDT"
        )
        text, entities = build_panel_text(f"{label}收益统计", body, "money")
        await render_callback(update, text, entities, detail_keyboard())
        return

    if data == "menu:tx":
        text, entities = rich_text(
            [
                ("📷", "camera"),
                (" 查询交易\n\n", None),
                ("💡", "light"),
                (" 直接发送交易哈希，我来返回详情和链接。", None),
            ]
        )
        set_pending_action(context, "tx_lookup")
        await render_callback(update, text, entities, detail_keyboard())
        return

    if data.startswith("watch:delete:"):
        watch_id = parts[2]
        deleted = await run_blocking(store.delete_watch, watch_id)
        if deleted:
            text, entities = rich_text([("🆗", "ok"), (" 删除成功。", None)])
        else:
            text, entities = rich_text([("⚠️", "warn"), (" 没找到这个地址。", None)])
        await render_callback(update, text, entities, detail_keyboard("menu:watches"))
        return

    if data.startswith("watch:balance:"):
        watch_id = parts[2]
        row = await run_blocking(store.get_watch, watch_id)
        if row is None:
            text, entities = rich_text([("⚠️", "warn"), (" 没找到这个地址。", None)])
            await render_callback(update, text, entities, detail_keyboard("menu:watches"))
            return
        try:
            trx_balance, usdt_balance = await client.fetch_account_balance(row.address)
            trx_text = format_amount(trx_balance)
            usdt_text = format_amount(usdt_balance)
        except TronGridRateLimitError:
            trx_text = "限流中"
            usdt_text = "限流中"
        body = (
            f"{row.remark or '未备注'}\n"
            f"{row.address}\n"
            f"TRX：{trx_text}\n"
            f"USDT：{usdt_text}"
        )
        text, entities = build_panel_text("地址余额", body, "money")
        await render_callback(update, text, entities, detail_keyboard("menu:watches"))
        return

    text, entities = rich_text([("⚠️", "warn"), (" 暂不支持这个按钮。", None)])
    await render_callback(update, text, entities, detail_keyboard())


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    message = update.effective_message
    if message is None or not message.text:
        return
    action = get_pending_action(context)
    settings, store, _, _ = get_services(context)

    if action == "add_watch":
        raw = message.text.strip()
        parts = raw.split(maxsplit=1)
        address = parts[0].strip() if parts else ""
        remark = parts[1].strip() if len(parts) > 1 else ""
        if not ADDRESS_RE.match(address):
            text, entities = rich_text([("⚠️", "warn"), (" 地址格式不对，请重新发送。", None)])
            await render_message(message, text, entities, detail_keyboard())
            return
        watch = await run_blocking(store.add_watch, address, remark)
        set_pending_action(context, None)
        text, entities = build_panel_text("保存成功", format_watch_line(watch), "ok")
        await render_message(message, text, entities, detail_keyboard())
        return

    if action == "tx_lookup":
        tx_hash = message.text.strip()
        rows = await run_blocking(store.find_events_by_tx_hash, tx_hash)
        set_pending_action(context, None)
        if not rows:
            text, entities = rich_text([("⚠️", "warn"), (" 本地没有这笔交易记录。", None)])
            await render_message(message, text, entities, detail_keyboard())
            return
        row = rows[0]
        when = datetime.fromtimestamp(int(row["block_timestamp"]) / 1000, tz=ZoneInfo(DISPLAY_TIMEZONE_NAME)).strftime("%Y-%m-%d %H:%M:%S")
        body = (
            f"备注：{row['remark'] or '未备注'}\n"
            f"监听地址：{row['owner_address']}\n"
            f"方向：{'转入' if row['direction'] == 'in' else '转出'}\n"
            f"金额：{format_amount(float(row['amount']))} USDT\n"
            f"来源：{row['from_address']}\n"
            f"去向：{row['to_address']}\n"
            f"时间：{when}\n"
            f"确认：{'是' if row['confirmed'] else '否'}"
        )
        text, entities = build_panel_text("交易详情", body, "camera")
        await render_message(message, text, entities, detail_keyboard(tx_hash=row["tx_hash"]))
        return

    await show_dashboard(update, context, use_edit=False)


async def poll_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    _, _, _, monitor = get_services(context)
    await monitor.poll_once(context.application.bot)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled bot error", exc_info=context.error)


async def post_shutdown(application: Application) -> None:
    client: TronGridClient = application.bot_data["client"]
    await client.close()


def build_application(settings: Settings) -> Application:
    store = Store(settings.database_path)
    client = TronGridClient(settings)
    monitor = MonitorService(settings, store, client)

    application = ApplicationBuilder().token(settings.bot_token).post_shutdown(post_shutdown).build()
    application.bot_data["settings"] = settings
    application.bot_data["store"] = store
    application.bot_data["client"] = client
    application.bot_data["monitor"] = monitor

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_error_handler(on_error)

    if application.job_queue is not None:
        application.job_queue.run_repeating(
            poll_job,
            interval=settings.poll_seconds,
            first=5,
            name="poll_trc20_watch_addresses",
        )
    return application


def main() -> None:
    settings = load_settings()
    application = build_application(settings)
    application.run_polling(drop_pending_updates=True)
