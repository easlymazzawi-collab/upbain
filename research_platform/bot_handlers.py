"""Bot handlers — delivery, VIP, share, catalog, membership."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from research_platform.archive_index import (
    get_day_by_label,
    get_day_items,
    get_delivery_progress,
    save_delivery_progress,
    upsert_user,
)
from research_platform.catalog import (
    apply_spam_ban,
    is_spam_contribution,
    queue_contribution,
    search_media_sets,
)
from research_platform.config import layer_enabled, load_platform_config
from research_platform.dates import parse_day_label, topic_label_vn, today_vn
from research_platform.giftcode import redeem_gift_code
from research_platform.membership import gate_or_prompt, pop_pending
from research_platform.share import (
    create_day_share_ref,
    ensure_user_ref_code,
    leaderboard,
    record_share_click,
    resolve_ref_payload,
)
from research_platform.vip import can_access_archive, get_user_vip_status, list_vip_plans, user_is_vip

log = logging.getLogger("platform.bot_handlers")

_contrib_waiting: set[int] = set()


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
    """Gửi media — port clender _serve_album: copy → forward batch → từng msg."""
    from aiogram.exceptions import TelegramRetryAfter

    src_chat = item["src_chat_id"]
    msg_ids = item.get("album_msg_ids") or [item["src_msg_id"]]
    if not msg_ids:
        return False

    # Thử 1: copy_messages / copy_message (batch hoặc đơn)
    for attempt in range(3):
        try:
            if len(msg_ids) > 1:
                result = await bot.copy_messages(
                    chat_id=chat_id,
                    from_chat_id=src_chat,
                    message_ids=msg_ids,
                )
            else:
                result = await bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=src_chat,
                    message_id=msg_ids[0],
                )
            if result:
                await asyncio.sleep(_delivery_delay())
                return True
            break
        except TelegramRetryAfter as e:
            wait = float(getattr(e, "retry_after", 3)) + 1
            log.warning("copy FloodWait %ss (%s/3)", wait, attempt + 1)
            await asyncio.sleep(wait)
        except Exception as e:
            log.warning("copy_messages from %s: %s: %s", src_chat, type(e).__name__, e)
            break

    # Thử 2: forward_messages / forward_message
    try:
        if len(msg_ids) > 1:
            result = await bot.forward_messages(
                chat_id=chat_id,
                from_chat_id=src_chat,
                message_ids=msg_ids,
            )
        else:
            result = await bot.forward_message(
                chat_id=chat_id,
                from_chat_id=src_chat,
                message_id=msg_ids[0],
            )
        if result:
            await asyncio.sleep(_delivery_delay())
            return True
    except Exception as e:
        log.warning("forward_messages from %s: %s: %s", src_chat, type(e).__name__, e)

    # Thử 3: từng message
    sent = 0
    for mid in msg_ids:
        try:
            await bot.copy_message(chat_id=chat_id, from_chat_id=src_chat, message_id=mid)
            sent += 1
            await asyncio.sleep(max(0.2, _delivery_delay()))
        except Exception:
            try:
                await bot.forward_message(chat_id=chat_id, from_chat_id=src_chat, message_id=mid)
                sent += 1
                await asyncio.sleep(max(0.2, _delivery_delay()))
            except Exception as e2:
                log.warning("Cannot serve msg %s: %s", mid, e2)

    return sent > 0


async def deliver_day_to_user(bot, user_id: int, day: dict) -> tuple[int, int]:
    items = get_day_items(day["id"])
    prog = get_delivery_progress(user_id, day["id"])
    start_seq = (prog or {}).get("last_seq_sent") or 0
    sent = 0
    total = len(items)
    for item in items:
        if item["seq"] <= start_seq:
            continue
        if await _copy_item(bot, user_id, item):
            sent += 1
            save_delivery_progress(user_id, day["id"], item["seq"], completed=(item["seq"] == total))
    if sent and items:
        save_delivery_progress(user_id, day["id"], items[-1]["seq"], completed=True)
    return sent, total


def build_handlers_for_bot(bot_cfg: dict, bot_db_id: int | None):
    from aiogram import Dispatcher, F
    from aiogram.filters import Command, CommandStart
    from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

    dp = Dispatcher()
    plat = load_platform_config()

    def _resolve_day(label: str | None = None):
        if not bot_db_id:
            return None
        lbl = label or topic_label_vn(today_vn())
        day = get_day_by_label(bot_db_id, lbl)
        if day and day.get("status") in ("published", "indexed", "closed"):
            return day
        return None

    async def _handle_day(msg: Message, label: str):
        if not layer_enabled("bot_delivery", bot_cfg):
            await msg.answer("Bot delivery đang tắt.")
            return
        ok, reason = can_access_archive(
            msg.from_user.id,
            require_vip=bool(plat.get("require_vip_for_archive")),
        )
        if not ok:
            await msg.answer(reason)
            return
        parsed = parse_day_label(label.replace("_", "-"))
        if parsed:
            label = topic_label_vn(parsed)
        day = _resolve_day(label)
        if not day:
            await msg.answer(f"Chưa có archive cho ngày <b>{label}</b>.", parse_mode="HTML")
            return
        await msg.answer(f"⏳ Đang gửi ngày <b>{label}</b>…", parse_mode="HTML")
        sent, total = await deliver_day_to_user(msg.bot, msg.from_user.id, day)
        uname = bot_cfg.get("username") or "bot"
        ref = create_day_share_ref(day["id"], bot_db_id or 0, msg.from_user.id)
        share = f"https://t.me/{uname}?start={ref}"
        allow_fwd = user_is_vip(msg.from_user.id) or plat.get("require_vip_for_archive") is False
        fwd_note = "\n🔗 VIP: được share media." if allow_fwd else ""
        await msg.answer(
            f"✅ Đã gửi {sent}/{total} bài · {label}\n"
            f"🔗 Mời bạn: {share}{fwd_note}",
            disable_web_page_preview=True,
        )

    @dp.message(CommandStart())
    async def cmd_start(msg: Message):
        upsert_user(
            msg.from_user.id,
            username=msg.from_user.username or "",
            first_name=msg.from_user.first_name or "",
            language_code=msg.from_user.language_code or "",
        )
        if not await gate_or_prompt(msg.bot, msg.from_user.id, pending="start"):
            return
        args = (msg.text or "").split(maxsplit=1)
        payload = args[1] if len(args) > 1 else ""
        if payload.startswith("day_"):
            await _handle_day(msg, payload[4:].replace("_", "-"))
            return
        ref = resolve_ref_payload(payload)
        if ref:
            if ref.get("type") == "user_ref":
                record_share_click(ref["ref_code"], msg.from_user.id)
            elif ref.get("type") == "day_ref":
                record_share_click(ref["ref_code"], msg.from_user.id)
                from research_platform.db import connect
                with connect() as c:
                    row = c.execute("SELECT topic_label FROM days WHERE id=?", (ref["day_id"],)).fetchone()
                if row:
                    await _handle_day(msg, row["topic_label"])
                    return
        if payload.isdigit() and len(payload) >= 8:
            from research_platform.db import connect
            with connect() as c:
                row = c.execute("SELECT * FROM share_refs WHERE ref_code=?", (payload,)).fetchone()
            if row:
                record_share_click(row["ref_code"], msg.from_user.id)
                with connect() as c:
                    drow = c.execute("SELECT topic_label FROM days WHERE id=?", (row["day_id"],)).fetchone()
                if drow:
                    await _handle_day(msg, drow["topic_label"])
                    return
        pending = pop_pending(msg.from_user.id)
        if pending and pending.startswith("day"):
            await _handle_day(msg, pending.replace("day:", ""))
            return
        await msg.answer(
            "👋 Research Platform\n"
            "📅 /today · /day DD-MM-YYYY\n"
            "🔍 /find từ khóa · /vip · /invite · /me",
            reply_markup=_keyboard(),
        )

    @dp.callback_query(F.data == "mem_recheck")
    async def cb_mem_recheck(cb: CallbackQuery):
        from research_platform.membership import check_membership, invalidate

        invalidate(cb.from_user.id)
        ok, _ = await check_membership(cb.bot, cb.from_user.id)
        if ok:
            await cb.message.answer("✅ Đã join — dùng menu bên dưới.", reply_markup=_keyboard())
            pending = pop_pending(cb.from_user.id)
            if pending == "start":
                pass
        else:
            await cb.answer("Chưa join kênh.", show_alert=True)
            return
        await cb.answer()

    @dp.message(Command("today"))
    async def cmd_today(msg: Message):
        if not await gate_or_prompt(msg.bot, msg.from_user.id):
            return
        await _handle_day(msg, topic_label_vn(today_vn()))

    @dp.message(Command("day"))
    async def cmd_day(msg: Message):
        if not await gate_or_prompt(msg.bot, msg.from_user.id):
            return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await msg.answer("Dùng: /day 30-06-2026")
            return
        await _handle_day(msg, parts[1].strip())

    @dp.message(Command("find"))
    async def cmd_find(msg: Message):
        if not await gate_or_prompt(msg.bot, msg.from_user.id):
            return
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await msg.answer("Dùng: /find từ khóa")
            return
        hits = search_media_sets(parts[1], bot_id=bot_db_id)
        if not hits:
            await msg.answer("Không tìm thấy bộ nào.")
            return
        lines = [f"🔍 {len(hits)} kết quả:"]
        for h in hits[:15]:
            lines.append(f"• {h['name']} (id={h['id']})")
        await msg.answer("\n".join(lines))

    @dp.message(Command("vip"))
    async def cmd_vip(msg: Message):
        st = get_user_vip_status(msg.from_user.id)
        plans = list_vip_plans()
        lines = [
            f"⭐ VIP: {'✅' if st['is_vip'] else '❌'}",
            f"Hết hạn: {st.get('vip_until') or '—'}",
            "",
            "Gói Stars:",
        ]
        for p in plans:
            lines.append(f"• {p['name']} — {p['stars_price']}⭐ → /buy_{p['id']}")
        lines.append("\nGiftcode: /redeem MÃ")
        await msg.answer("\n".join(lines))

    @dp.message(Command("redeem"))
    async def cmd_redeem(msg: Message):
        parts = (msg.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await msg.answer("Dùng: /redeem MÃ")
            return
        try:
            r = redeem_gift_code(msg.from_user.id, parts[1])
            await msg.answer(f"✅ VIP kích hoạt đến {r.get('vip_until') or 'vĩnh viễn'}")
        except ValueError as e:
            await msg.answer(str(e))

    @dp.message(Command("invite"))
    async def cmd_invite(msg: Message):
        code = ensure_user_ref_code(msg.from_user.id)
        uname = bot_cfg.get("username") or "bot"
        link = f"https://t.me/{uname}?start=ref_{code}"
        await msg.answer(f"🏆 Link mời bạn:\n{link}", disable_web_page_preview=True)

    @dp.message(Command("me"))
    async def cmd_me(msg: Message):
        st = get_user_vip_status(msg.from_user.id)
        await msg.answer(
            f"ID: {msg.from_user.id}\n"
            f"Tier: {st.get('tier') or '—'}\n"
            f"VIP: {'✅' if st['is_vip'] else '❌'}\n"
            f"Forward: {'✅' if st.get('allow_forward') else '❌'}"
        )

    @dp.message(F.text == "📅 Xem ngày")
    async def btn_today(msg: Message):
        await cmd_today(msg)

    @dp.message(F.text == "🔍 Tìm bộ")
    async def btn_find(msg: Message):
        await msg.answer("Gõ /find từ khóa (hỗ trợ có dấu).")

    @dp.message(F.text == "⭐ VIP / Stars")
    async def btn_vip(msg: Message):
        await cmd_vip(msg)

    @dp.message(F.text == "🏆 Mời bạn")
    async def btn_invite(msg: Message):
        await cmd_invite(msg)

    @dp.message(F.text == "💡 Đóng góp tên")
    async def btn_contrib(msg: Message):
        _contrib_waiting.add(msg.from_user.id)
        await msg.answer("Forward bài/album cho bot kèm caption = tên đề xuất.")

    @dp.message(F.text == "ℹ️ Hướng dẫn")
    async def btn_help(msg: Message):
        await msg.answer("/today · /day · /find · /vip · /invite · /redeem MÃ")

    @dp.message(F.text.startswith("/buy_"))
    async def cmd_buy(msg: Message):
        try:
            plan_id = int((msg.text or "").split("_", 1)[1])
        except Exception:
            return
        plans = {p["id"]: p for p in list_vip_plans()}
        plan = plans.get(plan_id)
        if not plan:
            await msg.answer("Gói không tồn tại.")
            return
        prices = [LabeledPrice(label=plan["name"], amount=int(plan["stars_price"] or 1))]
        await msg.bot.send_invoice(
            chat_id=msg.chat.id,
            title=plan["name"],
            description=f"VIP {plan['plan_type']}",
            payload=f"vip_plan_{plan_id}",
            currency="XTR",
            prices=prices,
            provider_token="",
        )

    @dp.pre_checkout_query()
    async def pre_checkout(q: PreCheckoutQuery):
        await q.answer(ok=True)

    @dp.message(F.successful_payment)
    async def paid(msg: Message):
        payload = msg.successful_payment.invoice_payload or ""
        if payload.startswith("vip_plan_"):
            from research_platform.vip import grant_vip
            plan_id = int(payload.split("_")[-1])
            grant_vip(msg.from_user.id, plan_id, source="stars")
            await msg.answer("✅ Thanh toán Stars OK — VIP đã kích hoạt.")

    @dp.message(F.text)
    async def on_text_or_forward(msg: Message):
        if msg.from_user.id not in _contrib_waiting:
            return
        if not (msg.forward_date or msg.forward_origin):
            return
        name = (msg.caption or msg.text or "").strip()
        if not name:
            await msg.answer("Thiếu tên trong caption.")
            return
        if is_spam_contribution(msg.from_user.id, name):
            until = apply_spam_ban(msg.from_user.id, escalate=True)
            _contrib_waiting.discard(msg.from_user.id)
            await msg.answer(f"Spam — khóa đến {until}")
            return
        src_chat = msg.forward_from_chat.id if msg.forward_from_chat else msg.chat.id
        msg_ids = [msg.message_id]
        if msg.media_group_id:
            msg_ids = [msg.message_id]
        qid = queue_contribution(
            bot_id=bot_db_id or 0,
            user_id=msg.from_user.id,
            src_chat_id=src_chat,
            msg_ids=msg_ids,
            proposed_name=name,
        )
        _contrib_waiting.discard(msg.from_user.id)
        topic = plat.get("admin_contrib_topic_id")
        if topic and plat.get("admin_forum_id"):
            try:
                await msg.forward(chat_id=plat["admin_forum_id"], message_thread_id=topic)
            except Exception as e:
                log.warning("forward contrib admin: %s", e)
        await msg.answer(f"✅ Đã gửi duyệt (#{qid}).")

    @dp.message(Command("leaderboard"))
    async def cmd_lb(msg: Message):
        lb = leaderboard()
        if not lb:
            await msg.answer("Đua top chưa bật hoặc chưa có click.")
            return
        lines = ["🏆 Top mời bạn:"]
        for row in lb:
            lines.append(f"{row['rank']}. {row['display']} — {row['clicks']} click")
        await msg.answer("\n".join(lines))

    return dp
