# Account Pool — hồ chứa tài khoản owned đa nền tảng

Module: `scripts/account_pool.py`
Chỉ tài khoản **sở hữu**. Không dump-login, không tự đăng nhập. Report **mask-only**.

Quản lý một pool các tài khoản owned (theo nền tảng), chọn/luân phiên khi gọi đơn,
theo dõi sức khoẻ (cooldown/disabled). **Không** lưu secret trong pool — credential
nằm ở `owned_credentials` (env/secrets) và `session_store`; pool chỉ giữ metadata sử dụng.

## Nguồn tài khoản

Nạp qua `owned_credentials.owned_accounts()`:
- `OWNED_ACCOUNTS_JSON=[{"platform","user","token","shop_id","label"}, ...]`
- `OWNED_MAP_<PLATFORM>=user|token|shop_id`
- Khoá env theo nền tảng trong `secrets/*.env` (xem `owned_credentials.PLATFORM_SPECS`).

## Lưu trữ

- `secrets/account_pool.json` (gitignored, `chmod 600`) — chỉ metadata:
  `{status, use_count, last_used_at, cooldown_until, last_error}` theo `account_key`.
- Override đường dẫn: `ACCOUNT_POOL_PATH` (dùng cho test).
- `account_key = "<platform>:<label|user|shop_id>"`.

## Chọn tài khoản (strategy)

| strategy | Hành vi |
|----------|---------|
| `lru` (mặc định) | Ưu tiên account lâu chưa dùng nhất (chưa dùng → cao nhất) |
| `least_used` | Ít `use_count` nhất |
| `first` | Account đủ điều kiện đầu tiên |

Account **đủ điều kiện** = `ready` (có token / user+password) · `status=active` · hết cooldown.

## CLI

```bash
python3 scripts/account_pool.py status                       # mask-only
python3 scripts/account_pool.py acquire --platform GHN        # chọn 1 account (LRU)
python3 scripts/account_pool.py acquire --platform Pancake --strategy least_used
python3 scripts/account_pool.py mark-bad --key "GHN:a" --reason "429" --cooldown 600
python3 scripts/account_pool.py disable --key "GHN:a"
python3 scripts/account_pool.py reset --key "GHN:a"           # bỏ --key để reset toàn bộ
```

## API (dùng trong code, kể cả worker async)

```python
import account_pool as ap
got = ap.acquire("GHN", strategy="lru")          # thread-safe
got = await ap.acquire_async("GHN")              # async-safe (asyncio.Lock)
if got:
    acc = got["account"]      # OwnedAccount (có token để gọi API)
    ...                       # dùng acc.token / acc.shop_id
    ap.mark_bad(got["key"], reason="auth_fail")  # cho vào cooldown khi lỗi
rep = ap.status_report()      # mask-only
```

## An toàn

- `secrets/` gitignored · pool state `chmod 600`.
- Pool **không** chứa raw token; `status` chỉ in `mask_secret`.
- Chỉ tài khoản **sở hữu**; không dump/stealer; không tự đăng nhập.
