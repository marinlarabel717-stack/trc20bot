from __future__ import annotations

import asyncio
import functools
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

from .config import Settings, load_settings
from .service import MonitorService, format_amount
from .store import Store
from .trongrid import TronGridClient


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

ADDRESS_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")


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
    if update.message is not None:
        await update.message.reply_text("无权限。")
    return False


def format_watch_line(row) -> str:
    remark = row.remark or "未备注"
    return f"{row.id}. {remark}\n{row.address}"


def parse_period(period: str, timezone_name: str) -> tuple[str, int | None]:
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


def format_event(row, timezone_name: str) -> str:
    tz = ZoneInfo(timezone_name)
    when = datetime.fromtimestamp(int(row["block_timestamp"]) / 1000, tz=tz).strftime("%m-%d %H:%M")
    direction = "转入" if row["direction"] == "in" else "转出"
    remark = str(row["remark"] or "未备注")
    return f"{when} {direction} {format_amount(float(row['amount']))} USDT\n{remark} | {row['tx_hash']}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    text = (
        "TRC20 监听机器人已启动。\n\n"
        "/addaddr <地址> [备注]\n"
        "/deladdr <id|地址>\n"
        "/listaddr\n"
        "/balance [id|地址]\n"
        "/history [day|month|all] [limit]\n"
        "/stats [day|month|all]\n"
        "/tx <hash>\n"
        "/scan"
    )
    if update.message is not None:
        await update.message.reply_text(text)


async def addaddr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    if update.message is None:
        return
    if not context.args:
        await update.message.reply_text("用法：/addaddr <TRC20地址> [备注]")
        return
    address = context.args[0].strip()
    if not ADDRESS_RE.match(address):
        await update.message.reply_text("地址格式不对。")
        return
    remark = " ".join(context.args[1:]).strip()
    _, store, _, _ = get_services(context)
    watch = await run_blocking(store.add_watch, address, remark)
    await update.message.reply_text(f"已保存监听地址。\n\n{format_watch_line(watch)}")


async def deladdr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    if update.message is None:
        return
    if not context.args:
        await update.message.reply_text("用法：/deladdr <id|地址>")
        return
    _, store, _, _ = get_services(context)
    deleted = await run_blocking(store.delete_watch, context.args[0])
    await update.message.reply_text("已删除。" if deleted else "没找到对应地址。")


async def listaddr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    if update.message is None:
        return
    _, store, _, _ = get_services(context)
    rows = await run_blocking(store.list_watches)
    if not rows:
        await update.message.reply_text("当前还没有监听地址。")
        return
    text = "监听地址列表：\n\n" + "\n\n".join(format_watch_line(row) for row in rows)
    await update.message.reply_text(text)


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    if update.message is None:
        return
    _, store, client, _ = get_services(context)
    if context.args:
        row = await run_blocking(store.get_watch, context.args[0])
        rows = [row] if row is not None else []
    else:
        rows = await run_blocking(store.list_watches)
    if not rows:
        await update.message.reply_text("没找到要查询的地址。")
        return

    lines = []
    for row in rows:
        trx_balance, usdt_balance = await client.fetch_account_balance(row.address)
        lines.append(
            f"{row.remark or '未备注'}\n"
            f"{row.address}\n"
            f"TRX：{format_amount(trx_balance)}\n"
            f"USDT：{format_amount(usdt_balance)}"
        )
    await update.message.reply_text("\n\n".join(lines))


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    if update.message is None:
        return
    settings, store, _, _ = get_services(context)
    period = context.args[0] if context.args else "all"
    limit = int(context.args[1]) if len(context.args) > 1 and context.args[1].isdigit() else settings.history_default_limit
    label, since_ts = parse_period(period, settings.timezone_name)
    rows = await run_blocking(store.list_events, since_ts=since_ts, limit=limit)
    if not rows:
        await update.message.reply_text(f"{label}暂无记录。")
        return
    text = f"{label}历史记录：\n\n" + "\n\n".join(format_event(row, settings.timezone_name) for row in rows)
    await update.message.reply_text(text)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    if update.message is None:
        return
    settings, store, _, _ = get_services(context)
    period = context.args[0] if context.args else "month"
    label, since_ts = parse_period(period, settings.timezone_name)
    summary = await run_blocking(store.get_stats, since_ts)
    text = (
        f"{label}收益统计\n\n"
        f"转入笔数：{summary.count_in}\n"
        f"转出笔数：{summary.count_out}\n"
        f"转入总额：{format_amount(summary.amount_in)} USDT\n"
        f"转出总额：{format_amount(summary.amount_out)} USDT\n"
        f"净额：{format_amount(summary.net)} USDT"
    )
    await update.message.reply_text(text)


async def tx(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    if update.message is None:
        return
    if not context.args:
        await update.message.reply_text("用法：/tx <交易哈希>")
        return
    settings, store, _, _ = get_services(context)
    rows = await run_blocking(store.find_events_by_tx_hash, context.args[0].strip())
    if not rows:
        await update.message.reply_text("本地没有这笔交易记录。")
        return
    parts = []
    for row in rows:
        when = datetime.fromtimestamp(int(row["block_timestamp"]) / 1000, tz=ZoneInfo(settings.timezone_name)).strftime("%Y-%m-%d %H:%M:%S")
        parts.append(
            f"备注：{row['remark'] or '未备注'}\n"
            f"监听地址：{row['owner_address']}\n"
            f"方向：{'转入' if row['direction'] == 'in' else '转出'}\n"
            f"金额：{format_amount(float(row['amount']))} USDT\n"
            f"来源：{row['from_address']}\n"
            f"去向：{row['to_address']}\n"
            f"时间：{when}\n"
            f"确认：{'是' if row['confirmed'] else '否'}\n"
            f"链接：https://tronscan.org/#/transaction/{row['tx_hash']}"
        )
    await update.message.reply_text("\n\n".join(parts))


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    if update.message is None:
        return
    _, _, _, monitor = get_services(context)
    inserted = await monitor.poll_once(context.application.bot)
    await update.message.reply_text(f"扫描完成，新记录 {inserted} 笔。")


async def poll_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    _, _, _, monitor = get_services(context)
    await monitor.poll_once(context.application.bot)


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
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("addaddr", addaddr))
    application.add_handler(CommandHandler("deladdr", deladdr))
    application.add_handler(CommandHandler("listaddr", listaddr))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("history", history))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("tx", tx))
    application.add_handler(CommandHandler("scan", scan))

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
