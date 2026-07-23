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

## Backend bưu cục · truy vấn DB

Materialize OMS ingest → SQLite, probe pipe bưu cục/3PL (secrets-only), SQL read-only:

```bash
python3 scripts/buucuc_backend_db_query.py
python3 scripts/buucuc_backend_db_query.py --sql "SELECT backend, buucuc, COUNT(*) FROM orders GROUP BY 1,2"
sqlite3 reports/telegram-classify/buucuc_backend.db "SELECT * FROM backends;"
```

Panel: **🗄 Backend BC·DB**. DB: `reports/telegram-classify/buucuc_backend.db`.

## Realtime đơn hàng theo backend

```bash
# một vòng + Telegram
python3 scripts/realtime_order_sync.py --once --notify

# poll liên tục (60s), chỉ báo khi có đơn mới
python3 scripts/realtime_order_sync.py --loop --interval 60 --notify --notify-new-only
```

- Pancake: kéo `/shops/{id}/orders` khi có API key  
- Telegram/direct_api: theo dõi file mới trong `quarantine/telegram`  
- GHN/TPOS: giữ pipe (cần token shop)  
Snapshot: `reports/telegram-classify/realtime/realtime_latest.json`  
Panel: nút **Realtime đơn**.

## Đấu nối OMS toàn diện

Bus trung tâm probe + ingest mọi ống: Telegram · Pancake · GHN · ViettelPost · Tracking · TPOS · direct_api · SPX local · VNPost · OMS bus.

```bash
# một vòng (probe + ingest local) + Telegram
python3 scripts/oms_interconnect.py --once --notify

# chỉ probe channel
python3 scripts/oms_interconnect.py --once --no-ingest
```

Mẫu secrets: `cp backend_pipes.env.example secrets/backend_pipes.env`  
Báo cáo: `reports/telegram-classify/oms_interconnect.txt` · Panel: **Đấu nối OMS**.

```bash
python3 scripts/fix_order_phones.py quarantine/telegram/orders_detailed_*.csv \
  --out reports/telegram-classify/phone-fix
```

Gắn nhãn `ok` / `missing` / `masked` / `invalid`, xuất `*.phone_fixed.csv` + todo khắc phục upstream (Pancake map, tắt PII mask, bắt buộc SĐT khi upload Telegram).

## Mapper đơn hàng realtime từ backend toàn diện

```bash
python3 scripts/realtime_order_backend_mapper.py
```

Gộp OMS probe + realtime sync + ingest local → map từng đơn theo backend/kho/carrier/NS.
Panel: **Mapper RT đơn**. Báo cáo: `reports/telegram-classify/realtime_order_backend_mapper.txt`.
