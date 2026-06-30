"""FastAPI web dashboard — cấu hình, SSE real-time, bot token, lịch auto."""

import asyncio
import json
import os
import time
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from core.config_store import (
    load_auto_config,
    load_inventory,
    save_auto_config,
    topic_key,
    upsert_topic_source,
)
from core.import_legacy import analyze_uploads, apply_import
from core.runtime import append_log, get_runtime
from core.scheduler import compute_next_run_ts
from core.settings import CHANNELS_FILE, api_ready, web_token
from core.web_actions import action_labels, list_pending_actions, queue_action

WEB_DIR = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="UpBain Control", version="27")


def _check_token(authorization: str | None, x_token: str | None, query_token: str | None = None):
    token = web_token()
    if not token:
        return True
    got = (
        (authorization or "").replace("Bearer ", "").strip()
        or (x_token or "").strip()
        or (query_token or "").strip()
    )
    if got != token:
        raise HTTPException(401, "Unauthorized")
    return True


def _auth(authorization: str | None = Header(default=None), x_token: str | None = Header(default=None)):
    _check_token(authorization, x_token)
    return True


class TopicSourceIn(BaseModel):
    src_chat_id: int
    topic_id: int
    topic_title: str = ""
    target_media_override: int | None = None
    target_ads_override: int | None = None
    media_per_round: int | None = None
    default_cpa: int | None = None
    default_mode: str = "normal"
    channels_no_ads: list[str] = []
    mapped_cmds: list[str] = []
    enabled: bool = True


class AllTaskIn(BaseModel):
    enabled: bool = False
    source_chat_id: int | None = None
    source_topic_id: int | None = None
    source_title: str = ""
    selected_channel_ids: list[int] = []
    run_after_regular: bool = True


class ScheduleIn(BaseModel):
    enabled: bool = False
    times: list[str] = Field(default_factory=lambda: ["08:00", "20:00"])
    timezone: str = "Asia/Ho_Chi_Minh"
    run_all_topics: bool = True
    run_all_task_after: bool = True


class GlobalIn(BaseModel):
    xepbai_off_default_cpa: int = 1
    xepbai_off_default_mode: str = "normal"
    low_media_warn_threshold: int = 50
    auto_run_enabled: bool = True
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    web_token: str = ""


class TelegramIn(BaseModel):
    api_id: int | None = None
    api_hash: str = ""
    intermediate_chat: int | None = None
    ads_chat: int | None = None
    bot_token: str = ""
    notify_chat_id: int | None = None


def _snapshot() -> dict[str, Any]:
    cfg = load_auto_config()
    sch = cfg.get("global", {}).get("schedule") or {}
    rt = get_runtime()
    next_ts = compute_next_run_ts(
        sch.get("times") or [],
        sch.get("timezone") or "Asia/Ho_Chi_Minh",
    ) if sch.get("enabled") else 0
    g = cfg.get("global", {})
    safe_global = {k: v for k, v in g.items() if k not in ("api_hash", "bot_token")}
    safe_global["has_api_hash"] = bool(g.get("api_hash"))
    safe_global["has_bot_token"] = bool(g.get("bot_token"))
    channels = []
    if os.path.exists(CHANNELS_FILE):
        with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
            channels = json.load(f)
    folders = []
    if os.path.exists("folders.json"):
        with open("folders.json", "r", encoding="utf-8") as f:
            folders = json.load(f)
    all_task = cfg.get("all_task") or {}
    return {
        "config": {**cfg, "global": safe_global},
        "inventory": load_inventory(),
        "runtime": {**rt, "next_run_at": next_ts or rt.get("next_run_at", 0)},
        "channels": channels,
        "folders": folders,
        "meta": {
            "channel_count": len(channels),
            "folder_count": len(folders),
            "topic_count": len(cfg.get("topic_sources") or {}),
            "userbot_ready": api_ready(),
            "pending_actions": len(list_pending_actions()),
            "all_task_enabled": bool(all_task.get("enabled")),
            "all_task_configured": bool(
                all_task.get("source_chat_id") and all_task.get("selected_channel_ids")
            ),
        },
        "ts": int(time.time()),
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    path = os.path.join(WEB_DIR, "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/stream")
async def api_stream(
    request: Request,
    token: str | None = None,
    authorization: str | None = Header(default=None),
    x_token: str | None = Header(default=None),
):
    _check_token(authorization, x_token, token)
    """SSE — push snapshot mỗi 2s + khi file thay đổi."""

    async def gen():
        last_sig = ""
        while True:
            if await request.is_disconnected():
                break
            try:
                snap = _snapshot()
                sig = json.dumps(snap, sort_keys=True, default=str)
                if sig != last_sig:
                    last_sig = sig
                    yield f"data: {sig}\n\n"
                else:
                    yield ": keepalive\n\n"
            except Exception as e:
                err = json.dumps({"error": str(e), "ts": int(time.time())})
                yield f"data: {err}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/config")
async def api_config(_=Depends(_auth)):
    return _snapshot()["config"]


@app.get("/api/status")
async def api_status(_=Depends(_auth)):
    return _snapshot()["runtime"]


@app.patch("/api/global")
async def patch_global(body: GlobalIn, _=Depends(_auth)):
    cfg = load_auto_config()
    cfg["global"].update(body.model_dump())
    save_auto_config(cfg)
    return cfg["global"]


@app.patch("/api/telegram")
async def patch_telegram(body: TelegramIn, _=Depends(_auth)):
    cfg = load_auto_config()
    g = cfg["global"]
    if body.api_id is not None:
        g["api_id"] = body.api_id
    if body.api_hash:
        g["api_hash"] = body.api_hash
    if body.intermediate_chat is not None:
        g["intermediate_chat"] = body.intermediate_chat
    if body.ads_chat is not None:
        g["ads_chat"] = body.ads_chat
    if body.bot_token:
        g["bot_token"] = body.bot_token
    if body.notify_chat_id is not None:
        g["notify_chat_id"] = body.notify_chat_id
    save_auto_config(cfg)
    return {
        "api_id": g.get("api_id"),
        "intermediate_chat": g.get("intermediate_chat"),
        "ads_chat": g.get("ads_chat"),
        "notify_chat_id": g.get("notify_chat_id"),
        "has_api_hash": bool(g.get("api_hash")),
        "has_bot_token": bool(g.get("bot_token")),
    }


@app.get("/api/schedule")
async def get_schedule(_=Depends(_auth)):
    return load_auto_config().get("global", {}).get("schedule", {})


@app.patch("/api/schedule")
async def patch_schedule(body: ScheduleIn, _=Depends(_auth)):
    cfg = load_auto_config()
    cfg["global"]["schedule"] = body.model_dump()
    save_auto_config(cfg)
    sch = cfg["global"]["schedule"]
    next_ts = compute_next_run_ts(sch.get("times") or [], sch.get("timezone") or "Asia/Ho_Chi_Minh")
    return {**sch, "next_run_at": next_ts}


@app.get("/api/inventory")
async def api_inventory(_=Depends(_auth)):
    return load_inventory()


@app.get("/api/topics")
async def api_topics(_=Depends(_auth)):
    cfg = load_auto_config()
    return list(cfg.get("topic_sources", {}).values())


@app.post("/api/topics")
async def post_topic(body: TopicSourceIn, _=Depends(_auth)):
    entry = upsert_topic_source(body.src_chat_id, body.topic_id, body.model_dump())
    return entry


@app.delete("/api/topics/{src_chat_id}/{topic_id}")
async def delete_topic(src_chat_id: int, topic_id: int, _=Depends(_auth)):
    cfg = load_auto_config()
    key = topic_key(src_chat_id, topic_id)
    cfg.get("topic_sources", {}).pop(key, None)
    save_auto_config(cfg)
    return {"ok": True}


@app.get("/api/all-task")
async def get_all_task(_=Depends(_auth)):
    return load_auto_config().get("all_task", {})


@app.post("/api/all-task")
async def post_all_task(body: AllTaskIn, _=Depends(_auth)):
    cfg = load_auto_config()
    cfg["all_task"] = body.model_dump()
    save_auto_config(cfg)
    return cfg["all_task"]


@app.get("/api/channels")
async def api_channels(_=Depends(_auth)):
    if os.path.exists(CHANNELS_FILE):
        with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


@app.post("/api/trigger/{src_chat_id}/{topic_id}")
async def api_trigger(src_chat_id: int, topic_id: int, _=Depends(_auth)):
    action = queue_action("run_topic", {
        "src_chat_id": src_chat_id,
        "topic_id": topic_id,
    })
    append_log("info", f"Web queue: chạy topic {src_chat_id}:{topic_id}")
    return {"ok": True, "action": action, "message": "Đã xếp hàng — userbot sẽ chạy trong vài giây"}


class ActionIn(BaseModel):
    type: str
    params: dict = Field(default_factory=dict)


@app.get("/api/actions")
async def api_actions_list(_=Depends(_auth)):
    return {
        "pending": list_pending_actions(),
        "labels": action_labels(),
        "userbot_ready": api_ready(),
    }


@app.post("/api/actions")
async def api_actions_run(body: ActionIn, _=Depends(_auth)):
    allowed = set(action_labels().keys())
    if body.type not in allowed:
        raise HTTPException(400, f"Action không hợp lệ. Cho phép: {', '.join(sorted(allowed))}")
    action = queue_action(body.type, body.params)
    label = action_labels().get(body.type, body.type)
    append_log("info", f"Web queue: {label}")
    msg = (
        f"Đã xếp hàng: {label}. Userbot xử lý trong ~2s."
        if api_ready()
        else f"Đã lưu lệnh '{label}' — restart userbot (có API) để chạy."
    )
    return {"ok": True, "action": action, "message": msg}


async def _read_uploads(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for f in files:
        if not f.filename:
            continue
        data = await f.read()
        if data:
            out.append((f.filename, data))
    return out


@app.post("/api/import/preview")
async def import_preview(
    files: list[UploadFile] = File(...),
    _=Depends(_auth),
):
    uploads = await _read_uploads(files)
    if not uploads:
        raise HTTPException(400, "Chưa chọn file")
    preview, _ = analyze_uploads(uploads)
    return preview.to_dict()


@app.post("/api/import")
async def import_apply(
    files: list[UploadFile] = File(...),
    _=Depends(_auth),
):
    uploads = await _read_uploads(files)
    if not uploads:
        raise HTTPException(400, "Chưa chọn file")
    preview, payload = analyze_uploads(uploads)
    if preview.errors:
        raise HTTPException(400, "; ".join(preview.errors))
    applied = apply_import(payload)
    msg = (
        f"Import xong: {len(applied['files_written'])} file, "
        f"{len(applied['global_updated'])} field config, "
        f"{applied['topics_upserted']} topic map"
    )
    append_log("info", msg)
    return {"ok": True, "preview": preview.to_dict(), "applied": applied, "message": msg}

