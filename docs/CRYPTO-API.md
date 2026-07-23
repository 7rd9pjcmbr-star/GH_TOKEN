# MaMoCrypto — thư viện mật mã học thông minh

9 module + API tra cứu tích hợp (`window.MaMoCrypto`).

## Module

| # | File | Vai trò |
|---|------|---------|
| 1 | `core.js` | Registry + event bus |
| 2 | `catalog.js` | Chỉ mục CRYPTO_ATLAS |
| 3 | `search.js` | Search thông minh + synonym |
| 4 | `concepts.js` | API khái niệm |
| 5 | `libraries.js` | API thư viện theo ngôn ngữ/tier |
| 6 | `recommend.js` | Gợi ý theo nhu cầu |
| 7 | `graph.js` | Quan hệ network / path |
| 8 | `encode.js` | Morse/Braille/Base64 (biểu diễn) |
| 9 | `api.js` | Facade tích hợp |

## API nhanh

```js
MaMoCrypto.lookup("AES-GCM")
MaMoCrypto.search("password", { kind: "concept" })
MaMoCrypto.suggest("libso")
MaMoCrypto.getLibrary("libsodium")
MaMoCrypto.listLibraries({ language: "Python", tier: "khuyến nghị" })
MaMoCrypto.recommend("browser encryption")
MaMoCrypto.recommend({ need: "mat khau", language: "Go" })
MaMoCrypto.related("aead")
MaMoCrypto.path("aes-gcm", "openssl")
MaMoCrypto.encode.toMorse("hello")
MaMoCrypto.stats()
MaMoCrypto.describe()
```

## Tích hợp trang

```html
<script src="/data/crypto-atlas.js"></script>
<script src="/data/network-map.js"></script>
<script src="/js/crypto/core.js"></script>
<!-- … catalog → search → concepts → libraries → recommend → graph → encode → api -->
<script src="/js/crypto/bootstrap.js"></script>
```

## Lưu ý

- Tài liệu **giáo dục** — không kèm exploit.
- `encode.*` chỉ biểu diễn (Morse/Braille/Base64), không phải encryption bảo mật.

## Module hỗ trợ giải mã (ops)

Python parity + AEAD decrypt (owned key):

```bash
python3 scripts/crypto_decode_assist.py
python3 scripts/crypto_decode_assist.py --text 'MDk3OTI2MzQ2Mw=='
# AES-GCM khi có key owned:
python3 scripts/crypto_decode_assist.py --aes-gcm KEY_B64 NONCE_B64 CT_B64 --aad oms:customer_phone
# Frida a11y offline AES (mapper-icon-aes-v1):
python3 scripts/crypto_decode_assist.py --frida-aes path/to/frida-a11y-offline-aes-*.json --key-b64 KEY_B64
# hoặc điền MAPPER_ICON_AES_KEY_B64 vào secrets/backend_pipes.env rồi:
python3 scripts/crypto_decode_assist.py --frida-aes path/to/bundle.json
```

Panel: **Hỗ trợ giải mã**. Báo cáo: `reports/telegram-classify/crypto_decode_assist.txt`.
Plaintext Frida (khi có key): `reports/telegram-classify/frida_a11y_aes_plaintext.json`.

### Ánh xạ giải mã × icon · mọi kho + bưu cục

Kết hợp decode assist với mapper icon phản hồi trên toàn bộ kho/bưu cục vận chuyển:

```bash
python3 scripts/decode_icon_logistics_mapper.py
```

Panel: **🗺 Giải mã×icon**. Báo cáo: `reports/telegram-classify/decode_icon_logistics_mapper.txt`.
