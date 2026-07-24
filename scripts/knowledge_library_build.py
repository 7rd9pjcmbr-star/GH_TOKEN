#!/usr/bin/env python3
"""Xây thư viện kiến thức toàn diện — học tập & thí nghiệm phòng thủ.

knowledge/ = curriculum + chương + thí nghiệm + index sinh từ harvest MSF.
KHÔNG: exploit PoC · payload · scan prod · msfvenom.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

KROOT = ROOT / "knowledge"
REPORTS = ROOT / "reports" / "telegram-classify"
GEN = KROOT / "generated"

CHAPTERS: list[dict[str, str]] = [
    {"id": "00", "file": "00-policy-ethics.md", "title": "Chính sách, đạo đức & phạm vi lab"},
    {"id": "01", "file": "01-lab-environment.md", "title": "Môi trường thí nghiệm (MaMoLab + Docker)"},
    {"id": "02", "file": "02-defender-threat-model.md", "title": "Mô hình đe dọa phía phòng thủ"},
    {"id": "03", "file": "03-msf-catalog-defenders.md", "title": "Metasploit như thư viện tham chiếu (defenders)"},
    {"id": "04", "file": "04-module-classes.md", "title": "Phân loại module → hành động học/lab"},
    {"id": "05", "file": "05-cve-triage.md", "title": "Phương pháp triage CVE"},
    {"id": "06", "file": "06-harden-network.md", "title": "Kiểm thử harden mạng & dịch vụ"},
    {"id": "07", "file": "07-endpoint-platforms.md", "title": "Endpoint Windows / Linux / mobile"},
    {"id": "08", "file": "08-web-checks.md", "title": "Kiểm thử bề mặt web"},
    {"id": "09", "file": "09-ioc-ttp.md", "title": "IOC, TTP & post-exploitation patterns"},
    {"id": "10", "file": "10-static-malware-lab.md", "title": "Lab phân tích mã tĩnh"},
    {"id": "11", "file": "11-mamolab-api.md", "title": "API MaMoLab & tự kiểm thử"},
    {"id": "12", "file": "12-study-path.md", "title": "Lộ trình học 7–14 buổi"},
    {"id": "13", "file": "13-msf-full-atlas.md", "title": "Atlas Metasploit đầy đủ — có những gì (không bỏ sót)"},
]

EXPERIMENTS: list[dict[str, str]] = [
    {"id": "EXP-01", "file": "EXP-01-lab-audit.md", "title": "Self-audit MaMoLab"},
    {"id": "EXP-02", "file": "EXP-02-static-analyze.md", "title": "Phân tích tĩnh mẫu trong quarantine"},
    {"id": "EXP-03", "file": "EXP-03-cve-backlog.md", "title": "Lập backlog CVE từ harvest"},
    {"id": "EXP-04", "file": "EXP-04-harden-http.md", "title": "Checklist harden HTTP (owned)"},
    {"id": "EXP-05", "file": "EXP-05-ioc-patterns.md", "title": "Ánh xạ post-TTP → IOC"},
    {"id": "EXP-06", "file": "EXP-06-rank-priority.md", "title": "Ưu tiên vá theo Rank MSF"},
    {"id": "EXP-07", "file": "EXP-07-policy-deny.md", "title": "Xác minh deny-list lab"},
    {"id": "EXP-08", "file": "EXP-08-atlas-coverage.md", "title": "Đối chiếu đủ 86 nhánh atlas MSF"},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_dirs() -> None:
    (KROOT / "experiments").mkdir(parents=True, exist_ok=True)
    GEN.mkdir(parents=True, exist_ok=True)


def write_if_absent(path: Path, content: str, *, force: bool = False) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return True


# ── Seed chapter bodies ─────────────────────────────────────────

SEEDS: dict[str, str] = {}

SEEDS["README.md"] = """# Thư viện kiến thức — Học tập & Thí nghiệm (phòng thủ)

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
"""

SEEDS["CURRICULUM.md"] = """# Curriculum — Lộ trình học & thí nghiệm

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
"""

SEEDS["00-policy-ethics.md"] = """# 00 — Chính sách, đạo đức & phạm vi lab

## Học gì

Phân biệt **học kiến thức tấn công công khai** (CVE, kỹ thuật đã công bố) với **hành vi tấn công**.  
Thư viện này chỉ phục vụ lớp thứ nhất + phòng thủ.

## Quy tắc cứng

1. Chỉ thí nghiệm trên hệ **owned** hoặc lab cô lập.
2. Không generate payload, không chạy exploit framework trên target thật.
3. Không dùng dump stealer / Acc_all để đăng nhập.
4. Không paste mẫu độc vào chat công khai.
5. Mọi “module Metasploit” trong tài liệu = **tham chiếu catalog**, không phải lệnh chạy.

## Ánh xạ policy code

- `js/lab/policy.js` → `noExploitGeneration`, `neverExecuteSample`
- `docs/SECURITY-LAB.md`
- Deny: msfvenom · msfrpcd · detonate

## Bài tập tư duy

Viết 5 dòng: “Nếu tôi thấy module `exploits/windows/...` trong catalog, việc đúng để làm tiếp theo là gì?”  
(Đáp án mong đợi: tra CVE → kiểm tra patch owned → rule detect — không `exploit`.)
"""

SEEDS["01-lab-environment.md"] = """# 01 — Môi trường thí nghiệm

## Ba lớp cô lập

1. **Host UI `/lab/`** — CSP chặt, Worker static-only
2. **MaMoLab `js/lab/*`** — policy · static · indicators · harden · report
3. **Docker `docker/lab`** — `network_mode: none`, RO quarantine

## Thao tác chuẩn

```bash
# Browser
# mở /lab/ → dán text → Analyze → Audit

# Docker
mkdir -p quarantine reports
# copy mẫu owned vào quarantine/
docker compose -f docker/lab/docker-compose.yml run --rm lab analyze /quarantine/<file>
```

## Kiểm chứng môi trường

- [ ] `MaMoLab.audit()` pass
- [ ] Docker lab không có network
- [ ] Không cài msfvenom trong image lab

## Liên kết

- `docs/SECURITY-LAB.md`
- `docker/lab/README.md`
- EXP-01, EXP-02, EXP-07
"""

SEEDS["02-defender-threat-model.md"] = """# 02 — Mô hình đe dọa phía phòng thủ

## Tài sản (assets)

- Endpoint (Windows/Linux)
- Dịch vụ mạng (HTTP, SSH, SMB, RDP, DB)
- Ứng dụng web / API
- Credential & session
- Pipeline CI / secrets

## Kẻ thù (từ góc catalog MSF)

| Class MSF | Ý nghĩa với defender |
|-----------|----------------------|
| exploit | CVE đã có kỹ thuật công khai → vá/detect |
| auxiliary/scanner | Bề mặt bị dò → harden |
| post | Sau xâm nhập → IOC persistence/lateral |
| payload/encoder | Artifact → signature/YARA |
| evasion | Kỹ thuật né AV → nâng detection |

## Câu hỏi threat model mỗi hệ owned

1. Attack surface nào expose?
2. CVE nào trong harvest khớp stack?
3. Log/detect chỗ nào bắt được post-TTP?
4. Blast radius nếu credential lộ?

## Bài tập

Chọn 1 hệ owned → điền bảng trên (1 trang).
"""

SEEDS["03-msf-catalog-defenders.md"] = """# 03 — Metasploit như thư viện tham chiếu

Metasploit Framework ≈ **bách khoa kỹ thuật đã công bố**.  
Dùng để học *cái gì tồn tại*, không để *bắn* vào hệ thống.

## Công cụ trong repo

```bash
python3 scripts/metasploit_suite_mapper.py      # taxonomy
python3 scripts/metasploit_library_harvest.py   # ~5k modules metadata
python3 scripts/metasploit_testing_knowledge.py # playbook P1–P6
python3 scripts/knowledge_library_build.py --with-harvest
```

## Cách đọc một module path

`exploits/windows/http/something_cve_2021_....rb`

- Platform: windows
- Vector: http
- Hành động học: tìm CVE → patch → WAF/IDS rule
- Hành động cấm: `use` / `exploit` trên prod

## Rank (ưu tiên học/vá)

excellent/great → ưu tiên hiểu + vá  
manual → điều kiện đặc biệt, ghi chú

Xem thêm: `generated/msf-summary.json` sau harvest.
"""

SEEDS["04-module-classes.md"] = """# 04 — Phân loại module → hành động học/lab

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
"""

SEEDS["05-cve-triage.md"] = """# 05 — Phương pháp triage CVE

## Quy trình

1. Harvest → `metasploit_cve_index.csv`
2. Lọc năm gần (2023–2026) hoặc khớp stack owned
3. Với mỗi CVE: **expose?** → **patched?** → **detect?** → **accepted risk?**
4. Gán owner + hạn

## Mẫu phiếu triage

```
CVE:
Module paths (catalog):
Stack owned khớp? (y/n + evidence)
Patch available / applied:
Detection (log/rule/YARA):
Ưu tiên (P1–P4):
Ghi chú:
```

## Thí nghiệm

EXP-03, EXP-06

## Lưu ý

Số “module refs” cao ≠ phổ biến trên mạng bạn — luôn đối chiếu inventory owned.
"""

SEEDS["06-harden-network.md"] = """# 06 — Kiểm thử harden mạng & dịch vụ

Dùng **tên family** `auxiliary/scanner/*` làm checklist — kiểm bằng công cụ owned (config review, nmap owned, CIS), không MSF scan prod.

## Family trọng tâm

| Family | Kiểm gì |
|--------|---------|
| http | TLS, auth, upload, version leak, CSP |
| ssh | PasswordAuth, RootLogin, cipher |
| smb | SMBv1, signing, guest |
| rdp | NLA, expose |
| ssl | TLS1.2+, HSTS |
| snmp | community, v3 |
| mysql/mssql/postgres/oracle | bind, default creds |
| redis | requirepass, bind |
| ldap/kerberos | anonymous, LDAPS |

Playbook chi tiết: `python3 scripts/metasploit_testing_knowledge.py`

## Thí nghiệm

EXP-04
"""

SEEDS["07-endpoint-platforms.md"] = """# 07 — Endpoint Windows / Linux / mobile

## Windows (từ subtree exploits/windows)

Học: patch cadence, ASR, credential guard, RDP harden, macro surface.  
Lab: IOC persistence (run key, service, WMI) — phát hiện, không cài.

## Linux (exploits/linux + local)

Học: kernel CVE, SUID, sudoers, sshd.  
Lab: audit `find` SUID trên VM owned.

## Mobile / multi

Android exported components; multi/http → app server CVE.

## Bài tập

Chọn Windows **hoặc** Linux owned → 10 mục harden từ catalog platform.
"""

SEEDS["08-web-checks.md"] = """# 08 — Kiểm thử bề mặt web

## Chủ đề học (từ catalog http)

- RCE class lịch sử: deserialization, template injection, upload
- Authn/z bypass patterns
- Disclosure (version, path, debug)
- Dependency CVE (Log4Shell, Rails YAML, …)

## Cách thí nghiệm an toàn

1. App **owned** / staging
2. Checklist cấu hình + dependency scan (OWASP Dependency-Check, npm audit, …)
3. Không chạy module exploit MSF

## MaMoLab

Dán response/header/log text vào `/lab/` → static heuristics.

EXP-04 bổ sung phần HTTP.
"""

SEEDS["09-ioc-ttp.md"] = """# 09 — IOC, TTP & post patterns

## Nguồn catalog

`modules/post/**/gather|escalate|manage` ≈ gợi ý TTP.

Ví dụ hướng detect (không chạy post):

- Credential dump tools/paths
- Persistence: service, cron, registry
- Lateral: smb/wmi/ssh hop patterns

## Lab repo

- `js/lab/indicators.js`
- Docker analyze → IOC section trong report

## Thí nghiệm

EXP-05: chọn 5 post path → viết 5 IOC giả định (path/hash/log).
"""

SEEDS["10-static-malware-lab.md"] = """# 10 — Lab phân tích mã tĩnh

## Quy trình

1. Đặt mẫu vào `quarantine/` (không mở trên host)
2. `docker ... lab analyze /quarantine/<file>`
3. Đọc entropy, string heuristics, hash
4. `wipe` sau khi xong
5. Hoặc dán text vào `/lab/` (Worker)

## Liên hệ payload catalog

Tên họ: meterpreter, reverse_tcp, shikata → chỉ để **nhận diện chuỗi**, không generate.

## Policy

`educationalHeuristicsOnly` · `noAttackPayloads`
"""

SEEDS["11-mamolab-api.md"] = """# 11 — API MaMoLab & tự kiểm thử

```js
await MaMoLab.analyze(text)
MaMoLab.audit()
MaMoLab.wipe()
MaMoLab.describe()
```

## Owns

`malware-static` · `security-audit` · `sandbox-policy` · `ioc-triage`

## Self-test checklist (harden.js)

CSP · policy loaded · no eval helper · Worker · ownership · storage leak

EXP-01 bắt buộc trước mọi thí nghiệm khác.
"""

SEEDS["12-study-path.md"] = """# 12 — Lộ trình học 7–14 buổi

## Gói tối thiểu (7 buổi)

Làm đúng bảng trong CURRICULUM.md.

## Gói sâu (14 buổi)

- Buổi 8–9: đào sâu CVE năm gần theo stack owned
- Buổi 10: viết YARA/IOC nội bộ từ chỉ mục payload names
- Buổi 11: harden full family scanner top-10
- Buổi 12: diễn tập báo cáo điều hành (1 CVE P1)
- Buổi 13: ôn policy + red-team-questions / blue answers
- Buổi 14: tự thêm 1 chương vào `knowledge/` (PR nội bộ)

## Mở rộng thư viện

```bash
python3 scripts/knowledge_library_build.py status
# thêm file chương mới → cập nhật CHAPTERS trong scripts/knowledge_library_build.py
```
"""

SEEDS["glossary.md"] = """# Glossary (rút gọn)

| Thuật ngữ | Nghĩa trong thư viện này |
|-----------|-------------------------|
| Catalog | Metadata module MSF đọc-only |
| Harvest | Quét modules/ lấy Name/Rank/CVE |
| Rank | Độ tin cậy catalog → ưu tiên vá |
| check() | Hook verify trong module — chỉ mục, không chạy |
| Quarantine | Thư mục mẫu RO cho Docker lab |
| MaMoLab | Lab trình duyệt phòng thủ |
| IOC | Chỉ báo thỏa thuận để detect |
| TTP | Tactic/Technique/Procedure |
| Owned | Hệ bạn có quyền thí nghiệm |
| Deny | Hành động bị cấm bởi policy |
"""


def experiment_seed(exp: dict[str, str]) -> str:
    bodies = {
        "EXP-01": """# EXP-01 — Self-audit MaMoLab

## Mục tiêu
Xác nhận lab đủ an toàn trước khi học mẫu.

## Bước
1. Mở `/lab/`
2. Chạy **Kiểm thử bảo mật** / `MaMoLab.audit()`
3. Ghi lại passed/failed

## Pass
`audit.ok === true`. Sửa mọi fail trước khi làm EXP khác.

## Cấm
Tắt policy, thêm eval helper, persist sample.
""",
        "EXP-02": """# EXP-02 — Phân tích tĩnh trong quarantine

## Mục tiêu
Quen quy trình Docker lab.

## Bước
1. Tạo file text owned (log/header) vào `quarantine/sample.txt`
2. `docker compose -f docker/lab/docker-compose.yml run --rm lab analyze /quarantine/sample.txt`
3. Đọc JSON trong `reports/`
4. `wipe` / xóa mẫu

## Pass
Có báo cáo hash/entropy/findings; không có network trong container.
""",
        "EXP-03": """# EXP-03 — Backlog CVE từ harvest

## Bước
```bash
python3 scripts/metasploit_library_harvest.py
python3 scripts/knowledge_library_build.py --with-harvest
```
1. Mở `reports/telegram-classify/metasploit_cve_index.csv` hoặc `knowledge/generated/cve-top.md`
2. Chọn ≥10 CVE khớp / gần stack owned
3. Điền phiếu triage (chương 05)

## Pass
Bảng backlog có cột patch/detect/priority.
""",
        "EXP-04": """# EXP-04 — Harden HTTP (owned)

## Bước
1. Chọn 1 web owned/staging
2. Checklist: TLS, HSTS, auth, upload, version disclosure, CSP
3. Đối chiếu family `http` trong testing knowledge
4. Ghi pass/fail + evidence

## Cấm
Không dùng MSF auxiliary scanner lên prod.
""",
        "EXP-05": """# EXP-05 — Post-TTP → IOC

## Bước
1. Từ harvest/subtree `post/`, chọn 5 path gather/persist
2. Với mỗi path: giả định artifact (path registry/file/log)
3. Viết IOC rule giả định (1 dòng/mục)
4. So với `js/lab/indicators.js` — thiếu gì?

## Pass
5 IOC + ghi chú gap.
""",
        "EXP-06": """# EXP-06 — Rank → ưu tiên vá

## Bước
1. Lấy `by_rank` từ harvest / `knowledge/generated/msf-summary.json`
2. Liệt kê module excellent có CVE năm ≥2023
3. Gán P1/P2 theo expose owned

## Pass
Danh sách ≥5 mục P1/P2 có lý do.
""",
        "EXP-07": """# EXP-07 — Xác minh deny-list

## Bước
1. Đọc `js/lab/policy.js` deny/allow
2. Xác nhận không có msfvenom trong Docker image lab
3. Chạy `python3 scripts/metasploit_suite_mapper.py` — xem blocked surfaces
4. Checklist: không payload binary trong workspace

## Pass
Biên bản “deny còn hiệu lực” ký ngày.
""",
        "EXP-08": """# EXP-08 — Đối chiếu đủ nhánh atlas MSF

## Mục tiêu
Chứng minh không bỏ sót nhánh depth-1.

## Bước
```bash
python3 scripts/metasploit_full_atlas.py
python3 scripts/knowledge_library_build.py --with-atlas
```
1. Đếm checklist trong `msf-coverage-checklist.md` (~86)
2. Spot-check 7 file `atlas-*.md` + CSV ≈5043 dòng module
3. Ghi biên bản số nhánh/module/CVE

## Pass
Đủ 7 lớp · checklist ≥80 · không chạy exploit.
""",
    }
    return bodies.get(exp["id"], f"# {exp['id']} — {exp['title']}\n\nTODO\n")


def load_harvest() -> dict[str, Any]:
    jp = REPORTS / "metasploit_library_knowledge.json"
    if not jp.is_file():
        return {}
    try:
        return json.loads(jp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_generated(harvest: dict[str, Any]) -> dict[str, str]:
    GEN.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    summary = {
        "built_at": utc_now(),
        "source": "metasploit_library_harvest",
        "modules_total": (harvest.get("summary") or {}).get("modules_total"),
        "unique_cves": (harvest.get("summary") or {}).get("unique_cves"),
        "totals": harvest.get("totals"),
        "by_rank": harvest.get("by_rank"),
        "by_platform": harvest.get("by_platform"),
        "top_cve_years": harvest.get("top_cve_years"),
        "subtrees_exploits": (harvest.get("subtrees") or {}).get("exploits"),
        "subtrees_auxiliary": (harvest.get("subtrees") or {}).get("auxiliary"),
    }
    p = GEN / "msf-summary.json"
    p.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["summary"] = str(p)

    lines = [
        "# Generated — MSF summary (readonly)",
        f"Built: {summary['built_at']}",
        f"Modules: {summary.get('modules_total')} · CVE unique: {summary.get('unique_cves')}",
        "",
        "## Totals",
    ]
    for k, v in (summary.get("totals") or {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("\n## Rank")
    for k, v in (summary.get("by_rank") or {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("\n## Platform top")
    for k, v in list((summary.get("by_platform") or {}).items())[:15]:
        lines.append(f"- {k}: {v}")
    lines.append("\n## Exploit subtrees")
    for k, v in list((summary.get("subtrees_exploits") or {}).items())[:20]:
        lines.append(f"- exploits/{k}: {v}")
    lines.append("\n## Auxiliary subtrees")
    for k, v in list((summary.get("subtrees_auxiliary") or {}).items())[:15]:
        lines.append(f"- auxiliary/{k}: {v}")
    lines.append("\n> Regenerate: `python3 scripts/knowledge_library_build.py --with-harvest`")
    tp = GEN / "msf-summary.md"
    tp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    paths["summary_md"] = str(tp)

    cve_lines = ["# Top CVE (từ harvest)", ""]
    for cve, info in list((harvest.get("cve_index") or {}).items())[:40]:
        cve_lines.append(f"## {cve} ×{info.get('modules_n')}")
        for m in (info.get("modules") or [])[:5]:
            cve_lines.append(f"- `{m}`")
        cve_lines.append("")
    cp = GEN / "cve-top.md"
    cp.write_text("\n".join(cve_lines) + "\n", encoding="utf-8")
    paths["cve"] = str(cp)

    # Study cards: one per class
    cards = []
    for cls, role in {
        "exploits": "malware-static / CVE triage",
        "auxiliary": "security-audit / harden checklist",
        "post": "ioc-triage / TTP",
        "payloads": "malware-static / signature only",
        "encoders": "malware-static / entropy",
        "evasion": "malware-static / detect bypass tech",
    }.items():
        n = (harvest.get("totals") or {}).get(cls, 0)
        cards.append(
            {
                "class": cls,
                "modules": n,
                "study_role": role,
                "samples": (harvest.get("samples_by_class") or {}).get(cls, [])[:8],
            }
        )
    sp = GEN / "study-cards.json"
    sp.write_text(json.dumps(cards, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["cards"] = str(sp)

    readme = GEN / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# knowledge/generated/",
                "Sinh tự động từ harvest — có thể commit summary nhỏ.",
                "Không chứa source `.rb` Metasploit.",
                "",
                "```bash",
                "python3 scripts/knowledge_library_build.py --with-harvest",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return paths


def write_index(
    harvest: dict[str, Any],
    generated: dict[str, str],
    *,
    atlas_info: dict[str, Any] | None = None,
) -> Path:
    atlas_info = atlas_info or {}
    idx = {
        "ok": True,
        "module": "knowledge_library",
        "built_at": utc_now(),
        "title": "Thư viện kiến thức học tập & thí nghiệm (phòng thủ)",
        "policy": {
            "defensive_only": True,
            "no_exploit": True,
            "no_payload_gen": True,
            "lab_only": True,
            "msf_complete_atlas": True,
        },
        "roots": {
            "knowledge": str(KROOT),
            "curriculum": str(KROOT / "CURRICULUM.md"),
            "experiments": str(KROOT / "experiments"),
            "generated": str(GEN),
            "msf_atlas": str(GEN / "msf-full-atlas.md"),
        },
        "chapters": CHAPTERS,
        "experiments": EXPERIMENTS,
        "harvest": {
            "modules_total": (harvest.get("summary") or {}).get("modules_total"),
            "unique_cves": (harvest.get("summary") or {}).get("unique_cves"),
            "checked_at": harvest.get("checked_at"),
        },
        "atlas": {
            "modules": atlas_info.get("grand_total_rb"),
            "branches_depth1": atlas_info.get("coverage_branches"),
            "unique_cves": atlas_info.get("unique_cves"),
            "verdict": atlas_info.get("verdict"),
            "files": [
                "generated/msf-full-atlas.md",
                "generated/msf-coverage-checklist.md",
                "generated/msf-module-index.csv",
                "generated/atlas-exploits.md",
                "generated/atlas-auxiliary.md",
                "generated/atlas-post.md",
                "generated/atlas-payloads.md",
                "generated/atlas-encoders.md",
                "generated/atlas-nops.md",
                "generated/atlas-evasion.md",
            ],
        },
        "generated_files": generated,
        "cli": [
            "python3 scripts/knowledge_library_build.py",
            "python3 scripts/knowledge_library_build.py --with-atlas",
            "python3 scripts/knowledge_library_build.py --with-harvest",
            "python3 scripts/metasploit_full_atlas.py",
            "python3 scripts/metasploit_testing_knowledge.py",
        ],
        "verdict": (
            f"✅ Thư viện kiến thức · chương={len(CHAPTERS)} · "
            f"EXP={len(EXPERIMENTS)} · "
            f"MSF modules={atlas_info.get('grand_total_rb') or (harvest.get('summary') or {}).get('modules_total') or 'chưa atlas'} · "
            f"nhánh={atlas_info.get('coverage_branches') or '-'} · "
            f"CVE={atlas_info.get('unique_cves') or (harvest.get('summary') or {}).get('unique_cves') or '-'}"
        ),
    }
    ip = KROOT / "INDEX.json"
    ip.write_text(json.dumps(idx, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "knowledge_library_status.json").write_text(
        json.dumps(idx, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORTS / "knowledge_library_status.txt").write_text(format_status(idx) + "\n", encoding="utf-8")
    return ip


def format_status(idx: dict[str, Any] | None = None) -> str:
    if idx is None:
        ip = KROOT / "INDEX.json"
        if ip.is_file():
            idx = json.loads(ip.read_text(encoding="utf-8"))
        else:
            return "Chưa build. Chạy: python3 scripts/knowledge_library_build.py"
    lines: list[str] = []
    L = lines.append
    L("📖 THƯ VIỆN KIẾN THỨC · HỌC TẬP & THÍ NGHIỆM")
    L(f"Lúc: {idx.get('built_at')}")
    L(f"Verdict: {idx.get('verdict')}")
    L("Policy: phòng thủ · không exploit · không payload · lab only")
    L("")
    L("=== Chương ===")
    for c in idx.get("chapters") or CHAPTERS:
        path = KROOT / c["file"]
        mark = "✓" if path.is_file() else "✗"
        L(f"  {mark} [{c['id']}] {c['title']}")
        L(f"      knowledge/{c['file']}")
    L("")
    L("=== Thí nghiệm ===")
    for e in idx.get("experiments") or EXPERIMENTS:
        path = KROOT / "experiments" / e["file"]
        mark = "✓" if path.is_file() else "✗"
        L(f"  {mark} {e['id']}: {e['title']}")
    L("")
    h = idx.get("harvest") or {}
    L(f"=== Harvest MSF === modules={h.get('modules_total')} CVE={h.get('unique_cves')}")
    a = idx.get("atlas") or {}
    L(
        f"=== Atlas đầy đủ === modules={a.get('modules')} · "
        f"nhánh_d1={a.get('branches_depth1')} · CVE={a.get('unique_cves')}"
    )
    if a.get("verdict"):
        L(f"  {a.get('verdict')}")
    L("  Đọc: knowledge/generated/msf-full-atlas.md · chương 13 · EXP-08")
    L("")
    L("=== Bắt đầu ===")
    L("  1. knowledge/README.md")
    L("  2. knowledge/13-msf-full-atlas.md  ← Metasploit có những gì")
    L("  3. knowledge/CURRICULUM.md → EXP-01")
    L("  $ python3 scripts/metasploit_full_atlas.py")
    L("  $ python3 scripts/knowledge_library_build.py --with-atlas")
    return "\n".join(lines)


def build(
    *,
    force_seeds: bool = False,
    with_harvest: bool = False,
    with_atlas: bool = False,
) -> dict[str, Any]:
    ensure_dirs()
    if with_harvest:
        from metasploit_library_harvest import harvest

        harvest(refresh=False)
        try:
            from metasploit_testing_knowledge import build_report as build_test

            build_test()
        except Exception:  # noqa: BLE001
            pass

    atlas_info: dict[str, Any] = {}
    if with_atlas or with_harvest:
        try:
            from metasploit_full_atlas import build_atlas

            atlas_info = build_atlas(with_module_index=True)
        except Exception as e:  # noqa: BLE001
            atlas_info = {"ok": False, "error": str(e)}

    written = []
    for fname in ("README.md", "CURRICULUM.md", "glossary.md"):
        if fname in SEEDS and write_if_absent(KROOT / fname, SEEDS[fname], force=force_seeds):
            written.append(fname)

    for ch in CHAPTERS:
        body = SEEDS.get(ch["file"])
        if not body:
            continue
        if write_if_absent(KROOT / ch["file"], body, force=force_seeds):
            written.append(ch["file"])

    for exp in EXPERIMENTS:
        body = experiment_seed(exp)
        if write_if_absent(KROOT / "experiments" / exp["file"], body, force=force_seeds):
            written.append(f"experiments/{exp['file']}")

    harvest = load_harvest()
    generated = write_generated(harvest) if harvest else write_generated({})
    if (GEN / "msf-full-atlas.md").is_file():
        generated["full_atlas"] = str(GEN / "msf-full-atlas.md")
        generated["coverage"] = str(GEN / "msf-coverage-checklist.md")
        generated["module_index_csv"] = str(GEN / "msf-module-index.csv")
    idx_path = write_index(harvest, generated, atlas_info=atlas_info)
    return {
        "ok": True,
        "written_seeds": written,
        "index": str(idx_path),
        "atlas": {
            "modules": atlas_info.get("grand_total_rb"),
            "branches": atlas_info.get("coverage_branches"),
            "cves": atlas_info.get("unique_cves"),
            "verdict": atlas_info.get("verdict"),
        },
        "status": format_status(),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build thư viện kiến thức học tập/thí nghiệm")
    ap.add_argument("command", nargs="?", default="build", choices=("build", "status"))
    ap.add_argument("--force-seeds", action="store_true", help="Ghi đè chapter seeds")
    ap.add_argument("--with-harvest", action="store_true")
    ap.add_argument(
        "--with-atlas",
        action="store_true",
        help="Rà soát toàn bộ MSF modules/ → atlas không bỏ sót",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.command == "status":
        if not (KROOT / "INDEX.json").is_file():
            build(
                force_seeds=False,
                with_harvest=args.with_harvest,
                with_atlas=args.with_atlas,
            )
        text = format_status()
        if args.json:
            print((KROOT / "INDEX.json").read_text(encoding="utf-8"))
        else:
            print(text)
        return 0

    result = build(
        force_seeds=args.force_seeds,
        with_harvest=args.with_harvest,
        with_atlas=args.with_atlas,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["status"])
        if result.get("written_seeds"):
            print("\nSeeds mới:", ", ".join(result["written_seeds"][:20]))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
