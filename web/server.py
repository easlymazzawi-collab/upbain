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
from core.all_config import merge_all_task, merge_plain_task
from core.map_config import (
    apply_start_link_flexible,
    apply_start_link_to_topic,
    clear_start_link,
    delete_topic_entry,
    sync_topic_to_map_file,
    update_topic_mapping_cmd,
    upsert_topic_mapping,
)
from core.runtime import append_log, get_runtime
from core.scheduler import compute_next_run_ts
from core.bot_notify import send_bot_notify
from core.settings import api_ready, web_token, system_armed, set_system_armed
from core.channel_store import (
    BRANCH_ADS,
    BRANCH_PLAIN,
    add_folder_link,
    gen_topic_map,
    load_channels,
    load_folders,
    load_topic_map_text,
    save_topic_map_text,
    set_channel_alias,
)
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
    xepbai_mode: str | None = "inherit"
    xepbai_whitelist_cmds: list[str] = []
    use_ads: bool | None = None
    channels_no_ads: list[str] = []
    mapped_cmds: list[str] = []
    map_post_limits: dict[str, int] = Field(default_factory=dict)
    target_posts_override: int | None = None
    enabled: bool = True
    pin_mode: str = "latest"
    start_link: str = ""
    start_msg_id: int | None = None
    cursor_msg_id: int | None = None


class AllTaskPatchIn(BaseModel):
    enabled: bool | None = None
    source_link: str | None = None
    start_link: str | None = None
    selected_channel_ids: list[int] | None = None
    run_after_regular: bool | None = None


class PlainTaskPatchIn(BaseModel):
    enabled: bool | None = None
    selected_channel_ids: list[int] | None = None
    skip_last_posts: int | None = None
    included_msg_ids: list[int] | None = None


class TopicPostLimitsIn(BaseModel):
    map_post_limits: dict[str, int] = Field(default_factory=dict)


class TopicMapAddIn(BaseModel):
    topic_title: str
    channel_cmd: str
    post_count: int | None = None
    src_chat_id: int | None = None
    topic_id: int | None = None


class TopicMapCmdIn(BaseModel):
    topic_title: str = ""
    old_cmd: str
    new_cmd: str
    post_count: int | None = None


class AliasIn(BaseModel):
    alias: str = ""


class PlainFolderIn(BaseModel):
    link: str


class PlainTopicMapIn(BaseModel):
    content: str


class AllTaskIn(BaseModel):
    enabled: bool = False
    source_chat_id: int | None = None
    source_topic_id: int | None = None
    source_title: str = ""
    selected_channel_ids: list[int] = []
    run_after_regular: bool = True
    use_ads: bool = False
    xep_cpa: int = 1
    xep_mode: str = "normal"
    target_media_override: int | None = None
    start_link: str = ""
    start_msg_id: int | None = None
    pin_mode: str = "latest"
    cursor_msg_id: int | None = None


class StartLinkIn(BaseModel):
    link: str


class TopicStartLinkIn(BaseModel):
    link: str
    src_chat_id: int | None = None
    topic_id: int | None = None
    topic_title: str = ""


class GlobalIn(BaseModel):
    xepbai_off_default_cpa: int = 1
    xepbai_off_default_mode: str = "normal"
    xepbai_mode: str = "off"
    xepbai_whitelist_cmds: list[str] = []
    low_media_warn_threshold: int = 50
    auto_run_enabled: bool = True
    require_full_batch: bool = True
    require_up_confirm: bool = True
    confirm_cancel_before_sec: int = 900
    stock_poll_interval_sec: int = 300
    system_armed: bool | None = None
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    web_token: str = ""


class ScheduleIn(BaseModel):
    enabled: bool = False
    times: list[str] = Field(default_factory=lambda: ["08:00", "20:00"])
    timezone: str = "Asia/Ho_Chi_Minh"
    run_all_topics: bool = True
    run_all_task_after: bool = True


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
    channels = load_channels(BRANCH_ADS)
    folders = load_folders(BRANCH_ADS)
    plain_channels = load_channels(BRANCH_PLAIN)
    plain_folders = load_folders(BRANCH_PLAIN)
    plain_topic_map = ""
    try:
        plain_topic_map = load_topic_map_text(BRANCH_PLAIN)
    except Exception:
        pass
    all_task = cfg.get("all_task") or {}
    plain_task = cfg.get("plain_task") or {}
    return {
        "config": {**cfg, "global": safe_global},
        "inventory": load_inventory(),
        "runtime": {
            **rt,
            "next_run_at": next_ts or rt.get("next_run_at", 0),
            "waiting_topics": rt.get("waiting_topics") or {},
            "pending_up": rt.get("pending_up") or {},
            "all_batch_preview": rt.get("all_batch_preview"),
        },
        "channels": channels,
        "folders": folders,
        "plain_channels": plain_channels,
        "plain_folders": plain_folders,
        "plain_topic_map": plain_topic_map,
        "meta": {
            "channel_count": len(channels),
            "plain_channel_count": len(plain_channels),
            "folder_count": len(folders),
            "plain_folder_count": len(plain_folders),
            "topic_count": len(cfg.get("topic_sources") or {}),
            "userbot_ready": api_ready(),
            "system_armed": system_armed(),
            "pending_actions": len(list_pending_actions()),
            "all_task_enabled": bool(all_task.get("enabled")),
            "all_task_configured": bool(
                all_task.get("source_chat_id") and (
                    all_task.get("selected_channel_ids") or plain_task.get("selected_channel_ids")
                )
            ),
            "plain_task_enabled": bool(plain_task.get("enabled")),
            "web_token_required": bool(web_token()),
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
    data = body.model_dump(exclude_none=True)
    if "system_armed" in data:
        set_system_armed(bool(data.pop("system_armed")))
    cfg["global"].update(data)
    save_auto_config(cfg)
    return {**cfg["global"], "system_armed": system_armed()}


@app.post("/api/system/start")
async def api_system_start(_=Depends(_auth)):
    set_system_armed(True)
    action = queue_action("run_full_cycle")
    append_log("info", "▶ START — arm system + full cycle")
    await send_bot_notify(
        "▶ <b>START</b> — bắt đầu full cycle\n"
        "Bot sẽ gửi link + tên topic khi chạy từng map.",
        parse_mode="HTML",
    )
    return {
        "ok": True,
        "system_armed": True,
        "action": action,
        "message": "Đã START — bot sẽ thông báo tiến trình + link topic",
    }


@app.post("/api/system/stop")
async def api_system_stop(_=Depends(_auth)):
    set_system_armed(False)
    append_log("info", "⏹ STOP — tắt auto + lịch")
    await send_bot_notify("⏹ <b>STOP</b> — tắt auto + lịch (tool vẫn online)", parse_mode="HTML")
    return {
        "ok": True,
        "system_armed": False,
        "message": "Đã STOP — tool online nhưng không chạy auto/lịch",
    }


@app.get("/api/system/status")
async def api_system_status(_=Depends(_auth)):
    return {"system_armed": system_armed(), "userbot_ready": api_ready()}


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


@app.post("/api/topics/map")
async def add_topic_map(body: TopicMapAddIn, _=Depends(_auth)):
    try:
        entry = upsert_topic_mapping(
            topic_title=body.topic_title,
            channel_cmd=body.channel_cmd,
            post_count=body.post_count,
            src_chat_id=body.src_chat_id,
            topic_id=body.topic_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    append_log("info", f"Thêm map {body.topic_title} → /{body.channel_cmd.lstrip('/')}")
    return {"ok": True, "entry": entry, "message": f"✓ {body.topic_title} → /{body.channel_cmd.lstrip('/')}"}


@app.patch("/api/topics/{src_chat_id}/{topic_id}/mapping")
async def patch_topic_mapping(
    src_chat_id: int, topic_id: int, body: TopicMapCmdIn, _=Depends(_auth),
):
    try:
        entry = update_topic_mapping_cmd(
            src_chat_id=src_chat_id,
            topic_id=topic_id,
            topic_title=body.topic_title,
            old_cmd=body.old_cmd,
            new_cmd=body.new_cmd,
            post_count=body.post_count,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    append_log("info", f"Sửa map /{body.old_cmd} → /{body.new_cmd.lstrip('/')}")
    return entry


@app.post("/api/topics")
async def post_topic(body: TopicSourceIn, _=Depends(_auth)):
    from core.map_limits import normalize_post_limits
    data = body.model_dump()
    if data.get("map_post_limits"):
        data["map_post_limits"] = normalize_post_limits(data["map_post_limits"])
    entry = upsert_topic_source(body.src_chat_id, body.topic_id, data)
    sync_topic_to_map_file(entry)
    return entry


@app.patch("/api/topics/{src_chat_id}/{topic_id}/post-limits")
async def patch_topic_post_limits(
    src_chat_id: int, topic_id: int, body: TopicPostLimitsIn, _=Depends(_auth),
):
    from core.map_limits import normalize_post_limits
    limits = normalize_post_limits(body.map_post_limits)
    entry = upsert_topic_source(src_chat_id, topic_id, {"map_post_limits": limits})
    sync_topic_to_map_file(entry)
    title = entry.get("topic_title") or f"{src_chat_id}:{topic_id}"
    append_log("info", f"Số bài/map {title}: {limits or '(mặc định media)'}")
    return entry


@app.post("/api/topics/start-link")
async def post_topic_start_link_flexible(body: TopicStartLinkIn, _=Depends(_auth)):
    try:
        entry = apply_start_link_flexible(
            body.link,
            src_chat_id=body.src_chat_id,
            topic_id=body.topic_id,
            topic_title=body.topic_title or None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    append_log(
        "info",
        f"Set start link {entry.get('topic_title') or entry.get('src_chat_id')} → msg {entry.get('start_msg_id')}",
    )
    return entry


@app.post("/api/topics/{src_chat_id}/{topic_id}/start-link")
async def post_topic_start_link(src_chat_id: int, topic_id: int, body: StartLinkIn, _=Depends(_auth)):
    try:
        entry = apply_start_link_to_topic(src_chat_id, topic_id, body.link)
    except ValueError as e:
        raise HTTPException(400, str(e))
    append_log("info", f"Set start link {src_chat_id}:{topic_id} → msg {entry.get('start_msg_id')}")
    return entry


@app.delete("/api/topics/{src_chat_id}/{topic_id}/start-link")
async def delete_topic_start_link(src_chat_id: int, topic_id: int, _=Depends(_auth)):
    return clear_start_link(src_chat_id, topic_id)


@app.delete("/api/topics/{src_chat_id}/{topic_id}")
async def delete_topic(
    src_chat_id: int,
    topic_id: int,
    topic_title: str | None = None,
    _=Depends(_auth),
):
    ok = delete_topic_entry(
        src_chat_id=src_chat_id,
        topic_id=topic_id,
        topic_title=topic_title or "",
    )
    if not ok:
        raise HTTPException(404, "Không tìm thấy topic")
    append_log("info", f"Xóa map topic {topic_title or f'{src_chat_id}:{topic_id}'}")
    return {"ok": True}


@app.get("/api/all-task")
async def get_all_task(_=Depends(_auth)):
    return load_auto_config().get("all_task", {})


@app.patch("/api/all-task")
async def patch_all_task(body: AllTaskPatchIn, _=Depends(_auth)):
    patch = body.model_dump(exclude_none=True)
    if "source_link" in patch and "start_link" not in patch:
        patch["start_link"] = patch.pop("source_link")
    elif "source_link" in patch:
        patch["start_link"] = patch.get("source_link") or patch.get("start_link")
        patch.pop("source_link", None)
    try:
        data = merge_all_task(patch)
    except ValueError as e:
        append_log("error", f"/all link lỗi: {e}")
        raise HTTPException(400, str(e))
    link_saved = "start_link" in patch
    if link_saved and data.get("source_chat_id"):
        tid = data.get("source_topic_id") or 0
        loc = f"{data['source_chat_id']}" + (f":{tid}" if tid else " (supergroup)")
        append_log(
            "info",
            f"/all lưu link → {loc} (msg {data.get('start_msg_id') or '—'})",
        )
    elif "enabled" in patch:
        append_log("info", f"/all {'bật' if data.get('enabled') else 'tắt'}")
    elif patch.get("selected_channel_ids") is not None:
        append_log("info", f"/all chọn {len(data.get('selected_channel_ids') or [])} kênh đích")
    msg = None
    if link_saved:
        tid = data.get("source_topic_id") or 0
        loc = f"{data['source_chat_id']}" + (f":{tid}" if tid else "")
        msg = (
            f"✓ Đã lưu nguồn {loc or data['source_chat_id']} "
            "— userbot quét batch trong ~2s"
        )
    return {**data, "message": msg}


@app.post("/api/all-task")
async def post_all_task(body: AllTaskIn, _=Depends(_auth)):
    patch = body.model_dump()
    if patch.get("start_link"):
        patch["source_link"] = patch["start_link"]
    return merge_all_task(patch)


@app.get("/api/plain-task")
async def get_plain_task(_=Depends(_auth)):
    return load_auto_config().get("plain_task", {})


@app.patch("/api/plain-task")
async def patch_plain_task(body: PlainTaskPatchIn, _=Depends(_auth)):
    data = merge_plain_task(body.model_dump(exclude_none=True))
    append_log("info", f"Up bài {'bật' if data.get('enabled') else 'tắt'} — {len(data.get('selected_channel_ids') or [])} kênh")
    return data


@app.get("/api/channels")
async def api_channels(_=Depends(_auth)):
    return load_channels(BRANCH_ADS)


@app.patch("/api/channels/{channel_id}/alias")
async def patch_channel_alias(channel_id: int, body: AliasIn, _=Depends(_auth)):
    try:
        ch = set_channel_alias(channel_id, body.alias, BRANCH_ADS)
    except ValueError as e:
        raise HTTPException(404, str(e))
    append_log("info", f"Alias kênh {channel_id} → {body.alias or '(xóa)'}")
    return ch


@app.get("/api/plain/channels")
async def api_plain_channels(_=Depends(_auth)):
    return load_channels(BRANCH_PLAIN)


@app.patch("/api/plain/channels/{channel_id}/alias")
async def patch_plain_channel_alias(channel_id: int, body: AliasIn, _=Depends(_auth)):
    try:
        ch = set_channel_alias(channel_id, body.alias, BRANCH_PLAIN)
    except ValueError as e:
        raise HTTPException(404, str(e))
    append_log("info", f"Alias Up bài {channel_id} → {body.alias or '(xóa)'}")
    return ch


@app.get("/api/plain/folders")
async def api_plain_folders(_=Depends(_auth)):
    return load_folders(BRANCH_PLAIN)


@app.post("/api/plain/folders")
async def post_plain_folder(body: PlainFolderIn, _=Depends(_auth)):
    try:
        fd = add_folder_link(body.link, BRANCH_PLAIN)
    except ValueError as e:
        raise HTTPException(400, str(e))
    append_log("info", f"Thêm folder Up bài: {fd['slug']}")
    action = queue_action("sync_plain_folders")
    return {"ok": True, "folder": fd, "action": action, "message": "Đã lưu folder — userbot sync trong ~2s"}


@app.get("/api/plain/topic-map")
async def get_plain_topic_map(_=Depends(_auth)):
    return {"content": load_topic_map_text(BRANCH_PLAIN)}


@app.patch("/api/plain/topic-map")
async def patch_plain_topic_map(body: PlainTopicMapIn, _=Depends(_auth)):
    save_topic_map_text(body.content, BRANCH_PLAIN)
    append_log("info", "Lưu plain_topic_map.txt")
    return {"ok": True, "content": body.content}


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

