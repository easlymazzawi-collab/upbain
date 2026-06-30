# Patterns port từ Clender

Repo: [easlymazzawi-collab/clender @ cursor/fix-web-backup-access-2234](https://github.com/easlymazzawi-collab/clender/tree/cursor/fix-web-backup-access-2234)

## Ưu tiên port

| Clender | Dùng cho UpBain v2 |
|---------|-------------------|
| `bot/membership.py` | Force-join trước xem / duyệt mem |
| `utils/token.py` | `start=ref_ABC`, `start=day_TOKEN` |
| `bot/handlers.py` `_serve_album` | copyMessages + flood retry |
| `database/models.py` `media_albums` | expires, max_views, allow_forward |
| `forwarder/link_sender.py` | Preview + link (tuỳ chọn catalog topic) |
| `utils/backup.py` | ZIP + upload topic Telegram |
| `app.py` `/health`, `/api/backup` | Web ops |
| `run.py` | Bot + web + backup watchdog |

## Membership flow

```
/start → gate() join channel? 
  No  → nút Join + Recheck
  Yes → pending token / main menu
```

Admin: `/forcejoin @channel`, whitelist upload (nếu cần duyệt mem file).

## Deep link share ngày

Kết hợp:

- Clender numeric token cho **album lẻ**.
- UpBain `day_id` + signed token cho **cả ngày** (nhiều seq).

URL: `t.me/<bot>?start=day_<token>`

Đua top: `t.me/<bot>?start=ref_<user_code>` — 1 uid = 1 click.

## Backup web

Port `/api/backup` + auto schedule → topic ZIP admin (K47).
