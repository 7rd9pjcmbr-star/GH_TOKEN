#!/usr/bin/env python3
"""Chuyển RAR → TXT trong lab (phòng thủ).

- Nếu chỉ có metadata/stub OnlyLogs: sinh TXT triage (như intake onlylogs-6).
- Nếu có binary .rar owned trong quarantine/lab: liệt kê thành viên → TXT
  (cần `7z`/`unrar`; không dump-login; không chạy payload trong archive).

Usage:
  python3 scripts/lab_rar_to_txt.py
  python3 scripts/lab_rar_to_txt.py --rar path/to/file.rar
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTAKE = ROOT / "quarantine" / "lab" / "intake" / "onlylogs-6"
OUT = INTAKE / "txt"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_rar(path: Path) -> str:
    for bin_name, args in (
        ("7z", ["l", "-ba", str(path)]),
        ("unrar", ["lb", str(path)]),
    ):
        if not shutil.which(bin_name):
            continue
        try:
            p = subprocess.run(
                [bin_name, *args],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if p.returncode == 0 and (p.stdout or "").strip():
                return p.stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
    return (
        "# Không liệt kê được nội dung RAR (thiếu 7z/unrar hoặc file lỗi).\n"
        f"# path={path}\n"
    )


def convert_rar_file(rar: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / (rar.stem + ".txt")
    listing = list_rar(rar)
    out.write_text(
        f"# converted_from: {rar.name}\n"
        f"# checked_at: {utc_now()}\n"
        f"# policy: no_dump_login no_execute\n"
        f"# size_bytes: {rar.stat().st_size}\n\n"
        "## Archive listing\n"
        f"{listing}\n",
        encoding="utf-8",
    )
    return out


def convert_onlylogs_manifest() -> dict:
    man = INTAKE / "MANIFEST.json"
    if not man.is_file():
        return {"ok": False, "error": "missing onlylogs-6 MANIFEST.json"}
    data = json.loads(man.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    done = []
    for i, item in enumerate(data.get("items") or [], 1):
        name = item.get("file_name") or "OnlyLogs.rar"
        base = name[:-4] if name.lower().endswith(".rar") else name
        uid = item.get("update_id")
        out = OUT / f"{i:02d}_{uid}_{base}.txt".replace("/", "_")
        out.write_text(
            f"# {base}.txt\n"
            f"# converted_from: {name}\n"
            f"# format: text/triage (binary RAR absent — Bot API limit)\n"
            f"# checked_at: {utc_now()}\n\n"
            f"index: {i}/6\n"
            f"size_gb: {item.get('size_gb')}\n"
            f"update_id: {uid}\n"
            f"file_id: {item.get('file_id')}\n"
            f"classification: {item.get('classification')}\n"
            "policy: no_dump_login no_execute no_unpack\n",
            encoding="utf-8",
        )
        flat = INTAKE / f"{i:02d}_{base}.txt"
        flat.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        done.append(str(out.relative_to(ROOT)))
    return {"ok": True, "converted": done, "n": len(done)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="RAR → TXT lab (defensive)")
    ap.add_argument("--rar", action="append", default=[], help="Đường dẫn .rar owned")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    results = []
    if args.rar:
        for r in args.rar:
            path = Path(r)
            if not path.is_file():
                results.append({"ok": False, "rar": r, "error": "missing"})
                continue
            out = convert_rar_file(path, OUT)
            results.append({"ok": True, "rar": str(path), "txt": str(out.relative_to(ROOT))})
    else:
        results.append(convert_onlylogs_manifest())
    report = {"ok": all(x.get("ok") for x in results), "results": results, "at": utc_now()}
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
