"""Multi-bot manager — polling, restart, status."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from research_platform.bot_handlers import build_handlers_for_bot
from research_platform.config import layer_enabled, list_bots_config, load_platform_config

log = logging.getLogger("platform.bot_manager")

_bots: dict[str, dict[str, Any]] = {}
_lock = asyncio.Lock()


def bot_status() -> list[dict]:
    out = []
    for key, st in _bots.items():
        task = st.get("task")
        running = task is not None and not task.done()
        out.append({
            "key": key,
            "username": st.get("username", ""),
            "bot_db_id": st.get("bot_db_id"),
            "running": running,
            "error": st.get("last_error"),
        })
    return out


async def _run_one(bot_cfg: dict, bot_db_id: int | None, key: str) -> None:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    token = (bot_cfg.get("token") or "").strip()
    if not token:
        return
    if not layer_enabled("bot_delivery", bot_cfg):
        log.info("bot %s delivery off", key)
        return

    dp = build_handlers_for_bot(bot_cfg, bot_db_id)
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    _bots[key]["instance"] = bot
    _bots[key]["last_error"] = None
    log.info("bot %s polling", key)
    try:
        await dp.start_polling(bot)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        _bots[key]["last_error"] = str(e)
        log.exception("bot %s crash: %s", key, e)
        raise
    finally:
        await bot.session.close()


async def start_all_bots() -> None:
    global _bots
    plat = load_platform_config()
    if not plat.get("enabled"):
        return
    from research_platform.archive_index import sync_all_bots_from_config

    bot_ids = sync_all_bots_from_config(plat)
    configs = list_bots_config(plat)
    async with _lock:
        for i, cfg in enumerate(configs):
            if not cfg.get("enabled", True):
                continue
            token = (cfg.get("token") or "").strip()
            if not token:
                continue
            key = cfg.get("username") or f"bot_{i}"
            if key in _bots:
                t = _bots[key].get("task")
                if t and not t.done():
                    continue
            db_id = bot_ids[i] if i < len(bot_ids) else None
            _bots[key] = {
                "username": cfg.get("username", ""),
                "bot_db_id": db_id,
                "task": None,
                "cfg": cfg,
            }

            async def _wrap(c=cfg, d=db_id, k=key):
                while True:
                    try:
                        await _run_one(c, d, k)
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        await asyncio.sleep(10)

            _bots[key]["task"] = asyncio.create_task(_wrap())


async def stop_all_bots() -> None:
    async with _lock:
        for key, st in list(_bots.items()):
            task = st.get("task")
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        _bots.clear()


async def restart_bot(username: str | None = None) -> dict:
    if username:
        async with _lock:
            st = _bots.get(username)
            if st:
                t = st.get("task")
                if t:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
        await start_all_bots()
        return {"restarted": username}
    await stop_all_bots()
    await start_all_bots()
    return {"restarted": "all"}


async def start_delivery_bot_background():
    """Compat P0 entry — start all configured bots."""
    plat = load_platform_config()
    if not plat.get("enabled"):
        return None

    async def _supervisor():
        while True:
            try:
                await start_all_bots()
                while True:
                    await asyncio.sleep(60)
                    await start_all_bots()
            except asyncio.CancelledError:
                await stop_all_bots()
                raise
            except Exception as e:
                log.exception("bot supervisor: %s", e)
                await asyncio.sleep(15)

    return asyncio.create_task(_supervisor())


async def stop_delivery_bot() -> None:
    await stop_all_bots()
