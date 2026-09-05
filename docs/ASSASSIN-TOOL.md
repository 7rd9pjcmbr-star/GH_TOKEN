# AssassinTool

Phân tích file **report / assassin / final_report** trong `quarantine/telegram` (và `_skipped_dumps/`).

## Mục tiêu

| Mục tiêu | Chi tiết |
|----------|----------|
| Tập trung | File có tên chứa `assassin`, `report`, `final_report` |
| Giữ tín hiệu lấy đơn | URL, host, user, shop, platform, tracking |
| An toàn | Che password · không dump-login · không Acc_all mass-login |
| Tích hợp | CLI · Telegram panel · MCP (Cursor) |

## CLI

```bash
python3 scripts/assassin_tool.py
python3 scripts/assassin_tool.py --all
python3 scripts/assassin_tool.py quarantine/telegram/my_report.txt --json
```

Báo cáo: `reports/telegram-classify/assassin_tool.{json,txt}`

## Telegram panel

Nút **🗡 Assassin·report** trên `telegram_control_panel.py`.

## MCP (Cursor)

Cấu hình: `.cursor/mcp.json`

```bash
pip install 'mcp[cli]'
python3 scripts/assassin_tool_mcp.py
```

Tools:

- `scan_assassin_reports` — quét và tóm tắt
- `analyze_assassin_file_tool` — phân tích một file
- `list_assassin_candidates` — liệt kê ứng viên

## Liên quan

- `scripts/telegram_inbox_scan_analyze.py` — quét inbox đầy đủ
- `scripts/order_signal_extract.py` — trích tín hiệu lấy đơn
- `docs/TELEGRAM-CLASSIFY.md` — phân loại inbox
