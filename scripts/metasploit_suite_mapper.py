#!/usr/bin/env python3
"""Mapper thư viện Metasploit Framework / Suite — phòng thủ.

Catalog cấu trúc MSF (modules / interfaces / datastore) → ánh xạ MaMoLab:
  malware-static · ioc-triage · security-audit · sandbox-policy

KHÔNG: cài MSF, generate payload, chạy exploit, reverse-shell helper.
Chỉ: taxonomy + role map + (optional) inventory local install nếu có (readonly).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
STATE_PATH = SECRETS / "metasploit_suite_mapper.state.json"

# ── Suite surfaces (thành phần framework) ───────────────────────
SUITE_SURFACES: list[dict[str, Any]] = [
    {
        "id": "msfconsole",
        "kind": "interface",
        "title": "msfconsole — CLI chính",
        "defensive_role": "security-audit",
        "lab_action": "Không mở trên máy làm việc chính; chỉ inventory docs",
    },
    {
        "id": "msfvenom",
        "kind": "interface",
        "title": "msfvenom — payload builder",
        "defensive_role": "malware-static",
        "lab_action": "Detect signatures của output; KHÔNG generate",
        "blocked": True,
    },
    {
        "id": "msfrpcd",
        "kind": "interface",
        "title": "msfrpcd / RPC API",
        "defensive_role": "sandbox-policy",
        "lab_action": "Cấm expose RPC ra mạng lab; network_mode=none",
        "blocked": True,
    },
    {
        "id": "armitage",
        "kind": "interface",
        "title": "Armitage / Cobalt-adjacent UI (legacy)",
        "defensive_role": "ioc-triage",
        "lab_action": "IOC: GUI C2 patterns — triage only",
    },
    {
        "id": "pro_suite",
        "kind": "product",
        "title": "Metasploit Pro / InsightVM suite hooks",
        "defensive_role": "security-audit",
        "lab_action": "Map vuln findings → patch, không exploit",
    },
]

# ── Module library taxonomy ─────────────────────────────────────
MODULE_CLASSES: list[dict[str, Any]] = [
    {
        "class": "exploit",
        "path": "modules/exploits/",
        "title": "Exploit modules",
        "defensive_role": "malware-static",
        "detect": ["CVE refs", "target OS/service", "check() fingerprints"],
        "mamolab": ["static-text-scan", "pattern-heuristics"],
        "allow_in_lab": False,
        "note": "Catalog CVE ↔ module name cho triage — không chạy",
    },
    {
        "class": "auxiliary",
        "path": "modules/auxiliary/",
        "title": "Auxiliary (scanner/fuzzer/admin)",
        "defensive_role": "security-audit",
        "detect": ["scanner/*", "gather/*", "dos/*"],
        "mamolab": ["security-audit", "ioc-triage"],
        "allow_in_lab": False,
        "note": "Scanner patterns → harden checklist; không quét mạng production",
    },
    {
        "class": "post",
        "path": "modules/post/",
        "title": "Post-exploitation",
        "defensive_role": "ioc-triage",
        "detect": ["persistence", "gather credentials", "lateral"],
        "mamolab": ["ioc-triage"],
        "allow_in_lab": False,
        "note": "Map TTPs → detection rules",
    },
    {
        "class": "payload",
        "path": "modules/payloads/",
        "title": "Payloads (singles/stagers/stages)",
        "defensive_role": "malware-static",
        "detect": ["meterpreter", "shell_*", "reverse_tcp", "bind_tcp"],
        "mamolab": ["malware-static", "entropy-metrics"],
        "allow_in_lab": False,
        "blocked": True,
        "note": "Chỉ signature/YARA — noExploitGeneration",
    },
    {
        "class": "encoder",
        "path": "modules/encoders/",
        "title": "Encoders",
        "defensive_role": "malware-static",
        "detect": ["shikata_ga_nai", "xor", "base64 wrappers"],
        "mamolab": ["entropy-metrics", "pattern-heuristics"],
        "allow_in_lab": False,
        "blocked": True,
    },
    {
        "class": "nop",
        "path": "modules/nops/",
        "title": "NOP generators",
        "defensive_role": "malware-static",
        "detect": ["nop sled patterns"],
        "mamolab": ["pattern-heuristics"],
        "allow_in_lab": False,
    },
    {
        "class": "evasion",
        "path": "modules/evasion/",
        "title": "Evasion modules",
        "defensive_role": "malware-static",
        "detect": ["AV bypass techniques"],
        "mamolab": ["malware-static", "ioc-triage"],
        "allow_in_lab": False,
        "blocked": True,
    },
]

# Subtype map (thư viện con thường gặp)
LIBRARY_SUBTREES: list[dict[str, Any]] = [
    {"tree": "exploits/windows", "platform": "windows", "role": "endpoint-detect"},
    {"tree": "exploits/linux", "platform": "linux", "role": "endpoint-detect"},
    {"tree": "exploits/multi", "platform": "multi", "role": "endpoint-detect"},
    {"tree": "exploits/android", "platform": "android", "role": "mobile-detect"},
    {"tree": "auxiliary/scanner", "platform": "network", "role": "net-audit"},
    {"tree": "auxiliary/gather", "platform": "network", "role": "cred-exposure"},
    {"tree": "auxiliary/admin", "platform": "network", "role": "misconfig"},
    {"tree": "post/windows/gather", "platform": "windows", "role": "cred-exposure"},
    {"tree": "post/linux/gather", "platform": "linux", "role": "cred-exposure"},
    {"tree": "payloads/singles", "platform": "multi", "role": "malware-static"},
    {"tree": "payloads/stagers", "platform": "multi", "role": "c2-detect"},
    {"tree": "payloads/stages", "platform": "multi", "role": "c2-detect"},
]

# Ánh xạ role phòng thủ → hành động MaMoLab / harden
ROLE_ACTIONS: dict[str, dict[str, Any]] = {
    "malware-static": {
        "mamolab_owns": "malware-static",
        "actions": [
            "docker/lab analyze-static.py trên mẫu nghi",
            "entropy + string heuristics (js/lab/static.js)",
            "YARA/IOC triage — không detonate",
        ],
    },
    "ioc-triage": {
        "mamolab_owns": "ioc-triage",
        "actions": [
            "js/lab/indicators.js pattern match",
            "Quarantine file → reports/",
            "Map TTP names → detection backlog",
        ],
    },
    "security-audit": {
        "mamolab_owns": "security-audit",
        "actions": [
            "MaMoLab.audit() self-hardening",
            "Patch CVE được catalog từ module refs",
            "Không chạy auxiliary scanner lên prod",
        ],
    },
    "sandbox-policy": {
        "mamolab_owns": "sandbox-policy",
        "actions": [
            "docker/lab network_mode=none",
            "Cấm msfrpcd / msfvenom trong lab image",
            "policy.noExploitGeneration = true",
        ],
    },
}

# Pipe: MSF library → Lab phòng thủ (không exploit)
PIPE_EDGES = [
    {"from": "suite.msfconsole", "to": "class.catalog", "via": "docs-only"},
    {"from": "class.exploit", "to": "role.malware-static", "via": "CVE triage"},
    {"from": "class.auxiliary", "to": "role.security-audit", "via": "harden"},
    {"from": "class.payload", "to": "role.malware-static", "via": "signature"},
    {"from": "class.post", "to": "role.ioc-triage", "via": "TTP map"},
    {"from": "role.malware-static", "to": "lab.docker-static", "via": "quarantine"},
    {"from": "role.sandbox-policy", "to": "lab.policy", "via": "deny exploit gen"},
]

POLICY = {
    "defensive_only": True,
    "no_exploit_generation": True,
    "no_attack_payloads": True,
    "no_msfvenom": True,
    "no_msfrpcd_expose": True,
    "no_detonate": True,
    "aligns_with": ["docs/SECURITY-LAB.md", "js/lab/policy.js", "docker/lab/README.md"],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_local_msf() -> dict[str, Any]:
    """Readonly: có MSF trên máy không? (không cài, không chạy module)."""
    out: dict[str, Any] = {
        "installed": False,
        "msfconsole": None,
        "msfvenom": None,
        "framework_root": None,
        "module_roots": {},
        "note": "Inventory only — lab không dùng để exploit",
    }
    console = shutil.which("msfconsole")
    venom = shutil.which("msfvenom")
    out["msfconsole"] = console
    out["msfvenom"] = venom
    roots = [
        Path("/usr/share/metasploit-framework"),
        Path("/opt/metasploit-framework"),
        Path.home() / "metasploit-framework",
        Path("/usr/share/metasploit-framework/modules"),
    ]
    root = None
    for r in roots:
        if (r / "modules").is_dir():
            root = r
            break
        if r.name == "modules" and r.is_dir():
            root = r.parent
            break
    if root:
        out["framework_root"] = str(root)
        out["installed"] = True
        mods = root / "modules"
        for cls in ("exploits", "auxiliary", "post", "payloads", "encoders", "nops", "evasion"):
            p = mods / cls
            if p.is_dir():
                # count .rb files shallow+deep (cap time)
                n = 0
                for _ in p.rglob("*.rb"):
                    n += 1
                    if n >= 5000:
                        break
                out["module_roots"][cls] = {"path": str(p), "rb_files_capped": n}
    elif console or venom:
        out["installed"] = True
        out["note"] = "Binary có trên PATH nhưng chưa thấy modules/ — không chạy"
    return out


def role_matrix() -> list[dict[str, Any]]:
    rows = []
    for m in MODULE_CLASSES:
        role = m["defensive_role"]
        act = ROLE_ACTIONS.get(role) or {}
        rows.append(
            {
                "class": m["class"],
                "path": m["path"],
                "defensive_role": role,
                "mamolab_owns": act.get("mamolab_owns"),
                "actions": act.get("actions"),
                "blocked": bool(m.get("blocked")),
                "allow_in_lab": bool(m.get("allow_in_lab")),
                "detect": m.get("detect"),
                "note": m.get("note"),
            }
        )
    return rows


def mermaid() -> str:
    return "\n".join(
        [
            "```mermaid",
            "flowchart TB",
            "  subgraph SUITE[Metasploit Suite — catalog only]",
            "    CON[msfconsole]",
            "    VEN[msfvenom BLOCKED]",
            "    RPC[msfrpcd BLOCKED]",
            "  end",
            "  subgraph LIB[Module library]",
            "    EX[exploit]",
            "    AU[auxiliary]",
            "    PO[post]",
            "    PA[payload BLOCKED gen]",
            "    EN[encoder]",
            "    EV[evasion]",
            "  end",
            "  subgraph LAB[MaMoLab defensive]",
            "    MS[malware-static]",
            "    IOC[ioc-triage]",
            "    AUD[security-audit]",
            "    POL[sandbox-policy]",
            "  end",
            "  CON --> EX",
            "  EX --> MS",
            "  AU --> AUD",
            "  PO --> IOC",
            "  PA --> MS",
            "  EN --> MS",
            "  EV --> IOC",
            "  VEN -.->|deny| POL",
            "  RPC -.->|deny| POL",
            "  MS --> DOCKER[docker/lab static]",
            "  POL --> DOCKER",
            "```",
        ]
    )


def build_report(*, inventory: bool = True) -> dict[str, Any]:
    local = find_local_msf() if inventory else {"skipped": True}
    matrix = role_matrix()
    blocked_n = sum(1 for m in MODULE_CLASSES if m.get("blocked")) + sum(
        1 for s in SUITE_SURFACES if s.get("blocked")
    )
    report: dict[str, Any] = {
        "ok": True,
        "module": "metasploit_suite_mapper",
        "checked_at": utc_now(),
        "policy": POLICY,
        "verdict": (
            f"✅ Mapper thư viện Metasploit Suite (phòng thủ) · "
            f"classes={len(MODULE_CLASSES)} · surfaces={len(SUITE_SURFACES)} · "
            f"blocked_gen={blocked_n} · local_msf={local.get('installed')}"
        ),
        "suite": SUITE_SURFACES,
        "module_classes": MODULE_CLASSES,
        "library_subtrees": LIBRARY_SUBTREES,
        "role_matrix": matrix,
        "role_actions": ROLE_ACTIONS,
        "pipe_edges": PIPE_EDGES,
        "local_inventory": local,
        "mamolab": {
            "owns": ["malware-static", "security-audit", "sandbox-policy", "ioc-triage"],
            "deny": [
                "msfvenom generate",
                "exploit run",
                "payload encode for attack",
                "msfrpcd expose",
            ],
            "cli_lab": [
                "docker compose -f docker/lab/docker-compose.yml run --rm lab analyze /quarantine/<file>",
                "Mở /lab/ → MaMoLab.analyze / audit",
            ],
        },
        "mermaid": mermaid(),
        "next": [
            "python3 scripts/metasploit_suite_mapper.py --json",
            "python3 scripts/metasploit_library_harvest.py  # rà soát toàn bộ modules/",
            "python3 scripts/metasploit_suite_mapper.py harvest",
            "Đặt mẫu nghi vào quarantine/ → docker/lab analyze (không MSF)",
            "docs/SECURITY-LAB.md · js/lab/policy.js noExploitGeneration",
        ],
        "knowledge_harvest": {
            "cli": "python3 scripts/metasploit_library_harvest.py",
            "reports": [
                "reports/telegram-classify/metasploit_library_knowledge.txt",
                "reports/telegram-classify/metasploit_cve_index.csv",
            ],
            "policy": "readonly catalog · no exploit · no payload gen",
        },
    }
    if local.get("installed") and local.get("msfvenom"):
        report["next"].insert(
            0,
            "⚠ msfvenom có trên PATH — lab policy: không dùng generate payload",
        )
    write_outputs(report)
    return report


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    SECRETS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "metasploit_suite_mapper.json"
    tp = REPORTS / "metasploit_suite_mapper.txt"
    mp = REPORTS / "metasploit_suite_mapper.mermaid.md"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    mp.write_text((report.get("mermaid") or "") + "\n", encoding="utf-8")
    STATE_PATH.write_text(
        json.dumps(
            {
                "updated_at": report.get("checked_at"),
                "ok": report.get("ok"),
                "verdict": report.get("verdict"),
                "local_msf": (report.get("local_inventory") or {}).get("installed"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"json": str(jp), "txt": str(tp), "mermaid": str(mp)}


def format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("🛡 METASPLOIT SUITE · MAPPER THƯ VIỆN (PHÒNG THỦ)")
    L(f"Lúc: {report.get('checked_at')}")
    L(f"Verdict: {report.get('verdict')}")
    L("Policy: no exploit gen · no payload · no msfrpcd · align MaMoLab")
    L("")
    L("=== Suite surfaces ===")
    for s in report.get("suite") or []:
        blk = " BLOCKED" if s.get("blocked") else ""
        L(f"· [{s.get('kind')}] {s.get('id')}{blk} → {s.get('defensive_role')}")
        L(f"    {s.get('title')}")
        L(f"    {s.get('lab_action')}")
    L("")
    L("=== Module classes → role ===")
    for m in report.get("role_matrix") or []:
        blk = " BLOCKED-GEN" if m.get("blocked") else ""
        L(f"· {m.get('class')}{blk} ({m.get('path')}) → {m.get('defensive_role')} / {m.get('mamolab_owns')}")
        L(f"    detect: {', '.join(m.get('detect') or [])}")
        L(f"    note: {m.get('note')}")
    L("")
    L("=== Library subtrees ===")
    for t in report.get("library_subtrees") or []:
        L(f"· {t.get('tree')} [{t.get('platform')}] role={t.get('role')}")
    L("")
    inv = report.get("local_inventory") or {}
    L(f"=== Local MSF inventory === installed={inv.get('installed')}")
    if inv.get("framework_root"):
        L(f"  root: {inv.get('framework_root')}")
        for k, v in (inv.get("module_roots") or {}).items():
            L(f"  · {k}: {v.get('rb_files_capped')} .rb (capped)")
    L(f"  msfconsole={inv.get('msfconsole')} msfvenom={inv.get('msfvenom')}")
    L("")
    L("=== MaMoLab deny ===")
    for d in (report.get("mamolab") or {}).get("deny") or []:
        L(f"  ✗ {d}")
    for n in report.get("next") or []:
        L(f"Next: {n}")
    return "\n".join(lines)


def run_harvest(*, refresh: bool = False, as_json: bool = False) -> int:
    """Rà soát toàn bộ modules/ MSF → knowledge report (readonly)."""
    from metasploit_library_harvest import format_text as fmt_k
    from metasploit_library_harvest import harvest

    report = harvest(refresh=refresh)
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(fmt_k(report) if report.get("ok") else report.get("error") or report)
    return 0 if report.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Mapper thư viện Metasploit Suite (phòng thủ — không exploit)"
    )
    ap.add_argument(
        "command",
        nargs="?",
        default="map",
        choices=("map", "harvest"),
        help="map=taxonomy · harvest=rà soát toàn bộ modules/",
    )
    ap.add_argument("--no-inventory", action="store_true", help="Bỏ quét local MSF")
    ap.add_argument("--refresh", action="store_true", help="(harvest) clone lại sparse modules/")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--mermaid", action="store_true")
    args = ap.parse_args(argv)
    if args.command == "harvest":
        return run_harvest(refresh=args.refresh, as_json=args.json)
    report = build_report(inventory=not args.no_inventory)
    if args.mermaid:
        print(report.get("mermaid") or "")
        return 0
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
