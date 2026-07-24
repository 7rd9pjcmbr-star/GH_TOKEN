# Thư viện kiến thức — Học tập & Thí nghiệm (phòng thủ)

Thư viện **toàn diện** để học và thực hành trong lab cô lập.  
Nguồn tham chiếu: cấu trúc Metasploit Framework (catalog) + MaMoLab + Docker lab.

## Nguyên tắc

| Được | Không được |
|------|------------|
| Đọc catalog CVE/TTP | Chạy exploit / msfvenom |
| Checklist harden trên **owned** | Scan mạng production bằng MSF |
| Phân tích tĩnh trong quarantine / `/lab/` | Thực thi mẫu / detonate |
| MaMoLab.audit() · docker analyze | Expose msfrpcd · reverse-shell helper |

## Vào nhanh

```bash
python3 scripts/metasploit_full_atlas.py                 # MSF có những gì (đủ)
python3 scripts/knowledge_library_build.py --with-atlas
python3 scripts/knowledge_library_build.py status
python3 scripts/metasploit_testing_knowledge.py
```

1. Đọc [13-msf-full-atlas.md](./13-msf-full-atlas.md) — **Metasploit có những gì**
2. Đọc [CURRICULUM.md](./CURRICULUM.md)
3. Học chương `00` → `13` + `generated/atlas-*.md`
4. Làm EXP-01…08

Panel: **📖 Thư viện·KT** · **📦 MSF·atlas đủ**
