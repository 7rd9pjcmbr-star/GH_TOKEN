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

Nút: Tổng quan · Theo nguồn · SĐT masked · SĐT thiếu · Todo khắc phục · Đường dẫn nóng · Pipe backend.
Gõ `/panel` trong chat để mở lại menu.

## Ống dẫn backend (chống logout)

Đấu nối pipe theo từng backend; heartbeat cảnh báo trước khi session/key chết.
Chỉ đọc `secrets/` — không dump, không auto-login mật khẩu.

```bash
# một vòng + gửi Telegram
python3 scripts/backend_pipe_keepalive.py --once --notify

# duy trì liên tục (mặc định 300s)
python3 scripts/backend_pipe_keepalive.py --loop --interval 300 --notify-on-risk
```

Điền `PANCAKE_POS_API_KEY` / `GHN_API_TOKEN` / `TPOS_*` vào `secrets/backend_pipes.env` (gitignored).
State: `secrets/backend_pipes.state.json` · báo cáo: `reports/telegram-classify/backend_pipe_keepalive.json`.

```bash
python3 scripts/fix_order_phones.py quarantine/telegram/orders_detailed_*.csv \
  --out reports/telegram-classify/phone-fix
```

Gắn nhãn `ok` / `missing` / `masked` / `invalid`, xuất `*.phone_fixed.csv` + todo khắc phục upstream (Pancake map, tắt PII mask, bắt buộc SĐT khi upload Telegram).
