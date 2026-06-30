# Web admin dashboard

Layout: **sidebar trái** (HTML app) — Trang chủ | Bot & nguồn | Archive | VIP & Stars | Giftcode | Share event | Ads | User | Phiên nguồn | Settings | Log | Backup

## Trang chủ
- START/STOP orchestrator
- Trạng thái 10 bot (online, flood, queue)
- Lượt auto hôm nay (VN)
- Nút **Restart** từng bot / **Restart all**

## Bot & nguồn (G28 + thiếu forum IDs)

Form mỗi bot:

| Field | Mô tả |
|-------|--------|
| Bot token | |
| @username | |
| Forum nguồn ID | |
| Topic nguồn ID | |
| Branch | ads / plain |
| Kênh đích (RR) | multi-select từ pool |
| ON/OFF | up kênh / index / bot delivery |
| Thứ tự | kênh trước hay bot trước |

**Forum global** (nhập ID một lần):

| Field | Mô tả |
|-------|--------|
| `admin_forum_id` | Duyệt tên, ZIP, broadcast |
| `admin_zip_topic_id` | Topic nhận file ZIP |
| `admin_notify_group_id` | Thông báo lượt auto |
| `membership_channel_id` | Force-join (clender) |
| `backup_forum_id` | Chỉ metadata/log (optional) |

## Archive (G29)
- Calendar VN — mỗi ô: bot + trạng thái day
- Xem sequence link (không preview media nặng)
- **Đóng ngày** / **Publish**
- **Revoke ads** theo alias (F23)

## VIP & Stars (D16)
- Plans: ngày / tháng / năm / vĩnh viễn
- Giá Stars từng plan
- **Thêm user VIP** trực tiếp (id @username)
- Hết hạn: không gia hạn = hết quyền (không auto-renew unless configured)

## Giftcode
- Tạo batch: `WELCOME2026`, max uses, expiry, plan gắn kèm
- Redeem log

## Share event (H)
- Bật/tắt đua top theo kỳ
- Leaderboard (top 10, ẩn bớt username)
- **Không** trao thưởng tự động (H35) — admin xử lý tay

## Ads (F23)
- CRUD alias → msg link nguồn
- **Chấm dứt HĐ** → inactive (archive view strip ads)
- Sau mỗi lượt up: nút **Recheck aliases**

## User (G30)
- id, username, tên, join, language_code, tier, VIP until, spam ban, clicks share

## Phiên nguồn / Settings
- Userbot session status
- Queue tuần tự, delay FWD
- `@xepbai`, mode, require_full_batch (legacy userbot)
- Forward/save policy default (D17)

## Log (G31)
- Userbot + từng bot + web actions
- `STAGE` / `FWD` / `INDEX` / `DELIVER 12/30`

## Backup (G32)
- **Tải ZIP** / **Backup ngay** → upload topic admin ZIP
- Lịch backup tự động (port clender `utils/backup.py`)

## Broadcast (J43)
- VIP batch: chọn ngày/bộ → preview count → Start → progress → **Done?**
- All-user: filter tier
