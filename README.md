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
- **Super Icon Network Mapper**: http://localhost:8080/mapper/

## Cấu trúc

| Đường dẫn | Mô tả |
|-----------|--------|
| `index.html` / `app.js` / `styles.css` | Công cụ Morse · Braille · câu nhanh |
| `atlas/` | UI Atlas (kiến thức, thư viện, chọn nhanh) |
| `mapper/` | Super Icon Mapper — sơ đồ network + tìm kiếm |
| `data/crypto-atlas.js` | Dữ liệu thư viện + khái niệm |
| `data/network-map.js` | Graph edges + icon map |
| `docs/CRYPTO-ATLAS.md` | Bản tóm tắt markdown |

## Atlas & Mapper

- 20+ khái niệm · 27+ thư viện · decision guide
- Network Mapper: icon node, pan/zoom, lọc loại, tìm kiếm toàn sơ đồ, cạnh quan hệ (cung cấp / phụ thuộc / ngôn ngữ)

Tài liệu mang tính **giáo dục**; ưu tiên thư viện đã kiểm chứng, không kèm exploit.
