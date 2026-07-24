# Docker Lab — môi trường tách biệt

Phân tích tĩnh mẫu đáng ngờ **ngoài host**. Không thực thi mẫu.

## Chạy

```bash
mkdir -p quarantine reports
# đặt file nghi ngờ vào quarantine/  (không mở / không chạy trên máy chính)
docker compose -f docker/lab/docker-compose.yml build
docker compose -f docker/lab/docker-compose.yml run --rm lab analyze /quarantine/<file>
```

Báo cáo JSON ghi vào `reports/`.

## Bảo đảm cô lập

| Kiểm soát | Giá trị |
|-----------|---------|
| Network | `network_mode: none` |
| Filesystem | `read_only: true` + tmpfs `/tmp` |
| Caps | `cap_drop: ALL` |
| Privileges | `no-new-privileges` |
| User | uid `10001` |
| Samples | mount **ro** `/quarantine` |

## Lệnh

- `analyze <path>` — static heuristics + hash + entropy
- `list` / `wipe` / `shell` / `help`

Không cài Metasploit, không generate payload, không reverse-shell helper.

Mapper taxonomy (host, phòng thủ):

```bash
python3 scripts/metasploit_suite_mapper.py
python3 scripts/metasploit_library_harvest.py   # full library knowledge (readonly)
```
