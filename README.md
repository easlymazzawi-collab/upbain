# UpBain

Telegram userbot auto-forward + **Research Platform v2** (archive index, bot delivery).

## Nhánh

| Nhánh | Mục đích |
|-------|----------|
| `main` | Userbot cơ bản |
| `cursor/web-bot-schedule-realtime-fe96` | Auto topic, Up bài, web SSE, flood/stage fix |
| **`cursor/research-bot-platform-fe96`** | **Research Platform P0 — index + bot /day + web** |

## Research Platform v2

→ **[docs/research-platform/README.md](docs/research-platform/README.md)**

Tab **Research Platform** trên web: bot token, forum IDs, ON/OFF 3 lớp (up kênh / index / bot delivery), backup ZIP.

## Import dữ liệu cũ

Tab **Import dữ liệu cũ** trên web — kéo thả hoặc chọn file (hoặc ZIP):

| File | Tự map |
|------|--------|
| `.env` | API, chat ID, bot token, web port/token → `data/auto_config.json` |
| `channels.json`, `folders.json`, `topic_map.txt`, `topic_rr.json` | Copy + topic map → web |
| `inventory.json`, `auto_config.json` | Merge kho & cấu hình |
| `*.session` | Session Pyrogram (restart sau import) |

Sau import → restart tool nếu có session hoặc API mới.

## Cài đặt

```bash
pip install -r requirements.txt
python tool__tauto_nostage.py
```

**Windows:** chạy bằng `python tool__tauto_nostage.py` trong terminal (không double-click file `.py`).
Thư mục code Research Platform là `research_platform/` — **không** đặt tên `platform` (trùng Python stdlib, Pyrogram crash).

Lần đầu chưa có API → tool chỉ mở web. Vào tab **Telegram & Bot** nhập `api_id`, `api_hash`, chat ID → restart tool.

## Web dashboard

`http://127.0.0.1:8080` — cấu hình toàn bộ, **real-time SSE**:

- Topic nguồn, kho media, /all task
- **Bot token** + chat ID nhận thông báo (hết bài, kho thấp, xong lượt...)
- **Lịch auto hằng ngày** — set giờ (HH:MM), tool tự chạy lại mỗi ngày
- **Research Platform** — index ngày, bot delivery, backup

## Bot thông báo

Cấu hình tab **Telegram & Bot** trên web:

- `Bot Token` — token từ @BotFather
- `Chat ID nhận thông báo` — group/channel/chat nhận status

## Lịch auto hằng ngày

Tab **Lịch auto**:

1. Bật lịch, thêm giờ (vd. `08:00`, `14:00`, `20:00`)
2. Mỗi ngày đúng giờ → chạy tất cả topic nguồn đã bật → /all task (nếu bật)
3. Xong lượt → chờ giờ tiếp theo (hiển thị trên dashboard real-time)

## Luồng auto

1. Ghim bài kế tiếp cần up trong topic nguồn
2. Caption: `30 media 2 ads`
3. `/runtopic` hoặc auto_run hoặc **lịch hằng ngày** → lấy topic → xếp → forward → pin bài kế
4. (Platform) Ghi index link → user xem lại qua bot `/day`

## Lệnh

- `/runtopic [chat_id topic_id]`
- `/scan <chat_id> <topic_id>`
- `/allrun`

Bot user-facing (Research Platform): `/start`, `/today`, `/day 30-06-2026`
