#!/usr/bin/env python3
"""Rà soát toàn bộ thư viện Metasploit — thu thập kiến thức (readonly).

Sparse clone / local modules/ → parse metadata (Name, Rank, CVE, Platform)
→ báo cáo kiến thức phòng thủ.

KHÔNG chạy exploit · KHÔNG generate payload · KHÔNG commit source MSF.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
DEFAULT_MSF = Path("/tmp/msf-knowledge/modules")
MSF_GIT = "https://github.com/rapid7/metasploit-framework.git"

CLASSES = ("exploits", "auxiliary", "post", "payloads", "encoders", "nops", "evasion")
ROLE_MAP = {
    "exploits": "malware-static",
    "auxiliary": "security-audit",
    "post": "ioc-triage",
    "payloads": "malware-static",
    "encoders": "malware-static",
    "nops": "malware-static",
    "evasion": "malware-static",
}

CVE_RE = re.compile(r"(?i)\bCVE-(\d{4}-\d{4,7})\b")
REF_RE = re.compile(r"\['([A-Z]+)'\s*,\s*'([^']+)'\]")
RANK_RE = re.compile(
    r"(?i)'Rank'\s*=>\s*([A-Za-z]+Rank|NormalRanking|ExcellentRanking|GreatRanking|"
    r"GoodRanking|AverageRanking|LowRanking|ManualRanking)"
)
RANK2_RE = re.compile(r"(?i)^\s*Rank\s*=\s*([A-Za-z]+)", re.M)
NAME_RE = re.compile(r"(?i)'Name'\s*=>\s*'((?:\\'|[^'])*)'")
DESC_RE = re.compile(
    r"(?i)'Description'\s*=>\s*%q\{([^}]{0,400})\}|'Description'\s*=>\s*'((?:\\'|[^']){0,400})'",
    re.S,
)
PLAT_RE = re.compile(
    r"(?i)'Platform'\s*=>\s*(?:'([^']+)'|%w\(([^)]+)\)|\[([^\]]+)\])"
)
DISC_RE = re.compile(r"(?i)'DisclosureDate'\s*=>\s*'([^']+)'")

RANK_MAP = {
    "ExcellentRanking": "excellent",
    "GreatRanking": "great",
    "GoodRanking": "good",
    "NormalRanking": "normal",
    "AverageRanking": "average",
    "LowRanking": "low",
    "ManualRanking": "manual",
    "ExcellentRank": "excellent",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_modules(root: Path, *, refresh: bool = False) -> Path:
    """Sparse-checkout modules/ vào /tmp (không commit vào repo)."""
    base = root.parent if root.name == "modules" else root
    modules = base / "modules" if (base / "modules").is_dir() or root.name != "modules" else root
    if root.name == "modules":
        modules = root
        base = root.parent
    else:
        modules = root / "modules"
        base = root

    if modules.is_dir() and any(modules.iterdir()) and not refresh:
        return modules

    base.parent.mkdir(parents=True, exist_ok=True)
    if base.exists() and refresh:
        subprocess.run(["rm", "-rf", str(base)], check=False)
    if not base.exists():
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--filter=blob:none",
                "--sparse",
                MSF_GIT,
                str(base),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(base), "sparse-checkout", "set", "modules"],
            check=True,
            capture_output=True,
            text=True,
        )
    return base / "modules"


def parse_module(path: Path, cls: str, modules_root: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if len(text) > 2_000_000:
        text = text[:2_000_000]
    rel = str(path.relative_to(modules_root)).replace("\\", "/")
    fullname = rel[:-3] if rel.endswith(".rb") else rel

    name_m = NAME_RE.search(text)
    name = name_m.group(1).replace("\\'", "'") if name_m else path.stem
    desc_m = DESC_RE.search(text)
    desc = ""
    if desc_m:
        desc = (desc_m.group(1) or desc_m.group(2) or "").strip()
        desc = re.sub(r"\s+", " ", desc)[:240]

    rank = None
    rm = RANK_RE.search(text) or RANK2_RE.search(text)
    if rm:
        raw = rm.group(1)
        rank = RANK_MAP.get(raw, raw.lower().replace("ranking", "").replace("rank", ""))

    cves = sorted({f"CVE-{c}" for c in CVE_RE.findall(text)})
    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind, val in REF_RE.findall(text):
        k = kind.upper()
        if k not in {"CVE", "EDB", "BID", "ZDI", "URL", "PACKETSTORM", "MSB", "WPVDB"}:
            continue
        key = (k, val[:120])
        if key in seen:
            continue
        seen.add(key)
        refs.append({"type": k, "id": val[:120]})

    plat = None
    pm = PLAT_RE.search(text)
    if pm:
        plat = (pm.group(1) or pm.group(2) or pm.group(3) or "").strip()
        plat = re.sub(r"['\"]", "", plat)[:80]
    parts = fullname.split("/")
    path_plat = parts[1] if len(parts) > 2 else None

    disc = DISC_RE.search(text)
    return {
        "class": "payload" if cls == "payloads" else cls.rstrip("s"),
        "path": fullname,
        "name": name[:160],
        "description": desc,
        "rank": rank,
        "cves": cves[:20],
        "refs_n": len(refs),
        "refs_sample": refs[:8],
        "platform": plat or path_plat,
        "disclosure_date": disc.group(1) if disc else None,
    }


def harvest(*, modules_root: Path | None = None, refresh: bool = False) -> dict[str, Any]:
    root = modules_root or DEFAULT_MSF
    if not root.is_dir() or refresh:
        root = ensure_modules(Path("/tmp/msf-knowledge"), refresh=refresh)
    if not root.is_dir():
        return {
            "ok": False,
            "error": f"Không thấy modules tại {root}",
            "hint": "python3 scripts/metasploit_library_harvest.py --refresh",
            "checked_at": utc_now(),
        }

    knowledge: dict[str, Any] = {
        "ok": True,
        "module": "metasploit_library_harvest",
        "checked_at": utc_now(),
        "policy": {
            "defensive_only": True,
            "readonly_catalog": True,
            "no_exploit_run": True,
            "no_payload_gen": True,
            "source": MSF_GIT + " (sparse modules/)",
        },
        "modules_root": str(root),
        "totals": {},
        "by_class": {},
        "by_platform": {},
        "by_rank": {},
        "top_cve_years": {},
        "subtrees": {},
        "samples_by_class": {},
        "cve_index": {},
        "knowledge_notes": [],
    }

    all_mods: list[dict[str, Any]] = []
    cve_to_mods: dict[str, list[str]] = defaultdict(list)
    platform_c: Counter[str] = Counter()
    rank_c: Counter[str] = Counter()
    year_c: Counter[str] = Counter()

    for cls in CLASSES:
        cls_root = root / cls
        if not cls_root.is_dir():
            knowledge["totals"][cls] = 0
            continue
        files = list(cls_root.rglob("*.rb"))
        knowledge["totals"][cls] = len(files)
        sub: Counter[str] = Counter()
        parsed: list[dict[str, Any]] = []
        for f in files:
            rel = f.relative_to(cls_root)
            top = rel.parts[0] if len(rel.parts) > 1 else "_"
            sub[top] += 1
            meta = parse_module(f, cls, root)
            if not meta:
                continue
            parsed.append(meta)
            all_mods.append(meta)
            for cve in meta["cves"]:
                cve_to_mods[cve].append(meta["path"])
                ym = re.match(r"CVE-(\d{4})-", cve)
                if ym:
                    year_c[ym.group(1)] += 1
            if meta.get("platform"):
                p0 = re.split(r"[\s,/]+", str(meta["platform"]))[0].lower()
                platform_c[p0] += 1
            if meta.get("rank"):
                rank_c[str(meta["rank"])] += 1

        knowledge["subtrees"][cls] = dict(sub.most_common(40))
        interesting = [
            m
            for m in parsed
            if m["cves"] or m.get("rank") in {"excellent", "great", "good"}
        ]
        interesting.sort(key=lambda m: (-len(m["cves"]), m.get("rank") or "z", m["path"]))
        knowledge["samples_by_class"][cls] = [
            {
                k: m[k]
                for k in (
                    "path",
                    "name",
                    "rank",
                    "cves",
                    "platform",
                    "disclosure_date",
                    "description",
                )
            }
            for m in interesting[:25]
        ]
        knowledge["by_class"][cls] = {
            "modules": len(files),
            "with_cve": sum(1 for m in parsed if m["cves"]),
            "with_rank": sum(1 for m in parsed if m.get("rank")),
            "defensive_role": ROLE_MAP[cls],
        }

    knowledge["by_platform"] = dict(platform_c.most_common(30))
    knowledge["by_rank"] = dict(rank_c.most_common())
    knowledge["top_cve_years"] = dict(sorted(year_c.items(), reverse=True)[:20])
    top_cves = sorted(cve_to_mods.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:100]
    knowledge["cve_index"] = {
        cve: {"modules_n": len(mods), "modules": mods[:12]} for cve, mods in top_cves
    }
    knowledge["summary"] = {
        "modules_total": sum(knowledge["totals"].values()),
        "classes": knowledge["totals"],
        "unique_cves": len(cve_to_mods),
        "modules_with_cve": sum(1 for m in all_mods if m["cves"]),
        "platforms_top": list(platform_c.most_common(10)),
    }
    knowledge["defensive_role_coverage"] = {
        ROLE_MAP[c]: knowledge["totals"].get(c, 0) for c in CLASSES
    }
    knowledge["knowledge_notes"] = [
        "KIỂM THỬ: python3 scripts/metasploit_testing_knowledge.py (playbook P1–P6)",
        "MSF = catalog kỹ thuật attack công khai → triage CVE/TTP, không chạy exploit.",
        "exploit/* → CVE → patch/detection backlog (malware-static).",
        "auxiliary/scanner|gather → harden checklist (security-audit) — không scan prod.",
        "payloads|encoders|evasion → signature/YARA only (noExploitGeneration).",
        "post/* → persistence/lateral → IOC rules (ioc-triage).",
        f"Harvest readonly · tổng {knowledge['summary']['modules_total']} module · "
        f"{knowledge['summary']['unique_cves']} CVE unique.",
    ]
    knowledge["verdict"] = (
        f"✅ Đã rà soát thư viện MSF · modules={knowledge['summary']['modules_total']} · "
        f"CVE={knowledge['summary']['unique_cves']} · "
        f"exploits={knowledge['totals'].get('exploits')} · "
        f"auxiliary={knowledge['totals'].get('auxiliary')}"
    )
    knowledge["next"] = [
        "⭐ python3 scripts/metasploit_testing_knowledge.py  # KIẾN THỨC KIỂM THỬ",
        "Đọc reports/telegram-classify/metasploit_testing_knowledge.txt",
        "Đọc reports/telegram-classify/metasploit_library_knowledge.txt",
        "CVE triage: reports/telegram-classify/metasploit_cve_index.csv",
        "python3 scripts/metasploit_suite_mapper.py",
        "Mẫu nghi → docker/lab analyze (không msfvenom/exploit)",
    ]
    write_outputs(knowledge)
    return knowledge


def write_outputs(knowledge: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "metasploit_library_knowledge.json"
    tp = REPORTS / "metasploit_library_knowledge.txt"
    cp = REPORTS / "metasploit_cve_index.csv"
    jp.write_text(json.dumps(knowledge, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tp.write_text(format_text(knowledge) + "\n", encoding="utf-8")
    cve_lines = ["cve,modules_n,module_paths"]
    for cve, info in (knowledge.get("cve_index") or {}).items():
        paths = ";".join((info.get("modules") or [])[:8])
        cve_lines.append(f"{cve},{info.get('modules_n')},{paths}")
    cp.write_text("\n".join(cve_lines) + "\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp), "csv": str(cp)}


def format_text(knowledge: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("📚 METASPLOIT · RÀ SOÁT TOÀN BỘ THƯ VIỆN · KIẾN THỨC (PHÒNG THỦ)")
    L(f"Lúc: {knowledge.get('checked_at')}")
    L(f"Verdict: {knowledge.get('verdict')}")
    L(f"Root: {knowledge.get('modules_root')}")
    s = knowledge.get("summary") or {}
    L(
        f"Tổng module: {s.get('modules_total')} · CVE unique: {s.get('unique_cves')} · "
        f"modules có CVE: {s.get('modules_with_cve')}"
    )
    L("Policy: readonly catalog · no exploit run · no payload gen")
    L("")
    L("⭐ KIẾN THỨC KIỂM THỬ (playbook):")
    L("   python3 scripts/metasploit_testing_knowledge.py")
    L("   Panel: 🧪 MSF·kiểm thử")
    L("   File: reports/telegram-classify/metasploit_testing_knowledge.txt")
    L("")
    L("=== Theo class ===")
    for c in CLASSES:
        bc = (knowledge.get("by_class") or {}).get(c) or {}
        L(
            f"· {c}: {knowledge.get('totals', {}).get(c, 0)} · "
            f"CVE-tagged={bc.get('with_cve', 0)} · ranked={bc.get('with_rank', 0)} → {ROLE_MAP[c]}"
        )
    L("")
    L("=== Platform (top) ===")
    for p, n in s.get("platforms_top") or []:
        L(f"· {p}: {n}")
    L("")
    L("=== Rank ===")
    for r, n in (knowledge.get("by_rank") or {}).items():
        L(f"· {r}: {n}")
    L("")
    L("=== CVE years ===")
    for y, n in list((knowledge.get("top_cve_years") or {}).items())[:12]:
        L(f"· {y}: {n}")
    L("")
    L("=== Top CVE ===")
    for cve, info in list((knowledge.get("cve_index") or {}).items())[:20]:
        L(f"· {cve} ×{info.get('modules_n')}")
        for m in (info.get("modules") or [])[:2]:
            L(f"    - {m}")
    L("")
    L("=== Subtrees exploits ===")
    for k, v in list((knowledge.get("subtrees") or {}).get("exploits", {}).items())[:15]:
        L(f"· exploits/{k}: {v}")
    L("")
    L("=== Subtrees auxiliary ===")
    for k, v in list((knowledge.get("subtrees") or {}).get("auxiliary", {}).items())[:12]:
        L(f"· auxiliary/{k}: {v}")
    L("")
    L("=== Samples ===")
    for cls in ("exploits", "auxiliary", "post", "payloads"):
        L(f"-- {cls} --")
        for m in ((knowledge.get("samples_by_class") or {}).get(cls) or [])[:6]:
            L(f"· [{m.get('rank') or '-'}] {m.get('path')}")
            L(f"  {m.get('name')}")
            if m.get("cves"):
                L(f"  CVE: {', '.join(m['cves'][:5])}")
    L("")
    L("=== Kiến thức ===")
    for n in knowledge.get("knowledge_notes") or []:
        L(f"· {n}")
    for n in knowledge.get("next") or []:
        L(f"Next: {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rà soát thư viện Metasploit — thu thập kiến thức")
    ap.add_argument("--modules", default="", help="Đường dẫn modules/ (mặc định /tmp/msf-knowledge/modules)")
    ap.add_argument("--refresh", action="store_true", help="Clone lại sparse modules/")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.modules) if args.modules else None
    report = harvest(modules_root=root, refresh=args.refresh)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report) if report.get("ok") else report.get("error") or report)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
