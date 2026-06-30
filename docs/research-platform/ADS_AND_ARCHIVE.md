# Ads, archive, /all tail

## Archive = link + metadata only (A2)

- **Không** forward ẩn tên vào forum backup.
- Mỗi `day_item` lưu: `src_chat_id`, `src_msg_id`, `album_msg_ids`, `seq`, `ads_alias`.
- Khi user xem: bot **copy/forward từ nguồn** theo index (fresh media, emoji premium giữ nguyên).

## Ads alias (F23)

Admin tab **Quảng cáo**:

```
alias: partner_A  →  link msg nguồn (có thể đổi bài, recheck sau up)
alias: all_slot_1  →  bài /all cuối batch (user xác nhận: /all = ads)
```

Khi index ngày: item type `ads` ghi `ads_alias`, không hardcode msg_id cố định nếu đã recheck.

## Chấm dứt hợp đồng (A1, F24)

| Nơi | Hành vi |
|-----|---------|
| Chat user đã nhận | **Không xóa** — giữ nguyên |
| Lần xem archive sau | **Strip ads** — chỉ media |
| Index DB | `ads_contracts.active = false` |
| Forum backup metadata | Flag item `ads` skipped |

## /all task tail (F23)

Batch `/all` cuối pipeline: các bài cuối **đánh dấu ads** trong index (alias riêng `all_*`).

## Sau up nhiều kênh RR (F26)

1. Một `day` / một bot / một ngày có thể map **nhiều kênh**.
2. Up xoay vòng như hiện tại.
3. **Cuối lượt**: `recheck_all_ads_aliases()` — alias trỏ đúng msg mới chưa.

## Plain branch

- Bot + web menu riêng.
- Cùng DB user/VIP.
- Không xen ads trong delivery plain.
