# Icon Atlas — tài liệu đầy đủ mọi icon trên mạng

Không bỏ sót icon nào xuất hiện trong không gian mạng (node / cạnh / format / army / SVG).

## Nguồn

- `data/icon-atlas.js` — `buildIconLibraryAtlas()`
- `NETWORK_MAP.iconArmy` + `ICON_DOCS` + `ICON_SVG`
- `CRYPTO_ATLAS.libraries` (url, summary, provides, notes, tier)

## API

```js
MaMoLogic.mapIconLibraries()
// → 17/17 icon docs đủ · 27 thư viện

MaMoLogic.iconDocs("hash")
MaMoLogic.iconCoverage()
```

## Phủ sóng

Mỗi icon có:

| Trường | Ý nghĩa |
|--------|---------|
| call / motto | Tên gọi mapper |
| documentation | Mô tả + SVG |
| appears | node/cạnh/format |
| libraries[] | Thư viện kèm URL tài liệu đầy đủ |

Icon chỉ trên cạnh (`layers` contains, `key` related) vẫn BFS hai đầu cạnh → thư viện.

## UI

- Mapper: panel **Atlas icon → thư viện**
- Logic-view: nút **Map mọi icon → thư viện**
