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
