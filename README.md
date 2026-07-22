# Mã Mở

Cấu trúc có **logics** rõ ràng (local):

```
UI → js/logic (schema/analyze/rules/…) → MaMoCrypto / A11y → Data
     ↘ /lab/ MaMoLab (Worker + Docker) — tách biệt, phòng thủ
```

## Surfaces

- `/` hỗ trợ đặc biệt (a11y)
- `/atlas/` MaMoCrypto + API
- `/mapper/` icon mọi đường tới thư viện — **gọi tên quân đội icon** trên dòng chảy
- `/logic-view/` cấu trúc & thử logic (+ phản hồi icon)
- `/lab/` **sandbox tách biệt** — phân tích tĩnh mã đáng ngờ + kiểm thử bảo mật

```js
MaMoLogic.query("password") // meta.iconFeedback · meta.optimized · optPlan
MaMoLogic.analyze(text)     // panorama + analysis + translation (+ icons)
MaMoLogic.panorama(text)    // toàn cảnh từ config
MaMoLogic.translate(text)   // thông dịch encoding
MaMoLogic.callIcons("concept:aead")
MaMoLogic.mapIconLibraries() // 17/17 icon → 27 thư viện có tài liệu
MaMoLogic.optimize.stats()   // LRU cache · adaptive · path memo
MaMoLogic.vars.get("$upstream_addr") // biến nhúng nginx upstream
```

Docs: `docs/ICON-ATLAS.md` · `docs/LOGIC-ARCHITECTURE.md` · `docs/NGINX-UPSTREAM-VARS.md`

## Lab (cô lập)

- Browser: Web Worker + CSP `connect-src 'none'` — không thực thi mẫu
- OS: `docker/lab` với `network_mode: none`
- Docs: `docs/SECURITY-LAB.md`

## Chạy

```bash
python3 -m http.server 8080
```

> Không commit/push khi cờ no-publish đang bật.
