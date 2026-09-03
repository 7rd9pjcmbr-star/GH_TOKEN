# Session Store — lưu token + cookie session & duy trì

Module: `scripts/session_store.py`
Chỉ credential **sở hữu**. Không dump-login, không tự đăng nhập. Report **mask-only**.

Lưu **đồng thời** token (Bearer / API key) **và** cookie session cho từng nền tảng, rồi
**duy trì** (kiểm tra hạn + refresh token owned + probe giữ ấm) — bổ sung cho
`order_session_env.py` (chỉ token) bằng một kho cookie bền vững.

## Lưu trữ

- `secrets/session_store.json` (gitignored, `chmod 600`).
- Override bằng `SESSION_STORE_PATH` (dùng cho test/CI).
- Mỗi nền tảng: `{ tokens: {KEY: value}, cookies: [{name,value,domain,path,expires,…}], meta }`.

## CLI

```bash
# Lưu token owned từ môi trường (không lộ trên dòng lệnh)
python3 scripts/session_store.py set --platform Pancake --from-env

# Hoặc chỉ định trực tiếp
python3 scripts/session_store.py set --platform GHN --token GHN_API_TOKEN=...

# Nạp cookie session từ storage_state (Playwright) — tái dùng phiên đã có
python3 scripts/session_store.py import-state --platform Pancake --file pancake_storage_state.json

# Trạng thái (mask-only): token/cookie + hạn (JWT exp, cookie expires)
python3 scripts/session_store.py status

# Áp token → os.environ (cho script khác dùng)
python3 scripts/session_store.py apply

# Duy trì 1 lần: refresh token owned + cập nhật last_ok (thêm --probe để giữ ấm)
python3 scripts/session_store.py ensure

# Duy trì liên tục
python3 scripts/session_store.py daemon --interval 300

# Xuất Cookie header để tái dùng (mặc định ghi ra file 600; --stdout để in)
python3 scripts/session_store.py export --platform Pancake --domain pancake.vn
```

## API (dùng trong code)

```python
import session_store as ss
ss.set_session("Pancake", tokens={"PANCAKE_POS_ACCESS_TOKEN": tok}, cookies=[...])
ss.apply_to_env()                       # token → os.environ
hdr = ss.cookie_header("Pancake", domain="pancake.vn")   # "k=v; k2=v2"
rep = ss.status_report()                # mask-only, có seconds_left + status
ss.keepalive(refresh=True, probe=False) # duy trì (không login)
```

## Trạng thái hạn

| status | Ý nghĩa |
|--------|---------|
| `ok` | Còn hạn (JWT exp còn > ngưỡng, mặc định 1h) |
| `expiring` | Sắp hết hạn (≤ ngưỡng) — nên refresh |
| `expired` | Đã hết hạn |
| `session` | Cookie phiên (không có expiry) |
| `unknown` | Không đọc được exp (vd api_key/base_url) |

## An toàn

- `secrets/` gitignored · file store `chmod 600`.
- **Không** in raw token/cookie trong `status`/report (chỉ `mask_secret`).
- `export` mặc định ghi file 600; chỉ in stdout khi `--stdout` (opt-in).
- **Không** đăng nhập tự động; chỉ tái sử dụng token/cookie **sở hữu** đã cung cấp.
- Refresh token owned uỷ quyền cho `access_token_rotate.ensure_tokens` (vd ViettelPost login owned).
