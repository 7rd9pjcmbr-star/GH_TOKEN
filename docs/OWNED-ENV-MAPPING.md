# Owned user/token → biến môi trường → mapping khi sync/mapper

Chỉ credential **sở hữu**. Không paste Acc_all / stealer dumps.

## File

```bash
cp backend_pipes.env.example secrets/backend_pipes.env
# hoặc
python3 scripts/owned_credentials.py ensure
```

Điền `PLATFORM_USER` / `PLATFORM_TOKEN` / `PLATFORM_SHOP_ID`, ví dụ:

```env
GHN_USER=0901xxxxxxx
GHN_API_TOKEN=your_owned_token
GHN_SHOP_ID=123456

# hoặc một dòng:
OWNED_MAP_GHN=0901xxxxxxx|your_owned_token|123456

# hoặc JSON nhiều account:
# OWNED_ACCOUNTS_JSON=[{"platform":"GHN","user":"...","token":"...","shop_id":"...","label":"kho-hcm"}]
```

## Module

```bash
python3 scripts/owned_credentials.py status
```

```python
from owned_credentials import load_env, owned_map, apply_owned_mapping, tokens_for

env = load_env()                 # nạp secrets + overlay canonical keys
m = owned_map(env)               # { "GHN": [OwnedAccount, ...] }
toks = tokens_for(env, "GHN")
row = apply_owned_mapping(order) # gắn owned_user / owned_ready khi mapper chạy
```

## Script đã nối

- `realtime_order_sync.py` — load env overlay + gắn owned lên đơn mới + `ensure_tokens` trước mỗi vòng
- `access_token_rotate.py` — đổi/refresh token rồi gọi đơn realtime (`docs/ACCESS-TOKEN-ROTATE.md`)
- `oms_interconnect.py` — load env overlay
- `telegram_inbox_today_mapper.py` — apply_owned_mapping từng đơn hôm nay

Panel: **🔐 Owned·env map** · **🔑 Token·realtime**

## Safety

`secrets/` gitignored · token chỉ hiện dạng mask trong báo cáo · no dump-login.
