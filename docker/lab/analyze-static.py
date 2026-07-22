#!/usr/bin/env python3
"""Phân tích tĩnh phòng thủ trong container — không thực thi mẫu."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

RULES = [
    ("dyn-eval", "high", "execution", re.compile(rb"\beval\s*\(|\bnew\s+Function\s*\(")),
    ("powershell-download", "critical", "dropper", re.compile(rb"IEX|DownloadString|Invoke-WebRequest|FromBase64String", re.I)),
    ("bash-curl-pipe", "critical", "dropper", re.compile(rb"curl[^\n|]{0,80}\|\s*(ba)?sh|wget[^\n|;]{0,80}\|\s*(ba)?sh", re.I)),
    ("webshell-php", "critical", "webshell", re.compile(rb"eval\s*\(\s*\$_(POST|GET|REQUEST)|passthru\s*\(", re.I)),
    ("macro-autoopen", "high", "office", re.compile(rb"AutoOpen|Document_Open|Workbook_Open|Shell\s*\(")),
]


def shannon(data: bytes) -> float:
    if not data:
        return 0.0
    c = Counter(data)
    n = len(data)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: analyze-static.py <file>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1]).resolve()
    if not str(path).startswith("/quarantine/"):
        print("refusing path outside /quarantine", file=sys.stderr)
        return 3
    raw = path.read_bytes()[: 2_000_000]
    text_like = raw
    findings = []
    for rid, sev, family, cre in RULES:
        hits = cre.findall(text_like)
        if hits:
            findings.append(
                {
                    "id": rid,
                    "severity": sev,
                    "family": family,
                    "count": len(hits),
                }
            )
    weight = {"critical": 40, "high": 22, "medium": 12, "low": 5}
    score = min(100, sum(weight.get(f["severity"], 0) for f in findings))
    ent = shannon(raw[:8000])
    if ent > 5.2 and len(raw) > 80:
        score = min(100, score + 8)
    report = {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "isolation": {
            "surface": "docker-lab",
            "executedSample": False,
            "networkUsed": False,
            "path": str(path),
        },
        "file": {
            "name": path.name,
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "md5": hashlib.md5(raw).hexdigest(),
            "entropyHead": round(ent, 3),
        },
        "summary": {
            "riskScore": score,
            "riskBand": "critical"
            if score >= 70
            else "high"
            if score >= 40
            else "medium"
            if score >= 18
            else "low",
            "findingCount": len(findings),
        },
        "findings": findings,
        "disclaimer": "Static-only container analysis. Does not execute the sample.",
    }
    out = Path("/reports") / f"{path.name}.report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\n# wrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
