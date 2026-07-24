# Docker Lab — MaMoLab v2

Phân tích tĩnh mẫu đáng ngờ **ngoài host**. Không thực thi mẫu.

## Host lab (không cần Docker)

```bash
python3 scripts/lab_control.py upgrade
python3 scripts/lab_control.py status
python3 scripts/lab_control.py analyze
python3 scripts/lab_control.py validate
```

## Docker (khi có docker)

```bash
mkdir -p quarantine reports
docker compose -f docker/lab/docker-compose.yml build
docker compose -f docker/lab/docker-compose.yml run --rm lab analyze /quarantine/lab/<file>
```

Báo cáo: `reports/lab/`.

## Cô lập

`network_mode: none` · `read_only` · `cap_drop: ALL` · uid 10001 · engine v2.

Không Metasploit / payload / reverse-shell helper.
