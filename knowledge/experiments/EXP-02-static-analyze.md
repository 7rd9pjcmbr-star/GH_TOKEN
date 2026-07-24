# EXP-02 — Phân tích tĩnh trong quarantine

## Mục tiêu
Quen quy trình Docker lab.

## Bước
1. Tạo file text owned (log/header) vào `quarantine/sample.txt`
2. `docker compose -f docker/lab/docker-compose.yml run --rm lab analyze /quarantine/sample.txt`
3. Đọc JSON trong `reports/`
4. `wipe` / xóa mẫu

## Pass
Có báo cáo hash/entropy/findings; không có network trong container.
