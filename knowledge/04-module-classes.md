# 04 — Phân loại module → hành động học/lab

| Class | Học | Thí nghiệm lab | Cấm |
|-------|-----|----------------|-----|
| exploit | CVE, điều kiện, impact | Tra cứu + backlog | run exploit |
| auxiliary | scanner/gather/admin patterns | Checklist harden | scan prod bằng MSF |
| post | TTP sau xâm nhập | Viết IOC | chạy post trên host thật ngoài lab |
| payload | Họ tên artifact | Signature static | msfvenom |
| encoder/evasion | Biến đổi payload | Entropy/heuristic | encode để tấn |

## MaMoLab owns

- malware-static ← exploit/payload/encoder
- security-audit ← auxiliary
- ioc-triage ← post
- sandbox-policy ← cấm venom/rpc

## Bài tập

Lấy 10 path từ harvest samples → ghi class + hành động học (bảng 3 cột).
