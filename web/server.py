"""FastAPI web dashboard for topic sources, inventory, /all task."""

import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.config_store import (
    load_auto_config,
    load_inventory,
    save_auto_config,
    topic_key,
    upsert_topic_source,
)

WEB_DIR = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="UpBain Control", version="26")


def _auth(authorization: str | None = Header(default=None), x_token: str | None = Header(default=None)):
    cfg = load_auto_config()
    token = cfg.get("global", {}).get("web_token") or os.getenv("WEB_TOKEN", "")
    if not token:
        return True
    got = (authorization or "").replace("Bearer ", "").strip() or (x_token or "").strip()
    if got != token:
        raise HTTPException(401, "Unauthorized")
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


class GlobalIn(BaseModel):
    xepbai_off_default_cpa: int = 1
    xepbai_off_default_mode: str = "normal"
    low_media_warn_threshold: int = 50
    auto_run_enabled: bool = True


@app.get("/", response_class=HTMLResponse)
async def index():
    path = os.path.join(WEB_DIR, "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/config")
async def api_config(_=Depends(_auth)):
    return load_auto_config()


@app.patch("/api/global")
async def patch_global(body: GlobalIn, _=Depends(_auth)):
    cfg = load_auto_config()
    cfg["global"].update(body.model_dump())
    save_auto_config(cfg)
    return cfg["global"]


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
    import json
    path = os.getenv("CHANNELS_FILE", "channels.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


@app.post("/api/channels/all-select")
async def save_all_channels(body: dict[str, Any], _=Depends(_auth)):
    ids = body.get("selected_channel_ids", [])
    cfg = load_auto_config()
    cfg.setdefault("all_task", {})["selected_channel_ids"] = ids
    save_auto_config(cfg)
    return cfg["all_task"]


@app.post("/api/trigger/{src_chat_id}/{topic_id}")
async def api_trigger(src_chat_id: int, topic_id: int, _=Depends(_auth)):
    """Queue trigger — client gọi /runtopic trên bot; endpoint lưu intent."""
    cfg = load_auto_config()
    cfg.setdefault("pending_triggers", []).append({
        "src_chat_id": src_chat_id,
        "topic_id": topic_id,
        "ts": __import__("time").time(),
    })
    save_auto_config(cfg)
    return {"ok": True, "message": "Gõ /runtopic trên bot hoặc bật auto_run"}
