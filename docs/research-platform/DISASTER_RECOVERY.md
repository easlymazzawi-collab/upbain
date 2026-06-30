# Disaster recovery

## K46 — Thứ tự fallback

1. **Link nguồn** (`src_chat_id` + `msg_id`) — nếu forum nguồn còn.
2. **Index DB** + `archive_index.json` trong ZIP.
3. **ZIP** trên topic admin (K47).

Bot die **giữa delivery** → resume từ `user_deliveries.last_seq` (K48).

## Bot token die (B7)

1. Tạo bot mới @BotFather.
2. Web → đổi token bot (hot-swap).
3. Cùng DB → menu / deep link cập nhật `@username` (clender `/d/TOKEN` redirect pattern).
4. User `start ref_*` cũ vẫn hoạt động nếu ref lưu DB không phụ thuộc token cũ.

## Archive / index die

- Media vẫn trên **forum nguồn** → rebuild index từ nguồn (scan ngày — chậm, manual).
- Prevent: ZIP hàng ngày + `MANIFEST.json`.

## Userbot ban / flood

- Queue pause toàn orchestrator.
- Bot delivery vẫn có thể chạy (chỉ copy từ nguồn) nếu acc bot/userbot đọc nguồn được.
- Up kênh tắt ON/OFF đến khi hết ban.

## Forum admin die

- Chuyển `admin_forum_id` trên web.
- Export ZIP local trước khi migrate.

## Không cần (user chốt)

- Xóa ads trong chat user (impossible / bỏ).
- Watermark (M).
- Sync 2 chiều xóa kênh ↔ archive (56).
