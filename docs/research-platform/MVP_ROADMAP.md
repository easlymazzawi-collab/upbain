# MVP roadmap (đề xuất — K44 user chưa chốt)

## P0 — Core index + 1 bot (2–3 tuần logic)

- [ ] SQLite schema `bots`, `days`, `day_items`, `users`
- [ ] Web: 1 bot, forum IDs, ON/OFF 3 lớp
- [ ] Userbot: up 1 kênh + ghi index link (không archive forward)
- [ ] Bot: menu + `/day` gửi chậm từ nguồn
- [ ] Admin notify group
- [ ] ZIP backup manual

**Done khi**: 1 nguồn 1 ngày xem lại được qua bot, kênh up OK.

## P1 — 10 bot + orchestrator

- [ ] Queue tuần tự 10 nguồn
- [ ] Restart bot đơn/all
- [ ] Resume delivery
- [ ] Recheck ads alias

## P2 — Stars + VIP + giftcode

- [ ] Web VIP plans (ngày/tháng/năm/lifetime)
- [ ] Telegram Stars checkout
- [ ] Giftcode redeem
- [ ] Gán VIP tay trên web/bot

## P3 — Catalog & đóng góp tên

- [ ] Forward duyệt admin forum
- [ ] `/find` đa ngôn ngữ có dấu
- [ ] Spam ban 24h/36h

## P4 — Share & clender membership

- [ ] `ref_ABC` leaderboard
- [ ] Share link sau gửi ngày
- [ ] Force-join + bot file mem
- [ ] VIP forward policy

## P5 — Rollup 30 ngày

- [ ] Topic tổng text index
- [ ] User vẫn tra cứu qua bot

---

## Không làm v1 (K58 — user chưa liệt kê, giữ mặc định)

- Xóa ads trong chat user
- Forward media vào forum backup
- Watermark
- Tự trao giải đua top
- Multi userbot pool

## Metric thành công (K59)

- User xem lại ngày cũ **không cần scroll kênh**
- Admin restore từ ZIP **< 15 phút**
- Auto 30–40 media **không ban userbot** (tuần tự + delay)
