# Mã Mở — Hệ thống giải mã hỗ trợ người khiếm khuyết

Ứng dụng web hỗ trợ giao tiếp cho người khiếm thị, hạn chế vận động, hoặc khó nói.

## Tính năng

- **Morse**: giải mã / mã hóa, bàn phím chấm–gạch, phát âm thanh Morse
- **Braille**: giải mã / mã hóa grade 1, bàn phím 6 điểm
- **Câu nhanh**: bảng câu giao tiếp thường dùng + đọc to (TTS tiếng Việt)
- Giao diện lớn, có skip-link, `aria-live`, và tôn trọng `prefers-reduced-motion`

## Chạy locally

Mở `index.html` trong trình duyệt, hoặc:

```bash
python3 -m http.server 8080
```

Sau đó vào `http://localhost:8080`.

## Cấu trúc

| File | Mô tả |
|------|--------|
| `index.html` | Giao diện chính |
| `styles.css` | Giao diện & accessibility |
| `app.js` | Logic Morse, Braille, TTS |

## Ghi chú

- TTS dùng Web Speech API (`vi-VN` nếu trình duyệt có giọng Việt).
- Chữ có dấu được bỏ dấu trước khi mã hóa Morse/Braille Latin grade 1.
