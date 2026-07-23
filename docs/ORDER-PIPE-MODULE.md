# Module Order Pipe — Truy vấn ngược toàn diện

Thiết kế module chức năng bao quanh dòng chảy **kho → bưu cục → vận đơn → địa chỉ nhận** (ASUMEE / Pancake POS), thay vì chồng thêm “hopN”.

## 1. Mục tiêu

| Mục tiêu | Chi tiết |
|----------|----------|
| API thống nhất | `ReverseFlow` / `ReversePipeline` — gọi theo **capability**, không theo số hop |
| An toàn | Secrets owned only · không dump-login · không bịa timestamp · không unmask `****` |
| Tái sử dụng | Facade trên `order_pipe_reverse_query.py` + `tracking_aship` + pipe DB |
| CLI mỏng | `python3 -m order_pipe` hoặc `scripts/order_pipe_module.py` |

## 2. Kiến trúc tầng

```
┌──────────────────────────────────────────────────────────┐
│  CLI / Telegram panel / agents                           │
├──────────────────────────────────────────────────────────┤
│  order_pipe  (scripts/order_pipe/)                       │
│   ReverseFlow · ReversePipeline · PipeStore · PathId     │
├──────────────┬───────────────┬───────────────┬───────────┤
│  lookup/flow │  enrich/live  │  tracking 3PL │  accept   │
├──────────────┴───────────────┴───────────────┴───────────┤
│  adapters: tracking_aship · crypto_decode · icon feedback│
├──────────────────────────────────────────────────────────┤
│  store: kho_buucuc_pipe.db (orders · fingerprints · events)
└──────────────────────────────────────────────────────────┘
```

## 3. Taxonomy đường (PathId)

| PathId | Ý nghĩa | Hành động |
|--------|---------|-----------|
| `PATH-CLEAR` | Đủ dữ liệu / đã map | Monitor |
| `PATH-WAIT` | Submitted/new chưa `extend_code` | Chờ ship → enrich |
| `PATH-MISSING` | Hard gap (ship/del không timestamp) | Accept · không bịa |
| `PATH-ACCEPT` | Soft gap / commune / canceled | Accept có chủ đích |
| `PATH-MASK-REDACTION` | PII `****` | Không AES-unmask |

## 4. Pipeline stages (capability)

Thứ tự mặc định:

1. **seed** — warehouse / kho / tỉnh / van_tay / buucuc  
2. **deep** — status · ward · gaps cohort · geo · icon  
3. **enrich** — live Pancake detail · timeline · carrier remap (opt-in live/apply)  
4. **tracking** — aship URL sync · 3PL matrix · probe (opt-in)  
5. **pancake_id** — cohort tracking=so · live backfill (opt-in)  
6. **accept** — soft/hard accept · SPX 26* · commune geo  
7. **waiting** — returning/submitted waiting live (opt-in)  
8. **close** — flow closure · PATH-WAIT accept · confirm scan  

Ánh xạ tương thích hop cũ: seed≈1 · deep≈2–5 · enrich≈6–7 · tracking≈8 · pancake_id≈9 · accept≈10–11 · waiting≈12 · close≈13.

## 5. Public API

```python
from order_pipe import ReverseFlow, PipeStore, PathId, StageId

store = PipeStore.open()          # hoặc .ensure()
rf = ReverseFlow(store)

rf.lookup.by_van_tay("…")
rf.lookup.by_tracking("SPXVN…")
rf.flow.panorama(order_row)
rf.flow.completeness(ASUMEE_WID)

report = rf.pipeline.run(
    stages=["seed", "deep", "accept", "close"],
    live=False,
    apply=False,
)
```

## 6. CLI

```bash
# Toàn pipeline (offline an toàn)
python3 -m order_pipe --run

# Chỉ đóng sổ + PATH-WAIT
python3 -m order_pipe --stages close --apply

# Live enrich + waiting (owned key)
python3 -m order_pipe --stages enrich,waiting --live --apply --limit 40

# Lookup nhanh
python3 -m order_pipe --tracking SPXVN067951046107
python3 -m order_pipe --kho ASUMEE

# Tương thích hop cũ
python3 -m order_pipe --legacy-continue-flow --hop13-apply
```

## 7. Policy bất biến

1. Chỉ credential trong `secrets/` (owned).  
2. Không invent `picked_at` / `delivered_at`.  
3. `****` = redaction → không claim decrypt.  
4. Aship HTML HTTP 200 ≠ timeline Pancake.  
5. Reports/DB gitignored.

## 8. File layout

```
scripts/order_pipe/
  __init__.py      # ReverseFlow, exports
  __main__.py      # python -m order_pipe
  constants.py     # ASUMEE_WID, StageId, PathId
  paths.py         # path helpers / labels
  store.py         # PipeStore
  lookup.py        # ReverseLookup facade
  flow.py          # FlowService facade
  pipeline.py      # ReversePipeline
  stages.py        # stage runners
  report.py        # build/write/format
  cli.py           # argparse
  adapters/
    tracking.py
    crypto.py
    icon.py
scripts/order_pipe_module.py   # thin shim
docs/ORDER-PIPE-MODULE.md      # this file
```

## 9. Tiến hóa

- Phase A (hiện tại): facade + stage registry + CLI.  
- Phase B: tách dần logic khỏi monolith `order_pipe_reverse_query.py` vào `stages/*.py`.  
- Phase C: hook Telegram panel / keepalive emit `pipe_events` theo PathId.
