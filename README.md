# Mã Mở

Hệ thống hỗ trợ giao tiếp + atlas mật mã:

1. **Chế độ đặc biệt** — một công tắc + quét tự động + TTS cho người không/hạn chế vận động  
2. Câu nhanh · Morse · Braille  
3. **Atlas mật mã** + **Super Icon Network Mapper**

## Chế độ đặc biệt (ưu tiên)

- Quét sáng từng câu (Có / Không / Cần giúp / nhu cầu cơ bản)
- Công tắc: **Space**, **Enter**, hoặc **nút lớn** dưới màn hình
- Nhấn ngắn = chọn câu đang sáng · Giữ lâu = gạch Morse
- Tốc độ quét, đọc khi quét, tương phản cao
- Morse một công tắc → giải mã → đọc to

## Chạy

```bash
python3 -m http.server 8080
```

- http://localhost:8080/ — hỗ trợ đặc biệt  
- http://localhost:8080/atlas/ — atlas mật mã  
- http://localhost:8080/mapper/ — network mapper  

## Cấu trúc

| Đường dẫn | Mô tả |
|-----------|--------|
| `index.html` / `app.js` / `access.js` / `styles.css` | Module hỗ trợ + switch-scan |
| `atlas/` | Kiến thức & thư viện crypto |
| `mapper/` | Sơ đồ network icon |
| `data/` | Atlas + graph data |
| `docs/CRYPTO-ATLAS.md` | Tóm tắt markdown |
