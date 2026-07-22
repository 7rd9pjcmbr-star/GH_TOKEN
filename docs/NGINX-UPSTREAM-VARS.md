# Biến nhúng & chỉ thị nginx — `ngx_http_upstream_module`

Catalog: `data/nginx-upstream-vars.js` · Module: `js/logic/vars.js`

## Tra cứu

```js
MaMoLogic.vars.get("$upstream_addr")
MaMoLogic.vars.get("resolver")
MaMoLogic.vars.getDirective("resolver")
MaMoLogic.vars.search("dns")
MaMoLogic.query("resolver 127.0.0.1 valid=30s;")
```

## Chỉ thị `queue` (context: `upstream`) — **commercial**

| | |
|--|--|
| **Cú pháp** | `queue number [timeout=time];` |
| **Mặc định** | — |
| **Context** | `upstream` |
| **Since** | **1.5.12** (chỉ đăng ký thương mại) |

- Không chọn được upstream ngay → request vào queue (tối đa `number`).
- Queue đầy **hoặc** không chọn được server trong `timeout` (mặc định **60s**) → **502 Bad Gateway**.
- Load-balancing method khác round-robin: **bật method trước** chỉ thị `queue`.
- Quan sát: `$upstream_queue_time`.

```nginx
upstream backend {
    least_conn;          # method ≠ round-robin → khai báo trước
    queue 10 timeout=30s;
}
```

## Chỉ thị `resolver` (context: `upstream`)

| | |
|--|--|
| **Cú pháp** | `resolver address ... [valid=time] [ipv4=on\|off] [ipv6=on\|off] [status_zone=zone];` |
| **Mặc định** | — |
| **Context** | `upstream` |
| **Since** | **1.27.3** (open source). Trước đó (1.17.5–&lt;1.27.3): commercial |

### Tham số

- `address` — nameserver (IP/domain) + cổng tùy chọn (mặc định **53**); round-robin
- `valid=time` — ghi đè TTL cache DNS (vd `valid=30s`)
- `ipv4=on|off` / `ipv6=on|off` — tắt tra IPv4/IPv6 (`ipv4=off` ≥1.23.1)
- `status_zone=zone` — thống kê DNS (**commercial**, ≥1.17.5)

### Ví dụ

```nginx
upstream backend {
    resolver 127.0.0.1 [::1]:5353;
    resolver 127.0.0.1 [::1]:5353 valid=30s;
}
```

### Bảo mật

Dùng DNS nội bộ đáng tin cậy, đã được bảo vệ — giảm rủi ro DNS spoofing.

## Biến nhúng `$upstream_*`

15 biến — `MaMoLogic.vars.all()`. Hai biến commercial: `$upstream_last_addr`, `$upstream_last_server_name`.

UI: `/logic-view/` → panel nginx upstream.
