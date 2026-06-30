# Bot UX — menu, lệnh, delivery

## Reply keyboard (2 hàng — D13)

```
[ 📅 Xem ngày ]     [ 🔍 Tìm bộ ]
[ ⭐ VIP / Stars ]   [ 🏆 Mời bạn ]
[ 💡 Đóng góp tên ] [ ℹ️ Hướng dẫn ]
```

## Lệnh (D14)

| Lệnh | Mô tả |
|------|--------|
| `/start` | Onboarding + membership gate (clender) |
| `/start ref_ABC` | Share / đua top |
| `/start day_30-06-2026` | Deep link xem ngày |
| `/today` | Ngày VN hôm nay (nếu published) |
| `/day 30-06-2026` | Parse nhiều format ngày |
| `/find <từ khóa>` | Tìm bộ media đã duyệt |
| `/dong_gop` | Hướng dẫn forward bài + tên |
| `/vip` | Trạng thái VIP + link Stars |
| `/invite` | Link `t.me/bot?start=ref_<code>` |
| `/me` | Tier, join date, quyền forward |

BotFather: bật **command list** + **menu button** trùng keyboard.

---

## Gửi ngày cho user (D15)

1. Check VIP / Stars / giftcode / tier.
2. Load `day_items` sorted by `seq`.
3. Với mỗi item:
   - Album: `copyMessages` / forward **đủ album** từ `src_chat_id` + `album_msg_ids`.
   - Ads: chỉ gửi nếu `ads_contracts.active`.
   - Delay **≥1s** giữa bài (config web).
4. Flood → `FloodWait` sleep, **không bỏ bài** (A15).
5. Xong → gửi **link share ngày**: `t.me/bot?start=share_day_<token>`.

User **không** vào forum backup — bot đọc index rồi pull từ nguồn.

---

## Sau khi gửi xong — share (D17)

Tin kèm:

> ✅ Đã gửi 32 bài (30 media + 2 ads).  
> 🔗 Mời bạn xem cùng ngày: [link]  
> (VIP: được share — user thường: forward tắt nếu admin cấu hình)

---

## Đóng góp tên (E18)

1. User bấm **💡 Đóng góp** hoặc `/dong_gop`.
2. **Forward** bài (hoặc album) cho bot + caption tên đề xuất.
3. Bot **forward nguyên khối** vào topic duyệt admin (không tách).
4. Admin ✅ trên Telegram **hoặc** Approve web → sync.

**Anti-spam**:
- Regex / rate limit / duplicate text → `spam_ban_until` 24h → 36h.
- Ban: không dùng bot trừ xem lịch up + admin broadcast.

---

## Membership (clender)

Trước `/start` và delivery:

- `gate()` kiểm tra join kênh bắt buộc.
- Pending token sau khi join xong.
- Port từ `bot/membership.py` — xem [CLENDER_PATTERNS.md](./CLENDER_PATTERNS.md).

---

## VIP share forward (H36)

- User thường: `allow_forward=false` trên copy (nếu API hỗ trợ) + policy web.
- VIP tier được bật share media ngày cũ qua lệnh đồng bộ admin.
