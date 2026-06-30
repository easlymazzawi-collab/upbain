# UpBain v26

Telegram userbot auto-forward với **nguồn topic**, **tin ghim**, **web dashboard**.

## Cài đặt

```bash
pip install -r requirements.txt
cp .env.example .env
python tool__tauto_nostage.py
```

## Web dashboard

`http://127.0.0.1:8080` — cấu hình topic nguồn, kho media, /all task.

## Luồng auto

1. Ghim bài kế tiếp cần up trong topic nguồn
2. Caption: `30 media 2 ads`
3. `/runtopic` hoặc auto_run → lấy topic → xếp → forward → pin bài kế

## Lệnh

- `/runtopic [chat_id topic_id]`
- `/scan <chat_id> <topic_id>`
- `/allrun`
