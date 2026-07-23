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
# Hỗ trợ giải mã unmask (phân loại MASK/ENCODING/AEAD + atlas + ASUNMEE):
python3 scripts/crypto_decode_assist.py --unmask
python3 scripts/crypto_decode_assist.py --unmask --text '+84335****64' --text 'MDk3OTI2MzQ2Mw=='
# AES-GCM khi có key owned:
python3 scripts/crypto_decode_assist.py --aes-gcm KEY_B64 NONCE_B64 CT_B64 --aad oms:customer_phone
python3 scripts/crypto_decode_assist.py --unmask --aes-gcm KEY_B64 NONCE_B64 CT_B64
# Frida a11y offline AES (mapper-icon-aes-v1):
python3 scripts/crypto_decode_assist.py --frida-aes path/to/frida-a11y-offline-aes-*.json --key-b64 KEY_B64
# hoặc điền MAPPER_AES_KEY_B64 / MAPPER_ICON_AES_KEY_B64 vào secrets/backend_pipes.env rồi:
python3 scripts/crypto_decode_assist.py --frida-aes path/to/bundle.json
# ASUNMEE mask assist:
python3 scripts/crypto_decode_assist.py --asunmee --live
```

Panel: **Hỗ trợ giải mã** (`--unmask`). Báo cáo: `reports/telegram-classify/unmask_decode_assist.txt`.
Plaintext Frida (khi có key): `reports/telegram-classify/frida_a11y_aes_plaintext.json`.

> Outer AES unwrap ≠ unmask Pancake PII — inner envelope vẫn có thể giữ `****`.

### Ánh xạ giải mã × icon · mọi kho + bưu cục

Kết hợp decode assist với mapper icon phản hồi trên toàn bộ kho/bưu cục vận chuyển:

```bash
python3 scripts/decode_icon_logistics_mapper.py
```

Panel: **🗺 Giải mã×icon**. Báo cáo: `reports/telegram-classify/decode_icon_logistics_mapper.txt`.

### Tra cứu unmask × redaction × CRYPTO_ATLAS

Mapper ánh xạ MASK/encoding/AEAD → action + thư viện mật mã học:

```bash
python3 scripts/unmask_redaction_crypto_mapper.py
python3 scripts/unmask_redaction_crypto_mapper.py --sample '+84335****64' --lookup 'unmask pii'
```

UI: `MaMoCrypto.recommend("unmask redaction")` · `MaMoCrypto.lookup("encryption-vs-encoding")`.
Báo cáo: `reports/telegram-classify/unmask_redaction_crypto_mapper.txt`.

### Mapper truy vấn sâu lớp bên trong (L0→L5)

Phân tích toàn diện envelope sau AES + ánh xạ path unmask:

```bash
python3 scripts/inner_unmask_deep_mapper.py
python3 scripts/inner_unmask_deep_mapper.py --bundle path/to/frida-a11y-offline-aes-*.json
```

Panel: **🧊 Inner·unmask sâu**. Báo cáo: `reports/telegram-classify/inner_unmask_deep_mapper.txt`.
