# 13 — Atlas Metasploit đầy đủ: thư viện có những gì (không bỏ sót)

## Trả lời thẳng

Phần **modules/** của Metasploit Framework (catalog học tập) gồm **7 lớp**, **5043** module `.rb`, **86 nhánh depth-1**, **~1004 CVE** unique trong index.

| Lớp | Số module | Nhánh chính (đủ trong atlas) | Việc học |
|-----|-----------|------------------------------|----------|
| **exploits** | 2672 | windows, linux, multi, unix, osx, freebsd, solaris, android, apple_ios, aix, netware, qnx, bsd/bsdi, firefox, hpux, irix, mainframe, openbsd, examples… | CVE → vá/detect |
| **auxiliary** | 1324 | scanner, admin, gather, dos⛔, server, fuzzers, sqli, spoof, analyze, fileformat, client, voip, cloud, vsploit, bnat, crawler, parser, pdf, sniffer… | Harden checklist |
| **post** | 435 | windows, multi, linux, osx, hardware, android, networking, solaris, firefox, apple_ios, aix, bsd | IOC / TTP |
| **payloads** | 529 | singles, stagers, adapters, stages | Signature (cấm gen) |
| **encoders** | 57 | x86, cmd, x64, riscv*, php, mips*, ppc, ruby, sparc, generic | Entropy/heuristic |
| **nops** | 14 | x86, x64, arm*, riscv*, mips*, ppc, sparc, php, cmd, tty, loongarch64 | Pattern |
| **evasion** | 12 | windows, linux | Tên kỹ thuật né AV |

> Suite còn **msfconsole / msfvenom⛔ / msfrpcd⛔** — lab deny venom & RPC. Atlas này tập trung **modules/**.

## Chạy atlas (đầy đủ, không cắt subtree)

```bash
python3 scripts/metasploit_full_atlas.py
python3 scripts/knowledge_library_build.py --with-atlas
```

## File bắt buộc đọc

| File | Nội dung |
|------|----------|
| `knowledge/generated/msf-full-atlas.md` | Tổng quan đủ depth1 |
| `knowledge/generated/atlas-*.md` | 7 file — depth1+2 (+depth3 mẫu) từng lớp |
| `knowledge/generated/msf-coverage-checklist.md` | Checkbox đủ 86 nhánh |
| `knowledge/generated/msf-module-index.csv` | **Mọi** module path/name/CVE/rank |
| `knowledge/generated/msf-atlas-depth3.json` | Đủ key depth-3 |

## Auxiliary — đừng bỏ sót dos/server/…

- `scanner` → checklist harden (chương 06)
- `admin` / `gather` → misconfig + disclosure
- `dos` → **chỉ biết tồn tại**, cấm thí nghiệm ngoài lab tách
- `server` / `spoof` / `vsploit` → pattern detect
- `sqli` / `fuzzers` / `cloud` / `voip` → surface riêng

## Exploits — platform hiếm cũng có trong checklist

mainframe, irix, hpux, qnx, netware, openbsd… đều có dòng trong coverage — học “tồn tại trên catalog”, ưu tiên theo stack owned.

## Payloads / encoders / nops / evasion

Học **tên họ & kiến trúc**, không generate. Liên hệ chương 10 (static lab) + policy `noExploitGeneration`.

## Thí nghiệm

EXP-08: đối chiếu checklist 86 nhánh — đánh dấu đã đọc/map vào curriculum.

## Cấm

exploit run · msfvenom · msfrpcd · MSF scan prod · chạy dos/evasion trên hệ ngoài lab owned.
