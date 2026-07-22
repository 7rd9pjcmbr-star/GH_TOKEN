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
