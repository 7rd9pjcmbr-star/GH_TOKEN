# Đổi access token → gọi đơn realtime (qua nginx)

Module: `scripts/access_token_rotate.py`  
Gateway: `docker/nginx-order` · `scripts/nginx_order_embed.py`  
Chỉ credential **sở hữu**. Không dump-login / Acc_all.

## Luồng bắt buộc

```text
client
  → nginx:18080  ($upstream_* nhúng header X-Upstream-*)
    → upstream:18081
      → access_token_rotate (set/ensure/refresh)
      → realtime_order_sync (danh sách đơn)
```

| Path nginx | Việc |
|------------|------|
| `POST /v1/token/set` | Nạp token sở hữu vào secrets |
| `POST /v1/token/refresh` | ViettelPost Login owned |
| `POST /v1/token/ensure` | Probe + auto-refresh VTP |
| `GET  /v1/token/status` | Trạng thái token |
| `POST /v1/orders/realtime` | ensure → danh sách đơn realtime |
| `GET  /orders` | Đơn mock local (kiểm thử embed) |

## CLI

```bash
# Pipeline đầy đủ (mặc định qua nginx)
python3 scripts/access_token_rotate.py apply-realtime
python3 scripts/nginx_order_embed.py token-realtime

# Nạp token sở hữu qua nginx
python3 scripts/access_token_rotate.py set --platform GHN --token YOUR_TOKEN

# Ensure / refresh qua nginx
python3 scripts/access_token_rotate.py ensure
python3 scripts/access_token_rotate.py refresh --platform ViettelPost

# Debug nội bộ (bỏ nginx) — không dùng production path
python3 scripts/access_token_rotate.py apply-realtime --direct
```

## Env

```env
VIETTELPOST_USER=
VIETTELPOST_PASSWORD=
VIETTELPOST_TOKEN=
GHN_API_TOKEN=
PANCAKE_POS_ACCESS_TOKEN=
TPOS_ACCESS_TOKEN=
TPOS_BASE_URL=
```

Panel: **🔑 Token·realtime** (qua nginx).

## Safety

`secrets/` gitignored · token mask · owned-only · no dump-login · via nginx required.
