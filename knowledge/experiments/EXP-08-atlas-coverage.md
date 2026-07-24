# EXP-08 — Đối chiếu đủ nhánh atlas MSF

## Mục tiêu
Chứng minh thư viện học tập **không bỏ sót** nhánh depth-1 của Metasploit modules/.

## Bước
```bash
python3 scripts/metasploit_full_atlas.py
python3 scripts/knowledge_library_build.py --with-atlas
```

1. Mở `knowledge/generated/msf-coverage-checklist.md`
2. Đếm số dòng `- [x]` — phải khớp `branches_depth1` trong atlas (~86)
3. Với mỗi lớp (exploits…evasion): mở `atlas-<class>.md`, xác nhận depth1 liệt kê đủ key
4. Spot-check: mở `msf-module-index.csv` — số dòng ≈ 5043 + header
5. Ghi biên bản: ngày · số nhánh · số module · CVE unique

## Pass
- [ ] 7/7 file `atlas-*.md` tồn tại
- [ ] Checklist ≥ 80 nhánh (thường 86)
- [ ] CSV index ≈ 5043 module
- [ ] Không có thao tác exploit/msfvenom trong biên bản

## Cấm
Chạy module · generate payload · bỏ qua nhánh `dos` / platform hiếm (vẫn phải có trong checklist).
