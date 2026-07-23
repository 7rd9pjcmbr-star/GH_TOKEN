# Đổi access token → gọi đơn realtime

Module: `scripts/access_token_rotate.py`  
Chỉ credential **sở hữu**. Không dump-login / Acc_all.

## CLI

```bash
# Trạng thái token trong secrets/backend_pipes.env
python3 scripts/access_token_rotate.py status

# Ghi token sở hữu (GHN / Pancake / TPOS / Sapo / …)
python3 scripts/access_token_rotate.py set --platform GHN --token YOUR_TOKEN [--user …] [--shop-id …]

# ViettelPost: Login(+ownerconnect) bằng USER/PASSWORD sở hữu → ghi VIETTELPOST_TOKEN
python3 scripts/access_token_rotate.py refresh --platform ViettelPost

# Probe; auto-refresh VTP khi auth_fail / missing
python3 scripts/access_token_rotate.py ensure

# ensure → realtime_order_sync một vòng
python3 scripts/access_token_rotate.py apply-realtime [--limit 20] [--notify]
```

## Env cần có

```env
# ViettelPost — refresh tự động
VIETTELPOST_USER=
VIETTELPOST_PASSWORD=
VIETTELPOST_TOKEN=

# Các platform khác — set thủ công từ dashboard sở hữu
GHN_API_TOKEN=
PANCAKE_POS_ACCESS_TOKEN=
# hoặc PANCAKE_POS_API_KEY=
TPOS_ACCESS_TOKEN=
TPOS_BASE_URL=
```

## Luồng realtime

1. `set` / `refresh` → `secrets/backend_pipes.env` (+ `secrets/access_tokens.state.json`)
2. `ensure` probe Pancake/GHN/VTP/TPOS; VTP tự Login khi thiếu/auth_fail
3. `realtime_order_sync.run_cycle` gọi `ensure_tokens` trước mỗi vòng sync
4. Panel Telegram: **🔑 Token·realtime** (`q:token_rotate`) → `apply-realtime`

Báo cáo: `reports/telegram-classify/access_token_rotate.{json,txt}`

## Safety

`secrets/` gitignored · token mask trong báo cáo · owned-only · no dump-login.
