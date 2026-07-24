# Curriculum — Lộ trình học & thí nghiệm

## Mục tiêu

Sau khi hoàn thành, bạn:

1. Hiểu biên giới lab phòng thủ và lý do deny exploit/payload.
2. Dùng catalog Metasploit như **chỉ mục kiến thức** (CVE, family, platform).
3. Triage CVE → backlog vá / detect.
4. Chạy thí nghiệm MaMoLab + Docker static analyze.
5. Viết checklist harden và IOC từ pattern post/auxiliary.

## Lộ trình đề xuất

| Buổi | Chương | Thí nghiệm | Kết quả |
|------|--------|------------|---------|
| 1 | 00–01 | EXP-01, EXP-07 | Policy + audit pass |
| 2 | 02–03 | — | Threat model + MSF map |
| 3 | 04–05 | EXP-03, EXP-06 | CVE backlog |
| 4 | 06–08 | EXP-04 | Harden checklist owned |
| 5 | 09–10 | EXP-02, EXP-05 | Static + IOC |
| 6 | 11–12 | ôn | Tự mở rộng syllabus |

## CLI đồng bộ kiến thức

```bash
python3 scripts/knowledge_library_build.py --with-harvest
python3 scripts/metasploit_suite_mapper.py test
```

## Chứng cứ hoàn thành (tự đánh giá)

- [ ] `MaMoLab.audit()` ok
- [ ] Có file báo cáo trong `reports/` từ docker analyze (mẫu owned/test)
- [ ] Có backlog CVE ≥ 10 mục từ harvest
- [ ] Checklist HTTP/SSH đánh dấu trên hệ owned
- [ ] Không có artifact msfvenom trong workspace
