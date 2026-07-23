# Rà soát & duy trì phiên đăng nhập / lấy đơn

Module: `scripts/order_session_env.py`  
Chỉ credential **sở hữu**. Không dump-login. Báo cáo chỉ hiện token **mask**.

## Mục tiêu

1. Rà soát mọi key liên quan **lấy đơn** + **login/session**
2. Gom vào `secrets/order_session.env` (gitignored, chmod 600)
3. Nạp vào `os.environ` + `ensure` token để **duy trì phiên**

## Nhóm key

| Nhóm | Ví dụ | Vai trò |
|------|--------|---------|
| `pancake_login_session` | `PANCAKE_POS_ACCESS_TOKEN`, secondary, USER, UID | Bearer phiên đăng nhập |
| `pancake_order_api` | `PANCAKE_POS_API_KEY`, `SHOP_ID`, page/warehouse | Gọi đơn shop |
| `ghn` / `viettelpost_login` / … | `*_TOKEN`, `*_PASSWORD` | 3PL / OMS khác |
| `crypto_session` | `MAPPER_*_AES_KEY_B64` | Frida AES (≠ unmask `****`) |
| `telegram` | `TELEGRAM_BOT_*` | Panel / notify |

## CLI

```bash
# Rà soát (mask only)
python3 scripts/order_session_env.py audit

# Gom → secrets/order_session.env + state
python3 scripts/order_session_env.py export

# Duy trì phiên: export → apply environ → ensure tokens → keepalive
python3 scripts/order_session_env.py ensure

# Trạng thái
python3 scripts/order_session_env.py status

# Nạp shell (sau export)
set -a && source secrets/order_session.env && set +a
```

Mẫu tên biến (không có secret): `order_session.env.example`

## Order Pipe

```bash
PYTHONPATH=scripts python3 -m order_pipe --session-audit
PYTHONPATH=scripts python3 -m order_pipe --session-ensure
PYTHONPATH=scripts python3 -m order_pipe --fetch-orders --limit 80
```

## Safety

- `secrets/` gitignored  
- Không in raw JWT/API key trong report  
- Không lấy cookie/token từ Acc_all / stealer  
- `api_key` Pancake: PII vẫn MASK — Bearer session cũng không tự unmask `****`

## GHN session / cookie

Module: `scripts/ghn_cookie_ingest.py` — lấy **API Token** từ:

- URL `printA5?token=<uuid>`
- Cookie Netscape `*.ghn.vn` tên `token` / `access_token` / …
- `GHN_API_TOKEN=<uuid>`

Từ chối `hjSession*`, `_ga*`, analytics (không phải Token API).

```bash
python3 scripts/ghn_cookie_ingest.py --raw 'https://online-gateway.ghn.vn/a5/public-api/printA5?token=<uuid>'
python3 scripts/nginx_order_embed.py ghn-ingest --raw-file FILE --keep
python3 scripts/order_session_env.py ensure
```

Chỉ ghi `GHN_API_TOKEN` khi probe `master-data/province` = 200.
