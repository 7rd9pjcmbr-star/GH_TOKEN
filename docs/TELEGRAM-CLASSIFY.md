# Phân loại file qua Telegram (local-only)

Bot đọc document / đoạn text `identifier:password` → tách nhóm → báo cáo.

## Không làm

- Không gọi DeHashed / LeakCheck / Snusbase
- Không Holehe / Sherlock
- Không suy portal login / VPN để tấn tài khoản

## Có làm

1. Tách: `corporate_email` · `generic_email` · `phone` · `unknown`
2. Pattern password: bcrypt / MD5 / SHA256 / PIN / plaintext…
3. Thống kê domain corporate + nhà mạng VN
4. Xuất `reports/telegram-classify/*.summary.json` + `.classified.csv`

## Chạy

```bash
# điền secrets/telegram.env trước
python3 scripts/telegram_classify_poll.py          # poll liên tục
python3 scripts/telegram_classify_poll.py --once   # một vòng

# hoặc phân loại file local
python3 scripts/classify_accounts.py path/to/file.txt --out reports/classify
```

Gửi file `.txt/.csv/.json` vào chat bot (cùng `TELEGRAM_CHAT_ID`).

## Bảng điều khiển Telegram

```bash
python3 scripts/telegram_control_panel.py          # gửi panel + trả lời đủ mục truy vấn
python3 scripts/telegram_control_panel.py --listen # giữ listener nhận bấm nút (~2 phút nếu không truyền thêm)
```

Nút: Tổng quan · Theo nguồn · SĐT masked · SĐT thiếu · Todo khắc phục · Đường dẫn nóng.
Gõ `/panel` trong chat để mở lại menu.

```bash
python3 scripts/fix_order_phones.py quarantine/telegram/orders_detailed_*.csv \
  --out reports/telegram-classify/phone-fix
```

Gắn nhãn `ok` / `missing` / `masked` / `invalid`, xuất `*.phone_fixed.csv` + todo khắc phục upstream (Pancake map, tắt PII mask, bắt buộc SĐT khi upload Telegram).
