#!/usr/bin/env python3
"""MaMoLab static engine v2 — shared host/container (phòng thủ).

Không thực thi mẫu · không dump-login · không generate exploit.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Rules: id, severity, family, regex, optional require_textish
RULES: list[tuple[str, str, str, re.Pattern[bytes], bool]] = [
    ("dyn-eval", "high", "execution", re.compile(rb"\beval\s*\(|\bnew\s+Function\s*\("), True),
    (
        "powershell-download",
        "critical",
        "dropper",
        re.compile(
            rb"(?i)(IEX\s*\(|Invoke-Expression|DownloadString|Invoke-WebRequest|FromBase64String)",
        ),
        True,
    ),
    (
        "bash-curl-pipe",
        "critical",
        "dropper",
        re.compile(rb"(?i)curl[^\n|]{0,80}\|\s*(ba)?sh|wget[^\n|;]{0,80}\|\s*(ba)?sh"),
        True,
    ),
    (
        "webshell-php",
        "critical",
        "webshell",
        re.compile(rb"(?i)eval\s*\(\s*\$_(POST|GET|REQUEST)|passthru\s*\(|system\s*\(\s*\$_"),
        True,
    ),
    (
        "macro-autoopen",
        "high",
        "office",
        re.compile(rb"AutoOpen|Document_Open|Workbook_Open|Shell\s*\("),
        False,
    ),
    (
        "cred-dump-hint",
        "high",
        "credential",
        re.compile(rb"(?i)\b(password|passwd|pwd)\s*[:=]\s*\S+|\bstealer\b|\bacc_all\b"),
        True,
    ),
    (
        "cookie-session",
        "medium",
        "session",
        re.compile(rb"(?i)\b(cookie|sessionid|authorization|bearer)\b.{0,48}[:=]"),
        True,
    ),
    (
        "private-key",
        "critical",
        "secret",
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        True,
    ),
    (
        "telegram-bot-token",
        "critical",
        "secret",
        re.compile(rb"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),
        True,
    ),
    (
        "aws-key",
        "critical",
        "secret",
        re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
        True,
    ),
    (
        "reverse-shell-hint",
        "high",
        "c2",
        re.compile(rb"(?i)(/dev/tcp/|bash\s+-i\s+>&|/bin/bash\s+-i|nc\s+-[el]|ncat\s+)"),
        True,
    ),
    (
        "onlylogs-stealer",
        "critical",
        "stealer",
        re.compile(rb"(?i)onlylogs|@onlylogscloud|stealer.?log"),
        False,
    ),
]

NAME_RULES: list[tuple[str, str, str, re.Pattern[str]]] = [
    ("name-stealer", "critical", "triage", re.compile(r"(?i)stealer|onlylogs|acc_all|assassin")),
    ("name-dump", "high", "triage", re.compile(r"(?i)dump|results_cookies|internal_search|ghn_tokens")),
    ("name-proxy-pool", "medium", "triage", re.compile(r"(?i)socks|proxies|proxy")),
    ("name-exe", "high", "triage", re.compile(r"(?i)\.(exe|dll|ps1|bat|vbs|apk)$")),
]

WEIGHT = {"critical": 40, "high": 22, "medium": 12, "low": 5}
TEXT_EXTS = {
    ".txt",
    ".json",
    ".csv",
    ".log",
    ".js",
    ".ts",
    ".py",
    ".php",
    ".ps1",
    ".sh",
    ".bat",
    ".cmd",
    ".xml",
    ".html",
    ".htm",
    ".yml",
    ".yaml",
    ".env",
    ".md",
    ".har",
    ".conf",
    ".cfg",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def shannon(data: bytes) -> float:
    if not data:
        return 0.0
    c = Counter(data)
    n = len(data)
    return -sum((v / n) * math.log2(v / n) for v in c.values())


def is_textish(path: Path, raw: bytes) -> bool:
    if path.suffix.lower() in TEXT_EXTS:
        return True
    if path.name.upper().endswith(".STUB.TXT") or path.suffix.lower() == ".stub.txt":
        return True
    sample = raw[:2048]
    if not sample:
        return True
    # printable ratio
    printable = sum(1 for b in sample if 9 <= b <= 13 or 32 <= b <= 126)
    return (printable / len(sample)) >= 0.75


def analyze_bytes(
    raw: bytes,
    *,
    name: str,
    size: int | None = None,
    rel_path: str | None = None,
    surface: str = "lab-static-v2",
) -> dict[str, Any]:
    path = Path(name)
    textish = is_textish(path, raw)
    findings: list[dict[str, Any]] = []

    for rid, sev, family, cre, need_text in RULES:
        if need_text and not textish:
            continue
        hits = cre.findall(raw)
        if hits:
            findings.append(
                {"id": rid, "severity": sev, "family": family, "count": len(hits)}
            )

    for rid, sev, family, cre in NAME_RULES:
        if cre.search(name):
            findings.append({"id": rid, "severity": sev, "family": family, "count": 1})

    score = min(100, sum(WEIGHT.get(f["severity"], 0) for f in findings))
    ent = shannon(raw[:8000])
    if ent > 7.2 and len(raw) > 200 and not textish:
        # packed/encrypted blob
        score = min(100, score + 12)
        findings.append(
            {"id": "high-entropy-blob", "severity": "medium", "family": "packing", "count": 1}
        )
    elif ent > 5.2 and len(raw) > 80 and textish:
        score = min(100, score + 6)

    size = size if size is not None else len(raw)
    band = (
        "critical"
        if score >= 70
        else "high"
        if score >= 40
        else "medium"
        if score >= 18
        else "low"
    )
    return {
        "ok": True,
        "engine": "lab_static_v2",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "isolation": {
            "surface": surface,
            "executedSample": False,
            "networkUsed": False,
            "loginAttempted": False,
            "path": rel_path or name,
        },
        "file": {
            "name": path.name,
            "size": size,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "md5": hashlib.md5(raw, usedforsecurity=False).hexdigest(),
            "entropyHead": round(ent, 3),
            "textish": textish,
        },
        "summary": {
            "riskScore": score,
            "riskBand": band,
            "findingCount": len(findings),
        },
        "findings": findings,
        "policy": {
            "noExecute": True,
            "noDumpLogin": True,
            "labOnly": True,
            "engineVersion": 2,
        },
        "disclaimer": "Static-only lab analysis. Does not execute the sample.",
    }


def analyze_path(path: Path, *, root: Path | None = None, surface: str = "lab-static-v2") -> dict[str, Any]:
    raw = path.read_bytes()[:2_000_000]
    rel = None
    if root:
        try:
            rel = str(path.resolve().relative_to(root.resolve()))
        except ValueError:
            rel = str(path)
    return analyze_bytes(
        raw,
        name=path.name,
        size=path.stat().st_size,
        rel_path=rel,
        surface=surface,
    )


def write_report(report: dict[str, Any], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
