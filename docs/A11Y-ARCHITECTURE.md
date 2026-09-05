# Kiến trúc Accessibility — Mã Mở

Cấu trúc module chức năng hỗ trợ đặc biệt cho người dùng khuyết tật (ưu tiên không vận động được).

## Sơ đồ module

```
js/a11y/
├── core.js        Event bus + registry + boot lifecycle
├── store.js       Prefs (localStorage)
├── speech.js      TTS vi-VN + live region + tone/blip
├── switch.js      Một công tắc (Space/Enter/nút lớn, ngắn/dài)
├── scan.js        Quét tự động .scan-target
├── phrases.js     Catalog câu (emergency / needs / social)
├── morse.js       Encode/decode + Morse một công tắc
├── braille.js     Braille grade 1 + 6 điểm
├── profiles.js    Hồ sơ: locked-in, low-motor, low-vision, speech, braille
├── shell.js       Điều hướng mode + thanh kết quả + compat MaMo
└── bootstrap.js   core.boot()
```

## Luồng sự kiện chính

```
switch:down / switch:up
    → switch:short | switch:long
        → (special + scanning) scan:activate → phrases.say
        → (special, không quét) morse:dot | morse:dash

prefs:changed ← store.set
profile:applied → shell.setMode + scan
mode:change → scan start/stop
status → #status-pill
```

## Hồ sơ người dùng

| Profile | Mặc định | Đặc điểm |
|---------|----------|----------|
| `locked-in` | special | Quét + đọc tên ô + một công tắc |
| `low-motor` | special | Quét chậm, ô lớn, long-press 500ms |
| `low-vision` | special | Tương phản cao + announce |
| `speech` | phrases | Câu nhanh / TTS |
| `blind-braille` | braille | Braille + TTS |

## Nguyên tắc thiết kế

1. **Một tín hiệu đủ dùng** — không phụ thuộc chuột chính xác  
2. **Module độc lập** — giao tiếp qua `MaMoA11y.core.emit/on`  
3. **Prefs bền** — `store` ghi localStorage  
4. **TTS + live region** — phản hồi cho người khiếm thị / không nhìn UI  
5. **Không exploit** — chỉ hỗ trợ giao tiếp & mã hoá biểu diễn (Morse/Braille)

## API nhanh

```js
MaMoA11y.core.get("phrases").say("Tôi cần giúp đỡ");
MaMoA11y.core.get("scan").toggle(true);
MaMoA11y.core.get("profiles").apply("locked-in");
MaMoA11y.core.list(); // ["store","speech",...]
```
