# Data model & backup ZIP

Source of truth (K46): **1 → link nguồn**, **2 → index DB**, **3 → ZIP export**.

## Bảng chính (SQLite đề xuất)

### `bots`
| Cột | Mô tả |
|-----|--------|
| id | PK |
| token_ref | Không lưu plain text trong git — web encrypt hoặc env |
| username | @bot |
| source_forum_id | Forum nguồn |
| source_topic_id | Topic map cố định |
| catalog_topic_id | Topic bot hiển thị (map vĩnh viễn) |
| branch | `ads` \| `plain` |
| enabled | ON/OFF delivery |

### `days`
| Cột | Mô tả |
|-----|--------|
| id | PK |
| bot_id | FK |
| date_vn | `2026-06-30` |
| topic_label | `30-06-2026` |
| status | `draft` \| `channel_done` \| `indexed` \| `published` \| `closed` |
| runs_count | 1 hoặc 2 |
| channel_first | bool — thứ tự vs bot |

### `day_items` (sequence)
| Cột | Mô tả |
|-----|--------|
| id | PK |
| day_id | FK |
| seq | Thứ tự 1..N |
| src_chat_id | Forum nguồn |
| src_msg_id | Link master |
| item_type | `content` \| `ads` \| `all_tail_ads` |
| ads_alias | FK → `ads_contracts.alias` |
| album_msg_ids | JSON array — **sai số 0** |
| channel_sent | bool |
| indexed | bool |

### `ads_contracts`
| Cột | Mô tả |
|-----|--------|
| alias | Từ khóa admin đặt |
| src_msg_ids | JSON — bài ads hiện tại |
| active | bool — chấm dứt = false |
| terminated_at | |

**Revoke (F24)**: set `active=false`, lần xem archive **strip** item `ads` — **không** xóa tin user đã nhận.

### `media_sets` (bộ media — user đặt tên)
| Cột | Mô tả |
|-----|--------|
| id | PK |
| name | Tên duyệt (VI/EN/ZH, có dấu) |
| day_id | FK |
| item_ids | JSON subset hoặc full day |
| approved_by | admin |
| contributor_user_id | |

### `users`
| Cột | Mô tả |
|-----|--------|
| telegram_id | PK |
| username, first_name | |
| joined_at | |
| language_code | proxy quốc gia |
| tier | admin gán |
| vip_until | nullable |
| spam_ban_until | E18 |
| allow_forward | default true, VIP share override H36 |

### `vip_plans` / `gift_codes` / `purchases`
- Plans: `day` \| `month` \| `year` \| `lifetime`
- Giftcode: server-wide, redeem once/user
- Stars: Telegram Payments API

### `share_refs`
| Cột | Mô tả |
|-----|--------|
| ref_code | `ref_ABC` |
| day_id | |
| creator_user_id | |
| unique_clicks | 1 user_id = 1 count (H34: không tính lại) |

---

## ZIP backup (G32 / K47)

Hàng ngày + nút **Backup ngay**:

```
backup_2026-06-30.zip
├── platform.db          # SQLite snapshot
├── auto_config.json     # merge config cũ nếu còn dùng userbot
├── archive_index.json   # export days + items
├── bots.json            # metadata (no tokens)
└── MANIFEST.json        # version, checksum, tool git hash
```

Upload vào **topic ZIP** trong forum quản lý admin.

Giải nén → trỏ `DATA_DIR` → chạy tiếp (K46).

---

## Resume (K48)

`user_deliveries` lưu `(user_id, day_id, last_seq_sent)` — bot die giữa seq 15/30 → tiếp tục từ 16.
