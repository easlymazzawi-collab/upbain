# UpBain v27

Telegram userbot auto-forward với **nguồn topic**, **tin ghim**, **web dashboard**, **bot thông báo**, **lịch auto hằng ngày**.

## Cài đặt

```bash
pip install -r requirements.txt
cp .env.example .env   # chỉ cần api_id/api_hash lần đầu (hoặc cấu hình trên web)
python tool__tauto_nostage.py
```

## Web dashboard

`http://127.0.0.1:8080` — cấu hình toàn bộ trên web, **real-time SSE**:

- Topic nguồn, kho media, /all task
- **Bot token** + chat ID nhận thông báo (hết bài, kho thấp, xong lượt...)
- **Lịch auto hằng ngày** — set giờ (HH:MM), tool tự chạy lại mỗi ngày
- Telegram: intermediate chat, ads chat, API credentials

## Bot thông báo

Cấu hình tab **Telegram & Bot** trên web:

- `Bot Token` — token từ @BotFather
- `Chat ID nhận thông báo` — group/channel/chat nhận status

Tool gửi qua bot khi: hết media, kho thấp, xong batch, lỗi, bắt đầu/kết thúc lịch auto.

## Lịch auto hằng ngày

Tab **Lịch auto**:

1. Bật lịch, thêm giờ (vd. `08:00`, `14:00`, `20:00`)
2. Mỗi ngày đúng giờ → chạy tất cả topic nguồn đã bật → /all task (nếu bật)
3. Xong lượt → chờ giờ tiếp theo (hiển thị trên dashboard real-time)

## Luồng auto

1. Ghim bài kế tiếp cần up trong topic nguồn
2. Caption: `30 media 2 ads`
3. `/runtopic` hoặc auto_run hoặc **lịch hằng ngày** → lấy topic → xếp → forward → pin bài kế

## Lệnh

- `/runtopic [chat_id topic_id]`
- `/scan <chat_id> <topic_id>`
- `/allrun`
