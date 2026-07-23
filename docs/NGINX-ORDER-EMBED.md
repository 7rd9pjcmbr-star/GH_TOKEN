# Kiểm thử nhúng gọi đơn qua nginx

Local mock — không gọi API thật, không dump-login.

## Luồng

```text
client → nginx:18080/orders → upstream order_backend (127.0.0.1:18081)
```

Biến nhúng `ngx_http_upstream_module` ghi vào:
- Response headers `X-Upstream-*`
- `docker/nginx-order/logs/order_access.log` (`log_format order_upstream`)

## Chạy

```bash
python3 scripts/nginx_order_embed_test.py
# hoặc panel Telegram: 🧪 Nginx·gọi đơn
```

## File

| Path | Vai trò |
|------|---------|
| `docker/nginx-order/nginx.conf` | upstream + log_format `$upstream_*` |
| `docker/nginx-order/server.conf` | `/orders` proxy + `add_header` |
| `docker/nginx-order/mock_orders.py` | mock order API |
| `scripts/nginx_order_embed_test.py` | orchestrate + assert |
| `data/nginx-upstream-vars.js` | catalog biến nhúng |

## Biến đã nhúng trong test

`$upstream_addr` · `$upstream_status` · `$upstream_response_time` · `$upstream_connect_time` · `$upstream_header_time` · `$upstream_bytes_received` · `$upstream_bytes_sent` · `$upstream_response_length` · `$upstream_cache_status`

```js
MaMoLogic.vars.get("$upstream_addr")
```
