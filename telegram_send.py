"""Telegram bot for signal alerts. Run: python telegram_send.py"""

import asyncio
import logging
import os
import sqlite3
from datetime import datetime

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def get_env(*keys, default=""):
    for k in keys:
        v = os.getenv(k)
        if v:
            return v.strip("'\"")
    return default


def get_subscribers():
    with sqlite3.connect(DB_PATH) as conn:
        return [r[0] for r in conn.execute("SELECT chat_id FROM subscribers").fetchall()]


def add_subscriber(chat_id, name):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO subscribers (chat_id,name,subscribed_at) VALUES (?,?,?)",
            (str(chat_id), name, datetime.utcnow().isoformat()),
        )
        conn.commit()


def format_signal(sig):
    sources = ", ".join(sig.get("sources_list", []))
    risk = "High" if sig["confidence"] >= 85 else "Medium"
    return (
        f"🟢 <b>BUY SIGNAL — {sig['ticker']}</b>\n"
        f"Confidence: {sig['confidence']:.0f}/100\n"
        f"Sources: {sources}\n"
        f"Hold: {sig['hold_days']} days\n"
        f"Risk: {risk}\n"
        f"Legal trail: {sig.get('legal_trail_path', 'n/a')}"
    )


async def send_signal(bot, signal_dict):
    msg = format_signal(signal_dict)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Legal Trail", callback_data="legal_trail")
    ]])
    subs = get_subscribers()
    if not subs:
        log.warning("No subscribers; signal not sent")
        return
    for chat_id in subs:
        await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML", reply_markup=kb)
        trail = signal_dict.get("legal_trail_path")
        if trail and os.path.exists(trail):
            with open(trail, "rb") as f:
                await bot.send_document(chat_id=chat_id, document=f, caption="Legal Trail")


def send_signal_sync(signal_dict):
    token = get_env("TELEGRAM_BOT_TOKEN")
    if not token:
        log.warning("No Telegram token")
        return

    async def _run():
        from telegram import Bot
        bot = Bot(token=token)
        await send_signal(bot, signal_dict)

    asyncio.run(_run())


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_subscriber(update.effective_chat.id, user.full_name or user.username or "user")
    await update.message.reply_text(
        "Subscribed to sn9trader signals.\nCommands: /status /help"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Status: paper trading mode active. (placeholder)")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start — subscribe\n/status — system status\n/help — this message"
    )


def run_daemon():
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    token = get_env("TELEGRAM_BOT_TOKEN")
    if not token:
        log.error("Set TELEGRAM_BOT_TOKEN in .env")
        return
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help", cmd_help))
    log.info("Telegram bot polling...")
    app.run_polling()


if __name__ == "__main__":
    run_daemon()
