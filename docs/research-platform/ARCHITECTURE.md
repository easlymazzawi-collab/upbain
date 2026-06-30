# Kiến trúc tổng thể

## Ba lớp tách ON/OFF

```mermaid
flowchart TB
    subgraph Backend["Backend chung (web + SQLite/JSON + queue)"]
        ORCH[Orchestrator]
        IDX[Index: ngày · bot · link nguồn · alias ads]
        USR[Users · VIP · giftcode · tier]
        EVT[Share ref · đua top]
    end

    subgraph L1["Lớp 1 — Phát sóng kênh (userbot)"]
        UB[1 userbot] --> CH[Kênh đích RR]
    end

    subgraph L2["Lớp 2 — Archive metadata"]
        SRC[Forum nguồn] -->|chỉ ghi link + thứ tự| IDX
    end

    subgraph L3["Lớp 3 — Bot trả user"]
        BOT[10 bot token] -->|copy/forward khi user xem| USER[Chat user]
    end

    ORCH --> L1
    ORCH --> L2
    ORCH --> L3
    IDX --> L3
```

| Lớp | Bật/tắt web | Mô tả |
|-----|-------------|--------|
| Up kênh | `publish_channels` | Userbot forward — **độc lập** archive |
| Archive index | `archive_index` | Lưu link msg nguồn + metadata ngày — **không** forward vào forum backup |
| Bot delivery | `bot_delivery` | Bot copy ra user khi bấm ngày / tìm bộ / share link |

Thứ tự mặc định: **kênh trước → bot sau** (đổi được trên web).

---

## Forum & bot mapping

| Thực thể | Quy tắc |
|----------|---------|
| Forum nguồn | 1+ nguồn topic (ads / plain tách) |
| Forum backup metadata | **Chung** — không chứa media, chỉ index (optional topic admin log) |
| Forum quản lý admin | Duyệt tên bộ, ZIP backup, broadcast, ads alias |
| **1 bot token** | **1 topic cố định** trong catalog bot (map vĩnh viễn, tool không tự tìm) |
| Ngày VN | Topic tên `30-06-2026` **trên mỗi bot** (1 bot = 1 topic/ngày) |

Sau **30 ngày**: gom metadata vào **topic tổng** (text mục lục), **không xóa** text cũ — user vẫn tra index.

---

## Luồng 1 ngày (VN 00:00–23:59)

```mermaid
sequenceDiagram
    participant W as Web/Cron
    participant UB as Userbot
    participant SRC as Forum nguồn
    participant IDX as Index DB
    participant ADM as Group admin notify
    participant BOT as Bot user-facing

    W->>UB: Lượt auto (tuần tự nguồn)
    UB->>SRC: Collect batch (cursor/link, không pin)
    UB->>UB: Xếp sequence + ads alias
    UB->>CH: Forward kênh (RR nhiều kênh)
    UB->>IDX: Ghi link từng bài + ads alias + thứ tự
    UB->>ADM: Thông báo link kế + topic bot + trạng thái
    Note over BOT,USER: User bấm ngày sau
    USER->>BOT: /day 30-06-2026 hoặc menu
    BOT->>IDX: Load sequence ngày
    BOT->>SRC: copyMessage/forward từng bài (chậm, đúng thứ tự)
    BOT->>USER: Gửi xong → link share ngày (ref_ABC)
```

**1 lượt/ngày** (mặc định); config cho phép 2 lượt sau này.

**Idempotent**: bài đã ghi `delivered_to_channel` / `indexed` không up lại — retry an toàn.

---

## Đóng ngày (định nghĩa — C11)

Nút web **「Đóng ngày」**:

1. Khóa thêm bài vào `day_id` hôm nay.
2. Chạy `recheck_ads_aliases` (alias ads có đổi bài không).
3. Đánh dấu `status=published` → bot được phép trả user.
4. (Tuỳ chọn) ZIP backup lên topic admin.

---

## Group thông báo admin (thay pin nguồn)

Mỗi lượt auto gửi 1 tin:

- Bot / nguồn / ngày
- Link msg **kế tiếp** trên forum nguồn (cho lần sau)
- Deep link bot + tên topic ngày
- Trạng thái kênh (OK / fail / flood chờ)

**Không ghim** trên forum nguồn nữa.

---

## Plain (Up bài)

- Forum nguồn / bot / web menu **tách** Ads.
- **Cùng** user DB, VIP, Stars, giftcode (đồng bộ web ↔ tele).

---

## Broadcast (J43 mở rộng)

| Loại | Hành vi |
|------|---------|
| VIP media broadcast | Admin chọn batch → gửi tuần tự 1s/bài → hết hỏi **Done?** |
| All-user broadcast | Giống trên, filter tier / VIP |

User bị ban spam (E18): chỉ xem theo lịch up hoặc admin broadcast.
