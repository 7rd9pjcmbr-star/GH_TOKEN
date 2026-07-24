# MaMoLab — môi trường tách biệt

Phân tích sâu mã đáng ngờ & kiểm thử bảo mật **phòng thủ**.  
Lab **không** thực thi mẫu, **không** tạo exploit/payload tấn.

## Ba lớp cô lập

```
┌─────────────────────────────────────────────────────────┐
│  Host UI  /lab/   CSP connect-src 'none'                │
│    └─ Web Worker  lab/worker/sandbox.worker.js          │
│         (static scan only — no fetch, no eval)          │
├─────────────────────────────────────────────────────────┤
│  MaMoLab modules  js/lab/*                              │
│    policy · static · indicators · harden · report       │
├─────────────────────────────────────────────────────────┤
│  Docker Lab  docker/lab/   network_mode: none           │
│    quarantine/ (ro) → analyze-static.py → reports/      │
└─────────────────────────────────────────────────────────┘
```

## API trình duyệt

```js
await MaMoLab.analyze(text)   // Worker/static + IOC (+ format nếu có)
MaMoLab.audit()               // self-hardening checklist
MaMoLab.wipe()                // terminate worker + clear
MaMoLab.describe()
```

Owns (không chồng a11y/crypto recommend): `malware-static`, `security-audit`, `sandbox-policy`, `ioc-triage`.

## Mapper thư viện Metasploit Suite

Catalog phòng thủ (taxonomy modules / suite) — **không** generate payload:

```bash
python3 scripts/metasploit_suite_mapper.py
python3 scripts/metasploit_suite_mapper.py --mermaid
```

Ánh xạ: `exploit|auxiliary|post|payload|encoder|evasion` → MaMoLab roles.  
Deny: msfvenom generate · exploit run · msfrpcd expose.  
Báo cáo: `reports/telegram-classify/metasploit_suite_mapper.txt`.

## Quy trình an toàn

1. Không mở/chạy mẫu trên máy làm việc chính.
2. Dán text vào `/lab/` **hoặc** copy file vào `quarantine/` rồi Docker analyze.
3. Đọc báo cáo risk/findings/IOC — triage, không “detonate”.
4. `wipe` / xóa `quarantine` sau khi xong.
5. Không paste mẫu vào chat/log công khai.

## Kiểm thử bảo mật (self)

Nút **Kiểm thử bảo mật** chạy `MaMoLab.audit()`: CSP, policy, ownership, storage leak, Worker.

## Liên quan

- Format classify: `MaMoLogic.analyze` (readonly, không execute)
- UI: `/lab/`
- Docker: `docker/lab/README.md`
