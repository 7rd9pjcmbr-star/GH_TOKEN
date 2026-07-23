# Module nhúng gọi đơn + token qua nginx (on-demand)

Chạy **khi cần** — không giữ nginx/mock suốt ngày trừ khi `start`.

Mọi thao tác **đổi access token / gọi danh sách đơn realtime** đi qua nginx trước khi nạp module.

## Module

| Lớp | Path |
|-----|------|
| Python | `scripts/nginx_order_embed.py` → `NginxOrderEmbed` · `token_realtime_pipeline()` |
| Token | `scripts/access_token_rotate.py` (mặc định `via_nginx`) |
| JS | `js/logic/nginx_embed.js` → `MaMoLogic.nginxEmbed` |
| Conf | `docker/nginx-order/nginx.conf` + `server.conf` |
| Upstream | `docker/nginx-order/mock_orders.py` (token + orders) |

## Khi cần

```bash
# Pipeline: nginx → token module → danh sách đơn RT
python3 scripts/nginx_order_embed.py token-realtime
python3 scripts/access_token_rotate.py apply-realtime

# một lần mock /orders
python3 scripts/nginx_order_embed.py once

# giữ sống
python3 scripts/nginx_order_embed.py start
python3 scripts/nginx_order_embed.py orders
python3 scripts/nginx_order_embed.py stop
```

```python
from nginx_order_embed import NginxOrderEmbed
NginxOrderEmbed().token_realtime_pipeline()
```

Panel: **🧪 Nginx·gọi đơn** · **🔑 Token·realtime**

## Luồng

```text
client → nginx:18080
  /v1/token/*                  → upstream → access_token_rotate
  /v1/orders/realtime          → upstream → access_token_rotate → realtime_order_sync
  /v1/ghn/token-proxy-orders   → upstream → token_proxy_bind → GHN orders (1 proxy/token)
  /orders                      → upstream mock list
```

## Token ↔ proxy → gọi đơn

Repo hiện **không có** list egress proxy sẵn. Điền `secrets/proxies.owned.txt` (mẫu: `config/proxies.owned.example`).

```bash
python3 scripts/token_proxy_bind.py scan
python3 scripts/token_proxy_bind.py bind
python3 scripts/token_proxy_bind.py nginx-orders --limit-tokens 10
# hoặc
python3 scripts/nginx_order_embed.py ghn-token-proxy-orders --keep
```

Biến nhúng `$upstream_*` → header `X-Upstream-*` + `logs/order_access.log`.

## Safety

Local upstream · owned-only token · no dump-login · no third-party credential dumps.
