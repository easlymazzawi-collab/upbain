"""Bot delivery — menu + /day gửi chậm từ link nguồn."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from platform.archive_index import (
    ensure_db,
    get_day_by_label,
    get_day_items,
    get_delivery_progress,
    save_delivery_progress,
    sync_bot_from_config,
    upsert_user,
)
from platform.config import layer_enabled, load_platform_config
from platform.dates import parse_day_label, topic_label_vn, today_vn

log = logging.getLogger("platform.bot_delivery")

_runner_task: asyncio.Task | None = None
_bot_instance: Any = None
_dp: Any = None


def _bot_token() -> str:
    plat = load_platform_config()
    return (plat.get("bot") or {}).get("token") or ""


def _delivery_delay() -> float:
    return float(load_platform_config().get("delivery_delay_sec") or 1.0)


def _keyboard():
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Xem ngày"), KeyboardButton(text="🔍 Tìm bộ")],
            [KeyboardButton(text="⭐ VIP / Stars"), KeyboardButton(text="🏆 Mời bạn")],
            [KeyboardButton(text="💡 Đóng góp tên"), KeyboardButton(text="ℹ️ Hướng dẫn")],
        ],
        resize_keyboard=True,
    )


async def _copy_item(bot, chat_id: int, item: dict) -> bool:
    from aiogram.exceptions import TelegramRetryAfter

    src_chat = item["src_chat_id"]
    msg_ids = item.get("album_msg_ids") or [item["src_msg_id"]]
    delay = _delivery_delay()

    try:
        if len(msg_ids) > 1:
            await bot.copy_messages(
                chat_id=chat_id,
                from_chat_id=src_chat,
                message_ids=msg_ids,
            )
        else:
            await bot.copy_message(
                chat_id=chat_id,
                from_chat_id=src_chat,
                message_id=msg_ids[0],
            )
        await asyncio.sleep(delay)
        return True
    except TelegramRetryAfter as e:
        log.warning("FloodWait %ss seq=%s", e.retry_after, item.get("seq"))
        await asyncio.sleep(float(e.retry_after) + 0.5)
        return await _copy_item(bot, chat_id, item)
    except Exception as e:
        log.warning("copy fail seq=%s: %s", item.get("seq"), e)
        return False


async def deliver_day_to_user(bot, user_id: int, day: dict) -> tuple[int, int]:
    items = get_day_items(day["id"])
    if not items:
        return 0, 0

    prog = get_delivery_progress(user_id, day["id"])
    start_seq = (prog or {}).get("last_seq_sent") or 0
    sent = 0
    total = len(items)

    for item in items:
        if item["seq"] <= start_seq:
            continue
        ok = await _copy_item(bot, user_id, item)
        if ok:
            sent += 1
            save_delivery_progress(user_id, day["id"], item["seq"], completed=(item["seq"] == total))

    if sent and items:
        save_delivery_progress(user_id, day["id"], items[-1]["seq"], completed=True)
    return sent, total


def _resolve_day(topic_label: str | None = None) -> dict | None:
    plat = load_platform_config()
    bot_cfg = plat.get("bot") or {}
    bot_id = sync_bot_from_config(bot_cfg)
    label = topic_label or topic_label_vn(today_vn())
    day = get_day_by_label(bot_id, label)
    if day and day.get("status") in ("published", "indexed", "closed"):
        return day
    return None


def build_handlers():
    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import Command, CommandStart
    from aiogram.types import Message

    dp = Dispatcher()

    @dp.message(CommandStart())
    async def cmd_start(msg: Message):
        upsert_user(
            msg.from_user.id,
            username=msg.from_user.username or "",
            first_name=msg.from_user.first_name or "",
            language_code=msg.from_user.language_code or "",
        )
        args = (msg.text or "").split(maxsplit=1)
        payload = args[1] if len(args) > 1 else ""
        if payload.startswith("day_"):
            label = payload[4:].replace("_", "-")
            await _handle_day(msg, label)
            return
        await msg.answer(
            "👋 Research Platform\n\n"
            "📅 /today — xem ngày hôm nay\n"
            "📅 /day 30-06-2026 — xem ngày cụ thể\n"
            "ℹ️ Bot gửi chậm từng bài theo thứ tự index.",
            reply_markup=_keyboard(),
        )

    @dp.message(Command("today"))
    async def cmd_today(msg: Message):
        await _handle_day(msg, topic_label_vn(today_vn()))

    @dp.message(Command("day"))
    async def cmd_day(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await msg.answer("Dùng: /day 30-06-2026")
            return
        await _handle_day(msg, parts[1].strip())

    @dp.message(F.text == "📅 Xem ngày")
    async def btn_today(msg: Message):
        await _handle_day(msg, topic_label_vn(today_vn()))

    @dp.message(F.text == "ℹ️ Hướng dẫn")
    async def btn_help(msg: Message):
        await msg.answer(
            "📅 /today hoặc /day DD-MM-YYYY\n"
            "Bot copy từng bài từ nguồn theo index — không cần scroll kênh."
        )

    @dp.message(F.text.in_({"🔍 Tìm bộ", "⭐ VIP / Stars", "🏆 Mời bạn", "💡 Đóng góp tên"}))
    async def btn_soon(msg: Message):
        await msg.answer("Tính năng sẽ có ở P1–P3. Hiện dùng /day để xem archive.")

    async def _handle_day(msg: Message, label: str):
        if not layer_enabled("bot_delivery"):
            await msg.answer("Bot delivery đang tắt trên web.")
            return
        parsed = parse_day_label(label.replace("_", "-"))
        if parsed:
            label = topic_label_vn(parsed)
        day = _resolve_day(label)
        if not day:
            await msg.answer(f"Chưa có archive cho ngày <b>{label}</b>.", parse_mode="HTML")
            return
        await msg.answer(f"⏳ Đang gửi ngày <b>{label}</b>…", parse_mode="HTML")
        bot = msg.bot
        sent, total = await deliver_day_to_user(bot, msg.from_user.id, day)
        plat = load_platform_config()
        uname = (plat.get("bot") or {}).get("username") or "bot"
        share = f"https://t.me/{uname}?start=day_{label.replace('-', '_')}" if uname else ""
        tail = f"\n🔗 Mời bạn: {share}" if share else ""
        await msg.answer(
            f"✅ Đã gửi {sent}/{total} bài cho ngày {label}.{tail}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    return dp


async def run_delivery_bot() -> None:
    global _bot_instance, _dp
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    token = _bot_token()
    if not token:
        log.info("platform bot: chưa có token")
        return
    if not layer_enabled("bot_delivery"):
        log.info("platform bot: delivery tắt")
        return

    ensure_db()
    _dp = build_handlers()
    _bot_instance = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    log.info("platform bot: polling…")
    await _dp.start_polling(_bot_instance)


async def start_delivery_bot_background() -> asyncio.Task | None:
    global _runner_task
    plat = load_platform_config()
    if not plat.get("enabled") or not layer_enabled("bot_delivery"):
        return None
    if not _bot_token():
        return None
    if _runner_task and not _runner_task.done():
        return _runner_task

    async def _wrap():
        while True:
            try:
                await run_delivery_bot()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("platform bot crashed: %s — retry 10s", e)
                await asyncio.sleep(10)

    _runner_task = asyncio.create_task(_wrap())
    return _runner_task


async def stop_delivery_bot() -> None:
    global _runner_task, _bot_instance, _dp
    if _runner_task:
        _runner_task.cancel()
        try:
            await _runner_task
        except asyncio.CancelledError:
            pass
        _runner_task = None
    if _bot_instance:
        await _bot_instance.session.close()
        _bot_instance = None
    _dp = None
