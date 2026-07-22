# Mã Mở

Hệ thống kép:

1. **Accessibility module** (`js/a11y/`) — hỗ trợ đặc biệt một công tắc  
2. **MaMoCrypto** (`js/crypto/`) — thư viện mật mã học thông minh + **API tra cứu**

## MaMoCrypto — 9 module + API

```
core · catalog · search · concepts · libraries
recommend · graph · encode · api
```

```js
MaMoCrypto.lookup("AES-GCM")
MaMoCrypto.search("password")
MaMoCrypto.recommend({ need: "browser", language: "JavaScript" })
MaMoCrypto.getLibrary("libsodium")
MaMoCrypto.path("aes-gcm", "openssl")
```

Tài liệu: [`docs/CRYPTO-API.md`](docs/CRYPTO-API.md) · Atlas UI có tab **API tra cứu**.

## Chạy

```bash
python3 -m http.server 8080
```

- `/` — hỗ trợ đặc biệt  
- `/atlas/` — MaMoCrypto Atlas + API playground  
- `/mapper/` — network mapper  

## Accessibility

Xem [`docs/A11Y-ARCHITECTURE.md`](docs/A11Y-ARCHITECTURE.md).
