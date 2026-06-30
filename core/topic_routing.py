"""Routing topic → alias kênh theo nhánh (ads / Up bài)."""

from __future__ import annotations

import os

from core.channel_store import BRANCH_ADS, BRANCH_PLAIN, load_channels, topic_map_file


def _read_topic_lines(branch: str = BRANCH_ADS) -> list[tuple[str, str]]:
    path = topic_map_file(branch)
    out: list[tuple[str, str]] = []
    if not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if "#" in s:
                    s = s.split("#", 1)[0].strip()
                if not s or s.startswith("@"):
                    continue
                sep = "=" if "=" in s else (":" if ":" in s else None)
                if not sep:
                    continue
                left, _, right = s.partition(sep)
                left, right = left.strip(), right.strip().lstrip("/")
                if left and right:
                    out.append((left, right.split()[0].lstrip("/")))
    except OSError:
        pass
    return out


def _topic_map_keys(title: str, src_id: int | None = None) -> list[str]:
    t = (title or "").strip().lower()
    keys: list[str] = []
    if src_id is not None:
        keys.append(f"{src_id}:{t}")
        s = str(src_id)
        if s.startswith("-100"):
            keys.append(f"{s[4:]}:{t}")
    if t:
        keys.append(t)
    return keys


def find_cmds_for_topic_title(
    title: str,
    src_id: int | None = None,
    branch: str = BRANCH_ADS,
) -> list[str]:
    if not title:
        return []
    t = title.strip().lower()
    out: list[str] = []
    seen: set[str] = set()
    lines = _read_topic_lines(branch)

    if src_id is not None:
        scoped = {f"{src_id}:{t}"}
        s = str(src_id)
        if s.startswith("-100"):
            scoped.add(f"{s[4:]}:{t}")
        for topic, cmd in lines:
            tl = topic.strip().lower()
            if tl in scoped:
                cl = cmd.lower()
                if cl not in seen:
                    seen.add(cl)
                    out.append(cmd)
        if out:
            return out

    for topic, cmd in lines:
        tl = topic.strip().lower()
        if ":" in tl:
            continue
        if tl == t:
            cl = cmd.lower()
            if cl not in seen:
                seen.add(cl)
                out.append(cmd)
    return out


def get_cmd_key(title: str, alias: str = "") -> str:
    alias = (alias or "").strip().lower()
    if alias:
        return alias
    parts = (title or "").strip().split(None, 1)
    if len(parts) >= 2:
        return parts[1].strip().lower()
    return (parts[0] if parts else "").lower()


def resolve_channels_by_cmd(cmd_key: str, branch: str = BRANCH_ADS) -> list[dict]:
    cmd_key = (cmd_key or "").strip().lower().lstrip("/")
    if not cmd_key:
        return []
    groups: dict[str, list[dict]] = {}
    for ch in load_channels(branch):
        k = get_cmd_key(ch.get("title") or "", ch.get("alias") or "")
        if k:
            groups.setdefault(k, []).append(ch)
    return groups.get(cmd_key, [])


def make_routing(branch: str):
    """Factory callbacks cho auto_runner / userbot."""
    b = branch

    def find_cmds(title: str, src_id: int | None = None) -> list[str]:
        return find_cmds_for_topic_title(title, src_id, b)

    def resolve(cmd: str) -> list[dict]:
        return resolve_channels_by_cmd(cmd, b)

    return find_cmds, resolve
