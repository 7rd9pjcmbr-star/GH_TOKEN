#!/usr/bin/env python3
"""Chia nhỏ mẫu / intake → đưa vào phòng thí nghiệm.

- OnlyLogs×6: tách mỗi RAR-entry thành nhiều chunk TXT nhỏ (id/size/policy/…)
- File lớn owned: cắt binary thành phần ≤ max-mb (mặc định 19MB, vừa Bot API)
- Không dump-login · không thực thi

Usage:
  python3 scripts/lab_split_to_lab.py onlylogs6
  python3 scripts/lab_split_to_lab.py file --path quarantine/lab/foo.bin
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lab_static_engine import analyze_path, write_report  # noqa: E402

INTAKE = ROOT / "quarantine" / "lab" / "intake" / "onlylogs-6"
CHUNKS = ROOT / "quarantine" / "lab" / "chunks" / "onlylogs-6"
REPORTS = ROOT / "reports" / "lab" / "chunks"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def split_onlylogs6() -> dict[str, Any]:
    man = INTAKE / "MANIFEST.json"
    if not man.is_file():
        return {"ok": False, "error": f"missing {man}"}
    data = json.loads(man.read_text(encoding="utf-8"))
    CHUNKS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    all_parts: list[dict[str, Any]] = []

    for i, item in enumerate(data.get("items") or [], 1):
        uid = item.get("update_id")
        name = item.get("file_name") or "OnlyLogs.rar"
        folder = CHUNKS / f"{i:02d}_{uid}"
        folder.mkdir(parents=True, exist_ok=True)

        pieces = {
            "01_meta.txt": (
                f"chunk: meta\nindex: {i}/6\nupdate_id: {uid}\n"
                f"file_name: {name}\nconverted_from: rar\nchecked_at: {now}\n"
            ),
            "02_size.txt": (
                f"chunk: size\nsize_bytes: {item.get('size_bytes')}\n"
                f"size_gb: {item.get('size_gb')}\nmime: {item.get('mime')}\n"
            ),
            "03_ids.txt": (
                f"chunk: ids\nfile_id: {item.get('file_id')}\n"
                f"file_unique_id: {item.get('file_unique_id')}\n"
                f"chat_id: {item.get('chat_id')}\ndate: {item.get('date')}\n"
            ),
            "04_class.txt": (
                f"chunk: classification\n"
                f"classification: {item.get('classification')}\n"
                f"download_status: {item.get('download_status')}\n"
                "risk_hint: critical_stealer_archive\n"
            ),
            "05_policy.txt": (
                "chunk: policy\nno_dump_login: true\nno_execute: true\n"
                "no_unpack: true\nlab_only: true\n"
                "note: binary RAR absent (Bot API ~20MB); these are split triage chunks\n"
            ),
            "06_plan_split_19mb.txt": _plan_split_19mb(item),
        }

        part_rows = []
        for fname, body in pieces.items():
            path = folder / fname
            path.write_text(body, encoding="utf-8")
            ana = analyze_path(path, root=ROOT, surface="lab-chunks-onlylogs6")
            write_report(ana, REPORTS / f"{i:02d}_{uid}_{fname}.v2.json")
            part_rows.append(
                {
                    "file": str(path.relative_to(ROOT)),
                    "band": (ana.get("summary") or {}).get("riskBand"),
                    "score": (ana.get("summary") or {}).get("riskScore"),
                }
            )
        # index for this item
        idx = {
            "index": i,
            "update_id": uid,
            "file_name": name,
            "size_gb": item.get("size_gb"),
            "chunks_n": len(pieces),
            "folder": str(folder.relative_to(ROOT)),
            "parts": part_rows,
            "checked_at": now,
        }
        (folder / "INDEX.json").write_text(
            json.dumps(idx, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        all_parts.append(idx)

    # master index + flat copy of tiny chunks into lab root chunks inbox
    flat = CHUNKS / "_flat"
    flat.mkdir(parents=True, exist_ok=True)
    for idx in all_parts:
        src = ROOT / idx["folder"]
        for p in src.glob("*.txt"):
            dest = flat / f"{idx['index']:02d}_{p.name}"
            dest.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")

    report = {
        "ok": True,
        "checked_at": now,
        "verdict": (
            f"✅ Chia nhỏ OnlyLogs×6 → lab chunks · items={len(all_parts)} · "
            f"parts_per_item=6 · flat={len(list(flat.glob('*.txt')))}"
        ),
        "lab_chunks": str(CHUNKS.relative_to(ROOT)),
        "flat": str(flat.relative_to(ROOT)),
        "items": all_parts,
        "policy": {
            "no_dump_login": True,
            "no_execute": True,
            "no_unpack": True,
        },
        "next": [
            "python3 scripts/lab_control.py analyze",
            "ls quarantine/lab/chunks/onlylogs-6/",
        ],
    }
    (CHUNKS / "MANIFEST.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORTS / "onlylogs6_split.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORTS / "onlylogs6_split.txt").write_text(format_split(report) + "\n", encoding="utf-8")
    (ROOT / "reports" / "telegram-classify" / "onlylogs6_split_lab.txt").write_text(
        format_split(report) + "\n", encoding="utf-8"
    )
    return report


def _plan_split_19mb(item: dict[str, Any]) -> str:
    size = int(item.get("size_bytes") or 0)
    part = 19 * 1024 * 1024
    n = max(1, (size + part - 1) // part) if size else 0
    lines = [
        "chunk: plan_split_19mb",
        "purpose: nếu có binary RAR owned — cắt ≤19MB để đưa qua Bot/lab",
        f"size_bytes: {size}",
        f"part_size_bytes: {part}",
        f"parts_needed: {n}",
        "command_example:",
        f"  python3 scripts/lab_split_to_lab.py file --path <rar> --max-mb 19",
        "policy: no_dump_login no_execute",
        "",
    ]
    for i in range(min(n, 12)):
        start = i * part
        end = min(size, (i + 1) * part)
        lines.append(f"part_{i+1:03d}: bytes {start}-{end}")
    if n > 12:
        lines.append(f"... +{n - 12} parts")
    return "\n".join(lines) + "\n"


def split_file(path: Path, *, max_mb: float = 19.0) -> dict[str, Any]:
    if not path.is_file():
        return {"ok": False, "error": f"missing {path}"}
    raw_size = path.stat().st_size
    part = int(max_mb * 1024 * 1024)
    out_dir = ROOT / "quarantine" / "lab" / "chunks" / "files" / path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    sha = hashlib.sha256()
    with path.open("rb") as f:
        idx = 0
        while True:
            blob = f.read(part)
            if not blob:
                break
            idx += 1
            sha.update(blob)
            dest = out_dir / f"{path.name}.part{idx:03d}"
            dest.write_bytes(blob)
            # sidecar txt
            meta = out_dir / f"{path.name}.part{idx:03d}.txt"
            meta.write_text(
                f"part: {idx}\nsource: {path.name}\nbytes: {len(blob)}\n"
                f"sha256_part: {hashlib.sha256(blob).hexdigest()}\n"
                f"policy: no_dump_login no_execute\nchecked_at: {utc_now()}\n",
                encoding="utf-8",
            )
            parts.append(str(dest.relative_to(ROOT)))
    report = {
        "ok": True,
        "source": str(path),
        "size_bytes": raw_size,
        "sha256": sha.hexdigest(),
        "parts_n": len(parts),
        "parts": parts,
        "out_dir": str(out_dir.relative_to(ROOT)),
        "max_mb": max_mb,
        "verdict": f"✅ Split {path.name} → {len(parts)} parts ≤{max_mb}MB",
    }
    (out_dir / "MANIFEST.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def format_split(report: dict[str, Any]) -> str:
    lines = [
        "🧩 CHIA NHỎ → PHÒNG THÍ NGHIỆM",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
        f"Lab: {report.get('lab_chunks')}",
        f"Flat: {report.get('flat')}",
        "Policy: no dump-login · no execute · no unpack",
        "",
        "=== Items ===",
    ]
    for it in report.get("items") or []:
        lines.append(
            f"· [{it.get('index')}/6] {it.get('file_name')} ({it.get('size_gb')} GB) → {it.get('chunks_n')} chunks"
        )
        lines.append(f"  {it.get('folder')}")
    for n in report.get("next") or []:
        lines.append(f"Next: {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Chia nhỏ đưa vào lab")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("onlylogs6", help="Chia 6 OnlyLogs thành chunk lab")
    p1.add_argument("--json", action="store_true")
    p1.add_argument("--notify", action="store_true")

    p2 = sub.add_parser("file", help="Cắt 1 file owned thành phần ≤ max-mb")
    p2.add_argument("--path", required=True)
    p2.add_argument("--max-mb", type=float, default=19.0)
    p2.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd == "onlylogs6":
        report = split_onlylogs6()
        # analyze staged chunks
        try:
            from lab_control import cmd_analyze

            ana = cmd_analyze(include_telegram=False, max_files=800)
            report["analyze"] = {
                "analyzed_n": ana.get("analyzed_n"),
                "high_critical_n": ana.get("high_critical_n"),
                "bands": ana.get("risk_bands"),
            }
        except Exception as e:  # noqa: BLE001
            report["analyze_error"] = str(e)[:200]
        text = format_split(report)
        if getattr(args, "notify", False):
            _notify(text)
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else text)
        return 0 if report.get("ok") else 1

    report = split_file(Path(args.path), max_mb=args.max_mb)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else report.get("verdict"))
    return 0 if report.get("ok") else 1


def _notify(text: str) -> None:
    import os
    import urllib.request

    env = dict(os.environ)
    for p in (ROOT / "secrets" / "telegram.env",):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        return
    body = json.dumps({"chat_id": chat, "text": text[:3500]}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


if __name__ == "__main__":
    raise SystemExit(main())
