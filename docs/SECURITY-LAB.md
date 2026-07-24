# MaMoLab — môi trường tách biệt

Phân tích sâu mã đáng ngờ & kiểm thử bảo mật **phòng thủ**.  
Lab **không** thực thi mẫu, **không** tạo exploit/payload tấn.

## Lab Control v2 (nâng cấp)

```bash
python3 scripts/lab_control.py upgrade   # áp v2 + validate
python3 scripts/lab_control.py status
python3 scripts/lab_control.py analyze   # batch quarantine/lab
python3 scripts/lab_control.py validate  # đối chứng policy (không exploit)
python3 scripts/telegram_to_lab_analyze.py
```

Báo cáo: `reports/lab/` · Panel: **🚀 Lab·nâng cấp** · **🧬 Lab·status**.

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

## Thư viện kiến thức học tập & thí nghiệm

Curriculum + chương + EXP + **atlas MSF đầy đủ** (không bỏ sót nhánh):

```bash
python3 scripts/metasploit_full_atlas.py              # Metasploit có những gì
python3 scripts/knowledge_library_build.py --with-atlas
python3 scripts/knowledge_library_build.py status
python3 scripts/knowledge_library_build.py --with-harvest
```

Vào: `knowledge/13-msf-full-atlas.md` · `knowledge/generated/msf-full-atlas.md` · `msf-module-index.csv`  
Panel: **📖 Thư viện·KT** · **📦 MSF·atlas đủ**.

## Mapper thư viện Metasploit Suite

Catalog phòng thủ (taxonomy modules / suite) — **không** generate payload:

```bash
python3 scripts/metasploit_suite_mapper.py
python3 scripts/metasploit_suite_mapper.py --mermaid
```

Ánh xạ: `exploit|auxiliary|post|payload|encoder|evasion` → MaMoLab roles.  
Deny: msfvenom generate · exploit run · msfrpcd expose.  
Báo cáo: `reports/telegram-classify/metasploit_suite_mapper.txt`.

## Rà soát toàn bộ thư viện MSF (kiến thức)

Sparse-clone `modules/` (readonly) → parse Name/Rank/CVE/Platform → báo cáo triage:

```bash
python3 scripts/metasploit_library_harvest.py
python3 scripts/metasploit_suite_mapper.py harvest
# làm mới clone: --refresh
```

Báo cáo: `metasploit_library_knowledge.txt|json` · `metasploit_cve_index.csv`  
(trong `reports/telegram-classify/`, gitignored). Panel: **📚 MSF·kiến thức**.

## Kiến thức kiểm thử (playbook)

Đây mới là **kiểm thử** — phases P1–P6, checklist scanner family, CVE backlog, MaMoLab audit:

```bash
python3 scripts/metasploit_testing_knowledge.py
python3 scripts/metasploit_testing_knowledge.py --with-harvest
```

Báo cáo: `reports/telegram-classify/metasploit_testing_knowledge.txt`  
Panel: **🧪 MSF·kiểm thử**. Không chạy exploit / msfvenom / scan prod.

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
