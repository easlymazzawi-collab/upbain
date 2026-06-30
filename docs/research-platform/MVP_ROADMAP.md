# MVP roadmap — trạng thái implementation

## P0 — Core index + 1 bot ✅

- [x] SQLite schema `bots`, `days`, `day_items`, `users`
- [x] Web: bot, forum IDs, ON/OFF 3 lớp
- [x] Userbot: up kênh + ghi index link
- [x] Bot: menu + `/day` gửi chậm từ nguồn
- [x] Admin notify group
- [x] ZIP backup manual

## P1 — 10 bot + orchestrator ✅

- [x] Queue tuần tự 10 nguồn (`research_platform/run_queue.py`, `queue_order`)
- [x] Restart bot đơn/all (`bot_manager.restart_bot`)
- [x] Resume delivery (`user_deliveries`)
- [x] Recheck ads alias (`research_platform/ads.py`)

## P2 — Stars + VIP + giftcode ✅

- [x] Web VIP plans
- [x] Telegram Stars checkout (`/buy_{plan_id}`, XTR invoice)
- [x] Giftcode redeem (`/redeem`)
- [x] Gán VIP tay trên web

## P3 — Catalog & đóng góp tên ✅

- [x] Forward duyệt admin forum (`contribution_queue`)
- [x] `/find` đa ngôn ngữ có dấu (`normalize_name`)
- [x] Spam ban 24h/36h

## P4 — Share & clender membership ✅

- [x] `ref_ABC` leaderboard
- [x] Share link sau gửi ngày
- [x] Force-join + recheck (`membership.py`)
- [x] VIP forward policy flag

## P5 — Rollup 30 ngày ✅

- [x] Topic tổng text index (`rollup_indexes` + post catalog)
- [x] User vẫn tra cứu qua bot
