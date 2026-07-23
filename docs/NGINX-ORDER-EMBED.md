# Module nhúng gọi đơn qua nginx (on-demand)

Chạy **khi cần** — không giữ nginx/mock suốt ngày trừ khi `start`.

## Module

| Lớp | Path |
|-----|------|
| Python | `scripts/nginx_order_embed.py` → `NginxOrderEmbed` · `run_when_needed()` |
| JS | `js/logic/nginx_embed.js` → `MaMoLogic.nginxEmbed` |
| Test | `scripts/nginx_order_embed_test.py` |
| Conf | `docker/nginx-order/nginx.conf` + `server.conf` |
| Mock | `docker/nginx-order/mock_orders.py` |

## Khi cần

```bash
# một lần: bật → gọi /orders → tắt
python3 scripts/nginx_order_embed.py once

# giữ sống để gọi nhiều lần
python3 scripts/nginx_order_embed.py start
python3 scripts/nginx_order_embed.py orders
python3 scripts/nginx_order_embed.py order --id OMS-NGX-001
python3 scripts/nginx_order_embed.py status
python3 scripts/nginx_order_embed.py stop
```

```python
from nginx_order_embed import NginxOrderEmbed, run_when_needed

run_when_needed()                         # once
m = NginxOrderEmbed()
m.ensure_up(); m.call_orders(); m.stop()  # chủ động
```

```js
MaMoLogic.nginxEmbed.describe()
MaMoLogic.nginxEmbed.runWhenNeeded()  // cần stack đã start
MaMoLogic.nginxEmbed.callOrders()
```

Panel Telegram: **🧪 Nginx·gọi đơn** → `run_when_needed()`.

## Luồng

```text
client → nginx:18080/orders → upstream mock:18081
```

Biến nhúng `$upstream_*` → header `X-Upstream-*` + `logs/order_access.log`.

## Safety

Local mock only · no dump-login · no third-party order API.
