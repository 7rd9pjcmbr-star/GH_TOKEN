# Mã Mở

Hệ thống kép:

1. **Giải mã hỗ trợ** — Morse, Braille, câu nhanh + TTS cho người khiếm khuyết  
2. **Atlas mật mã học** — bản đồ thư viện crypto và kiến thức mã hoá / giải mã

## Chạy

```bash
python3 -m http.server 8080
```

- Trang hỗ trợ: http://localhost:8080/
- Atlas mật mã: http://localhost:8080/atlas/

## Cấu trúc

| Đường dẫn | Mô tả |
|-----------|--------|
| `index.html` / `app.js` / `styles.css` | Công cụ Morse · Braille · câu nhanh |
| `atlas/` | UI Atlas (kiến thức, thư viện, chọn nhanh) |
| `data/crypto-atlas.js` | Dữ liệu thư viện + khái niệm |
| `docs/CRYPTO-ATLAS.md` | Bản tóm tắt markdown |

## Atlas gồm

- 20+ khái niệm (đối xứng, bất đối xứng, hash/KDF, TLS, PQC…)
- 25+ thư viện (OpenSSL, libsodium, WebCrypto, pyca, Go/Rust/Java/.NET, liboqs…)
- Decision guide + checklist nên / không nên

Tài liệu mang tính **giáo dục**; ưu tiên thư viện đã kiểm chứng, không kèm exploit.
