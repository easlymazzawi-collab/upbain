"""
tool__tauto_nostage.py  v26
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Luồng hoạt động:
  1. Forward bài vào Saved Messages
  2. /done* / /xdone / /zdone  → tool xếp sequence trong memory
  3. Gõ tên kênh / tap lệnh   → forward thẳng ra kênh đích
  4. Auto reset, sẵn sàng batch tiếp

Tính năng nền:
  • Auto sync folder mỗi 1h
  • Auto remove kênh chết mỗi 6h + on-the-fly
  • Anti-flood: retry 6 lần, global flood gate + TokenBucket
  • Im lặng: không spam confirm

Lệnh:
  /add /addf /list /del /alias /check /clean
  /map /mapgen /xepbai /xepbaiwhite /all /next /skip /help

Fixes v25 (so với v24):
  [LOCK]     _lock_batch_intake: đóng băng batch (_batch_n, waiting=False) trước auto xếp
  [LOCK]     _begin_auto_batch: lock → slot mới ngay → user forward batch tiếp không lẫn
  [RACE]     Handler: batch đang xếp → tạo slot mới; awaiting_channel → báo /skip
  [TOPIC]    resolve_forward_topic v24: cache (src_id,top_id), không cache title giả khi flood
  [TOPIC]    _topic_fallback_once: thử msg đầu/cuối/giữa batch
  [MAP]      Scoped topic map: -100xxx:topic = cmd (ưu tiên hơn tên topic chung)
  [WAIT]     TOPIC_DETECT_MAX_WAIT_SEC = 45; topic detect nền trong _auto_process_batch
  [MENU]     update_menu non-blocking + debounce _menu_gen (v24)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

# Pyrogram cần stdlib `platform` — folder local tên `platform/` (bản cũ) sẽ che → crash Windows.
def _bootstrap_stdlib_platform() -> None:
    import sys
    import os

    root = os.path.dirname(os.path.abspath(__file__))
    shadow_dir = os.path.join(root, "platform")
    shadow_py = os.path.join(root, "platform.py")
    has_shadow = os.path.isdir(shadow_dir) or os.path.isfile(shadow_py)

    mod = sys.modules.get("platform")
    if mod is not None and not hasattr(mod, "python_implementation"):
        del sys.modules["platform"]
        for key in list(sys.modules):
            if key == "platform" or key.startswith("platform."):
                del sys.modules[key]

    if has_shadow:
        print(
            "⚠️  Phát hiện folder/file `platform` cũ trong project.\n"
            "    Xóa `platform\\` (chỉ giữ `research_platform\\`) sau khi git pull.\n"
            "    Tool vẫn cố chạy bằng stdlib platform..."
        )

    if "platform" not in sys.modules or not hasattr(sys.modules.get("platform"), "python_implementation"):
        saved = sys.path.copy()
        try:
            sys.path[:] = [
                p for p in sys.path
                if os.path.abspath(p or root) != root
            ]
            import platform as _stdlib_platform  # noqa: F401
            if not hasattr(_stdlib_platform, "python_implementation"):
                raise RuntimeError("stdlib platform not loaded")
        finally:
            sys.path[:] = saved


_bootstrap_stdlib_platform()

import asyncio
import copy
import os
import json
import random
import re as _re_cmd
import time
import traceback
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.errors import (
    FloodWait,
    ChannelInvalid,
    ChannelPrivate,
    ChatWriteForbidden,
    PeerIdInvalid,
    UserBannedInChannel,
    ChatAdminRequired,
)

from core.settings import (
    ads_chat_id as _ads_chat_id,
    api_hash as _api_hash,
    api_id as _api_id,
    api_ready as _api_ready,
    intermediate_chat_id as _intermediate_chat_id,
    web_port as _web_port,
)
from core.channel_store import (
    BRANCH_ADS,
    BRANCH_PLAIN,
    invalidate_channels_cache as _cs_invalidate,
    load_channels as _cs_load_channels,
    load_folders as _cs_load_folders,
    remember_folder as _cs_remember_folder,
    save_channels as _cs_save_channels,
)


def get_intermediate_chat() -> int:
    cid = _intermediate_chat_id()
    if cid is None:
        raise RuntimeError("intermediate_chat chưa cấu hình trên web")
    return cid


def get_ads_chat() -> int:
    cid = _ads_chat_id()
    if cid is None:
        raise RuntimeError("ads_chat chưa cấu hình trên web")
    return cid


SAVED_MESSAGES    = "me"
CHANNELS_FILE     = "channels.json"
FOLDERS_FILE      = "folders.json"
FAILED_FILE       = "failed_msgs.json"
RR_FILE           = "topic_rr.json"

FOLDER_SYNC_INTERVAL_SEC = 3600
DEAD_CHECK_INTERVAL_SEC  = 6 * 3600

# Auto: tuần tự 1 kênh, chậm hơn để tránh FloodWait GetMessages
FWD_BASE_DELAY_SEC          = 2.0
FWD_JITTER_SEC              = 0.6
FWD_MAX_RETRY               = 6
FWD_BETWEEN_CHANNELS_SEC    = 3.0
FWD_MAX_CONCURRENT_CHANNELS = 2

FWD_BATCH_SIZE      = 50
FWD_BATCH_MIN_DELAY = 1.0
FWD_GLOBAL_RATE     = 4.0
FWD_GLOBAL_BURST    = 6

TOPIC_DETECT_MAX_WAIT_SEC = 45
MENU_DEBOUNCE_SEC         = 1.5

DEAD_CHANNEL_ERRORS = (
    ChannelInvalid,
    ChannelPrivate,
    PeerIdInvalid,
    UserBannedInChannel,
)

SKIP_NOT_DEAD_ERRORS = (
    ChatWriteForbidden,
    ChatAdminRequired,
)

app: Client | None = None
if _api_ready():
    app = Client("test_session", api_id=int(_api_id()), api_hash=str(_api_hash()))

fwd_lock             = asyncio.Lock()
_auto_pipeline_lock  = asyncio.Lock()
_channels_write_lock = asyncio.Lock()

# Global flood gate
_flood_gate  = asyncio.Lock()
_flood_until = 0.0

_batch_intake_lock  = asyncio.Lock()
_topic_resolve_lock = asyncio.Lock()
_fwd_bucket         = None


class TokenBucket:
    def __init__(self, rate: float, capacity: float):
        self.rate     = rate
        self.capacity = capacity
        self.tokens   = capacity
        self.last     = time.monotonic()
        self.lock     = asyncio.Lock()

    async def acquire(self, n: float = 1.0):
        async with self.lock:
            now     = time.monotonic()
            elapsed = now - self.last
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last   = now
            if self.tokens >= n:
                self.tokens -= n
                return
            need = n - self.tokens
            wait = need / self.rate
            self.tokens = 0
            self.last   = now + wait
        await asyncio.sleep(wait)


def _fwd_bucket_get():
    global _fwd_bucket
    if _fwd_bucket is None:
        _fwd_bucket = TokenBucket(FWD_GLOBAL_RATE, FWD_GLOBAL_BURST)
    return _fwd_bucket


class ForwardFetchCache:
    """Cache get_messages / get_media_group trong 1 lượt — tránh gọi lại cho từng kênh."""

    __slots__ = ("_msgs", "_albums", "_lock")

    def __init__(self):
        self._msgs: dict[tuple[int, int], object] = {}
        self._albums: dict[tuple[int, int], list] = {}
        self._lock = asyncio.Lock()

    async def get_message(self, src_chat, msg_id):
        key = (int(src_chat), int(msg_id))
        cached = self._msgs.get(key)
        if cached is not None:
            return cached
        async with self._lock:
            if key not in self._msgs:
                await _fwd_bucket_get().acquire()
                self._msgs[key] = await app.get_messages(key[0], key[1])
            return self._msgs[key]

    async def get_album(self, src_chat, msg_id):
        key = (int(src_chat), int(msg_id))
        cached = self._albums.get(key)
        if cached is not None:
            return cached
        async with self._lock:
            if key not in self._albums:
                await _fwd_bucket_get().acquire()
                self._albums[key] = await app.get_media_group(key[0], key[1])
            return self._albums[key]


# ─────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────

def log(tag, msg):
    print(f"[{tag}] {msg}")


# ─────────────────────────────────────────────────────────
# [FIX-1] flood_wait_globally
# Coroutine thứ 2 vào sau khi gate mở → chờ nốt rồi return,
# không set lại _flood_until → tránh nhân đôi thời gian chờ
# ─────────────────────────────────────────────────────────

async def flood_wait_globally(seconds: float, source: str = ""):
    global _flood_until
    async with _flood_gate:
        now       = time.monotonic()
        remaining = _flood_until - now
        if remaining > 0:
            log("FLOOD", f"[{source}] gate đang chờ {remaining:.0f}s — xếp hàng")
            await asyncio.sleep(remaining)
            return   # không set lại, chờ đủ là xong
        _flood_until = time.monotonic() + seconds
        log("FLOOD", f"[{source}] FloodWait toàn cục {seconds:.0f}s")
        await asyncio.sleep(seconds)
        _flood_until = 0.0


# ─────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────

def make_slot():
    return {
        "content_msgs":      [],
        "seen_media_groups": set(),
        "ads_msgs":          [],
        "ads_chat_id":       None,
        "ads_index":         0,
        "final_sequence":    [],
        "menu_msg_id":       None,
        "waiting":           True,
        "awaiting_channel":  False,
        "channel_commands":  {},
        "topic_id":          None,
        "topic_title":       None,
        "topic_src_id":      None,
        "topic_checked":     False,
        "all_mode":          False,
        "_album_pending":         0,
        "total_media_count":      0,
        "_topic_detect_started":  False,
        "_menu_gen":              0,
        "_batch_processing":      False,
        "_batch_n":               None,
    }

state = {
    "my_id":    None,
    "slots":    [make_slot()],
    "checking": False,
}

def active_slot():
    if not state["slots"]:
        log("WARN", "active_slot() called on empty slots — tạo mới")
        state["slots"].append(make_slot())
    return state["slots"][-1]

def waiting_slot():
    for s in state["slots"]:
        if s["awaiting_channel"]:
            return s
    return None

def reset_slot(slot):
    slot["content_msgs"].clear()
    slot["seen_media_groups"].clear()
    slot["ads_index"]        = 0
    slot["ads_msgs"]         = []
    slot["final_sequence"]   = []
    slot["menu_msg_id"]      = None
    slot["waiting"]          = True
    slot["awaiting_channel"] = False
    slot["channel_commands"] = {}
    slot["topic_id"]         = None
    slot["topic_title"]      = None
    slot["topic_src_id"]     = None
    slot["topic_checked"]    = False
    slot["all_mode"]              = False
    slot["_album_pending"]        = 0
    slot["total_media_count"]     = 0
    slot["_topic_detect_started"] = False
    slot["_menu_gen"]             = 0
    slot["_batch_processing"]     = False
    slot.pop("_batch_n", None)
    slot.pop("_topic_event", None)

def reset_state():
    reset_slot(active_slot())
    state["slots"] = [s for s in state["slots"] if s["waiting"] or s["awaiting_channel"]]
    if not state["slots"]:
        state["slots"].append(make_slot())
    log("RESET", "State đã reset")


# ─────────────────────────────────────────────────────────
# INTERMEDIATE_CHAT messaging
# ─────────────────────────────────────────────────────────

IM_SEND_MAX_RETRY = 6
IM_SEND_MAX_WAIT  = 300
IM_SEND_SPACING   = 0.4

im_send_lock     = asyncio.Lock()
_last_im_send_ts = 0.0

async def _im_spacing():
    global _last_im_send_ts
    now = time.monotonic()
    gap = now - _last_im_send_ts
    if gap < IM_SEND_SPACING:
        await asyncio.sleep(IM_SEND_SPACING - gap)
    _last_im_send_ts = time.monotonic()

async def robust_send(text, chat_id=None,
                      max_retries=IM_SEND_MAX_RETRY,
                      max_wait=IM_SEND_MAX_WAIT):
    target   = get_intermediate_chat() if chat_id is None else chat_id
    use_lock = (target == get_intermediate_chat())

    async def _do():
        for attempt in range(max_retries):
            try:
                if use_lock:
                    await _im_spacing()
                return await app.send_message(target, text)
            except FloodWait as e:
                wait = min(e.value + 1, max_wait)
                log("FLOOD", f"robust_send FloodWait {wait}s — retry {attempt+1}/{max_retries}")
                await asyncio.sleep(wait)
            except Exception as e:
                log("WARN", f"robust_send fail: {type(e).__name__}: {e}")
                return None
        log("ERROR", f"robust_send bỏ cuộc — MẤT MSG: {text[:60]!r}")
        return None

    if use_lock:
        async with im_send_lock:
            return await _do()
    return await _do()

async def robust_edit(chat_id, msg_id, text,
                      max_retries=IM_SEND_MAX_RETRY,
                      max_wait=IM_SEND_MAX_WAIT):
    use_lock = (chat_id == get_intermediate_chat())

    async def _do():
        for attempt in range(max_retries):
            try:
                if use_lock:
                    await _im_spacing()
                await app.edit_message_text(chat_id, msg_id, text)
                return True
            except FloodWait as e:
                wait = min(e.value + 1, max_wait)
                log("FLOOD", f"robust_edit FloodWait {wait}s — retry {attempt+1}/{max_retries}")
                await asyncio.sleep(wait)
            except Exception as e:
                log("WARN", f"robust_edit fail: {type(e).__name__}: {e}")
                return False
        return False

    if use_lock:
        async with im_send_lock:
            return await _do()
    return await _do()

async def safe_send(text):
    await robust_send(text)


# ─────────────────────────────────────────────────────────
# Channel list helpers
# ─────────────────────────────────────────────────────────

_channels_cache = None

def load_channels(branch: str = BRANCH_ADS):
    return _cs_load_channels(branch)

def save_channels(channels, branch: str = BRANCH_ADS):
    _cs_save_channels(channels, branch)

def invalidate_channels_cache(branch: str | None = None):
    _cs_invalidate(branch)

async def remove_dead_channel(chat_id, branch: str = BRANCH_ADS):
    async with _channels_write_lock:
        channels = load_channels(branch)
        new_list = [ch for ch in channels if str(ch.get("id")) != str(chat_id)]
        if len(new_list) == len(channels):
            return None
        removed = next((ch for ch in channels if str(ch.get("id")) == str(chat_id)), None)
        save_channels(new_list, branch)
        return (removed or {}).get("title", str(chat_id))

def get_match_key(title: str) -> str:
    parts = title.strip().split(None, 1)
    if len(parts) >= 2:
        return parts[1].strip().lower()
    return (parts[0] if parts else "").lower()

def find_channels(query: str):
    q = query.lower().strip()
    if not q:
        return []
    result = []
    for ch in load_channels():
        alias     = ch.get("alias", "").lower().strip()
        title     = ch.get("title", "") or ""
        match_key = get_match_key(title)
        if alias:
            if q in alias:
                result.append(ch)
        else:
            if match_key and q in match_key:
                result.append(ch)
    return result


# ─────────────────────────────────────────────────────────
# Topic detection
# ─────────────────────────────────────────────────────────

_topic_title_cache = {}


def _is_valid_topic_title(title) -> bool:
    if not title:
        return False
    t = str(title).strip()
    if not t or t == "General":
        return False
    if t.startswith("topic "):
        return False
    return True


def _set_slot_topic(slot, src_id, top_id, title, source=""):
    slot["topic_src_id"] = src_id
    slot["topic_id"]     = top_id
    slot["topic_title"]  = title
    log("TOPIC", f"{source} topic='{title}' id={top_id} src={src_id}")


async def resolve_forward_topic(client, saved_msg_id):
    from pyrogram.raw import functions as fn, types as tt
    from pyrogram import utils as ut
    for attempt in range(FWD_MAX_RETRY):
        try:
            while True:
                remain = _flood_until - time.monotonic()
                if remain <= 0:
                    break
                log("FLOOD", f"resolve_forward_topic chờ gate {remain:.0f}s")
                await asyncio.sleep(remain)

            raw  = await client.invoke(fn.messages.GetMessages(id=[tt.InputMessageID(id=saved_msg_id)]))
            rm   = raw.messages[0]
            fwd  = getattr(rm, "fwd_from", None)
            sp   = getattr(fwd, "saved_from_peer", None)
            smid = getattr(fwd, "saved_from_msg_id", None)
            if not sp or not smid:
                return (None, None, None)
            src_id = ut.get_peer_id(sp)
            ipc    = await client.resolve_peer(src_id)
            if not hasattr(ipc, "channel_id"):
                return (src_id, None, None)
            inch = tt.InputChannel(channel_id=ipc.channel_id, access_hash=ipc.access_hash)
            og   = await client.invoke(fn.channels.GetMessages(channel=inch, id=[tt.InputMessageID(id=smid)]))
            omsg = og.messages[0]
            rt   = getattr(omsg, "reply_to", None)
            if rt is not None and getattr(rt, "forum_topic", False):
                top_id = (getattr(rt, "reply_to_top_id", None) or getattr(rt, "reply_to_msg_id", None))
            else:
                top_id = 1

            cache_key = (src_id, top_id)
            title = _topic_title_cache.get(cache_key)
            if title is None:
                try:
                    ft    = await client.invoke(fn.channels.GetForumTopicsByID(channel=inch, topics=[top_id]))
                    title = (ft.topics[0].title if getattr(ft, "topics", None) else None)
                except FloodWait:
                    raise
                except Exception as exc:
                    log("WARN", f"GetForumTopicsByID fail src={src_id} topic={top_id}: {exc}")
                    title = None
                if _is_valid_topic_title(title):
                    _topic_title_cache[cache_key] = title
                else:
                    title = None

            if not _is_valid_topic_title(title):
                return (src_id, top_id, None)
            return (src_id, top_id, title)

        except FloodWait as e:
            wait = e.value + 2
            log("FLOOD", f"resolve_forward_topic FloodWait {wait}s — retry {attempt+1}/{FWD_MAX_RETRY}")
            await flood_wait_globally(wait, source="topic_resolve")
            continue
        except Exception as e:
            if attempt < FWD_MAX_RETRY - 1:
                backoff = 2 * (attempt + 1)
                log("WARN", f"resolve_forward_topic attempt {attempt+1}: {type(e).__name__}: {e} — retry {backoff}s")
                await asyncio.sleep(backoff)
                continue
            log("WARN", f"resolve_forward_topic: {type(e).__name__}: {e}")
            return (None, None, None)
    return (None, None, None)


async def wait_for_topic(slot, max_wait=TOPIC_DETECT_MAX_WAIT_SEC):
    if _is_valid_topic_title(slot.get("topic_title")) and slot.get("topic_src_id"):
        return True
    ev = slot.get("_topic_event")
    if ev is not None and not ev.is_set():
        try:
            await asyncio.wait_for(ev.wait(), timeout=max_wait)
        except asyncio.TimeoutError:
            log("TOPIC", f"wait_for_topic: timeout {max_wait}s")
    return _is_valid_topic_title(slot.get("topic_title")) and bool(slot.get("topic_src_id"))


# ─────────────────────────────────────────────────────────
# Topic map (topic_map.txt)
# ─────────────────────────────────────────────────────────

TOPIC_MAP_TXT = "topic_map.txt"

TOPIC_MAP_TEMPLATE = (
    "# ===== MAP TOPIC -> KÊNH (sửa tay file này) =====\n"
    "# Mỗi dòng 1 mapping:   tên_topic = tên_kênh\n"
    "# Scoped (ưu tiên khi biết nguồn):   chat_id:tên_topic = tên_kênh\n"
    "# Dòng # là ghi chú. Sửa xong lưu là dùng được ngay.\n"
    "#\n"
    "# Ví dụ:\n"
    "# vitamin = pro\n"
    "# -1001234567890:vitamin = pro\n"
    "# real    = real\n"
    "#\n"
    "# ----- Cấu hình xếp bài -----\n"
    "# @xepbai = on\n"
    "# @xepbaiwhite =\n"
)

def ensure_topic_map_txt():
    if not os.path.exists(TOPIC_MAP_TXT):
        try:
            with open(TOPIC_MAP_TXT, "w", encoding="utf-8") as f:
                f.write(TOPIC_MAP_TEMPLATE)
            log("MAP", f"Tạo file mẫu {TOPIC_MAP_TXT}")
        except Exception as e:
            log("WARN", f"ensure_topic_map_txt: {e}")

def _read_topic_lines():
    out = []
    if not os.path.exists(TOPIC_MAP_TXT):
        return out
    try:
        with open(TOPIC_MAP_TXT, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if "#" in s:
                    s = s.split("#", 1)[0].strip()
                if not s:
                    continue
                sep = "=" if "=" in s else (":" if ":" in s else None)
                if not sep:
                    continue
                left, _, right = s.partition(sep)
                out.append((left.strip(), right.strip()))
    except Exception as e:
        log("WARN", f"_read_topic_lines: {e}")
    return out

def load_topic_txt():
    return [(l, r.lstrip("/")) for (l, r) in _read_topic_lines()
            if l and r and not l.startswith("@")]

def get_xepbai_mode():
    val = "on"
    for l, r in _read_topic_lines():
        if l.lower() == "@xepbai":
            val = "off" if r.strip().lower() == "off" else "on"
    return val

def get_xepbai_whitelist():
    wl   = set()
    seen = False
    for l, r in _read_topic_lines():
        if l.lower() == "@xepbaiwhite":
            wl   = {x.strip().lower().lstrip("/") for x in r.replace(" ", ",").split(",") if x.strip()}
            seen = True
    return wl if seen else set()

def set_topic_directive(key, value):
    ensure_topic_map_txt()
    try:
        with open(TOPIC_MAP_TXT, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        lines = []
    kept = []
    for ln in lines:
        s  = ln.strip()
        cs = s.split("#", 1)[0].strip() if "#" in s else s
        if "=" in cs and cs.partition("=")[0].strip().lower() == key.lower():
            continue
        kept.append(ln)
    kept.append(f"{key} = {value}")
    with open(TOPIC_MAP_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")

def _topic_map_keys(title, src_id=None):
    t = (title or "").strip().lower()
    keys = []
    if src_id is not None:
        keys.append(f"{src_id}:{t}")
        s = str(src_id)
        if s.startswith("-100"):
            keys.append(f"{s[4:]}:{t}")
    if t:
        keys.append(t)
    return keys


def find_cmds_for_topic_title(title, src_id=None):
    if not title:
        return []
    t = title.strip().lower()
    out  = []
    seen = set()

    if src_id is not None:
        scoped_variants = {f"{src_id}:{t}"}
        s = str(src_id)
        if s.startswith("-100"):
            scoped_variants.add(f"{s[4:]}:{t}")
        for topic, cmd in load_topic_txt():
            tl = topic.strip().lower()
            if tl in scoped_variants:
                cl = cmd.lower()
                if cl not in seen:
                    seen.add(cl)
                    out.append(cmd)
        if out:
            return out

    for topic, cmd in load_topic_txt():
        tl = topic.strip().lower()
        if ":" in tl:
            continue
        if tl == t:
            cl = cmd.lower()
            if cl not in seen:
                seen.add(cl)
                out.append(cmd)
    return out

def resolve_channels_by_cmd(cmd_key):
    cmd_key = (cmd_key or "").strip().lower().lstrip("/")
    if not cmd_key:
        return []
    groups = {}
    for ch in load_channels():
        alias = (ch.get("alias") or "").strip().lower()
        title = (ch.get("title") or "").strip()
        k     = get_cmd_key(title, alias)
        if k:
            groups.setdefault(k, []).append(ch)
    return groups.get(cmd_key, [])

def all_channel_cmds():
    groups = {}
    for ch in load_channels():
        alias = (ch.get("alias") or "").strip().lower()
        title = (ch.get("title") or "").strip()
        k     = get_cmd_key(title, alias)
        if k:
            groups.setdefault(k, []).append(ch)
    return sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))

def gen_topic_map_txt():
    existing    = load_topic_txt()
    mapped_cmds = {c.lower() for _, c in existing}
    groups      = all_channel_cmds()
    lines       = [TOPIC_MAP_TEMPLATE.rstrip("\n"), ""]
    if existing:
        lines.append("# ===== ĐÃ MAP =====")
        for topic, cmd in existing:
            lines.append(f"{topic} = {cmd}")
        lines.append("")
    lines.append("# ===== ĐIỀN TÊN TOPIC VÀO TRƯỚC DẤU = =====")
    n_new = 0
    for cmd, chs in groups:
        if cmd.lower() in mapped_cmds:
            continue
        titles = ", ".join((c.get("title") or "") for c in chs)
        lines.append(f" = {cmd}    # {titles}")
        n_new += 1
    with open(TOPIC_MAP_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(existing), n_new


# ─────────────────────────────────────────────────────────
# Round-robin
# ─────────────────────────────────────────────────────────

def load_topic_rr() -> dict:
    if os.path.exists(RR_FILE):
        try:
            with open(RR_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_topic_rr(data: dict):
    with open(RR_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def pick_next_rr(topic_title: str, cmds: list) -> str:
    if not cmds:
        return ""
    if len(cmds) == 1:
        return cmds[0]
    rr   = load_topic_rr()
    key  = (topic_title or "").strip().lower()
    prev = rr.get(key, -1)
    idx  = (prev + 1) % len(cmds)
    rr[key] = idx
    save_topic_rr(rr)
    return cmds[idx]


# ─────────────────────────────────────────────────────────
# Quick-tap /cmd builder
# ─────────────────────────────────────────────────────────

RESERVED_CMDS = {
    "add", "addf", "addchan", "addfolder",
    "list", "listchan",
    "del", "delchan",
    "alias", "aliaschan",
    "check", "checkchan",
    "clean", "cleanchan",
    "skip", "next", "help",
    "xdone", "zdone",
    "map", "unmap", "mapgen",
    "xepbai", "xepbaiwhite",
    "all",
    "done", "done1", "done2", "done3", "done4", "done5",
    "done6", "done7", "done8", "done9", "done10",
}

_CMD_OK = _re_cmd.compile(r"^[a-z0-9_]+$")

def _is_junk_word(w: str) -> bool:
    return not w or w.isdigit() or not any(c.isalnum() for c in w)

def _strip_junk(w: str) -> str:
    i, j = 0, len(w)
    while i < j and not w[i].isalnum():
        i += 1
    while j > i and not w[j-1].isalnum():
        j -= 1
    return w[i:j]

def get_cmd_key(title: str, alias: str = "") -> str:
    if alias and alias.strip():
        return _strip_junk(alias.strip()).lower()
    parts = (title or "").strip().split()
    if len(parts) <= 1:
        return _strip_junk(parts[0]).lower() if parts else ""
    rest = parts[1:]
    while rest and _is_junk_word(rest[-1]):
        rest.pop()
    if not rest:
        return ""
    return _strip_junk(rest[-1]).lower()

def build_channel_commands(channels):
    if not channels:
        return "  (Chưa có kênh — dùng /add <link/id> để thêm)", {}
    groups = {}
    for ch in channels:
        alias = (ch.get("alias") or "").strip().lower()
        title = (ch.get("title") or "").strip()
        key   = get_cmd_key(title, alias)
        if not key:
            continue
        groups.setdefault(key, []).append(ch)
    sorted_keys = sorted(groups.keys(), key=lambda k: (-len(groups[k]), k))
    lines   = []
    cmd_map = {}
    for key in sorted_keys:
        chs       = groups[key]
        titles    = ", ".join((ch.get("title") or "").strip() for ch in chs)
        clickable = bool(_CMD_OK.match(key)) and key not in RESERVED_CMDS
        if clickable:
            cmd_map[key] = chs
            lines.append(f"/{key}   ({titles})")
        else:
            lines.append(f"• {key}   ({titles})")
    return "\n\n".join(lines), cmd_map


# ─────────────────────────────────────────────────────────
# Folder persistence
# ─────────────────────────────────────────────────────────

def load_folders(branch: str = BRANCH_ADS):
    return _cs_load_folders(branch)

def save_folders(folders, branch: str = BRANCH_ADS):
    from core.channel_store import save_folders as _save
    _save(folders, branch)

def remember_folder(slug, title="", branch: str = BRANCH_ADS):
    return _cs_remember_folder(slug, title, branch)


# ─────────────────────────────────────────────────────────
# Failed messages
# ─────────────────────────────────────────────────────────

def load_failed():
    if os.path.exists(FAILED_FILE):
        try:
            with open(FAILED_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_failed(items):
    with open(FAILED_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def record_failed(target_id, target_title, items):
    if not items:
        return
    data = load_failed()
    data.append({
        "target_id":    target_id,
        "target_title": target_title,
        "items":        items,
        "ts":           int(time.time()),
    })
    save_failed(data)


# ─────────────────────────────────────────────────────────
# Ads loader
# ─────────────────────────────────────────────────────────

async def load_ads_into(slot):
    ads     = []
    chat_id = slot.get("ads_chat_id") or get_ads_chat()
    try:
        async for msg in app.get_chat_history(chat_id, limit=200):
            if not msg.empty and not msg.service:
                ads.append(msg.id)
        ads.reverse()
        slot["ads_msgs"]    = ads
        slot["ads_chat_id"] = chat_id
        log("ADS", f"Load xong {len(ads)} ads")
    except Exception as e:
        log("ERROR", f"load_ads: {e}")

async def load_ads():
    await load_ads_into(active_slot())


# ─────────────────────────────────────────────────────────
# Batch lock & auto-process
# ─────────────────────────────────────────────────────────

async def _topic_fallback_once(slot, client=None):
    msgs = slot.get("content_msgs") or []
    if not msgs:
        return False
    client = client or app
    try_ids = []
    seen    = set()
    for mid in (msgs[0], msgs[-1], msgs[len(msgs) // 2]):
        if mid not in seen:
            seen.add(mid)
            try_ids.append(mid)
    async with _topic_resolve_lock:
        if _is_valid_topic_title(slot.get("topic_title")):
            return True
        for msg_id in try_ids:
            src_id, top_id, top_title = await resolve_forward_topic(client, msg_id)
            if top_id is not None and _is_valid_topic_title(top_title):
                _set_slot_topic(slot, src_id, top_id, top_title, source="fallback")
                return True
    log("TOPIC", "fallback không detect được topic")
    return False


async def _lock_batch_intake(slot, expected_n=None):
    async with _batch_intake_lock:
        deadline = time.monotonic() + 15
        while slot.get("_album_pending", 0) > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        n = len(slot["content_msgs"])
        if expected_n is not None and n != expected_n:
            log("WARN", f"batch lock: expected n={expected_n} got {n}")
        slot["_batch_n"] = n
        slot["waiting"]  = False
        return n


async def _auto_process_batch(slot, n, gen=None):
    """Xếp + forward nền trên batch đã lock. Topic detect từ bài đầu + fallback."""
    try:
        if gen is not None and gen != slot.get("_menu_gen"):
            return
        n = slot.get("_batch_n") or n or len(slot.get("content_msgs") or [])

        mode = get_xepbai_mode()
        await wait_for_topic(slot)
        if not _is_valid_topic_title(slot.get("topic_title")):
            await _topic_fallback_once(slot, app)

        if gen is not None and gen != slot.get("_menu_gen"):
            return

        src_id       = slot.get("topic_src_id")
        mapped_cmds  = find_cmds_for_topic_title(slot.get("topic_title"), src_id=src_id)
        whitelist    = get_xepbai_whitelist()
        force_manual = any(c.lower() in whitelist for c in mapped_cmds)
        topic_auto   = bool(mapped_cmds) and not force_manual
        auto         = (mode == "off" and not force_manual) or topic_auto

        if not auto:
            text = get_menu(n, slot=slot)
            await safe_send(text)
            return

        tid = slot.get("topic_id")
        sid = slot.get("topic_src_id")
        if sid and tid is not None and load_auto_config().get("global", {}).get("auto_run_enabled", True):
            if system_armed():
                await _try_auto_source_topic(sid, tid, slot.get("topic_title") or "")
            return

        if not slot["ads_msgs"]:
            await load_ads_into(slot)
        ads_count = max(1, len(slot["ads_msgs"]))
        best      = max(1, round(n / ads_count))
        reason    = f"topic={slot.get('topic_title')!r}" if topic_auto else "xepbai=off"
        log("XEPBAI", f"nền {reason} → /done{best} mapped={mapped_cmds}")
        await build_sequence_for_slot(slot, content_per_ads=best)

    except Exception as e:
        log("ERROR", f"_auto_process_batch: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        await safe_send(f"❌ Lỗi xếp batch: {type(e).__name__}")
    finally:
        slot["_batch_processing"] = False


async def _begin_auto_batch(slot, n, gen, media_hint):
    locked_n = await _lock_batch_intake(slot, expected_n=n)
    if gen is not None and gen != slot.get("_menu_gen"):
        return
    slot["_batch_processing"] = True
    await safe_send(f"⏳ {media_hint} — chạy nền...")
    new_s = make_slot()
    state["slots"].append(new_s)
    asyncio.ensure_future(load_ads_into(new_s))
    asyncio.ensure_future(_auto_process_batch(slot, locked_n, gen))


# ─────────────────────────────────────────────────────────
# Menu
# ─────────────────────────────────────────────────────────

def get_menu(n_content, slot=None):
    slot      = slot or active_slot()
    ads_count = max(1, len(slot["ads_msgs"]))
    best      = max(1, round(n_content / ads_count))
    media_cnt = slot.get("total_media_count", 0)
    media_str = f" / {media_cnt} media" if media_cnt > 0 else ""
    lines     = [f"📥 Đã nhận {n_content} bài{media_str}\n━━━━━━━━━━━━━━━"]
    for i in range(1, 11):
        mark = " ✅" if i == best else ""
        lines.append(f"/done{i} — {i} content/ads{mark}")
    lines += [
        "━━━━━━━━━━━━━━━",
        f"➡️ Gợi ý: /done{best}",
        "━━━━━━━━━━━━━━━",
        "/xdone — nhét hết ads (có thể 2 ads liền)",
        "/zdone — ads trước, content sau",
    ]
    return "\n".join(lines)


def schedule_update_menu(n):
    slot = active_slot()
    slot["_menu_gen"] = slot.get("_menu_gen", 0) + 1
    gen = slot["_menu_gen"]
    asyncio.ensure_future(update_menu(slot, n, gen))


async def update_menu(slot, n, gen=None):
    await asyncio.sleep(MENU_DEBOUNCE_SEC)
    if gen is not None and gen != slot.get("_menu_gen"):
        return
    if not slot.get("waiting") or slot.get("_batch_processing"):
        return

    for _ in range(30):
        if slot.get("_album_pending", 0) <= 0:
            break
        await asyncio.sleep(0.5)

    if len(slot["content_msgs"]) != n or not slot.get("waiting"):
        return
    if gen is not None and gen != slot.get("_menu_gen"):
        return

    if slot.get("all_mode"):
        log("ALL", f"/all → up {n} bài lên tất cả kênh")
        media_cnt  = slot.get("total_media_count", 0)
        media_hint = f"{n} bài / {media_cnt} media" if media_cnt else f"{n} bài"
        await _lock_batch_intake(slot, expected_n=n)
        slot["_batch_processing"] = True
        await safe_send(f"⏳ {media_hint} — chạy nền (/all)...")
        new_s = make_slot()
        state["slots"].append(new_s)
        asyncio.ensure_future(load_ads_into(new_s))

        async def _all_bg(s):
            try:
                s["all_mode"] = True
                await do_all_forward(s)
            finally:
                s["_batch_processing"] = False

        asyncio.ensure_future(_all_bg(slot))
        return

    mode       = get_xepbai_mode()
    media_cnt  = slot.get("total_media_count", 0)
    media_hint = f"{n} bài / {media_cnt} media" if media_cnt else f"{n} bài"
    src_id     = slot.get("topic_src_id")

    if mode == "off":
        mapped_cmds  = find_cmds_for_topic_title(slot.get("topic_title"), src_id=src_id)
        whitelist    = get_xepbai_whitelist()
        force_manual = any(c.lower() in whitelist for c in mapped_cmds)
        if not force_manual:
            await _begin_auto_batch(slot, n, gen, media_hint)
            return

    mapped_cmds  = find_cmds_for_topic_title(slot.get("topic_title"), src_id=src_id)
    whitelist    = get_xepbai_whitelist()
    force_manual = any(c.lower() in whitelist for c in mapped_cmds)
    topic_auto   = bool(mapped_cmds) and not force_manual
    if topic_auto:
        await _begin_auto_batch(slot, n, gen, media_hint)
        return

    if slot.get("_topic_detect_started") and not force_manual:
        await _begin_auto_batch(slot, n, gen, media_hint)
        return

    text = get_menu(n, slot=slot)
    if slot.get("menu_msg_id"):
        ok = await robust_edit(get_intermediate_chat(), slot["menu_msg_id"], text)
        if ok:
            return
        slot["menu_msg_id"] = None

    if len(slot["content_msgs"]) != n or not slot.get("waiting"):
        return
    if gen is not None and gen != slot.get("_menu_gen"):
        return

    m = await robust_send(text)
    if m is not None:
        slot["menu_msg_id"] = m.id
    else:
        log("ERROR", f"update_menu: không gửi được menu n={n}")


# ─────────────────────────────────────────────────────────
# Raw forward
# ─────────────────────────────────────────────────────────

async def raw_forward(from_peer, to_peer, ids: list):
    from pyrogram.raw import functions
    await app.invoke(
        functions.messages.ForwardMessages(
            from_peer   = from_peer,
            to_peer     = to_peer,
            id          = ids,
            drop_author = True,
            random_id   = [random.randint(0, 2**63) for _ in ids],
            silent      = False,
        )
    )


# ─────────────────────────────────────────────────────────
# Forward sequence → 1 kênh
#
# [FIX-A] sequence phải là bản sao riêng cho mỗi kênh (truyền từ caller)
#         seen_groups là set LOCAL, không dùng chung → album không bị skip
#
# [FIX-B] msg.empty sau max retry → skip (không ghi failed)
#         vì message có thể bị xóa khỏi Saved Messages, không phải lỗi kênh đích
#
# [FIX-2] flood gate check bằng while loop trước mỗi attempt
# ─────────────────────────────────────────────────────────

async def forward_sequence_to_channel(target_id, sequence, *, fetch_cache: ForwardFetchCache | None = None):
    """
    sequence: list[(src_chat, msg_id)] — BẢN SAO RIÊNG cho kênh này (caller deepcopy)
    Returns: (sent_count, failed_items, dead_reason)
    """
    to_peer    = await app.resolve_peer(target_id)
    peer_cache = {}

    async def get_peer(chat_id):
        if chat_id not in peer_cache:
            peer_cache[chat_id] = await app.resolve_peer(chat_id)
        return peer_cache[chat_id]

    # seen_groups: LOCAL cho kênh này — không share với kênh khác [FIX-A]
    seen_groups  = set()
    sent_count   = 0
    failed_items = []
    dead_reason  = None

    for seq_idx, (src_chat, msg_id) in enumerate(sequence):
        last_err = None
        sent_ok  = False

        for attempt in range(FWD_MAX_RETRY):
            # [FIX-2] chờ flood gate bằng while loop
            while True:
                remain = _flood_until - time.monotonic()
                if remain <= 0:
                    break
                log("FLOOD", f"Chờ flood gate {remain:.0f}s — msg={msg_id}")
                await asyncio.sleep(remain)

            try:
                if fetch_cache:
                    msg = await fetch_cache.get_message(src_chat, msg_id)
                else:
                    await _fwd_bucket_get().acquire()
                    msg = await app.get_messages(src_chat, msg_id)

                if msg.empty:
                    if attempt < FWD_MAX_RETRY - 1:
                        log("WARN", f"msg empty attempt {attempt+1}/{FWD_MAX_RETRY}, retry 3s: {src_chat}/{msg_id}")
                        await asyncio.sleep(3)
                        last_err = Exception("msg.empty")
                        continue
                    # [FIX-B] empty sau max retry → skip, KHÔNG ghi vào failed
                    # (message bị xóa khỏi Saved Messages — không phải lỗi kênh đích)
                    log("WARN", f"msg empty sau {FWD_MAX_RETRY} lần → skip (đã xóa khỏi Saved?): {src_chat}/{msg_id}")
                    sent_ok = True   # mark skip để không append failed_items
                    break

                from_peer = await get_peer(src_chat)

                if msg.media_group_id:
                    key = (src_chat, msg.media_group_id)
                    if key in seen_groups:
                        # Album đã forward rồi (item khác của cùng album)
                        sent_ok = True
                        break
                    seen_groups.add(key)
                    if fetch_cache:
                        album = await fetch_cache.get_album(src_chat, msg_id)
                    else:
                        await _fwd_bucket_get().acquire()
                        album = await app.get_media_group(src_chat, msg_id)
                    ids   = [m.id for m in album]
                    if not ids:
                        log("WARN", f"Album rỗng: {key}")
                        sent_ok = True
                        break
                    await _fwd_bucket_get().acquire()
                    await raw_forward(from_peer, to_peer, ids)
                    log("FWD", f"  album {msg.media_group_id} ({len(ids)} items) → ch={target_id}")
                else:
                    await _fwd_bucket_get().acquire()
                    await raw_forward(from_peer, to_peer, [msg_id])
                    log("FWD", f"  msg={msg_id} → ch={target_id}")

                sent_count += 1
                sent_ok     = True
                await asyncio.sleep(FWD_BASE_DELAY_SEC + random.uniform(-FWD_JITTER_SEC, FWD_JITTER_SEC))
                break

            except FloodWait as e:
                wait = e.value + 3
                log("FLOOD", f"FloodWait {wait}s — msg={msg_id} ch={target_id} attempt {attempt+1}/{FWD_MAX_RETRY}")
                await flood_wait_globally(wait, source=f"ch={target_id}")
                last_err = e

            except DEAD_CHANNEL_ERRORS as e:
                dead_reason = f"{type(e).__name__}: {str(e)[:80]}"
                log("DEAD", f"Kênh {target_id} chết: {dead_reason}")
                # Trả về các bài chưa kịp forward (từ seq_idx trở đi)
                return sent_count, list(sequence[seq_idx:]), dead_reason

            except SKIP_NOT_DEAD_ERRORS as e:
                log("SKIP", f"Skip kênh {target_id} ({type(e).__name__}): {str(e)[:80]}")
                return sent_count, list(sequence[seq_idx:]), None

            except Exception as e:
                last_err = e
                backoff  = 2 * (attempt + 1) + random.uniform(0, 1.5)
                log("WARN", f"forward msg={msg_id} ch={target_id} attempt {attempt+1}/{FWD_MAX_RETRY}: "
                            f"{type(e).__name__}: {e} — retry {backoff:.1f}s")
                await asyncio.sleep(backoff)

        if not sent_ok:
            failed_items.append((src_chat, msg_id))
            log("ERROR", f"Bỏ msg={msg_id} sau {FWD_MAX_RETRY} lần: {last_err}")

    if failed_items:
        log("FWD", f"⚠️ ch={target_id}: {len(failed_items)} msg fail sau retry")

    return sent_count, failed_items, dead_reason


# ─────────────────────────────────────────────────────────
# Build sequence
# ─────────────────────────────────────────────────────────

async def build_sequence(content_per_ads=1, mode="normal"):
    await build_sequence_for_slot(active_slot(), content_per_ads=content_per_ads, mode=mode)


async def build_sequence_for_slot(slot, content_per_ads=1, mode="normal"):
    contents     = slot["content_msgs"]
    n            = slot.get("_batch_n") or len(contents)
    ads_chat     = slot["ads_chat_id"] or get_ads_chat()
    content_chat = slot.get("content_chat") or SAVED_MESSAGES
    skip_ads     = slot.get("skip_ads", False)
    src_id       = slot.get("topic_src_id")
    media_cnt    = slot.get("total_media_count", 0)
    media_str    = f"{n} bài / {media_cnt} media" if media_cnt else f"{n} bài"

    log("BUILD", f"mode={mode} n={n} media={media_cnt} ads={len(slot['ads_msgs'])} cpa={content_per_ads}")

    if slot.get("_topic_detect_started") and not _is_valid_topic_title(slot.get("topic_title")):
        await wait_for_topic(slot, max_wait=10)
        if not _is_valid_topic_title(slot.get("topic_title")):
            await _topic_fallback_once(slot, app)

    if skip_ads or not slot["ads_msgs"]:
        log("BUILD", "Không xen ads — forward content only")
        sequence = [(content_chat, mid) for mid in contents]
        slot["final_sequence"]   = sequence
        slot["awaiting_channel"] = True
        slot["waiting"]          = False
        if slot.get("_build_only"):
            slot["awaiting_channel"] = False
            return
        auto_chs = slot.get("_auto_forward_channels")
        if auto_chs:
            await _start_forward(slot, auto_chs, slot.get("_auto_forward_cmd", ""))
            return
        channels            = load_channels()
        chan_lines, cmd_map = build_channel_commands(channels)
        slot["channel_commands"] = cmd_map
        await safe_send(
            f"⚠️ Không có ads — forward {media_str} không xen ads.\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📡 Tap lệnh để forward:\n{chan_lines}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"/skip — bỏ qua"
        )
        return

    ads_queue = list(slot["ads_msgs"][slot["ads_index"]:])
    sequence  = []
    ads_used  = 0

    def take_ads(count):
        nonlocal ads_used
        taken = []
        for _ in range(count):
            if not ads_queue:
                break
            aid = ads_queue.pop(0)
            slot["ads_index"] += 1
            taken.append((ads_chat, aid))
            ads_used += 1
        return taken

    if mode == "normal":
        total_ads = len(ads_queue)
        if total_ads == 0 or n <= 1:
            for mid in contents:
                sequence.append((content_chat, mid))
        else:
            groups      = total_ads + 1
            base, extra = divmod(n, groups)
            sizes       = [base + (1 if g < extra else 0) for g in range(groups)]
            idx         = 0
            for g, size in enumerate(sizes):
                for _ in range(size):
                    sequence.append((content_chat, contents[idx]))
                    idx += 1
                if g < groups - 1:
                    sequence.extend(take_ads(1))

    elif mode == "xdone":
        slots_between = n - 1
        if slots_between <= 0:
            for mid in contents:
                sequence.append((content_chat, mid))
        else:
            total       = len(ads_queue)
            base, extra = divmod(total, slots_between)
            for i, mid in enumerate(contents):
                sequence.append((content_chat, mid))
                if i < n - 1:
                    sequence.extend(take_ads(base + (1 if i < extra else 0)))

    elif mode == "zdone":
        total       = len(ads_queue)
        base, extra = divmod(total, n)
        for i, mid in enumerate(contents):
            sequence.extend(take_ads(base + (1 if i < extra else 0)))
            sequence.append((content_chat, mid))

    log("BUILD", f"Xếp xong len(seq)={len(sequence)} ads_used={ads_used}")

    if not sequence:
        await safe_send("⚠️ Sequence rỗng.")
        return

    slot["final_sequence"]   = sequence
    slot["awaiting_channel"] = True
    slot["waiting"]          = False

    if slot.get("_build_only"):
        slot["awaiting_channel"] = False
        return

    mapped_cmds = find_cmds_for_topic_title(slot.get("topic_title"), src_id=src_id)

    if len(mapped_cmds) >= 2:
        picked_cmd = pick_next_rr(slot.get("topic_title", ""), mapped_cmds)
        rr_state   = load_topic_rr()
        rr_key     = (slot.get("topic_title") or "").strip().lower()
        rr_idx     = rr_state.get(rr_key, 0)
        rr_display = f"{rr_idx + 1}/{len(mapped_cmds)}"

        results = resolve_channels_by_cmd(picked_cmd)
        if results:
            names = ", ".join(ch["title"] for ch in results)
            await safe_send(
                f"🔄 Topic '{slot.get('topic_title')}' (lượt {rr_display}) → /{picked_cmd}\n"
                f"✅ Xếp {media_str} + {ads_used} ads → tự forward tới {len(results)} kênh: {names}"
            )
            await _start_forward(slot, results, f"/{picked_cmd}")
            return
        for fallback_cmd in mapped_cmds:
            if fallback_cmd == picked_cmd:
                continue
            results = resolve_channels_by_cmd(fallback_cmd)
            if results:
                names = ", ".join(ch["title"] for ch in results)
                await safe_send(
                    f"⚠️ /{picked_cmd} không có kênh → fallback /{fallback_cmd}\n"
                    f"✅ Xếp {media_str} + {ads_used} ads → tự forward tới {len(results)} kênh: {names}"
                )
                await _start_forward(slot, results, f"/{fallback_cmd}")
                return
        cmd_map = {c: resolve_channels_by_cmd(c) for c in mapped_cmds if resolve_channels_by_cmd(c)}
        slot["channel_commands"] = cmd_map
        opts = "\n\n".join(f"/{c}" for c in mapped_cmds)
        await safe_send(
            f"⚠️ Topic '{slot.get('topic_title')}' — tất cả kênh đều không resolve được.\n"
            f"✅ Đã xếp {media_str} + {ads_used} ads. Vui lòng chọn tay:\n\n{opts}\n\n/skip — bỏ qua"
        )
        return

    if len(mapped_cmds) == 1:
        mapped_cmd = mapped_cmds[0]
        results    = resolve_channels_by_cmd(mapped_cmd)
        if results:
            names = ", ".join(ch["title"] for ch in results)
            await safe_send(
                f"🎯 Topic '{slot.get('topic_title')}' → /{mapped_cmd}\n"
                f"✅ Xếp {media_str} + {ads_used} ads → tự forward tới {len(results)} kênh: {names}"
            )
            await _start_forward(slot, results, f"/{mapped_cmd}")
            return
        await safe_send(
            f"⚠️ Topic '{slot.get('topic_title')}' map tới /{mapped_cmd} "
            f"nhưng không có kênh nào khớp (xem /list). Chọn tay:"
        )

    channels            = load_channels()
    chan_lines, cmd_map = build_channel_commands(channels)
    slot["channel_commands"] = cmd_map

    auto_chs = slot.get("_auto_forward_channels")
    if auto_chs:
        await _start_forward(slot, auto_chs, slot.get("_auto_forward_cmd", ""))
        return

    hint = ""
    if slot.get("topic_title") and not mapped_cmds:
        hint = (f"\n━━━━━━━━━━━━━━━\n"
                f"ℹ️ Topic: '{slot.get('topic_title')}' — chưa map.\n"
                f"Mở {TOPIC_MAP_TXT}, thêm: {slot.get('topic_title')} = <tenkenh>")

    await safe_send(
        f"✅ Xếp xong: {media_str} + {ads_used} ads\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📡 Tap lệnh để forward:\n{chan_lines}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"/skip — bỏ qua"
        f"{hint}"
    )


# ─────────────────────────────────────────────────────────
# /all mode
# ─────────────────────────────────────────────────────────

async def do_all_forward(slot):
    from core.all_config import load_destination_channels

    seq = [(SAVED_MESSAGES, mid) for mid in slot["content_msgs"]]
    slot["all_mode"] = False
    if not seq:
        return
    channels = load_destination_channels()
    if not channels:
        await safe_send("⚠️ /all: chưa chọn kênh đích trên web (tab /all hoặc Up bài).")
        reset_slot(slot)
        return
    slot["final_sequence"]   = seq
    slot["awaiting_channel"] = True
    slot["waiting"]          = False
    media_cnt = slot.get("total_media_count", 0)
    media_str = f"{len(seq)} bài / {media_cnt} media" if media_cnt else f"{len(seq)} bài"
    await safe_send(f"📦 /all: up {media_str} → {len(channels)} kênh đích (không ads, ẩn tên).")
    await _start_forward(slot, channels, "/all")


# ─────────────────────────────────────────────────────────
# Forward job
#
# [FIX-A] mỗi kênh nhận copy(sequence) riêng → seen_groups không bị share
# [FIX-C] track ok/dead/error theo channel id, không theo title string
# [FIX-4] reset_slot trong finally
# ─────────────────────────────────────────────────────────

async def do_forward_job(slot, results):
    sequence = slot["final_sequence"]
    serial = bool(slot.get("_auto_forward_wait"))
    cache = ForwardFetchCache()

    # Tracking theo id để tránh nhầm kênh trùng tên [FIX-C]
    ok_ids   = set()
    err_ids  = set()
    dead_ids = set()

    dead_killed = []
    fail_recap  = []
    sem         = asyncio.Semaphore(1 if serial else FWD_MAX_CONCURRENT_CHANNELS)

    async def _fwd_one(ch, idx: int = 0, total: int = 0):
        ch_id    = ch["id"]
        ch_label = f"{ch['title']} (id={ch_id})"
        if serial and total:
            log("FWD", f"Auto {idx}/{total} → {ch.get('title', ch_id)}")

        async with sem:
            try:
                seq_copy = copy.copy(sequence)
                sent, failed_items, dead_reason = await forward_sequence_to_channel(
                    ch_id, seq_copy, fetch_cache=cache,
                )

                if dead_reason:
                    dead_ids.add(ch_id)
                    removed = await remove_dead_channel(ch_id)
                    if removed:
                        dead_killed.append(removed)
                    if failed_items:
                        record_failed(ch_id, ch["title"], failed_items)
                        fail_recap.append((ch["title"], len(failed_items)))
                    log("DEAD", f"💀 {ch_label} — CHẾT, lưu {len(failed_items)} bài | {dead_reason}")
                else:
                    ok_ids.add(ch_id)
                    log("FWD", f"✓ {ch_label} — OK {sent} msg, fail={len(failed_items)}")
                    if failed_items:
                        record_failed(ch_id, ch["title"], failed_items)
                        fail_recap.append((ch["title"], len(failed_items)))

                await asyncio.sleep(FWD_BETWEEN_CHANNELS_SEC + random.uniform(0, 1.0))

            except Exception as e:
                err_ids.add(ch_id)
                log("ERROR", f"forward to {ch_label}: {e}\n{traceback.format_exc()}")

    async with fwd_lock:
        if serial:
            total = len(results)
            for i, ch in enumerate(results, 1):
                await _fwd_one(ch, i, total)
        else:
            await asyncio.gather(*[_fwd_one(ch) for ch in results])

    # Summary
    lines = [f"✅ Xong! Forward tới {len(results)} kênh"]
    if ok_ids:
        ok_names = [f"{ch['title']}(id={ch['id']})" for ch in results if ch["id"] in ok_ids]
        lines.append(f"  ✓ OK: {', '.join(ok_names)}")
    if dead_killed:
        lines.append(f"  💀 Kênh chết (đã xóa): {', '.join(dead_killed)}")
    if err_ids:
        err_names = [ch["title"] for ch in results if ch["id"] in err_ids]
        lines.append(f"  ❌ Lỗi: {', '.join(err_names)}")
    if fail_recap:
        detail = ", ".join(f"{t}({n} bài)" for t, n in fail_recap)
        lines.append(f"  ⚠️ Bài lưu retry: {detail}")

    # [FIX-4] finally đảm bảo slot luôn được dọn dù safe_send có lỗi
    try:
        await safe_send("\n".join(lines))
    finally:
        reset_slot(slot)
        if slot in state["slots"]:
            state["slots"].remove(slot)
        if not state["slots"]:
            new_s = make_slot()
            state["slots"].append(new_s)
            asyncio.ensure_future(load_ads_into(new_s))
        if not any(s["awaiting_channel"] for s in state["slots"]):
            await safe_send("✨ Sẵn sàng! Forward bài mới vào Saved Messages.")


async def _start_forward(slot, results, query_display: str = ""):
    if not results:
        await safe_send(f"❌ Không tìm thấy kênh khớp '{query_display}'.\nDùng /list để xem hoặc gõ lại.")
        return
    if not slot or not slot["final_sequence"]:
        await safe_send("⚠️ Không có batch nào đang chờ forward.")
        return
    names = ", ".join(ch["title"] for ch in results)
    if len(names) > 300:
        names = names[:300] + f"… (+{len(results)} kênh)"
    content_n   = len(slot["content_msgs"])
    content_med = slot.get("total_media_count", 0)
    media_str   = f"{content_n} bài / {content_med} media" if content_med else f"{content_n} bài"
    slot["awaiting_channel"] = False
    if slot.get("_auto_forward_wait"):
        await safe_send(
            f"📡 Forward → {len(results)} kênh: {names}\n"
            f"📦 {media_str} — {len(slot['final_sequence'])} seq items\n"
            f"▶️ Đang up kênh…"
        )
        await do_forward_job(slot, results)
        return
    await safe_send(
        f"📡 Forward → {len(results)} kênh: {names}\n"
        f"📦 {media_str} — {len(slot['final_sequence'])} seq items\n"
        f"▶️ Chạy nền — bạn có thể forward bài mới ngay!"
    )
    if not slot.get("_batch_processing"):
        new_s = make_slot()
        state["slots"].append(new_s)
        asyncio.ensure_future(load_ads_into(new_s))
    asyncio.ensure_future(do_forward_job(slot, results))

async def cmd_select_channel(query: str):
    slot    = waiting_slot()
    results = find_channels(query)
    await _start_forward(slot, results, query)

async def cmd_select_by_cmd(slot, cmd_key: str):
    cmd_map = slot.get("channel_commands") or {}
    results = cmd_map.get(cmd_key, [])
    await _start_forward(slot, results, f"/{cmd_key}")


# ─────────────────────────────────────────────────────────
# Folder sync
# ─────────────────────────────────────────────────────────

async def _fetch_folder_chats(slug):
    from pyrogram.raw import functions as raw_fn
    result = await app.invoke(raw_fn.chatlists.CheckChatlistInvite(slug=slug))
    return getattr(result, "title", slug) or slug, getattr(result, "chats", [])

async def cmd_addfolder(link: str, silent: bool = False, remember: bool = True, branch: str = BRANCH_ADS):
    import re as _re
    match = _re.search(r"addlist/([A-Za-z0-9_+=-]+)", link.strip())
    if not match:
        if not silent:
            await safe_send("❌ Link folder không hợp lệ.\nĐịnh dạng: https://t.me/addlist/xxxxx")
        return 0
    slug = match.group(1)
    if not silent:
        await safe_send("⏳ Đang đọc folder...")
    try:
        folder_title, chats = await _fetch_folder_chats(slug)
    except Exception as e:
        if not silent:
            await safe_send(f"❌ Không đọc được folder: {e}")
        log("ERROR", f"addfolder({slug}): {e}")
        return 0
    if not chats:
        if not silent:
            await safe_send("⚠️ Folder trống.")
        if remember:
            remember_folder(slug, folder_title, branch)
        return 0
    channels       = load_channels(branch)
    added, skipped = [], []
    for chat in chats:
        title    = getattr(chat, "title", "") or ""
        username = getattr(chat, "username", "") or ""
        raw_id   = getattr(chat, "id", None)
        if not raw_id or not title:
            continue
        tg_id = int(f"-100{raw_id}") if raw_id > 0 else raw_id
        if any(str(ch["id"]) == str(tg_id) for ch in channels):
            skipped.append(title)
            continue
        channels.append({"id": tg_id, "title": title, "username": username, "alias": ""})
        added.append(f"✅ #{len(channels)}. {title}" + (f" (@{username})" if username else ""))
    save_channels(channels, branch)
    if remember:
        remember_folder(slug, folder_title, branch)
    if not silent:
        out = [f"📁 Folder: {folder_title}"]
        if added:
            out.append(f"✅ Thêm {len(added)} kênh:")
            out.extend(added)
        if skipped:
            out.append(f"⚠️ Bỏ qua {len(skipped)} (đã có): {', '.join(skipped)}")
        if added:
            out.append("💡 /alias <số> <tên> để đặt tên tắt")
        out.append("🔄 Folder đã lưu — kênh mới sẽ tự sync mỗi 1h")
        await safe_send("\n".join(out))
    else:
        if added:
            log("FOLDER-SYNC", f"+{len(added)} kênh mới từ folder '{folder_title}'")
    return len(added)


# ─────────────────────────────────────────────────────────
# /add command
# ─────────────────────────────────────────────────────────

async def cmd_addchan(raw: str):
    lines_raw    = [l.strip() for l in raw.splitlines() if l.strip()]
    if not lines_raw:
        await safe_send("❌ Không có link/id nào.")
        return
    folder_links = [l for l in lines_raw if "addlist" in l]
    identifiers  = [l for l in lines_raw if "addlist" not in l]
    for fl in folder_links:
        await cmd_addfolder(fl)
    if not identifiers:
        return
    channels               = load_channels()
    added, skipped, failed = [], [], []
    await safe_send(f"⏳ Đang xử lý {len(identifiers)} kênh...")
    for ident in identifiers:
        try:
            chat = await app.get_chat(ident)
            if any(str(ch["id"]) == str(chat.id) for ch in channels):
                skipped.append(f"⚠️ {chat.title} (đã có)")
                continue
            channels.append({"id": chat.id, "title": chat.title or "", "username": chat.username or "", "alias": ""})
            added.append(f"✅ #{len(channels)}. {chat.title}  (@{chat.username or 'private'})")
        except Exception as e:
            failed.append(f"❌ {ident}  → {e}")
        await asyncio.sleep(0.3)
    save_channels(channels)
    out = []
    if added:
        out.append(f"✅ Đã thêm {len(added)} kênh:")
        out.extend(added)
    if skipped:
        out.append(f"⚠️ Bỏ qua: {len(skipped)}")
        out.extend(skipped)
    if failed:
        out.append(f"❌ Thất bại {len(failed)}:")
        out.extend(failed)
    if added:
        out.append("💡 /alias <số> <tên> để đặt tên tắt")
    await safe_send("\n".join(out))


# ─────────────────────────────────────────────────────────
# /check & /clean
# ─────────────────────────────────────────────────────────

async def _probe_channel(ch):
    last_err = None
    for attempt in range(3):
        try:
            chat = await app.get_chat(ch["id"])
            return ("alive", chat)
        except FloodWait as e:
            wait = e.value + 2
            log("CHECK", f"FloodWait {wait}s khi check '{ch.get('title','?')}' — retry {attempt+1}/3")
            await asyncio.sleep(wait)
            last_err = e
        except DEAD_CHANNEL_ERRORS as e:
            return ("dead", f"{type(e).__name__}: {str(e)[:80]}")
        except SKIP_NOT_DEAD_ERRORS as e:
            return ("unknown", f"{type(e).__name__}: {str(e)[:80]}")
        except Exception as e:
            return ("unknown", f"{type(e).__name__}: {str(e)[:80]}")
    return ("unknown", f"FloodWait persistent: {last_err}")

async def cmd_checkchan(auto_clean: bool = False, silent: bool = False, branch: str = BRANCH_ADS):
    channels = load_channels(branch)
    if not channels:
        if not silent:
            await safe_send("📭 Chưa có kênh nào để check.")
        return 0
    total      = len(channels)
    status_msg = None
    if not silent:
        status_msg = await robust_send(f"🔍 Đang check {total} kênh... (0/{total})")
    alive, dead, unknown = [], [], []
    for i, ch in enumerate(channels):
        status, payload = await _probe_channel(ch)
        if status == "alive":
            chat = payload
            if chat.title:
                ch["title"] = chat.title
            ch["username"] = chat.username or ""
            alive.append(ch)
            log("CHECK", f"✓ {ch['title']}")
        elif status == "dead":
            dead.append({"ch": ch, "err": payload})
            log("CHECK", f"✗ DEAD {ch.get('title','?')} → {payload}")
        else:
            unknown.append({"ch": ch, "err": payload})
            log("CHECK", f"? UNKNOWN {ch.get('title','?')} → {payload} — GIỮ LẠI")
        if not silent and status_msg and ((i + 1) % 5 == 0 or i == total - 1):
            await robust_edit(
                get_intermediate_chat(), status_msg.id,
                f"🔍 Đang check... ({i+1}/{total})\n"
                f"✅ {len(alive)}   ❌ {len(dead)}   ❓ {len(unknown)}"
            )
        await asyncio.sleep(0.5)
    keep = alive + [item["ch"] for item in unknown]
    if auto_clean:
        save_channels(keep, branch)
    else:
        save_channels(keep + [item["ch"] for item in dead], branch)
    if silent:
        return len(dead)
    lines = [
        f"📊 Kết quả check {total} kênh:",
        "━━━━━━━━━━━━━━━",
        f"✅ Hoạt động:   {len(alive)}",
        f"❌ Chết:        {len(dead)}",
        f"❓ Không rõ:    {len(unknown)}  (FloodWait/network — giữ lại)",
    ]
    if dead:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("🪦 Kênh chết (sẽ xóa nếu /clean):")
        for item in dead:
            ch   = item["ch"]
            name = ch.get("title") or str(ch.get("id"))
            lines.append(f"  • {name}")
            lines.append(f"     └ {item['err']}")
    if unknown:
        lines.append("━━━━━━━━━━━━━━━")
        lines.append("❓ Không xác định (KHÔNG xóa — thử /check lại sau):")
        for item in unknown[:10]:
            ch   = item["ch"]
            name = ch.get("title") or str(ch.get("id"))
            lines.append(f"  • {name}  ({item['err'].split(':')[0]})")
        if len(unknown) > 10:
            lines.append(f"  ... và {len(unknown)-10} kênh khác")
    if auto_clean and dead:
        lines += ["━━━━━━━━━━━━━━━", f"🗑️ Đã xóa {len(dead)} kênh chết."]
    elif dead:
        lines += ["━━━━━━━━━━━━━━━", "💡 /clean — xóa các kênh chết ra khỏi danh sách"]
    elif not unknown:
        lines += ["━━━━━━━━━━━━━━━", "🎉 Tất cả kênh đều hoạt động!"]
    final = "\n".join(lines)
    if status_msg:
        ok = await robust_edit(get_intermediate_chat(), status_msg.id, final)
        if not ok:
            await robust_send(final)
    else:
        await robust_send(final)
    return len(dead)


# ─────────────────────────────────────────────────────────
# Background tasks
# ─────────────────────────────────────────────────────────

async def task_auto_sync_folders():
    await asyncio.sleep(30)
    while True:
        from core.runtime import pipeline_busy

        if pipeline_busy():
            log("AUTO-SYNC", "Hoãn — pipeline auto đang chạy")
            await asyncio.sleep(120)
            continue
        for branch in (BRANCH_ADS, BRANCH_PLAIN):
            folders = load_folders(branch)
            if not folders:
                continue
            label = "Up bài" if branch == BRANCH_PLAIN else "ads"
            log("AUTO-SYNC", f"Bắt đầu sync {len(folders)} folder ({label})...")
            total_added = 0
            for fd in folders:
                slug = fd.get("slug")
                if not slug:
                    continue
                try:
                    added = await cmd_addfolder(
                        f"https://t.me/addlist/{slug}", silent=True, remember=False, branch=branch,
                    )
                    total_added += added or 0
                except Exception as e:
                    log("AUTO-SYNC", f"folder {slug} ({label}): {type(e).__name__}: {e}")
                await asyncio.sleep(2)
            if total_added > 0:
                await safe_send(f"🔄 Auto-sync ({label}): thêm {total_added} kênh mới từ folder đã lưu.")
            log("AUTO-SYNC", f"Hoàn tất ({label}) — {'thêm ' + str(total_added) if total_added else 'không có kênh mới'}")
        await asyncio.sleep(FOLDER_SYNC_INTERVAL_SEC)

async def task_auto_clean_dead():
    await asyncio.sleep(300)
    while True:
        await asyncio.sleep(DEAD_CHECK_INTERVAL_SEC)
        if state.get("checking"):
            log("AUTO-CLEAN", "Bỏ lượt — đang có check khác chạy")
            continue
        def _user_busy():
            return any(
                s.get("content_msgs") or s.get("awaiting_channel") or s.get("all_mode")
                for s in state["slots"]
            )
        waited = 0
        while _user_busy() and waited < 600:
            await asyncio.sleep(60)
            waited += 60
        if _user_busy():
            log("AUTO-CLEAN", "Bỏ lượt — user vẫn đang bận")
            continue
        state["checking"] = True
        try:
            dead_count = await cmd_checkchan(auto_clean=True, silent=True)
            if dead_count and dead_count > 0:
                await safe_send(f"🧹 Auto-clean: đã xóa {dead_count} kênh chết.")
                log("AUTO-CLEAN", f"Hoàn tất — xóa {dead_count} kênh chết")
            else:
                log("AUTO-CLEAN", "Hoàn tất — không có kênh chết")
        except Exception as e:
            log("AUTO-CLEAN", f"Lỗi: {type(e).__name__}: {e}")
        finally:
            state["checking"] = False


# ─────────────────────────────────────────────────────────
# Command aliases
# ─────────────────────────────────────────────────────────

COMMAND_ALIASES = {
    "/addchan":   "/add",
    "/addfolder": "/addf",
    "/listchan":  "/list",
    "/delchan":   "/del",
    "/aliaschan": "/alias",
    "/checkchan": "/check",
    "/cleanchan": "/clean",
}

def normalize_command(text: str):
    if not text.startswith("/"):
        return text
    for old, new in COMMAND_ALIASES.items():
        if text == old:
            return new
        if text.startswith(old + " ") or text.startswith(old + "\n"):
            return new + text[len(old):]
    return text


# ─────────────────────────────────────────────────────────
# Message handler
# ─────────────────────────────────────────────────────────

async def handler(client, msg: Message):

    # ══ 1. Bài forward vào Saved Messages ══════════════════════════
    if msg.chat.id == state["my_id"]:
        slot = active_slot()

        if not msg.forward_date:
            return

        if not slot["waiting"]:
            if slot.get("awaiting_channel"):
                await safe_send(
                    "⏳ Batch đang chờ chọn kênh.\n"
                    "/skip — bỏ qua  |  chọn kênh hoặc gõ tên"
                )
                return
            if slot.get("_batch_processing"):
                log("SLOT", "Forward mới khi batch đang xếp — tạo slot mới")
                new_s = make_slot()
                state["slots"].append(new_s)
                asyncio.ensure_future(load_ads_into(new_s))
                slot = new_s
            else:
                return

        # Phát hiện ads forward nhầm vào Saved
        if msg.forward_from_chat and msg.forward_from_chat.id == get_ads_chat():
            slot["ads_chat_id"] = get_ads_chat()
            await load_ads_into(slot)
            return

        is_first = not slot.get("_topic_detect_started") and not slot.get("all_mode")
        if is_first:
            slot["_topic_detect_started"] = True
            slot["_topic_event"] = asyncio.Event()

            async def _detect(saved_msg_id, ev, target_slot):
                try:
                    async with _topic_resolve_lock:
                        for attempt in range(FWD_MAX_RETRY):
                            try:
                                while True:
                                    remain = _flood_until - time.monotonic()
                                    if remain <= 0:
                                        break
                                    await asyncio.sleep(remain)
                                src_id, top_id, top_title = await resolve_forward_topic(client, saved_msg_id)
                                if top_id is not None and _is_valid_topic_title(top_title):
                                    _set_slot_topic(target_slot, src_id, top_id, top_title, source="Batch")
                                    break
                                if attempt < FWD_MAX_RETRY - 1:
                                    await asyncio.sleep(2 * (attempt + 1))
                            except FloodWait as e:
                                wait = e.value + 2
                                log("FLOOD", f"_detect FloodWait {wait}s — retry {attempt+1}/{FWD_MAX_RETRY}")
                                await flood_wait_globally(wait, source="detect")
                                continue
                            except Exception as e:
                                if attempt < FWD_MAX_RETRY - 1:
                                    log("WARN", f"_detect attempt {attempt+1}: {type(e).__name__}: {e}")
                                    await asyncio.sleep(2 * (attempt + 1))
                                    continue
                                log("TOPIC", f"Không detect được topic: {e}")
                                break
                        else:
                            log("TOPIC", "Không detect được topic sau hết retry")
                finally:
                    target_slot["topic_checked"] = True
                    ev.set()

            asyncio.ensure_future(_detect(msg.id, slot["_topic_event"], slot))
        elif "_topic_event" not in slot:
            slot["_topic_event"] = asyncio.Event()
            slot["_topic_event"].set()

        # Album handling
        if msg.media_group_id:
            gid = msg.media_group_id
            if gid in slot["seen_media_groups"]:
                return
            slot["seen_media_groups"].add(gid)
            slot["_album_pending"] = slot.get("_album_pending", 0) + 1
            album = None
            try:
                for attempt, delay in enumerate([0.5, 1.0, 2.0]):
                    await asyncio.sleep(delay)
                    try:
                        album = await client.get_media_group(SAVED_MESSAGES, msg.id)
                        if album and len(album) >= 1:
                            break
                        album = None
                    except FloodWait as e:
                        wait = e.value + 2
                        log("FLOOD", f"get_media_group FloodWait {wait}s")
                        await flood_wait_globally(wait, source="album")
                    except Exception as e:
                        log("WARN", f"get_media_group attempt {attempt+1}: {e}")
                if not album:
                    log("ERROR", f"Bỏ album {gid} — không fetch được sau 3 lần")
                    return
                slot["content_msgs"].append(album[0].id)
                slot["total_media_count"] = slot.get("total_media_count", 0) + len(album)
                log("MSG", f"Album {gid} ({len(album)} items) — "
                           f"bài #{len(slot['content_msgs'])} | "
                           f"total_media={slot['total_media_count']}")
            except Exception as e:
                log("ERROR", f"Album {gid} exception: {e}")
                return
            finally:
                slot["_album_pending"] = max(0, slot.get("_album_pending", 0) - 1)
                if not album:
                    slot["seen_media_groups"].discard(gid)
        else:
            slot["content_msgs"].append(msg.id)
            has_media = bool(msg.media)
            if has_media:
                slot["total_media_count"] = slot.get("total_media_count", 0) + 1
            log("MSG", f"Bài #{len(slot['content_msgs'])} id={msg.id}"
                       f"{' (media)' if has_media else ' (text)'}"
                       f" | total_media={slot.get('total_media_count', 0)}")

        schedule_update_menu(len(slot["content_msgs"]))
        return

    # ══ 2. Lệnh trong intermediate chat hoặc nhóm thông báo ═══════════
    try:
        im_chat = get_intermediate_chat()
    except RuntimeError:
        return

    notify_id = None
    try:
        from core.settings import notify_chat_id as _notify_chat_id
        notify_id = _notify_chat_id()
    except Exception:
        pass

    in_im = msg.chat.id == im_chat
    in_notify = notify_id and msg.chat.id == notify_id
    if not in_im and not in_notify:
        return

    text_raw = (msg.text or msg.caption or "")
    text     = text_raw.strip()
    if not text:
        return

    text     = normalize_command(text)
    text_raw = normalize_command(text_raw)

    if in_notify:
        if text == "/upngay" or text.startswith("/upngay "):
            await cmd_upngay(text)
        return

    if text == "/upngay" or text.startswith("/upngay "):
        await cmd_upngay(text)
        return

    # /all
    if text == "/all":
        from core.all_config import load_destination_channels

        reset_slot(active_slot())
        active_slot()["all_mode"] = True
        chs = load_destination_channels()
        n_ch = len(chs)
        if not n_ch:
            await safe_send(
                "📦 Chế độ /all ĐÃ BẬT (dùng 1 lần).\n"
                "⚠️ Chưa chọn kênh đích trên web — tab <b>/all</b> hoặc <b>Up bài</b>.\n"
                "➡️ Forward bài vào Saved Messages → tool tự up.\n"
                "Gõ /next để huỷ nếu đổi ý."
            )
        else:
            await safe_send(
                f"📦 Chế độ /all ĐÃ BẬT (dùng 1 lần).\n"
                f"➡️ Forward bài vào Saved Messages → tool tự up lên {n_ch} kênh đích (web).\n"
                f"Gõ /next để huỷ nếu đổi ý."
            )
        return

    # /xepbaiwhite (kiểm trước /xepbai)
    if text == "/xepbaiwhite" or text.startswith("/xepbaiwhite "):
        arg = text[len("/xepbaiwhite"):].strip()
        if not arg:
            wl = get_xepbai_whitelist()
            await safe_send(
                "⭐ Whitelist (luôn hiện nút /done dù đang OFF):\n"
                + (", ".join(sorted(wl)) if wl else "  (trống)")
                + "\n━━━━━━━━━━━━━━━\n"
                  "Dùng: /xepbaiwhite pro,real   (hoặc /xepbaiwhite clear để xoá)"
            )
            return
        if arg.lower() == "clear":
            set_topic_directive("@xepbaiwhite", "")
            await safe_send("⭐ Đã xoá whitelist.")
            return
        items = [x.strip().lstrip("/").lower()
                 for x in arg.replace(" ", ",").split(",") if x.strip()]
        set_topic_directive("@xepbaiwhite", ", ".join(items))
        await safe_send("⭐ Whitelist: " + (", ".join(items) if items else "(trống)")
                        + "\n→ Các nhóm này luôn hiện nút /done kể cả khi /xepbai off.")
        return

    if text == "/xepbai" or text.startswith("/xepbai "):
        arg = text[len("/xepbai"):].strip().lower()
        if arg in ("on", "off"):
            set_topic_directive("@xepbai", arg)
            if arg == "on":
                await safe_send("🟢 /xepbai ON — forward xong sẽ hiện nút /done để tự chọn.")
            else:
                await safe_send(
                    "🔴 /xepbai OFF — forward xong tool TỰ xếp theo gợi ý.\n"
                    "(Các nhóm trong /xepbaiwhite vẫn hiện nút bình thường.)"
                )
            return
        mode = get_xepbai_mode()
        wl   = get_xepbai_whitelist()
        await safe_send(
            f"⚙️ /xepbai đang: {mode.upper()}\n"
            f"⭐ Whitelist: {', '.join(sorted(wl)) if wl else '(trống)'}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"Dùng: /xepbai on  |  /xepbai off"
        )
        return

    # /mapgen, /map
    if text == "/mapgen" or text == "/map gen":
        n_kept, n_new = gen_topic_map_txt()
        groups        = all_channel_cmds()
        lines         = [
            f"🧩 Đã ghi {TOPIC_MAP_TXT}: giữ {n_kept} dòng đã map, thêm {n_new} kênh chưa map.",
            "Mở file đó, điền tên topic vào TRƯỚC dấu =",
            "━━━━━━━━━━━━━━━",
            f"📡 Các nhóm kênh ({len(groups)}):",
        ]
        for cmd, chs in groups:
            titles = ", ".join((c.get("title") or "") for c in chs)
            lines.append(f"  /{cmd}  →  {titles}")
        await safe_send("\n".join(lines))
        return

    if text == "/map" or text.startswith("/map "):
        entries = load_topic_txt()
        if not entries:
            await safe_send(
                f"📭 Chưa map topic nào.\n"
                f"➡️ Gõ /mapgen để tool in sẵn danh sách kênh vào {TOPIC_MAP_TXT}.\n"
                f"Mỗi dòng:  tên_topic = tên_kênh"
            )
        else:
            lines = [f"🗺️ Mapping topic → kênh ({TOPIC_MAP_TXT}):"]
            for topic, cmd in entries:
                lines.append(f"  • {topic} → /{cmd}")
            lines += ["━━━━━━━━━━━━━━━", f"Sửa/thêm/xoá: mở file {TOPIC_MAP_TXT}. /mapgen để bổ sung kênh mới."]
            await safe_send("\n".join(lines))
        return

    # /add
    if text.startswith("/add ") or text.startswith("/add\n") or text == "/add":
        raw = text_raw[4:].strip()
        if not raw:
            await safe_send("❌ Dùng:\n/add @kenh1\n@kenh2\nhttps://t.me/+xxx\n-100123456")
            return
        await cmd_addchan(raw)
        return

    # /addf
    if text.startswith("/addf"):
        lnk = text[5:].strip()
        if not lnk:
            await safe_send("❌ Dùng: /addf https://t.me/addlist/xxxxx")
            return
        await cmd_addfolder(lnk)
        return

    # /list
    if text == "/list":
        channels = load_channels()
        if not channels:
            await safe_send("📭 Chưa có kênh nào.\n/add <link hoặc id> để thêm.")
            return
        lines = ["📋 Danh sách kênh:"]
        for i, ch in enumerate(channels):
            alias = f"  [{ch['alias']}]" if ch.get("alias") else ""
            lines.append(
                f"{i+1}. {ch['title']}{alias}\n"
                f"   @{ch.get('username') or 'private'}  |  ID: {ch['id']}"
            )
        folders = load_folders()
        if folders:
            lines.append("━━━━━━━━━━━━━━━")
            lines.append(f"📁 Folder auto-sync ({len(folders)}):")
            for fd in folders:
                lines.append(f"  • {fd.get('title') or fd.get('slug')}")
        await safe_send("\n".join(lines))
        return

    # /del
    if text.startswith("/del "):
        try:
            idx      = int(text[5:].strip()) - 1
            channels = load_channels()
            if 0 <= idx < len(channels):
                removed = channels.pop(idx)
                save_channels(channels)
                await safe_send(f"🗑️ Đã xóa: {removed['title']}")
            else:
                await safe_send("❌ Số thứ tự không hợp lệ.")
        except ValueError:
            await safe_send("❌ Dùng: /del <số thứ tự>")
        return

    # /alias
    if text.startswith("/alias "):
        parts = text[7:].split(None, 1)
        if len(parts) == 2:
            try:
                idx      = int(parts[0]) - 1
                alias    = parts[1].strip()
                channels = load_channels()
                if 0 <= idx < len(channels):
                    channels[idx]["alias"] = alias
                    save_channels(channels)
                    await safe_send(f"✏️ Alias '{alias}' → {channels[idx]['title']}")
                else:
                    await safe_send("❌ Số thứ tự không hợp lệ.")
            except ValueError:
                await safe_send("❌ Dùng: /alias <số> <tên tắt>")
        return

    # /check, /clean
    if text == "/check" or text == "/clean":
        if state.get("checking"):
            await safe_send("⏳ Đang check rồi, đợi lượt này xong đã nhé.")
            return
        auto_clean = (text == "/clean")
        async def _bg_check():
            state["checking"] = True
            try:
                await cmd_checkchan(auto_clean=auto_clean)
            except Exception as e:
                log("ERROR", f"check nền lỗi: {type(e).__name__}: {e}")
                await safe_send(f"❌ Check lỗi: {type(e).__name__}")
            finally:
                state["checking"] = False
        asyncio.ensure_future(_bg_check())
        await safe_send("🔍 Bắt đầu check ở nền — bạn vẫn forward bài bình thường.")
        return

    # Chọn kênh khi đang đợi
    if waiting_slot():
        if text == "/skip":
            ws = waiting_slot()
            if ws and ws in state["slots"]:
                state["slots"].remove(ws)
            if not state["slots"]:
                new_s = make_slot()
                state["slots"].append(new_s)
                await load_ads_into(new_s)
            await safe_send("⏭️ Đã bỏ qua. Sẵn sàng nhận bài mới!")
            return
        if text.startswith("/") and len(text) > 1 and " " not in text and "\n" not in text:
            ws        = waiting_slot()
            candidate = text[1:].lower()
            if ws and candidate in (ws.get("channel_commands") or {}):
                await cmd_select_by_cmd(ws, candidate)
                return
        if text and not text.startswith("/"):
            await cmd_select_channel(text)
            return

    # /skip ngoài context
    if text == "/skip":
        await safe_send(
            "⏭️ /skip chỉ dùng khi đang đợi chọn kênh (sau /done).\n"
            "Muốn bỏ batch đang gom dở thì gõ /next."
        )
        return

    if text == "/next":
        reset_state()
        await load_ads()
        await safe_send("🔄 Reset xong! Forward bài mới vào Saved Messages.")
        return

    if text == "/xdone":
        if not active_slot()["content_msgs"]:
            await safe_send("⚠️ Chưa có bài nào.")
            return
        if not active_slot()["ads_msgs"]:
            await load_ads()
        await build_sequence(mode="xdone")
        return

    if text == "/zdone":
        if not active_slot()["content_msgs"]:
            await safe_send("⚠️ Chưa có bài nào.")
            return
        if not active_slot()["ads_msgs"]:
            await load_ads()
        await build_sequence(mode="zdone")
        return

    if text.startswith("/done"):
        num = text[5:]
        try:
            cpa = int(num) if num else 1
        except ValueError:
            cpa = 1
        if not active_slot()["content_msgs"]:
            await safe_send("⚠️ Chưa có bài nào.")
            return
        if not active_slot()["ads_msgs"]:
            await load_ads()
        await build_sequence(cpa)
        return

    if text == "/runtopic" or text.startswith("/runtopic "):
        parts = text.split()
        slot  = active_slot()
        if len(parts) >= 3:
            try:
                sid, tid = int(parts[1]), int(parts[2])
                title = parts[3] if len(parts) > 3 else ""
                await _try_auto_source_topic(sid, tid, title, manual=True)
            except ValueError:
                await safe_send("❌ Dùng: /runtopic <chat_id> <topic_id> [tên]")
        elif slot.get("topic_src_id") and slot.get("topic_id"):
            await _try_auto_source_topic(
                slot["topic_src_id"], slot["topic_id"], slot.get("topic_title") or "", manual=True
            )
        else:
            await safe_send("❌ Chưa có topic. Dùng: /runtopic <chat_id> <topic_id>")
        return

    if text == "/scan" or text.startswith("/scan "):
        parts = text.split()
        if len(parts) >= 3:
            await cmd_scan_inventory(int(parts[1]), int(parts[2]))
        else:
            await safe_send("❌ Dùng: /scan <chat_id> <topic_id>")
        return

    if text == "/allrun":
        ok = await run_all_task(
            app, notify=_notify, forward_sequence_fn=_forward_seq_all_channels,
            build_and_forward_all=_all_build_and_forward, force_run=True,
        )
        if not ok:
            await safe_send("⚠️ /all task chưa bật hoặc chưa chọn kênh trên web.")
        return

    if text == "/help":
        await safe_send(
            "📖 Hướng dẫn v27\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🚀 Auto nguồn topic:\n"
            "  • Ghim bài kế tiếp cần up trong topic nguồn\n"
            "  • Caption ghim: 30 media 2 ads (hoặc cấu hình web)\n"
            "  • /runtopic — lấy từ topic → xếp → forward → ghim bài kế\n"
            "  • /scan <chat> <topic> — xem kho media còn lại\n"
            "  • /allrun — chạy /all task (web chọn kênh)\n"
            "  • /upngay — up ngay khi bot báo đủ bài (nhóm thông báo)\n"
            "  • /upngay vitamin — up topic cụ thể\n"
            "  • Web: bot thông báo, lịch auto hằng ngày, real-time\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🚀 Flow Saved Messages (legacy):\n"
            "  1. Forward bài vào Saved Messages\n"
            "  2. /done* / /xdone / /zdone → tool xếp sequence\n"
            "  3. Gõ tên kênh → forward thẳng\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📡 Quản lý kênh:\n"
            "  /add <link/id>     thêm kênh\n"
            "  /addf <link>       thêm folder (tự sync mỗi 1h)\n"
            "  /list              xem danh sách\n"
            "  /del <số>          xóa kênh\n"
            "  /alias <số> <tên>  đặt tên tắt\n"
            "  /check             check kênh sống/chết\n"
            "  /clean             check + xóa kênh chết\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🗺️ Auto theo topic forum:\n"
            "  /mapgen            in sẵn danh sách kênh vào topic_map.txt\n"
            "  /map               xem mapping đang có\n"
            "  Scoped map: -1001234567890:vitamin = pro\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚙️ Xếp bài:\n"
            "  /xepbai on/off     hiện/ẩn nút /done\n"
            "  /xepbaiwhite a,b   nhóm kênh luôn hiện nút dù off\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📋 Mode xếp:\n"
            "  /done1 ~ /done10   N content / 1 ads\n"
            "  /xdone             ads xen đều giữa bài\n"
            "  /zdone             ads trước, content sau\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📢 Lệnh khác:\n"
            "  /all    up nguyên bài lên TẤT CẢ kênh\n"
            "  /next   reset batch hiện tại\n"
            "  /skip   bỏ qua batch đang chờ chọn kênh\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 Tự động nền:\n"
            "  • Sync folder mỗi 1h\n"
            "  • Check + xóa kênh chết mỗi 6h\n"
            "  • Anti-flood: retry 6 lần, flood gate + TokenBucket\n"
            "  • Batch lock: chờ album xong rồi mới auto-process\n"
            "✨ Emoji premium giữ nguyên (drop_author)!"
        )
        return


# ─────────────────────────────────────────────────────────
# v26 — Auto source topic + Web control
# ─────────────────────────────────────────────────────────

from core.config_store import get_topic_source, load_auto_config, upsert_topic_source
from core.settings import system_armed, set_system_armed
from core.auto_runner import run_topic_batch, run_all_task, preview_topic_batch
from core.bot_notify import send_bot_notify
from core.runtime import append_log, mark_run_done, mark_run_start
from core.scheduler import scheduler_loop
from core.stock_watcher import stock_poll_loop
from core.all_batch_preview import all_batch_preview_loop
from core.source_collector import collect_batch_from_topic
from core.inventory import get_all_inventory


async def _source_build_and_forward(slot_data, channels, cmd):
    from core.saved_staging import maybe_stage_slot_data
    from research_platform.config import layer_enabled, load_platform_config
    from research_platform.hooks import after_auto_forward

    plat = load_platform_config()
    skip_forward = bool(
        plat.get("enabled") and not layer_enabled("publish_channels", plat.get("bot") or {})
    )
    if skip_forward:
        await _notify("⏸ Platform: up kênh tắt — chỉ ghi index (nếu bật).")

    slot_data = await maybe_stage_slot_data(
        app, slot_data, acquire=_fwd_bucket_get().acquire,
    )
    slot = make_slot()
    slot["content_msgs"]        = list(slot_data["content_msgs"])
    slot["content_chat"]        = slot_data["content_chat"]
    slot["total_media_count"]   = slot_data.get("total_media_count", 0)
    slot["topic_title"]         = slot_data.get("topic_title")
    slot["topic_src_id"]        = slot_data.get("topic_src_id")
    slot["_batch_n"]            = len(slot["content_msgs"])
    slot["skip_ads"]            = not slot_data.get("use_ads", True)
    slot["_build_only"]         = True
    if slot_data.get("use_ads") and not slot["ads_msgs"]:
        await load_ads_into(slot)
    await build_sequence_for_slot(
        slot,
        content_per_ads=slot_data.get("default_cpa", 1),
        mode=slot_data.get("mode", "normal"),
    )
    seq = list(slot.get("final_sequence") or [])
    if not seq:
        return
    if not skip_forward:
        await _forward_seq_to_channels(channels, seq, label=cmd or "topic")

    collect = slot_data.get("_collect_result")
    next_pin = getattr(collect, "next_pin_msg_id", None) if collect else None
    if plat.get("enabled") and layer_enabled("archive_index", plat.get("bot") or {}):
        await after_auto_forward(
            src_chat_id=int(slot_data["topic_src_id"]),
            topic_id=int(slot_data["topic_id"]),
            branch=slot_data.get("_branch", "ads"),
            sequence=seq,
            atomic_posts=slot_data.get("_atomic_posts"),
            next_pin_msg_id=next_pin,
            notify=_notify,
        )
    reset_slot(slot)


async def _forward_seq_to_channels(channels, seq, *, label: str = "batch"):
    """Up tuần tự từng kênh, dùng chung cache fetch."""
    if not channels or not seq:
        return
    cache = ForwardFetchCache()
    total = len(channels)
    async with fwd_lock:
        for i, ch in enumerate(channels, 1):
            log("FWD", f"[{label}] kênh {i}/{total} → {ch.get('title', ch['id'])}")
            await forward_sequence_to_channel(ch["id"], list(seq), fetch_cache=cache)
            if i < total:
                await asyncio.sleep(FWD_BETWEEN_CHANNELS_SEC + random.uniform(0, 1.0))


async def _build_all_sequence_and_forward(slot_data, channels, *, use_ads: bool):
    from core.saved_staging import maybe_stage_slot_data

    if not channels:
        return
    slot_data = await maybe_stage_slot_data(
        app, slot_data, acquire=_fwd_bucket_get().acquire,
    )
    slot = make_slot()
    slot["content_msgs"]      = list(slot_data["content_msgs"])
    slot["content_chat"]      = slot_data["content_chat"]
    slot["total_media_count"] = slot_data.get("total_media_count", 0)
    slot["topic_title"]       = slot_data.get("topic_title")
    slot["skip_ads"]          = not use_ads
    slot["_batch_n"]          = len(slot["content_msgs"])
    if use_ads and not slot["ads_msgs"]:
        await load_ads_into(slot)
    await build_sequence_for_slot(
        slot,
        content_per_ads=slot_data.get("default_cpa", 1),
        mode=slot_data.get("mode", "normal"),
    )
    seq = slot.get("final_sequence") or []
    if not seq:
        await _notify("⚠️ /all: sequence rỗng sau xếp bài")
        return
    await _forward_seq_to_channels(channels, seq, label="/all")


async def _all_build_and_forward(slot_data, channels):
    """Xếp bài → forward: tab /all (có thể ads) + tab Up bài (không ads)."""
    from core.all_config import split_destination_channels
    from core.saved_staging import maybe_stage_slot_data

    slot_data = await maybe_stage_slot_data(
        app, slot_data, acquire=_fwd_bucket_get().acquire,
    )
    all_chs, plain_chs = split_destination_channels()
    if all_chs:
        await _build_all_sequence_and_forward(
            slot_data, all_chs, use_ads=bool(slot_data.get("use_ads", False)),
        )
    if plain_chs:
        plain_msgs = slot_data.get("plain_content_msgs")
        id_map = slot_data.get("_stage_id_map") or {}
        if plain_msgs is None:
            from core.all_config import resolve_plain_posts
            from core.source_collector import AtomicPost

            posts = slot_data.get("_atomic_posts") or [
                AtomicPost(msg_id=int(m), media_count=0)
                for m in (slot_data.get("content_msgs") or [])
            ]
            plain_msgs = [
                id_map.get(p.msg_id, p.msg_id)
                for p in resolve_plain_posts(posts)
            ]
        elif id_map:
            plain_msgs = [id_map.get(int(m), int(m)) for m in plain_msgs]
        plain_media = slot_data.get("plain_total_media_count")
        if plain_media is None and plain_msgs:
            plain_media = slot_data.get("total_media_count", 0)
        if plain_msgs:
            await _build_all_sequence_and_forward(
                {
                    **slot_data,
                    "use_ads": False,
                    "content_msgs": list(plain_msgs),
                    "total_media_count": plain_media or 0,
                },
                plain_chs,
                use_ads=False,
            )


async def _notify(text, parse_mode=None):
    level = "error" if "❌" in text else ("warn" if ("⚠️" in text or "HẾT" in text or "📉" in text) else "info")
    append_log(level, text.replace("<b>", "").replace("</b>", "").replace("<a href=", " ["))
    sent = await send_bot_notify(text, parse_mode=parse_mode)
    if not sent:
        try:
            await safe_send(text)
        except Exception:
            pass


async def _try_auto_source_topic(
    src_id, topic_id, topic_title, *, manual=False, force_run=False, branch=BRANCH_ADS,
):
    from core.topic_routing import make_routing

    cfg = load_auto_config()
    g = cfg.get("global", {})
    if not g.get("auto_run_enabled", True):
        return
    if not manual and not system_armed():
        return
    key_src = get_topic_source(src_id, topic_id, branch=branch)
    find_cmds_fn, resolve_fn = make_routing(branch)
    if not key_src and not find_cmds_fn(topic_title, src_id):
        return
    if key_src and not key_src.get("enabled", True):
        return

    async def _go():
        await run_topic_batch(
            app, src_id, topic_id, topic_title or "",
            notify=_notify,
            build_and_forward=_source_build_and_forward,
            find_cmds_for_topic=find_cmds_fn,
            resolve_channels_by_cmd=resolve_fn,
            pick_next_rr=pick_next_rr,
            force_run=force_run or manual,
            branch=branch,
        )

    if _auto_pipeline_lock.locked():
        await _go()
    else:
        async with _auto_pipeline_lock:
            await _go()


async def _stock_poll_run(kind, sid, tid, title, *, check_only=False, force_run=False, branch=BRANCH_ADS):
    """Kiểm tra kho — đủ bài thì offer /upngay, không up ngay."""
    from core.topic_routing import make_routing

    if kind == "all_task":
        return await run_all_task(
            app, notify=_notify, forward_sequence_fn=_forward_seq_all_channels,
            build_and_forward_all=_all_build_and_forward,
            force_run=force_run, check_only=check_only,
        )
    find_cmds_fn, resolve_fn = make_routing(branch)
    return await run_topic_batch(
        app, sid, tid, title or "",
        notify=_notify,
        build_and_forward=_source_build_and_forward,
        find_cmds_for_topic=find_cmds_fn,
        resolve_channels_by_cmd=resolve_fn,
        pick_next_rr=pick_next_rr,
        force_run=force_run, check_only=check_only,
        branch=branch,
    )


async def cmd_upngay(text: str):
    from core.config_store import topic_key
    from core.runtime import clear_pending_up, get_pending_up

    parts = text.split(maxsplit=1)
    arg = (parts[1] if len(parts) > 1 else "").strip().lower()
    pending = get_pending_up()
    if not pending:
        await _notify("Không có topic nào đang chờ /upngay")
        return

    to_run: list[tuple[str, dict]] = []
    for key, p in pending.items():
        title = (p.get("title") or "").lower()
        if arg and arg not in title and arg not in key.lower():
            continue
        to_run.append((key, p))

    if arg and not to_run:
        await _notify(f"Không thấy '{arg}' trong danh sách chờ /upngay")
        return
    if not to_run:
        to_run = list(pending.items())

    await _notify(f"▶ /upngay — chạy {len(to_run)} task...")
    for key, p in to_run:
        clear_pending_up(key)
        kind = p.get("kind", "topic")
        sid, tid = int(p["src_chat_id"]), int(p["topic_id"])
        title = p.get("title") or ""
        mark_run_start(f"/upngay {title}")
        try:
            if kind == "all_task":
                ok = await run_all_task(
                    app, notify=_notify, forward_sequence_fn=_forward_seq_all_channels,
                    build_and_forward_all=_all_build_and_forward, force_run=True,
                )
            else:
                from core.topic_routing import make_routing

                br = p.get("branch") or BRANCH_ADS
                find_cmds_fn, resolve_fn = make_routing(br)
                ok = await run_topic_batch(
                    app, sid, tid, title,
                    notify=_notify,
                    build_and_forward=_source_build_and_forward,
                    find_cmds_for_topic=find_cmds_fn,
                    resolve_channels_by_cmd=resolve_fn,
                    pick_next_rr=pick_next_rr,
                    force_run=True,
                    branch=br,
                )
            mark_run_done("ok" if ok else "skip")
        except Exception as e:
            mark_run_done("error")
            await _notify(f"❌ /upngay {title}: {type(e).__name__}")


async def _forward_seq_all_channels(ch_id, seq):
    await forward_sequence_to_channel(ch_id, list(seq))


async def _run_all_topics_only(*, force_run=False):
    cfg = load_auto_config()
    ran = 0
    for branch in (BRANCH_ADS, BRANCH_PLAIN):
        sk = "topic_sources" if branch == BRANCH_ADS else "plain_topic_sources"
        label = "Up bài" if branch == BRANCH_PLAIN else "Ads"
        for t in cfg.get(sk, {}).values():
            if not t.get("enabled", True):
                continue
            sid, tid = t.get("src_chat_id"), t.get("topic_id")
            title = t.get("topic_title") or ""
            if not sid:
                continue
            if tid is None:
                tid = 0
            mark_run_start(f"[{label}] Topic {title or tid}")
            await _try_auto_source_topic(sid, tid, title, manual=True, force_run=force_run, branch=branch)
            ran += 1
    return ran


async def _run_scheduled_cycle(*, manual=False):
    """Chạy tất cả topic nguồn + /all task theo lịch web."""
    from core.up_confirm import clear_all_pending_up

    async with _auto_pipeline_lock:
        cfg = load_auto_config()
        g = cfg.get("global", {})
        sch = g.get("schedule") or {}

        if not manual and not system_armed():
            await _notify("⏸️ Lịch auto: chưa bấm START trên web.")
            return

        if not g.get("auto_run_enabled", True):
            await _notify("⏸️ Lịch auto: auto_run đang tắt trên web.")
            return

        n_cancel = clear_all_pending_up(
            log_msg="⏰ Hủy chờ /upngay — chạy theo lịch chung"
        )
        if n_cancel:
            append_log("info", f"Đã hủy {n_cancel} task /upngay (im lặng trên bot)")

        mark_run_start("Lịch auto hằng ngày")
        await _notify("🕐 Bắt đầu lượt auto theo lịch")

        if sch.get("run_all_topics", True):
            await _run_all_topics_only(force_run=True)

        task = cfg.get("all_task", {})
        plain = cfg.get("plain_task") or {}
        if sch.get("run_all_task_after", True) and (task.get("enabled") or plain.get("enabled")):
            await _notify("📦 Tất cả topic đã up xong — bắt đầu /all task")
            mark_run_start("/all task")
            await run_all_task(
                app, notify=_notify, forward_sequence_fn=_forward_seq_all_channels,
                build_and_forward_all=_all_build_and_forward, force_run=True,
            )

        mark_run_done("ok")
        try:
            from research_platform.config import load_platform_config
            from research_platform.ads import recheck_all_ads_aliases
            if load_platform_config().get("enabled"):
                r = recheck_all_ads_aliases()
                append_log("info", f"Platform recheck ads: {r}")
        except Exception as e:
            append_log("warn", f"Platform recheck: {e}")
        await _notify("✅ Hoàn thành lượt auto theo lịch — chờ giờ chạy tiếp theo")


async def _manual_sync_folders(branch: str = BRANCH_ADS):
    folders = load_folders(branch)
    if not folders:
        label = "Up bài" if branch == BRANCH_PLAIN else "ads"
        await _notify(f"⚠️ Chưa có folder ({label}) — thêm trên web hoặc /addf trên Telegram")
        return 0
    mark_run_start("Sync folder kênh" + (" Up bài" if branch == BRANCH_PLAIN else ""))
    total_added = 0
    for fd in folders:
        slug = fd.get("slug")
        if not slug:
            continue
        try:
            added = await cmd_addfolder(
                f"https://t.me/addlist/{slug}", silent=True, remember=False, branch=branch,
            )
            total_added += added or 0
        except Exception as e:
            log("SYNC", f"folder {slug}: {e}")
        await asyncio.sleep(2)
    invalidate_channels_cache(branch)
    mark_run_done("ok")
    pool = load_channels(branch)
    label = "Up bài" if branch == BRANCH_PLAIN else "ads"
    await _notify(f"🔄 Sync folder ({label}) — thêm {total_added} kênh mới, tổng {len(pool)} kênh")
    return total_added


async def _platform_rollup_loop():
    """Rollup mục lục tháng — chạy 1 lần/ngày."""
    while True:
        await asyncio.sleep(86400)
        try:
            from research_platform.config import load_platform_config
            from research_platform.rollup import run_rollup_all
            if load_platform_config().get("enabled"):
                r = await run_rollup_all()
                append_log("info", f"Platform rollup: {len(r)} bot")
        except Exception as e:
            append_log("warn", f"Platform rollup: {e}")


async def _execute_web_action(action: dict):
    from core.web_actions import action_labels

    atype = action.get("type", "")
    params = action.get("params") or {}
    label = action_labels().get(atype, atype)
    append_log("info", f"▶ Web: {label}")

    try:
        if atype == "run_full_cycle":
            await _run_scheduled_cycle(manual=True)
        elif atype == "run_all_topics":
            mark_run_start("Tất cả topic nguồn")
            n = await _run_all_topics_only(force_run=True)
            mark_run_done("ok")
            await _notify(f"✅ Đã chạy {n} topic nguồn")
        elif atype == "run_all_task":
            async def _all():
                mark_run_start("/all task")
                ok = await run_all_task(
                    app, notify=_notify, forward_sequence_fn=_forward_seq_all_channels,
                    build_and_forward_all=_all_build_and_forward, force_run=True,
                )
                mark_run_done("ok" if ok else "skip")
                if not ok:
                    await _notify("⚠️ /all task: kiểm tra chat nguồn + kênh đích trên web")

            if _auto_pipeline_lock.locked():
                await _all()
            else:
                async with _auto_pipeline_lock:
                    await _all()
        elif atype == "refresh_all_batch":
            from core.all_batch_preview import refresh_all_batch_preview
            p = await refresh_all_batch_preview(app)
            if p:
                append_log("info", f"Batch /all: {p.get('total_posts')} bài / {p.get('total_media')} media")
            else:
                append_log("info", "Batch /all: chưa có link nguồn")
        elif atype == "run_topic":
            sid = int(params["src_chat_id"])
            tid = int(params["topic_id"])
            title = params.get("topic_title") or ""
            mark_run_start(f"Topic {title or tid}")
            await _try_auto_source_topic(sid, tid, title, manual=True, force_run=True)
            mark_run_done("ok")
        elif atype == "run_plain_topic":
            sid = int(params["src_chat_id"])
            tid = int(params["topic_id"])
            title = params.get("topic_title") or ""
            mark_run_start(f"Up bài {title or tid}")
            await _try_auto_source_topic(
                sid, tid, title, manual=True, force_run=True, branch=BRANCH_PLAIN,
            )
            mark_run_done("ok")
        elif atype == "scan_topic":
            sid = int(params["src_chat_id"])
            tid = int(params["topic_id"])
            await cmd_scan_inventory(sid, tid)
        elif atype == "sync_folders":
            await _manual_sync_folders(BRANCH_ADS)
        elif atype == "sync_plain_folders":
            await _manual_sync_folders(BRANCH_PLAIN)
        elif atype == "check_channels":
            mark_run_start("Check kênh")
            dead = await cmd_checkchan(auto_clean=True, silent=False, branch=BRANCH_ADS)
            invalidate_channels_cache(BRANCH_ADS)
            mark_run_done("ok")
            if dead:
                await _notify(f"🧹 Đã xóa {dead} kênh chết — còn {len(load_channels(BRANCH_ADS))} kênh")
            else:
                await _notify(f"✅ Tất cả {len(load_channels(BRANCH_ADS))} kênh OK")
        elif atype == "check_plain_channels":
            mark_run_start("Check kênh Up bài")
            dead = await cmd_checkchan(auto_clean=True, silent=False, branch=BRANCH_PLAIN)
            invalidate_channels_cache(BRANCH_PLAIN)
            mark_run_done("ok")
            if dead:
                await _notify(f"🧹 Up bài: xóa {dead} kênh chết — còn {len(load_channels(BRANCH_PLAIN))} kênh")
            else:
                await _notify(f"✅ Up bài: {len(load_channels(BRANCH_PLAIN))} kênh OK")
        elif atype == "gen_plain_topic_map":
            from core.channel_store import gen_topic_map
            n_kept, n_new = gen_topic_map(BRANCH_PLAIN)
            await _notify(f"🗺 plain_topic_map.txt — giữ {n_kept} dòng, thêm {n_new} gợi ý")
            append_log("info", f"Sinh plain_topic_map — {n_kept} map, {n_new} mới")
        elif atype == "xep_preview":
            sid = int(params["src_chat_id"])
            tid = int(params["topic_id"])
            p = await preview_topic_batch(app, sid, tid)
            msg = (
                f"👁 Preview {sid}:{tid}\n"
                f"  Bắt đầu: msg {p.get('cursor_start')} | ghim {p.get('pinned_msg_id')}\n"
                f"  Lấy: {p.get('posts')} bài / {p.get('media')} media (target {p.get('target_media')})\n"
                f"  Xếp: /done{p.get('cpa')} mode={p.get('mode')} ads={'có' if p.get('use_ads') else 'không'}\n"
                f"  Kho còn: {p.get('remaining_media')} media\n"
                + (f"  {p.get('warn')}" if p.get("warn") else "")
                + (f"\n  Caption ghim: {p.get('pinned_text')[:120]}" if p.get("pinned_text") else "")
            )
            await _notify(msg)
            append_log("info", f"Preview {sid}:{tid} — {p.get('media')} media")
        elif atype == "apply_start_link":
            from core.map_config import apply_start_link_to_topic
            sid = int(params["src_chat_id"])
            tid = int(params["topic_id"])
            link = params.get("link") or params.get("start_link") or ""
            entry = apply_start_link_to_topic(sid, tid, link)
            await _notify(f"📍 Đã set bắt đầu từ msg {entry.get('start_msg_id')}")
        elif atype == "clear_start_link":
            from core.map_config import clear_start_link
            sid = int(params["src_chat_id"])
            tid = int(params["topic_id"])
            clear_start_link(sid, tid)
            await _notify("📍 Đã xóa link — dùng ghim mới nhất")
        else:
            append_log("warn", f"Action không hỗ trợ: {atype}")
    except Exception as e:
        mark_run_done("error")
        append_log("error", f"Lỗi {label}: {e}")
        await _notify(f"❌ Lỗi {label}: {type(e).__name__}")


async def task_process_web_actions():
    await asyncio.sleep(5)
    while True:
        try:
            from core.web_actions import pop_next_action
            action = pop_next_action()
            if action:
                await _execute_web_action(action)
        except Exception as e:
            log("WARN", f"web action: {e}")
        await asyncio.sleep(2)


async def cmd_scan_inventory(src_id: int, topic_id: int):
    from core.link_parser import msg_link

    cfg = load_auto_config()
    tcfg = get_topic_source(src_id, topic_id) or {"enabled": True}
    title = tcfg.get("topic_title") or str(topic_id)
    result = await collect_batch_from_topic(app, src_id, topic_id, tcfg, cfg.get("global", {}))
    pin = msg_link(src_id, topic_id, result.pinned_msg_id) if result.pinned_msg_id else "—"
    nxt = msg_link(src_id, topic_id, result.next_pin_msg_id) if result.next_pin_msg_id else "—"
    msg = (
        f"📊 Scan <b>{title}</b> · {msg_link(src_id, topic_id, label='topic')}\n"
        f"  Ghim: {pin} | cursor → {nxt}\n"
        f"  Kho: {result.remaining_media} media / {result.remaining_posts} bài\n"
        f"  Target: {result.params.get('target_media')} media / {result.params.get('target_ads')} ads\n"
        + (f"  {result.warn}" if result.warn else "")
    )
    await _notify(msg, parse_mode="HTML")


def _start_web_server():
    try:
        import threading
        import uvicorn
        from web.server import app as web_app
        from core.config_store import load_auto_config
        cfg = load_auto_config()
        host = cfg.get("global", {}).get("web_host", "0.0.0.0")
        port = _web_port()

        def _run():
            uvicorn.run(web_app, host=host, port=port, log_level="warning")

        threading.Thread(target=_run, daemon=True).start()
        log("WEB", f"Dashboard http://{host}:{port}")
    except Exception as e:
        log("WARN", f"Web server không khởi động: {e}")


def _run_web_only():
    """Chưa có API trên web — chỉ chạy dashboard để cấu hình lần đầu."""
    import uvicorn
    from web.server import app as web_app
    from core.config_store import load_auto_config

    cfg = load_auto_config()
    host = cfg.get("global", {}).get("web_host", "0.0.0.0")
    port = _web_port()
    print(
        f"\n⚙️  Chưa có api_id/api_hash trong data/auto_config.json\n"
        f"🌐 Mở http://127.0.0.1:{port} → tab Telegram & Bot → nhập API → restart tool\n"
    )
    uvicorn.run(web_app, host=host, port=port, log_level="info")


# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────

async def main():
    try:
        im = get_intermediate_chat()
        ads = get_ads_chat()
    except RuntimeError as e:
        log("WARN", f"{e} — cấu hình tab Telegram trên web rồi restart tool")
        im = ads = None

    log("CONFIG", f"INTERMEDIATE={im} | ADS_CHAT={ads}")
    ensure_topic_map_txt()
    await app.start()

    me = await app.get_me()
    state["my_id"] = me.id
    log("START", f"Userbot chạy | my_id={me.id}")

    log("START", "Đang cache dialogs...")
    try:
        count = 0
        async for _ in app.get_dialogs():
            count += 1
        log("START", f"Cache xong {count} dialogs")
    except Exception as e:
        log("WARN", f"get_dialogs: {e}")

    if im:
        try:
            await app.get_chat(im)
            log("START", "Resolved intermediate_chat ✓")
        except Exception as e:
            log("ERROR", f"Không resolve được intermediate_chat: {e}")
            return

    if ads:
        try:
            await app.get_chat(ads)
            log("START", "Resolved ads_chat ✓")
        except Exception as e:
            log("WARN", f"ads_chat chưa cache: {e}")

    await load_ads()

    n_folders  = len(load_folders())
    n_channels = len(load_channels())
    web_port   = _web_port()

    asyncio.ensure_future(task_auto_sync_folders())
    asyncio.ensure_future(task_auto_clean_dead())
    asyncio.ensure_future(scheduler_loop(_run_scheduled_cycle))
    asyncio.ensure_future(stock_poll_loop(_stock_poll_run))
    asyncio.ensure_future(all_batch_preview_loop(app))
    asyncio.ensure_future(task_process_web_actions())
    _start_web_server()

    try:
        from research_platform.bot_delivery import start_delivery_bot_background
        from research_platform.config import load_platform_config

        if load_platform_config().get("enabled"):
            asyncio.ensure_future(start_delivery_bot_background())
            asyncio.ensure_future(_platform_rollup_loop())
            from research_platform.backup import start_backup_scheduler

            start_backup_scheduler()
            log("START", "Research Platform bot delivery + rollup + backup nền")
    except Exception as e:
        log("WARN", f"Platform bot không khởi động: {e}")

    await _notify(
        "🤖 Userbot v27 online\n"
        f"📡 {n_channels} kênh • 📁 {n_folders} folder\n"
        f"🌐 Web: http://127.0.0.1:{web_port}\n"
        + (
            "✅ Đã START — auto + lịch sẵn sàng\n"
            if system_armed()
            else "⏸ Chờ START trên web — chưa chạy auto\n"
        )
        + "🔔 Thông báo qua bot token\n"
        "Gõ /help để xem hướng dẫn."
    )

    log("START", "📡 Đang lắng nghe + lịch auto + web real-time...")
    await asyncio.Event().wait()


if app is not None:
    app.on_message()(handler)
    app.run(main())
else:
    _run_web_only()
