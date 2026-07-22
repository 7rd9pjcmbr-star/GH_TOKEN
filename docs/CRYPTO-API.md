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
