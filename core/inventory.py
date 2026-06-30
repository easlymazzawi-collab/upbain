"""Track remaining posts/media per source topic + low-stock warnings."""

from core.config_store import load_auto_config, load_inventory, save_inventory, topic_key


def _topic_inv(inv: dict, src_chat_id: int, topic_id: int) -> dict:
    key = topic_key(src_chat_id, topic_id)
    topics = inv.setdefault("topics", {})
    if key not in topics:
        topics[key] = {
            "remaining_posts": 0,
            "remaining_media": 0,
            "total_posts": 0,
            "total_media": 0,
            "last_batch_media": 0,
            "last_batch_posts": 0,
            "cursor_msg_id": None,
            "pinned_msg_id": None,
        }
    return topics[key]


def update_after_scan(
    src_chat_id: int,
    topic_id: int,
    remaining_posts: int,
    remaining_media: int,
    cursor_msg_id: int | None = None,
    pinned_msg_id: int | None = None,
) -> dict:
    inv = load_inventory()
    t = _topic_inv(inv, src_chat_id, topic_id)
    t["remaining_posts"] = remaining_posts
    t["remaining_media"] = remaining_media
    if cursor_msg_id is not None:
        t["cursor_msg_id"] = cursor_msg_id
    if pinned_msg_id is not None:
        t["pinned_msg_id"] = pinned_msg_id
    save_inventory(inv)
    return t


def update_after_batch(
    src_chat_id: int,
    topic_id: int,
    used_posts: int,
    used_media: int,
    next_pin_msg_id: int | None,
    old_pin_msg_id: int | None,
) -> dict:
    inv = load_inventory()
    t = _topic_inv(inv, src_chat_id, topic_id)
    t["remaining_posts"] = max(0, t.get("remaining_posts", 0) - used_posts)
    t["remaining_media"] = max(0, t.get("remaining_media", 0) - used_media)
    t["last_batch_posts"] = used_posts
    t["last_batch_media"] = used_media
    if next_pin_msg_id is not None:
        t["cursor_msg_id"] = next_pin_msg_id
        t["pinned_msg_id"] = next_pin_msg_id
    save_inventory(inv)
    return t


def check_low_stock(src_chat_id: int, topic_id: int, upcoming_media: int) -> str | None:
    cfg = load_auto_config()
    threshold = cfg.get("global", {}).get("low_media_warn_threshold", 50)
    inv = load_inventory()
    t = _topic_inv(inv, src_chat_id, topic_id)
    remaining = t.get("remaining_media", 0)
    if remaining <= 0:
        return f"⚠️ Topic {src_chat_id}:{topic_id} — HẾT media trong kho!"
    if remaining < upcoming_media:
        return (
            f"⚠️ Topic {src_chat_id}:{topic_id} — còn {remaining} media, "
            f"batch cần ~{upcoming_media}"
        )
    if remaining <= threshold:
        return (
            f"📉 Topic {src_chat_id}:{topic_id} — còn {remaining} media "
            f"(ngưỡng cảnh báo {threshold}). Nên bổ sung nguồn!"
        )
    return None


def get_all_inventory() -> dict:
    return load_inventory()
