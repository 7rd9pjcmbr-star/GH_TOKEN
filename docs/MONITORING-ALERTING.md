# Monitoring + Alerting — giám sát & cảnh báo

Module: `scripts/monitor_alert.py`
Owned-only · **dry-run mặc định** (không gửi Telegram trừ khi `--send` + có creds) · report mask-only.

Kiểm tra định kỳ các thành phần, phân loại severity, **dedup + cooldown** để không spam,
và cảnh báo qua Telegram (bot owned).

## Thành phần được giám sát

| Check | Nguồn | ok / warn / critical |
|-------|-------|----------------------|
| `web:*` | GET `/healthz` (async aiohttp) | 200 → ok · khác/unreachable → critical |
| `platform:*` | `backend_pipe_keepalive.run_once` | alive → ok · missing_cred → warn · session_risk/lỗi → critical |
| `sessions` | `session_store.status_report` *(nếu có)* | ok/session → ok · expiring/unknown → warn · expired → critical |
| `account_pool` | `account_pool.status_report` *(nếu có)* | eligible>0 → ok · có cooldown → warn · eligible=0 → critical |

`sessions`/`account_pool` là **soft-import** — bỏ qua nếu module chưa có trên nhánh.

## Severity & alert

- Thứ tự: `ok < warn < critical`; `overall` = mức tệ nhất.
- **Alert khi**: severity **xấu đi** (vd ok→warn), hoặc đang warn/critical và đã qua **cooldown** (mặc định 900s) → nhắc lại.
- **Recovered**: khi trở lại `ok` từ trạng thái xấu → gửi thông báo hồi phục.
- Dedup/cooldown lưu ở `secrets/monitor_alert.state.json` (gitignored, `chmod 600`; override `MONITOR_STATE_PATH`).

## CLI

```bash
# Một lần (dry-run: không gửi Telegram)
python3 scripts/monitor_alert.py once --web-url http://localhost:8080/healthz

# Vòng lặp async liên tục
python3 scripts/monitor_alert.py loop --interval 300 --web-url http://localhost:8080/healthz

# Bật gửi Telegram (cần TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID owned)
python3 scripts/monitor_alert.py once --web-url http://localhost:8080/healthz --send

# Xem state dedup
python3 scripts/monitor_alert.py status
```

## An toàn

- **Dry-run mặc định**: không gửi gì trừ khi `--send` **và** có `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`.
  `send_telegram` tự no-op khi thiếu creds.
- Owned-only · không dump-login · không tự đăng nhập · report mask-only.
- State file `chmod 600`, gitignored.
