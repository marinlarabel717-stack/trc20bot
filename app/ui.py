from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity


CUSTOM_EMOJI = {
    "brain": ("🧠", "6237934454019461140"),
    "ok": ("🆗", "5312028599803460968"),
    "warn": ("⚠️", "5301246586918024418"),
    "sparkle": ("✨", "5217818964612108191"),
    "star": ("⭐️", "5220064167356025824"),
    "money": ("💰", "4965219701572503640"),
    "screen": ("🖥", "5282843764451195532"),
    "light": ("💡", "5193127592764394874"),
    "clock": ("⏱️", "5382194935057372936"),
    "camera": ("📷", "5474194650661147876"),
    "blue": ("🔵", "5954227490179255253"),
    "plus": ("➕", "5397916757333654639"),
    "trade_type": ("📢", "5771695636411847302"),
    "trade_amount": ("🪙", "5992430854909989581"),
    "addr_out": ("⬆️", "5258043150110301407"),
    "addr_in": ("⬇️", "5258336354642697821"),
    "trade_time": ("✏️", "5839380464116175529"),
    "trade_hash": ("💊", "5933768993285345899"),
    "today_income": ("➕", "5775937998948404844"),
    "today_expense": ("🚫", "5877413297170419326"),
    "today_profit": ("💰", "5987880246865565644"),
    "usdt_balance": ("🪙", "5778311685638984859"),
}


def rich_text(parts: list[tuple[str, str | None]]) -> tuple[str, list[MessageEntity]]:
    text = ""
    entities: list[MessageEntity] = []
    for chunk, emoji_key in parts:
        offset = len(text)
        text += chunk
        if emoji_key:
            emoji_char, custom_emoji_id = CUSTOM_EMOJI[emoji_key]
            entities.append(
                MessageEntity(
                    type="custom_emoji",
                    offset=offset,
                    length=len(emoji_char),
                    custom_emoji_id=custom_emoji_id,
                )
            )
    return text, entities


def dashboard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ 添加地址", callback_data="menu:add"),
                InlineKeyboardButton("📋 地址列表", callback_data="menu:watches"),
            ],
            [
                InlineKeyboardButton("⚡ 立即扫描", callback_data="menu:scan"),
                InlineKeyboardButton("💰 余额查询", callback_data="menu:balance_all"),
            ],
            [
                InlineKeyboardButton("📜 今日记录", callback_data="menu:history:day"),
                InlineKeyboardButton("📊 本月统计", callback_data="menu:stats:month"),
            ],
            [
                InlineKeyboardButton("🔎 查询交易", callback_data="menu:tx"),
                InlineKeyboardButton("♻️ 刷新面板", callback_data="menu:home"),
            ],
        ]
    )


def watches_keyboard(watch_ids: list[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for watch_id in watch_ids:
        rows.append(
            [
                InlineKeyboardButton(f"💰 地址 {watch_id}", callback_data=f"watch:balance:{watch_id}"),
                InlineKeyboardButton(f"🗑 删除 {watch_id}", callback_data=f"watch:delete:{watch_id}"),
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ 返回后台", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def detail_keyboard(back_target: str = "menu:home", tx_hash: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if tx_hash:
        rows.append([InlineKeyboardButton("📷 查看 TX", url=f"https://tronscan.org/#/transaction/{tx_hash}")])
    rows.append([InlineKeyboardButton("⬅️ 返回后台", callback_data=back_target)])
    return InlineKeyboardMarkup(rows)
