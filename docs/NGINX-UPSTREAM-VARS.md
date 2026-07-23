# Biến nhúng & chỉ thị nginx — `ngx_http_upstream_module`

Catalog: `data/nginx-upstream-vars.js` · Module: `js/logic/vars.js` · version **1.1.0**

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

## Các biến được nhúng

Module `ngx_http_upstream_module` hỗ trợ các biến nhúng sau. Nhiều kết nối: phân tách bằng **dấu phẩy**; chuyển hướng nội bộ giữa nhóm (`X-Accel-Redirect` / `error_page`): nhóm phân tách bằng **dấu hai chấm** (giống mẫu của `$upstream_addr`).

15 biến — `MaMoLogic.vars.all()`. Hai biến **commercial**: `$upstream_last_addr`, `$upstream_last_server_name`.

| Biến | Since | Mô tả |
|------|-------|--------|
| `$upstream_addr` | — | IP:port hoặc đường dẫn UNIX socket của upstream. Nhiều server: phẩy. Nhiều nhóm (redirect nội bộ): hai chấm. Không chọn được server → tên nhóm. |
| `$upstream_bytes_received` | 1.11.4 | Số byte nhận từ upstream. |
| `$upstream_bytes_sent` | 1.15.8 | Số byte gửi tới upstream. |
| `$upstream_cache_status` | 0.8.3 | `MISS` · `BYPASS` · `EXPIRED` · `STALE` · `UPDATING` · `REVALIDATED` · `HIT` |
| `$upstream_connect_time` | 1.9.1 | Thời gian thiết lập kết nối (giây, ms). SSL gồm handshake. |
| `$upstream_cookie_name` | 1.7.1 | Cookie tên `name` từ `Set-Cookie` của **server cuối**. |
| `$upstream_header_time` | 1.7.10 | Thời gian nhận header phản hồi (giây, ms). |
| `$upstream_http_name` | — | Header phản hồi upstream (quy tắc như `$http_*`). Chỉ server cuối. VD: `Server` → `$upstream_http_server`. |
| `$upstream_last_addr` | 1.29.3 | **Commercial** — IP/UNIX socket của upstream được chọn cuối. |
| `$upstream_last_server_name` | 1.25.3 | **Commercial** — tên upstream cuối; dùng SNI: `proxy_ssl_server_name on; proxy_ssl_name $upstream_last_server_name;` |
| `$upstream_queue_time` | 1.13.9 | Thời gian request nằm trong hàng đợi upstream (giây, ms). |
| `$upstream_response_length` | 0.7.27 | Độ dài phản hồi từ upstream (byte). |
| `$upstream_response_time` | — | Thời gian nhận toàn bộ phản hồi (giây, ms). |
| `$upstream_status` | — | Mã trạng thái từ upstream. Không chọn được server → **502**. |
| `$upstream_trailer_name` | 1.13.10 | Trường trailer cuối phản hồi upstream. |

### Ví dụ `$upstream_addr`

```text
192.168.1.1:80, 192.168.1.2:80, unix:/tmp/sock
192.168.1.1:80, 192.168.1.2:80, unix:/tmp/sock : 192.168.10.1:80, 192.168.10.2:80
```

### Gợi ý `log_format`

```nginx
log_format upstream_debug '$remote_addr - $request '
    'upstream=$upstream_addr status=$upstream_status '
    'rt=$upstream_response_time uct=$upstream_connect_time '
    'uht=$upstream_header_time cache=$upstream_cache_status';
```

UI: `/logic-view/` → panel nginx upstream.
