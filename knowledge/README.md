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
python3 scripts/knowledge_library_build.py          # build/index
python3 scripts/knowledge_library_build.py status   # tình trạng thư viện
python3 scripts/metasploit_testing_knowledge.py     # playbook kiểm thử
python3 scripts/metasploit_library_harvest.py       # catalog MSF → enrich
```

1. Đọc [CURRICULUM.md](./CURRICULUM.md)
2. Học lần lượt chương `00` → `12`
3. Làm thí nghiệm trong `experiments/`
4. Xem `generated/` sau khi harvest

Panel Telegram: **📖 Thư viện·KT**
