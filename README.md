# Mã Mở

Hệ thống hỗ trợ giao tiếp (accessibility) + atlas mật mã, thiết kế **module hoá**.

## Accessibility — cấu trúc module

```
js/a11y/
  core · store · speech · switch · scan
  phrases · morse · braille · profiles · shell · bootstrap
```

Chi tiết: [`docs/A11Y-ARCHITECTURE.md`](docs/A11Y-ARCHITECTURE.md)

### Chức năng hỗ trợ đặc biệt

- **Hồ sơ**: locked-in / vận động hạn chế / thị lực kém / khó nói / Braille
- **Một công tắc** + **quét tự động** + TTS
- Câu nhanh · Morse · Braille
- Prefs lưu `localStorage`

## Atlas & Mapper

- `/atlas/` — kiến thức + thư viện crypto  
- `/mapper/` — Super Icon Network Mapper  

## Chạy

```bash
python3 -m http.server 8080
```

- http://localhost:8080/ — hỗ trợ đặc biệt  
- http://localhost:8080/atlas/  
- http://localhost:8080/mapper/  
