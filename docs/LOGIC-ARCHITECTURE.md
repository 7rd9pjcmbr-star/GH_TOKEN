# Kiến trúc logic — Mã Mở

Cấu trúc có **logic rõ ràng**: tầng → hợp đồng → luồng sự kiện → quyết định.

## 1. Tầng (layers)

```
┌─────────────────────────────────────────────────────────┐
│  UI  (index / atlas / mapper / a11y dock)               │
├─────────────────────────────────────────────────────────┤
│  LOGIC KERNEL  js/logic/                                │
│   schema · rules · index · paths · router · orchestrator│
├──────────────────────┬──────────────────────────────────┤
│  MaMoCrypto          │  MaMoA11y                        │
│  catalog/search/…    │  switch/scan/phrases/…           │
├──────────────────────┴──────────────────────────────────┤
│  DATA  crypto-atlas.js · network-map.js                 │
└─────────────────────────────────────────────────────────┘
```

| Tầng | Trách nhiệm | Không làm |
|------|-------------|-----------|
| **UI** | Hiển thị, nhận input | Không chứa rule nghiệp vụ |
| **Logic** | Quyết định, định tuyến, path, validate | Không gọi DOM trực tiếp (trừ orchestrator.bind) |
| **Domain libs** | Crypto knowledge / A11y primitives | Không biết UI cụ thể |
| **Data** | Sự thật tĩnh (atlas, edges) | Không có side-effect |

## 2. Hợp đồng (schema)

Mọi thực thể logic có `kind` + `id`:

- `concept:<id>` · `lib:<id>` · `group:<id>` · `lang:<name>` · `hub:crypto-libs`
- `Query` → `{ text, intent?, filters? }`
- `Decision` → `{ action, payload, reason, ruleId }`
- `Path` → `{ from, to, nodes[], edges[], length }`

## 3. Logic quyết định (rules)

Thứ tự ưu tiên:

1. **Intent exact** (lookup id / tên khớp tuyệt đối)
2. **Recommend rules** (password → Argon2/libsodium, browser → WebCrypto…)
3. **Search ranked** (synonym + score)
4. **Graph neighbors / paths** (đường tới thư viện)
5. **Fallback** cheat-sheet + encoding disclaimer

## 4. Luồng tra cứu

```
User query
  → router.classify(intent)
  → rules.match OR search.query
  → index.resolve(refs)
  → paths.toLibraries (nếu cần sơ đồ)
  → Decision { results, reason, paths }
  → UI render
```

## 5. Luồng đường dẫn thư viện

```
Node nguồn (hub|concept|lang|lib)
  → paths.allToLibraries(from)
  → mỗi lib: BFS shortest path
  → gắn icon cạnh (edge.icon / lib.icon)
  → Mapper highlight
```

## 6. Module `js/logic/`

| File | Logic |
|------|--------|
| `config.js` | Feature flags, priority, ownership |
| `schema.js` | Kiểu / chuẩn hoá id / validate |
| `analyze.js` | Nhận dạng/phân loại định dạng mã (chữ ký cấu trúc riêng) |
| `index.js` | Chỉ mục thống nhất + resolve |
| `rules.js` | Luật recommend + intent |
| `paths.js` | Mọi đường tới thư viện |
| `pipeline.js` | Stage có trật tự + claim domain |
| `router.js` | Ủy quyền pipeline |
| `orchestrator.js` | API `MaMoLogic.*` |
| `bootstrap.js` | Khởi động theo order |

## 7. API

```js
MaMoLogic.query("password hashing")
MaMoLogic.analyze("eyJhbGciOiJIUzI1NiJ9.e30.sig")
MaMoLogic.classifyFormat(".... . .-.. .-.. ---")
MaMoLogic.pathsToLibraries("concept:aead")
MaMoLogic.config.setEnabled("search", false)
MaMoLogic.config.conflicts()
MaMoLogic.describe()
```

Giáo dục — không exploit; `encode` ≠ encryption.

## 8. Cấu hình nâng cao & chống dẫm chân

File: `js/logic/config.js` + `pipeline.js`

| Module | Priority | Owns (độc quyền) |
|--------|----------|------------------|
| router | 1 | dispatch |
| schema | 5 | contracts, normalize |
| analyze | 8 | format-detect, format-classify |
| index | 10 | resolve, entity-lookup |
| rules | 20 | intent, need-match |
| recommend | 25 | recommendation |
| search | 30 | ranked-search |
| paths | 40 | graph-paths, lib-routes |
| encode | 50 | representation-encode |
| a11y | 60 | switch-scan, tts-assist |

**Pipeline:** `validate → analyze → resolve → classify → rules → search → paths → finalize`

**Policy `first-wins`:** domain đã claim → stage sau bỏ qua (không ghi đè).  
**Enrichment:** paths / searchAlt chỉ gắn thêm khi đã có `primaryOwner`.

## 9. Phân tích sâu định dạng mã

Module `analyze` — **mỗi định dạng một detector cấu trúc riêng** (JWT ≠ Base64 ≠ Morse ≠ Braille ≠ PEM ≠ hash-hex…).

Owns: `format-detect`, `format-classify` — không chiếm `resolve` / `recommend`.

```js
MaMoLogic.analyze("eyJhbGciOiJIUzI1NiJ9.e30.signature")
MaMoLogic.classifyFormat(".... . .-.. .-.. ---")
// → primary + candidates[] + uniqueness + structure
```

Pipeline stage `analyze` chạy sau `validate`, trước `resolve`; kết quả gắn `meta.enrichment.format`.

## 10. Lab bảo mật tách biệt

Surface `/lab/` (`MaMoLab`) **không** nằm trong pipeline crypto/a11y.  
Owns: `malware-static`, `security-audit`, `sandbox-policy`, `ioc-triage`.

- Browser: Worker + CSP `connect-src 'none'` — không thực thi mẫu
- Docker: `docker/lab` · `network_mode: none`
- Docs: `docs/SECURITY-LAB.md`

## 11. Quân đội icon trên dòng chảy

Module `icons` + `NETWORK_MAP.iconArmy` — **Mapper gọi tên icon** khi analyze / query.

```js
MaMoLogic.query("password hashing")
// meta.enrichment.icons.feedback → "Mapper gọi quân đội icon: …"
// meta.iconFeedback · callChant · uniqueIcons

MaMoLogic.analyze(jwtText).feedback
MaMoLogic.callIcons("concept:aead")
MaMoLogic.iconArmy()
```

Pipeline: `… → paths → icons → finalize`  
Owns: `icon-call`, `icon-flow` (enrichment, không đè primary).

Mỗi path tới thư viện mang `icons[]` + `iconFlow[]`; UI Mapper/Logic hiện tên gọi (Tia Lửa Hub, Khối Thư Viện, …).

## 12. Icon Atlas — không bỏ sót

`data/icon-atlas.js` + `MaMoLogic.mapIconLibraries()`:

- 17/17 icon trên mạng có SVG + tài liệu + thư viện (URL đầy đủ)
- 27/27 thư viện được map ít nhất một icon
- Icon chỉ trên cạnh (`layers`, `key`) vẫn BFS → libs

Docs: `docs/ICON-ATLAS.md` · UI Mapper panel **Atlas icon → thư viện**.

## 13. Tối ưu logic nâng cao (`optimize`)

Module `js/logic/optimize.js` — MaMoLogic **v1.2**:

| Tính năng | Mô tả |
|-----------|--------|
| **Soft screen** (mặc định) | Sàng lọc / thu hẹp / demote — **không loại bỏ** stage hay năng lực |
| Query LRU cache | Trùng query → trả decision đã tối ưu |
| Adaptive depth | `analyzeDepth` light/full, `pathLimit` — vẫn chạy stage |
| Path memo | `toLibraries(from)` cache theo origin |
| Rank refine | Sắp results theo tier / language / score |
| Dedupe | Gộp trùng ref (không xoá module) |
| Stage metrics | `meta.stageTiming` + `optimize.stats()` |

`softScreen: false` mới cho phép skip stage kiểu cũ (hard). Mặc định giữ đủ analyze/paths/icons.

```js
MaMoLogic.query("password hashing")
// meta.optimized.elapsedMs · meta.optPlan · meta.stageTiming

MaMoLogic.optimize.stats()
MaMoLogic.optimize.invalidate()
MaMoLogic.optimize.planPreview("jwt token")
```

Owns: `query-cache`, `adaptive-plan`, `rank-refine` — không đè recommend.

## 14. Biến nhúng nginx upstream

`data/nginx-upstream-vars.js` + `MaMoLogic.vars.*`

```js
MaMoLogic.vars.get("$upstream_cache_status")
MaMoLogic.query("$upstream_addr")  // action: upstream-vars
```

15 biến (`$upstream_*`); 2 commercial (`$upstream_last_addr`, `$upstream_last_server_name`).  
Chỉ thị `resolver` (upstream, ≥1.27.3) + `queue` (commercial, ≥1.5.12).  
Docs: `docs/NGINX-UPSTREAM-VARS.md`.
