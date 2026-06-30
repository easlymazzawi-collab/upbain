# UpBain Research Platform (v2)

Nhánh **`cursor/research-bot-platform-fe96`** — thiết kế hệ thống mới, **tách khỏi** nhánh auto-up kênh (`cursor/web-bot-schedule-realtime-fe96`).

## Mục tiêu

- Nâng tầm bot research Telegram: archive theo ngày, Stars, VIP, share đua top.
- **10 nguồn = 10 bot token** (mở rộng sau), **1 userbot** up kênh, **backend web chung**.
- Archive = **link + metadata** (không forward vào forum backup).
- User xem qua bot (copy/forward từ nguồn lúc xem), không duyệt forum backup trực tiếp.

## Tài liệu

| File | Nội dung |
|------|----------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Kiến trúc 3 lớp, luồng ngày, ON/OFF |
| [DATA_MODEL.md](./DATA_MODEL.md) | Schema DB + ZIP backup |
| [BOT_UX.md](./BOT_UX.md) | Menu, lệnh, gửi chậm, share link |
| [WEB_ADMIN.md](./WEB_ADMIN.md) | Dashboard, VIP, giftcode, forum IDs |
| [ADS_AND_ARCHIVE.md](./ADS_AND_ARCHIVE.md) | Alias ads, chấm dứt HĐ, /all = ads |
| [DISASTER_RECOVERY.md](./DISASTER_RECOVERY.md) | Bot die, archive die, resume |
| [CLENDER_PATTERNS.md](./CLENDER_PATTERNS.md) | Port từ clender (membership, deep link) |
| [MVP_ROADMAP.md](./MVP_ROADMAP.md) | Phase P0–P4 |

## Tham chiếu code cũ (main / nhánh auto)

- Userbot up kênh: `tool__tauto_nostage.py`, `core/auto_runner.py`
- Web SSE: `web/server.py`

## Tham chiếu ngoài

- Membership / deep link: [clender @ fix-web-backup-access-2234](https://github.com/easlymazzawi-collab/clender/tree/cursor/fix-web-backup-access-2234)

## Trạng thái nhánh này

**Chỉ spec + scaffold** — chưa implement runtime v2. Implement theo `MVP_ROADMAP.md`.
