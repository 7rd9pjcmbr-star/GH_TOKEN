# Ngăn bí mật (secrets)

Token / API key **không** đưa vào Git hay chat.

## Telegram

1. Mở file local: `secrets/telegram.env` (đã tạo, chmod 600).
2. Điền từ [@BotFather](https://t.me/BotFather):

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_BOT_USERNAME=TondaithanhBot
TELEGRAM_CHAT_ID=
```

3. Hoặc sao chép mẫu: `cp .env.example .env` rồi điền.

Thư mục `secrets/` và file `.env` đã nằm trong `.gitignore`.

## Kiểm tra (local)

```bash
set -a && source secrets/telegram.env && set +a
curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe"
```

Token cũ đã lộ trong chat → `/revoke` trên BotFather rồi dùng token mới.

## Backend pipes / OMS

```bash
cp backend_pipes.env.example secrets/backend_pipes.env
# điền PANCAKE_* · GHN_API_TOKEN · VIETTELPOST_* · TPOS_* (owned only)

# Rà soát + gom + duy trì phiên lấy đơn/login
python3 scripts/order_session_env.py audit
python3 scripts/order_session_env.py export   # → secrets/order_session.env
python3 scripts/order_session_env.py ensure
```

Mẫu tên biến: `order_session.env.example` · docs: `docs/ORDER-SESSION-ENV.md`

Không dán credential từ dump `Acc_all` / `Ghn.txt`.
