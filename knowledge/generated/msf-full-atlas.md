# Thư viện Metasploit có những gì — Atlas đầy đủ

Built: 2026-07-24T05:23:27Z
Verdict: ✅ Atlas MSF đầy đủ · modules=5043 · classes=7 · branches_depth1=86 · CVE_unique_in_index=1004
Root: `/tmp/msf-knowledge/modules`

## Trả lời ngắn

Metasploit Framework (phần **modules/** dùng để học trong lab này) gồm **7 lớp module**:

1. **exploits** — 2672 module · học: CVE / kỹ thuật công khai → triage patch + detect
1. **auxiliary** — 1324 module · học: Scanner/gather/admin/dos patterns → harden checklist
1. **post** — 435 module · học: TTP sau xâm nhập → IOC persistence/lateral/cred
1. **payloads** — 529 module · học: Họ artifact (singles/stagers/stages) → signature
1. **encoders** — 57 module · học: Biến đổi payload theo arch → entropy/heuristic
1. **nops** — 14 module · học: NOP sled patterns theo arch
1. **evasion** — 12 module · học: Kỹ thuật né AV/AppLocker (tên module)

Tổng: **5043** file `.rb` · CVE unique (index): **1004** · nhánh depth1: **86**

Ngoài modules/: suite còn msfconsole, msfvenom(⛔), msfrpcd(⛔), plugins/scripts/tools — lab này catalog modules/ + deny venom/rpc.

## Chi tiết từng lớp (depth1 — KHÔNG bỏ sót)

### exploits (2672 modules)

- Lab: Chỉ tra cứu path/CVE; không exploit

  - `windows` ×1231
  - `linux` ×566
  - `multi` ×538
  - `unix` ×227
  - `osx` ×43
  - `freebsd` ×16
  - `solaris` ×15
  - `android` ×10
  - `apple_ios` ×6
  - `aix` ×5
  - `netware` ×2
  - `qnx` ×2
  - `bsd` ×1
  - `bsdi` ×1
  - `example.rb` ×1
  - `example_linux_persistence.rb` ×1
  - `example_linux_priv_esc.rb` ×1
  - `example_webapp.rb` ×1
  - `firefox` ×1
  - `hpux` ×1
  - `irix` ×1
  - `mainframe` ×1
  - `openbsd` ×1

Chi tiết depth2/3: `knowledge/generated/atlas-exploits.md`

### auxiliary (1324 modules)

- Lab: Checklist owned; không MSF scan prod; dos = cấm

  - `scanner` ×646
  - `admin` ×237
  - `gather` ×178
  - `dos` ×112
  - `server` ×53
  - `fuzzers` ×21
  - `sqli` ×19
  - `spoof` ×11
  - `analyze` ×9
  - `fileformat` ×9
  - `client` ×6
  - `voip` ×6
  - `cloud` ×5
  - `vsploit` ×5
  - `bnat` ×2
  - `crawler` ×1
  - `example.rb` ×1
  - `parser` ×1
  - `pdf` ×1
  - `sniffer` ×1

Chi tiết depth2/3: `knowledge/generated/atlas-auxiliary.md`

### post (435 modules)

- Lab: Viết IOC; không chạy post trên host ngoài lab

  - `windows` ×242
  - `multi` ×79
  - `linux` ×51
  - `osx` ×23
  - `hardware` ×12
  - `android` ×7
  - `networking` ×6
  - `solaris` ×6
  - `firefox` ×5
  - `apple_ios` ×2
  - `aix` ×1
  - `bsd` ×1

Chi tiết depth2/3: `knowledge/generated/atlas-post.md`

### payloads (529 modules)

- Lab: Nhận diện tên/chuỗi; cấm msfvenom

  - `singles` ×315
  - `stagers` ×103
  - `adapters` ×64
  - `stages` ×47

Chi tiết depth2/3: `knowledge/generated/atlas-payloads.md`

### encoders (57 modules)

- Lab: Signature only

  - `x86` ×24
  - `cmd` ×8
  - `riscv32le` ×4
  - `riscv64le` ×4
  - `x64` ×4
  - `php` ×3
  - `generic` ×2
  - `mipsbe` ×2
  - `mipsle` ×2
  - `ppc` ×2
  - `ruby` ×1
  - `sparc` ×1

Chi tiết depth2/3: `knowledge/generated/atlas-encoders.md`

### nops (14 modules)

- Lab: Pattern heuristic

  - `x86` ×2
  - `aarch64` ×1
  - `armle` ×1
  - `cmd` ×1
  - `loongarch64` ×1
  - `mipsbe` ×1
  - `php` ×1
  - `ppc` ×1
  - `riscv32le` ×1
  - `riscv64le` ×1
  - `sparc` ×1
  - `tty` ×1
  - `x64` ×1

Chi tiết depth2/3: `knowledge/generated/atlas-nops.md`

### evasion (12 modules)

- Lab: Detect technique names; không chạy evasion

  - `windows` ×9
  - `linux` ×3

Chi tiết depth2/3: `knowledge/generated/atlas-evasion.md`

## File sinh kèm

- `msf-full-atlas.md` (file này)
- `atlas-<class>.md` ×7
- `msf-coverage-checklist.md`
- `msf-module-index.csv` / `.json` (mọi module)
- `msf-atlas-depth3.json`

## Policy học tập

Catalog only · không exploit · không msfvenom · không scan prod · dos chỉ biết tồn tại.
