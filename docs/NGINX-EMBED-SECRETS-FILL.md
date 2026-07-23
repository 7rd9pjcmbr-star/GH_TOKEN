# Nginx embed: rà soát token/đơn → điền secrets

```bash
python3 scripts/nginx_embed_order_secrets_fill.py
```

Luồng: **audit → classify owned/dump → nginx `/v1/owned/fill` → `secrets/backend_pipes.env`**

## Điền được (owned exports)

| Nguồn | Giá trị |
|-------|---------|
| `orders_detailed_*.json` | `PANCAKE_SHOP_ID`, `PANCAKE_SECONDARY_SHOP_IDS`, `PANCAKE_PAGE_ID` |
| `thanhcoong.xlsx` | `SPX_SHOP_ID` |

Export đơn **không** có `access_token`/`api_key` thật.

## Không điền (dump)

`Acc_all` · `stealer_*` · `ghn_tokens` · `results_cookies` · `internal_search_*` · `valid_accounts`

Panel: **📥 Embed·fill secrets**

Token API sở hữu vẫn cần `access_token_rotate.py set --platform … --token …` (qua nginx).
