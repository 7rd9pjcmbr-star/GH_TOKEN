# Nginx embed: rà soát ĐẦY ĐỦ token/đơn → điền secrets (không bỏ sót)

```bash
python3 scripts/nginx_embed_order_secrets_fill.py
```

Luồng: **mọi file inbox → classify → nginx `/v1/owned/fill` → `secrets/backend_pipes.env`**

Báo cáo liệt kê **từng file** (owned scanned / dump blocked). `missed_files` phải = 0.

## Điền từ owned exports

| Key | Nguồn |
|-----|--------|
| `PANCAKE_SHOP_ID` / `PANCAKE_SECONDARY_SHOP_IDS` | orders_detailed_* |
| `PANCAKE_PAGE_ID` / `PANCAKE_WAREHOUSE_ID` | orders_detailed_*.json |
| `ORDER_API_HOSTS` / `ORDER_PLATFORMS_SEEN` / `ORDER_SOURCES_SEEN` | json+csv+xlsx |
| `ORDER_TOKEN_SOURCE_LABELS` | label nguồn (không phải token) |
| `SPX_SHOP_ID` / `SPX_USER` / `SPX_SENDER_NAME` / `SPX_3PL` | thanhcoong.xlsx |

## Chặn dump (không điền)

Acc_all · stealer · ghn_tokens · results_cookies · valid_accounts · vnpost_ok (user:pass) · internal_search

Export đơn **không** có `access_token`/`api_key` — cần `access_token_rotate.py set --token` sở hữu.

Panel: **📥 Embed·fill secrets**
