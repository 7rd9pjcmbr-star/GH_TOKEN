#!/usr/bin/env python3
"""Rà soát TOÀN BỘ thư viện Metasploit — atlas không cắt cụt.

Sinh catalog học tập: mọi class · mọi subtree · depth2/3 · payloads/encoders/nops/evasion.
Readonly · không chạy exploit · không generate payload · không commit source .rb.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
KGEN = ROOT / "knowledge" / "generated"
DEFAULT_MSF = Path("/tmp/msf-knowledge/modules")
MSF_GIT = "https://github.com/rapid7/metasploit-framework.git"

CLASSES = ("exploits", "auxiliary", "post", "payloads", "encoders", "nops", "evasion")

# Ánh xạ học tập (defensive)
STUDY_MAP: dict[str, dict[str, str]] = {
    "exploits": {
        "study": "CVE / kỹ thuật công khai → triage patch + detect",
        "lab": "Chỉ tra cứu path/CVE; không exploit",
        "role": "malware-static",
    },
    "auxiliary": {
        "study": "Scanner/gather/admin/dos patterns → harden checklist",
        "lab": "Checklist owned; không MSF scan prod; dos = cấm",
        "role": "security-audit",
    },
    "post": {
        "study": "TTP sau xâm nhập → IOC persistence/lateral/cred",
        "lab": "Viết IOC; không chạy post trên host ngoài lab",
        "role": "ioc-triage",
    },
    "payloads": {
        "study": "Họ artifact (singles/stagers/stages) → signature",
        "lab": "Nhận diện tên/chuỗi; cấm msfvenom",
        "role": "malware-static",
    },
    "encoders": {
        "study": "Biến đổi payload theo arch → entropy/heuristic",
        "lab": "Signature only",
        "role": "malware-static",
    },
    "nops": {
        "study": "NOP sled patterns theo arch",
        "lab": "Pattern heuristic",
        "role": "malware-static",
    },
    "evasion": {
        "study": "Kỹ thuật né AV/AppLocker (tên module)",
        "lab": "Detect technique names; không chạy evasion",
        "role": "malware-static",
    },
}

AUX_KIND_NOTES = {
    "scanner": "Checklist dò bề mặt → harden",
    "admin": "Misconfig / default cred patterns → audit cấu hình",
    "gather": "Thu thập info → giảm info disclosure",
    "dos": "CẤM thí nghiệm ngoài lab tách — chỉ biết tồn tại",
    "server": "Listener/capture patterns → C2/ cred capture detect",
    "fuzzers": "Fuzz surface — chỉ học khái niệm trong owned lab",
    "sqli": "SQLi helper patterns → WAF/query harden",
    "spoof": "Spoof DNS/ARP… → network detect",
    "fileformat": "File độc → static lab",
    "analyze": "Phân tích phụ trợ → tham chiếu",
    "voip": "VoIP surface",
    "client": "Client-side aux",
    "cloud": "Cloud API/misconfig",
    "vsploit": "Mô phỏng traffic — lab only",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_modules(root: Path | None = None) -> Path:
    modules = root or DEFAULT_MSF
    if modules.is_dir() and any(modules.iterdir()):
        return modules
    from metasploit_library_harvest import ensure_modules as ens

    return ens(Path("/tmp/msf-knowledge"), refresh=False)


def count_tree(path: Path, *, depth: int) -> dict[str, int]:
    c: Counter[str] = Counter()
    if not path.is_dir():
        return {}
    for f in path.rglob("*.rb"):
        rel = f.relative_to(path)
        parts = rel.parts
        if depth <= 0:
            key = str(rel)
        elif len(parts) >= depth:
            key = "/".join(parts[:depth])
        else:
            key = "/".join(parts) if parts else "_root"
        c[key] += 1
    return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def parse_light_meta(path: Path) -> dict[str, Any]:
    """Name + CVE only — đủ cho atlas index."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:120_000]
    except OSError:
        return {"name": path.stem, "cves": []}
    name_m = re.search(r"(?i)'Name'\s*=>\s*'((?:\\'|[^'])*)'", text)
    name = name_m.group(1).replace("\\'", "'") if name_m else path.stem
    cves = sorted({f"CVE-{m}" for m in re.findall(r"(?i)\bCVE-(\d{4}-\d{4,7})\b", text)})
    rank_m = re.search(
        r"(?i)Rank\s*=\s*(\w+)|'Rank'\s*=>\s*(\w+)",
        text,
    )
    rank = None
    if rank_m:
        rank = (rank_m.group(1) or rank_m.group(2) or "").replace("Ranking", "").replace("Rank", "")
        rank = rank.lower() or None
    return {"name": name[:160], "cves": cves[:12], "rank": rank}


def build_atlas(*, modules_root: Path | None = None, with_module_index: bool = True) -> dict[str, Any]:
    root = ensure_modules(modules_root)
    atlas: dict[str, Any] = {
        "ok": True,
        "module": "metasploit_full_atlas",
        "checked_at": utc_now(),
        "source": MSF_GIT + " (modules/ sparse readonly)",
        "modules_root": str(root),
        "policy": {
            "defensive_only": True,
            "complete_inventory": True,
            "no_exploit_run": True,
            "no_payload_gen": True,
            "no_omit_subtree": True,
        },
        "top_level": sorted(p.name for p in root.iterdir()),
        "classes": {},
        "study_map": STUDY_MAP,
        "auxiliary_kind_notes": AUX_KIND_NOTES,
        "grand_total_rb": 0,
        "unique_cves": 0,
        "coverage_checklist": [],
    }

    cve_set: set[str] = set()
    module_index: list[dict[str, Any]] = []

    for cls in CLASSES:
        cls_path = root / cls
        if not cls_path.is_dir():
            atlas["classes"][cls] = {"rb": 0, "missing": True}
            continue
        rbs = list(cls_path.rglob("*.rb"))
        depth1 = count_tree(cls_path, depth=1)
        depth2 = count_tree(cls_path, depth=2)
        # depth3 only for large classes (avoid huge evasion filenames as keys noise)
        depth3 = count_tree(cls_path, depth=3) if cls in {"exploits", "auxiliary", "post", "payloads"} else {}

        entry: dict[str, Any] = {
            "rb": len(rbs),
            "study": STUDY_MAP[cls],
            "subtrees_depth1": depth1,  # FULL — không cắt
            "subtrees_depth2": depth2,  # FULL
            "subtrees_depth3_count": len(depth3),
            "subtrees_depth3_top": dict(list(depth3.items())[:80]) if depth3 else {},
            "subtrees_depth3_all_keys": sorted(depth3.keys()) if depth3 else [],
        }
        # For completeness store full depth3 in separate generated file later via side dict
        entry["_depth3_full"] = depth3

        if with_module_index:
            for f in rbs:
                rel = str(f.relative_to(root)).replace("\\", "/")
                meta = parse_light_meta(f)
                for c in meta["cves"]:
                    cve_set.add(c)
                row = {
                    "path": rel[:-3] if rel.endswith(".rb") else rel,
                    "class": cls,
                    "name": meta["name"],
                    "cves": meta["cves"],
                    "rank": meta["rank"],
                }
                module_index.append(row)

        atlas["classes"][cls] = entry
        atlas["grand_total_rb"] += len(rbs)

    atlas["unique_cves"] = len(cve_set)
    atlas["module_index_n"] = len(module_index)
    atlas["module_index"] = module_index  # full list

    # Coverage checklist — mọi nhánh depth1 phải có mặt
    checklist = []
    for cls in CLASSES:
        d1 = (atlas["classes"].get(cls) or {}).get("subtrees_depth1") or {}
        for sub, n in d1.items():
            note = ""
            if cls == "auxiliary":
                note = AUX_KIND_NOTES.get(sub, "Tham chiếu catalog")
            checklist.append(
                {
                    "id": f"{cls}/{sub}",
                    "modules": n,
                    "study_hint": note or STUDY_MAP[cls]["study"],
                    "covered_in_library": True,
                }
            )
    atlas["coverage_checklist"] = checklist
    atlas["coverage_branches"] = len(checklist)

    atlas["verdict"] = (
        f"✅ Atlas MSF đầy đủ · modules={atlas['grand_total_rb']} · "
        f"classes={sum(1 for c in CLASSES if not atlas['classes'].get(c, {}).get('missing'))} · "
        f"branches_depth1={atlas['coverage_branches']} · "
        f"CVE_unique_in_index={atlas['unique_cves']}"
    )
    atlas["what_msf_contains"] = summarize_what(atlas)
    write_outputs(atlas)
    return atlas


def summarize_what(atlas: dict[str, Any]) -> dict[str, Any]:
    """Trả lời ngắn: Thư viện Metasploit có những gì."""
    out: dict[str, Any] = {
        "interfaces_note": (
            "Ngoài modules/: suite còn msfconsole, msfvenom(⛔), msfrpcd(⛔), "
            "plugins/scripts/tools — lab này catalog modules/ + deny venom/rpc."
        ),
        "module_classes": {},
    }
    for cls in CLASSES:
        c = atlas["classes"].get(cls) or {}
        out["module_classes"][cls] = {
            "count": c.get("rb", 0),
            "branches": list((c.get("subtrees_depth1") or {}).keys()),
            "study": (c.get("study") or {}).get("study"),
            "lab": (c.get("study") or {}).get("lab"),
        }
    return out


def write_outputs(atlas: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    KGEN.mkdir(parents=True, exist_ok=True)

    # Strip heavy _depth3_full from JSON report copy for telegram; keep in generated
    light = json.loads(json.dumps(atlas))  # deep copy via json
    depth3_pack = {}
    for cls, entry in list(light.get("classes", {}).items()):
        if isinstance(entry, dict) and "_depth3_full" in entry:
            depth3_pack[cls] = entry.pop("_depth3_full")
        if isinstance(atlas["classes"].get(cls), dict):
            atlas["classes"][cls].pop("_depth3_full", None)

    # Full atlas JSON may be large due to module_index — write split files
    index = light.pop("module_index", [])
    jp = REPORTS / "metasploit_full_atlas.json"
    # keep summary without full index in reports
    light["module_index_sample"] = index[:30]
    light["module_index_n"] = len(index)
    jp.write_text(json.dumps(light, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Generated knowledge files — COMPLETE
    (KGEN / "msf-atlas-summary.json").write_text(
        json.dumps(light, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (KGEN / "msf-atlas-depth3.json").write_text(
        json.dumps(depth3_pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (KGEN / "msf-module-index.json").write_text(
        json.dumps(index, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # CSV index
    csv_lines = ["class,path,name,rank,cves"]
    for row in index:
        cves = ";".join(row.get("cves") or [])
        name = (row.get("name") or "").replace('"', "'")
        csv_lines.append(
            f"{row.get('class')},{row.get('path')},\"{name}\",{row.get('rank') or ''},{cves}"
        )
    (KGEN / "msf-module-index.csv").write_text("\n".join(csv_lines) + "\n", encoding="utf-8")

    # Human markdown — FULL depth1 + depth2 for every class
    md = format_atlas_md(light, depth3_pack)
    (KGEN / "msf-full-atlas.md").write_text(md + "\n", encoding="utf-8")
    (REPORTS / "metasploit_full_atlas.txt").write_text(format_atlas_txt(light) + "\n", encoding="utf-8")

    # Coverage checklist md
    cov = ["# MSF coverage checklist (mọi nhánh depth1)", ""]
    for row in light.get("coverage_checklist") or []:
        cov.append(f"- [x] `{row['id']}` ×{row['modules']} — {row['study_hint']}")
    (KGEN / "msf-coverage-checklist.md").write_text("\n".join(cov) + "\n", encoding="utf-8")

    # Per-class chapter-style generated pages
    for cls in CLASSES:
        write_class_page(cls, light, depth3_pack)

    return {
        "summary": str(KGEN / "msf-atlas-summary.json"),
        "atlas_md": str(KGEN / "msf-full-atlas.md"),
        "index_csv": str(KGEN / "msf-module-index.csv"),
        "checklist": str(KGEN / "msf-coverage-checklist.md"),
    }


def write_class_page(cls: str, light: dict[str, Any], depth3: dict[str, Any]) -> None:
    c = (light.get("classes") or {}).get(cls) or {}
    lines = [
        f"# Atlas · {cls} ({c.get('rb', 0)} modules)",
        "",
        f"**Học:** {(c.get('study') or {}).get('study')}",
        f"**Lab:** {(c.get('study') or {}).get('lab')}",
        f"**Role:** {(c.get('study') or {}).get('role')}",
        "",
        "## Depth-1 (đầy đủ, không cắt)",
        "",
    ]
    for k, n in (c.get("subtrees_depth1") or {}).items():
        hint = ""
        if cls == "auxiliary":
            hint = f" — {AUX_KIND_NOTES.get(k, '')}"
        lines.append(f"- `{cls}/{k}` ×{n}{hint}")
    lines += ["", "## Depth-2 (đầy đủ, không cắt)", ""]
    for k, n in (c.get("subtrees_depth2") or {}).items():
        lines.append(f"- `{cls}/{k}` ×{n}")
    d3 = depth3.get(cls) or {}
    if d3:
        lines += ["", f"## Depth-3 ({len(d3)} nhánh — đủ key trong msf-atlas-depth3.json)", ""]
        for k, n in list(d3.items())[:100]:
            lines.append(f"- `{cls}/{k}` ×{n}")
        if len(d3) > 100:
            lines.append(f"- … +{len(d3) - 100} nhánh nữa (xem JSON)")
    lines += [
        "",
        "> Nguồn: `python3 scripts/metasploit_full_atlas.py`",
        "> Cấm: exploit run · msfvenom · scan prod",
        "",
    ]
    (KGEN / f"atlas-{cls}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_atlas_md(light: dict[str, Any], depth3: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("# Thư viện Metasploit có những gì — Atlas đầy đủ")
    L("")
    L(f"Built: {light.get('checked_at')}")
    L(f"Verdict: {light.get('verdict')}")
    L(f"Root: `{light.get('modules_root')}`")
    L("")
    L("## Trả lời ngắn")
    L("")
    L("Metasploit Framework (phần **modules/** dùng để học trong lab này) gồm **7 lớp module**:")
    L("")
    for cls in CLASSES:
        c = (light.get("classes") or {}).get(cls) or {}
        L(f"1. **{cls}** — {c.get('rb', 0)} module · học: {(c.get('study') or {}).get('study')}")
    L("")
    L(f"Tổng: **{light.get('grand_total_rb')}** file `.rb` · CVE unique (index): **{light.get('unique_cves')}** · nhánh depth1: **{light.get('coverage_branches')}**")
    L("")
    L((light.get("what_msf_contains") or {}).get("interfaces_note") or "")
    L("")
    L("## Chi tiết từng lớp (depth1 — KHÔNG bỏ sót)")
    L("")
    for cls in CLASSES:
        c = (light.get("classes") or {}).get(cls) or {}
        L(f"### {cls} ({c.get('rb')} modules)")
        L("")
        L(f"- Lab: {(c.get('study') or {}).get('lab')}")
        L("")
        for k, n in (c.get("subtrees_depth1") or {}).items():
            L(f"  - `{k}` ×{n}")
        L("")
        L(f"Chi tiết depth2/3: `knowledge/generated/atlas-{cls}.md`")
        L("")
    L("## File sinh kèm")
    L("")
    L("- `msf-full-atlas.md` (file này)")
    L("- `atlas-<class>.md` ×7")
    L("- `msf-coverage-checklist.md`")
    L("- `msf-module-index.csv` / `.json` (mọi module)")
    L("- `msf-atlas-depth3.json`")
    L("")
    L("## Policy học tập")
    L("")
    L("Catalog only · không exploit · không msfvenom · không scan prod · dos chỉ biết tồn tại.")
    return "\n".join(lines)


def format_atlas_txt(light: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("📦 METASPLOIT · ATLAS TOÀN BỘ (KHÔNG BỎ SÓT)")
    L(f"Lúc: {light.get('checked_at')}")
    L(f"Verdict: {light.get('verdict')}")
    L(f"Modules: {light.get('grand_total_rb')} · CVE: {light.get('unique_cves')} · nhánh d1: {light.get('coverage_branches')}")
    L("Policy: complete inventory · no exploit · no payload gen")
    L("")
    L("=== Thư viện có những gì ===")
    for cls in CLASSES:
        c = (light.get("classes") or {}).get(cls) or {}
        L(f"· {cls}: {c.get('rb')} modules")
        L(f"    học: {(c.get('study') or {}).get('study')}")
        branches = ", ".join(
            f"{k}×{n}" for k, n in list((c.get("subtrees_depth1") or {}).items())[:12]
        )
        more = len(c.get("subtrees_depth1") or {})
        L(f"    nhánh: {branches}" + (f" …({more} nhánh)" if more > 12 else ""))
    L("")
    L("=== Coverage depth1 (đủ) ===")
    for row in light.get("coverage_checklist") or []:
        L(f"  [x] {row['id']} ×{row['modules']}")
    L("")
    L("Chi tiết: knowledge/generated/msf-full-atlas.md")
    L("Index mọi module: knowledge/generated/msf-module-index.csv")
    L("$ python3 scripts/metasploit_full_atlas.py")
    L("$ python3 scripts/knowledge_library_build.py --with-atlas")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Atlas toàn bộ thư viện Metasploit (học tập)")
    ap.add_argument("--modules", default="")
    ap.add_argument("--no-index", action="store_true", help="Bỏ parse từng module (nhanh, thiếu CVE)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.modules) if args.modules else None
    atlas = build_atlas(modules_root=root, with_module_index=not args.no_index)
    if args.json:
        # avoid dumping full module_index to stdout
        out = {k: v for k, v in atlas.items() if k != "module_index"}
        out["module_index_n"] = atlas.get("module_index_n")
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_atlas_txt(atlas))
    return 0 if atlas.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
