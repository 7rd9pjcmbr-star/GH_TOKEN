# 06 — Kiểm thử harden mạng & dịch vụ

Dùng **tên family** từ `auxiliary/scanner/*` làm khung checklist.  
Thực hiện kiểm tra bằng quy trình owned (review cấu hình, CIS, scanner nội bộ được phép) — **không** MSF scan production.

## Playbook nhanh

```bash
python3 scripts/metasploit_testing_knowledge.py
```

## Family → checklist học/lab

| Family | Modules≈ | Kiểm trên owned |
|--------|----------|-----------------|
| http | 315 | TLS, HSTS, auth, upload, version leak, CSP/CORS |
| sap | 36 | gateway ACL, default users |
| snmp | 17 | community public/private, v3 |
| scada | 15 | VLAN/air-gap, default eng pass |
| ssh | 13 | PasswordAuth, RootLogin, cipher |
| smb | 12 | SMBv1 off, signing, guest |
| vmware | 12 | vCenter patch, SSO, legacy Log4j |
| oracle | 12 | listener ACL, default accounts |
| ftp | 9 | anonymous, chuyển SFTP |
| ntp | 9 | mode 6, monlist |
| mysql | 7 | bind-address, remote root |
| discovery | 7 | inventory vs thực tế |
| mssql / postgres | 5 | sa/superuser, TLS, xp_cmdshell |
| rdp / ssl | 3 | NLA, TLS≥1.2 |
| redis / ldap / kerberos | — | requirepass, anonymous bind, ticket hygiene |

Số module là **độ phủ catalog**, không phải số lỗ hổng trên mạng bạn.

## Quy trình 1 dịch vụ (mẫu)

1. Chọn family khớp dịch vụ owned.
2. Copy checklist → phiếu pass/fail + evidence (screenshot config / lệnh read-only).
3. Ghi residual risk.
4. Lặp family tiếp theo theo độ expose.

## Liên hệ curriculum

- Chương 02 (threat model) chọn family nào?
- Chương 05: CVE của dịch vụ đó có trong harvest không?
- EXP-04: làm sâu HTTP.

## Cấm

- `auxiliary/scanner` qua msfconsole lên prod
- DoS modules (`auxiliary/dos`) dưới mọi hình thức ngoài lab mạng tách
