# Realtime nâng cao — async adaptive order engine

Module: `scripts/realtime_advanced.py`
Owned-only · async · mask-only. Nâng cấp `realtime_order_sync` (poll tuần tự, sync)
thành engine bất đồng bộ, đa nguồn, chu kỳ thích ứng.

## Nâng cấp so với realtime_order_sync

| | realtime_order_sync | realtime_advanced |
|---|---|---|
| Concurrency | tuần tự (sync) | **đồng thời** (asyncio.gather nhiều source) |
| Loop | `time.sleep` cố định | **asyncio** + **chu kỳ thích ứng** |
| Interval | cố định (`--interval`) | nhanh khi có đơn · giãn khi rảnh · **backoff** khi lỗi |
| Dedup | fingerprint (state) | fingerprint (tái dùng cùng hàm) |
| Hooks | notify | session_store keepalive · account_pool · monitor (soft) |

## Chu kỳ thích ứng (`next_interval`)

- **Có đơn mới** → về `min_interval` (mặc định 5s) để bắt nhịp.
- **Rảnh** → về `base_interval` (30s), rồi giãn dần ×1.5 tới `max_interval` (300s).
- **Lỗi** → backoff ×2 (theo error streak), chặn trần `max_interval`.

Ví dụ thực tế: `NEW→5s · idle→30s · ERROR→60s · idle→90s`.

## Nguồn (source)

- Mặc định: bọc `realtime_order_sync.run_cycle` (đa nền tảng, đã kiểm chứng) qua
  `asyncio.to_thread` — không chặn event loop.
- Có thể inject `sources=[async_callable, ...]` (mỗi cái trả `{"orders": [(backend, order), ...]}`)
  để chạy nhiều nguồn/nhiều tài khoản đồng thời, hoặc để test.

## CLI

```bash
python3 scripts/realtime_advanced.py once                 # 1 tick
python3 scripts/realtime_advanced.py run                  # loop async thích ứng
python3 scripts/realtime_advanced.py run --ensure-sessions --notify \
    --min-interval 5 --base-interval 30 --max-interval 300
python3 scripts/realtime_advanced.py status               # state + fingerprints
```

## An toàn

- State: `secrets/realtime_advanced.state.json` (gitignored, `chmod 600`; override `REALTIME_ADV_STATE_PATH`).
- Owned-only · không dump-login · không tự đăng nhập · mask-only.
- Hooks `session_store` / `account_pool` / `monitor_alert` là **soft-import** — bỏ qua nếu chưa có.
