#!/usr/bin/env python3
"""Kiến thức kiểm thử phòng thủ rút từ thư viện Metasploit.

Đây là playbook KIỂM THỬ — không phải bảng đếm module.
Ánh xạ catalog MSF → checklist / bước verify / MaMoLab audit.
KHÔNG chạy exploit · KHÔNG msfvenom · KHÔNG scan production.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
DEFAULT_MSF = Path("/tmp/msf-knowledge/modules")

# ── Playbook cố định (luôn có trong repo) ────────────────────────
# Nguồn: cấu trúc modules/ Metasploit Framework → hành động phòng thủ.

PHASES: list[dict[str, Any]] = [
    {
        "id": "P1",
        "name": "Inventory & scope",
        "msf_source": "modules/ taxonomy · suite mapper",
        "test": [
            "Xác định bề mặt: OS (win/linux/osx), dịch vụ (http/ssh/smb/rdp), app stack",
            "Chạy: python3 scripts/metasploit_suite_mapper.py",
            "Chạy: python3 scripts/metasploit_library_harvest.py",
            "Ghi scope vào báo cáo — không mở msfconsole trên máy làm việc",
        ],
        "pass_when": "Có map class→role + tổng module/CVE trong knowledge report",
    },
    {
        "id": "P2",
        "name": "CVE / vuln triage (không exploit)",
        "msf_source": "modules/exploits/* + References CVE",
        "test": [
            "Lọc CVE theo năm gần (2023–2026) từ metasploit_cve_index.csv",
            "Với mỗi CVE: patch status · detection rule · exposure trên owned stack",
            "Ưu tiên rank excellent/great (reliability cao trong catalog = ưu tiên vá)",
            "Không chạy module exploit — chỉ dùng path/name làm chỉ mục triage",
        ],
        "pass_when": "Backlog CVE có owner + trạng thái patched/mitigated/accepted",
    },
    {
        "id": "P3",
        "name": "Harden checklist từ auxiliary/scanner",
        "msf_source": "modules/auxiliary/scanner/*",
        "test": [
            "Dùng tên scanner family làm checklist cấu hình (không quét prod bằng MSF)",
            "http → CSP, auth, upload, path traversal, version disclosure",
            "ssh/smb/rdp/ftp/telnet → disable legacy · MFA · network ACL",
            "snmp/ldap/kerberos → community string / bind ACL / ticket hygiene",
            "ssl → TLS1.2+ · cipher · cert chain",
            "db (mysql/mssql/postgres/oracle) → không expose 0.0.0.0 · strong auth",
        ],
        "pass_when": "Checklist harden theo family đã đánh dấu pass/fail trên owned assets",
    },
    {
        "id": "P4",
        "name": "IOC / post-TTP detection",
        "msf_source": "modules/post/*/gather|escalate|manage|persist*",
        "test": [
            "Map post modules → TTP: credential dump, persistence, lateral",
            "Viết/ghép IOC vào js/lab/indicators.js hoặc SIEM rules",
            "Mẫu nghi → quarantine/ → docker/lab analyze (static only)",
        ],
        "pass_when": "Có rule detect cho ít nhất gather + persistence patterns",
    },
    {
        "id": "P5",
        "name": "Payload / encoder signature (no gen)",
        "msf_source": "modules/payloads|encoders|evasion",
        "test": [
            "Chỉ signature: meterpreter · reverse_tcp · shikata · xor patterns",
            "policy.noExploitGeneration = true · cấm msfvenom trong lab image",
            "MaMoLab.audit() phải pass (nút Kiểm thử bảo mật /lab/)",
        ],
        "pass_when": "Lab audit OK · không có binary MSF payload trong workspace",
    },
    {
        "id": "P6",
        "name": "Self-test lab (MaMoLab)",
        "msf_source": "js/lab/harden.js · policy.js · docker/lab",
        "test": [
            "Mở /lab/ → Kiểm thử bảo mật → MaMoLab.audit()",
            "docker compose -f docker/lab/docker-compose.yml run --rm lab analyze /quarantine/<file>",
            "Xác nhận network_mode=none · không msfrpcd",
        ],
        "pass_when": "audit.ok == true · analyze chỉ static findings",
    },
]

# Checklist kiểm thử theo family scanner (defensive)
SCANNER_TESTS: list[dict[str, Any]] = [
    {"family": "http", "n_hint": 315, "checks": ["version disclosure", "default creds pages", "upload paths", "CSP/CORS", "auth bypass surface"]},
    {"family": "ssh", "n_hint": 13, "checks": ["password auth off", "root login", "weak ciphers", "key-only"]},
    {"family": "smb", "n_hint": 12, "checks": ["SMBv1 off", "guest", "signing", "share ACL"]},
    {"family": "rdp", "n_hint": 0, "checks": ["NLA", "network exposure", "lockout"]},
    {"family": "ssl", "n_hint": 0, "checks": ["TLS≥1.2", "HSTS", "cert validity"]},
    {"family": "snmp", "n_hint": 17, "checks": ["public/private community", "v3 only", "ACL"]},
    {"family": "ftp", "n_hint": 9, "checks": ["anonymous", "cleartext", "replace with SFTP"]},
    {"family": "mysql", "n_hint": 7, "checks": ["bind address", "root remote", "old auth plugin"]},
    {"family": "mssql", "n_hint": 5, "checks": ["xp_cmdshell", "sa account", "TLS"]},
    {"family": "postgres", "n_hint": 5, "checks": ["pg_hba", "superuser remote"]},
    {"family": "oracle", "n_hint": 12, "checks": ["listener ACL", "default accounts"]},
    {"family": "ldap", "n_hint": 0, "checks": ["anonymous bind", "LDAPS"]},
    {"family": "kerberos", "n_hint": 0, "checks": ["PAC/ticket hygiene", "spn audit"]},
    {"family": "redis", "n_hint": 0, "checks": ["requirepass", "bind localhost", "no COMMAND rename"]},
    {"family": "vmware", "n_hint": 12, "checks": ["vCenter patch", "Log4Shell legacy", "SSO hardening"]},
    {"family": "sap", "n_hint": 36, "checks": ["gateway ACL", "default users", "diag ports"]},
    {"family": "scada", "n_hint": 15, "checks": ["air-gap / VLAN", "default eng passwords"]},
    {"family": "discovery", "n_hint": 7, "checks": ["asset inventory sync", "unexpected listeners"]},
]

# Exploit platform → kiểm thử endpoint
PLATFORM_TESTS: list[dict[str, str]] = [
    {"platform": "windows", "test": "Patch Tuesday backlog · Defender ASR · LSA protection · RDP harden"},
    {"platform": "linux", "test": "kernel/CVE local · sudoers · SUID audit · sshd_config"},
    {"platform": "multi/http", "test": "WAF/CSP · dependency CVE (Log4j, Rails, PHP) · upload"},
    {"platform": "android", "test": "Play Integrity · debuggable off · exported components"},
    {"platform": "osx", "test": "SIP · Gatekeeper · MDM baseline"},
    {"platform": "php", "test": "disable_functions · open_basedir · framework CVE"},
]

RANK_PRIORITY = [
    {"rank": "excellent/great", "testing_action": "Vá / mitigate trước — catalog coi là ổn định"},
    {"rank": "good/normal", "testing_action": "Đưa vào backlog detect + patch window"},
    {"rank": "average/low", "testing_action": "Theo dõi; ưu tiên thấp trừ khi owned stack match"},
    {"rank": "manual", "testing_action": "Cần điều kiện đặc biệt — ghi chú trong triage, không auto"},
]

DENY = [
    "msfconsole exploit / run trên target ngoài lab owned",
    "msfvenom generate payload",
    "msfrpcd expose",
    "auxiliary scanner quét mạng production",
    "dùng dump Acc_all / stealer để login",
]

CLI_NOW = [
    "python3 scripts/metasploit_testing_knowledge.py",
    "python3 scripts/metasploit_library_harvest.py",
    "python3 scripts/metasploit_suite_mapper.py",
    "docker compose -f docker/lab/docker-compose.yml run --rm lab help",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def live_scanner_counts(modules: Path) -> dict[str, int]:
    root = modules / "auxiliary" / "scanner"
    if not root.is_dir():
        return {}
    c: Counter[str] = Counter()
    for p in root.rglob("*.rb"):
        rel = p.relative_to(root)
        c[rel.parts[0] if len(rel.parts) > 1 else "_"] += 1
    return dict(c.most_common(40))


def live_check_stats(modules: Path) -> dict[str, int]:
    """Số module có def check (safe-ish verify hook trong catalog)."""
    out = {"exploits_with_check": 0, "exploits_total": 0}
    ex = modules / "exploits"
    if not ex.is_dir():
        return out
    for p in ex.rglob("*.rb"):
        out["exploits_total"] += 1
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "\ndef check" in text or "\n  def check" in text:
            out["exploits_with_check"] += 1
    return out


def load_harvest_summary() -> dict[str, Any]:
    jp = REPORTS / "metasploit_library_knowledge.json"
    if not jp.is_file():
        return {}
    try:
        data = json.loads(jp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {
        "modules_total": (data.get("summary") or {}).get("modules_total"),
        "unique_cves": (data.get("summary") or {}).get("unique_cves"),
        "totals": data.get("totals"),
        "by_rank": data.get("by_rank"),
        "top_cve_years": data.get("top_cve_years"),
        "checked_at": data.get("checked_at"),
    }


def build_report(*, modules_root: Path | None = None) -> dict[str, Any]:
    root = modules_root or DEFAULT_MSF
    harvest = load_harvest_summary()
    scanners = live_scanner_counts(root) if root.is_dir() else {}
    checks = live_check_stats(root) if root.is_dir() else {}

    # Enrich scanner tests with live counts
    enriched_scanners = []
    for row in SCANNER_TESTS:
        fam = row["family"]
        n = scanners.get(fam, row.get("n_hint") or 0)
        enriched_scanners.append({**row, "modules_live": n})

    recent_cves = []
    if harvest.get("top_cve_years"):
        for y, n in list(harvest["top_cve_years"].items())[:6]:
            recent_cves.append({"year": y, "module_refs": n, "test": f"Patch/verify owned stack cho CVE-{y}-*"})

    report: dict[str, Any] = {
        "ok": True,
        "module": "metasploit_testing_knowledge",
        "checked_at": utc_now(),
        "title": "KIẾN THỨC KIỂM THỬ (phòng thủ) · từ thư viện Metasploit",
        "policy": {
            "defensive_only": True,
            "no_exploit_run": True,
            "no_payload_gen": True,
            "no_prod_scan": True,
        },
        "verdict": (
            "✅ Playbook kiểm thử sẵn sàng · "
            f"phases={len(PHASES)} · scanner_families={len(SCANNER_TESTS)} · "
            f"harvest_modules={harvest.get('modules_total') or 'chưa harvest'} · "
            f"CVE={harvest.get('unique_cves') or '-'}"
        ),
        "harvest_snapshot": harvest,
        "phases": PHASES,
        "scanner_tests": enriched_scanners,
        "platform_tests": PLATFORM_TESTS,
        "rank_priority": RANK_PRIORITY,
        "check_hook_stats": checks,
        "recent_cve_years": recent_cves,
        "deny": DENY,
        "cli_now": CLI_NOW,
        "mamolab": {
            "audit": "MaMoLab.audit() / js/lab/harden.js",
            "static": "docker/lab analyze · js/lab/static.js",
            "policy": "js/lab/policy.js noExploitGeneration",
        },
        "how_to_use": [
            "1) Đọc PHASES P1→P6 — đó là quy trình kiểm thử",
            "2) Đánh dấu pass/fail từng check trong scanner_tests trên owned assets",
            "3) CVE years gần → backlog vá; không chạy exploit module",
            "4) Self-test: /lab/ Kiểm thử bảo mật + docker/lab",
            "5) Panel Telegram: 🧪 MSF·kiểm thử",
        ],
    }
    write_outputs(report)
    return report


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "metasploit_testing_knowledge.json"
    tp = REPORTS / "metasploit_testing_knowledge.txt"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp)}


def format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("🧪 KIẾN THỨC KIỂM THỬ · METASPLOIT → PHÒNG THỦ")
    L(f"Lúc: {report.get('checked_at')}")
    L(f"Verdict: {report.get('verdict')}")
    L("Policy: không exploit · không payload · không scan prod")
    L("")
    h = report.get("harvest_snapshot") or {}
    if h:
        L(
            f"Harvest: modules={h.get('modules_total')} · CVE={h.get('unique_cves')} · "
            f"lúc {h.get('checked_at')}"
        )
        totals = h.get("totals") or {}
        if totals:
            L(
                "  "
                + " · ".join(f"{k}={v}" for k, v in totals.items() if v)
            )
        L("")
    L("=== CÁCH DÙNG NGAY ===")
    for s in report.get("how_to_use") or []:
        L(s)
    L("")
    L("=== PHASES KIỂM THỬ (P1→P6) ===")
    for p in report.get("phases") or []:
        L(f"[{p['id']}] {p['name']}")
        L(f"  Nguồn MSF: {p['msf_source']}")
        for t in p.get("test") or []:
            L(f"  ☐ {t}")
        L(f"  Pass khi: {p.get('pass_when')}")
        L("")
    L("=== CHECKLIST SCANNER FAMILY ===")
    for s in report.get("scanner_tests") or []:
        L(f"· {s['family']} (modules≈{s.get('modules_live', s.get('n_hint'))})")
        for c in s.get("checks") or []:
            L(f"    ☐ {c}")
    L("")
    L("=== PLATFORM → KIỂM THỬ ===")
    for p in report.get("platform_tests") or []:
        L(f"· {p['platform']}: {p['test']}")
    L("")
    L("=== RANK → ƯU TIÊN VÁ ===")
    for r in report.get("rank_priority") or []:
        L(f"· {r['rank']}: {r['testing_action']}")
    L("")
    ch = report.get("check_hook_stats") or {}
    if ch.get("exploits_total"):
        L(
            f"=== Catalog check() hooks === exploits có def check: "
            f"{ch.get('exploits_with_check')}/{ch.get('exploits_total')} "
            f"(dùng làm chỉ mục verify — không chạy exploit)"
        )
        L("")
    if report.get("recent_cve_years"):
        L("=== CVE YEARS → BACKLOG ===")
        for y in report["recent_cve_years"]:
            L(f"· {y['year']}: {y['module_refs']} refs → {y['test']}")
        L("")
    L("=== DENY ===")
    for d in report.get("deny") or []:
        L(f"  ✗ {d}")
    L("")
    L("=== MaMoLab ===")
    m = report.get("mamolab") or {}
    for k, v in m.items():
        L(f"· {k}: {v}")
    L("")
    L("=== CLI ===")
    for c in report.get("cli_now") or []:
        L(f"$ {c}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Kiến thức kiểm thử từ thư viện Metasploit")
    ap.add_argument("--modules", default="", help="Đường dẫn modules/")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--with-harvest",
        action="store_true",
        help="Chạy harvest trước rồi mới build playbook",
    )
    args = ap.parse_args(argv)
    if args.with_harvest:
        from metasploit_library_harvest import harvest

        harvest(refresh=False)
    root = Path(args.modules) if args.modules else None
    report = build_report(modules_root=root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
