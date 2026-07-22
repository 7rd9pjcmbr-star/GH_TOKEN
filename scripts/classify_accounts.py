#!/usr/bin/env python3
"""
Phân loại giá trị local-only từ file identifier:password.
Không gọi breach DB / OSINT / không suy portal tấn.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

GENERIC_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.com.vn",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "msn.com",
    "icloud.com",
    "me.com",
    "protonmail.com",
    "proton.me",
    "aol.com",
    "mail.com",
    "yandex.com",
    "yandex.ru",
    "zoho.com",
    "gmx.com",
    "gmx.net",
    "mail.ru",
    "qq.com",
    "163.com",
    "126.com",
    "gmail.com.vn",
}

PHONE_VN = re.compile(r"^(?:\+?84|0)(?:3|5|7|8|9)\d{8}$")
PHONE_INTL = re.compile(r"^\+[1-9]\d{7,14}$")
PHONE_LOOSE = re.compile(r"^\+?[0-9]{9,15}$")

VN_CARRIERS = {
    "Viettel": ("032", "033", "034", "035", "036", "037", "038", "086", "096", "097", "098"),
    "Mobifone": ("070", "076", "077", "078", "079", "089", "090", "093"),
    "VinaPhone": ("081", "082", "083", "084", "085", "088", "091", "094"),
    "Vietnamobile": ("056", "058", "092"),
}


def identify_vn_carrier(phone: str) -> str | None:
    p = phone.strip()
    if p.startswith("+84"):
        p = "0" + p[3:]
    elif p.startswith("84") and len(p) >= 11:
        p = "0" + p[2:]
    pref = p[:3]
    for name, prefixes in VN_CARRIERS.items():
        if pref in prefixes:
            return name
    return None


def classify_password(pw: str) -> tuple[str, str | None]:
    if re.match(r"^\$2[aby]\$\d{2}\$", pw):
        return "bcrypt_hash", "modern_platform"
    if re.fullmatch(r"[0-9a-fA-F]{32}", pw):
        return "md5_hash", "legacy_platform_md5"
    if re.fullmatch(r"[0-9a-fA-F]{64}", pw):
        return "sha256_hash", "modern_platform_sha256"
    if re.fullmatch(r"[0-9]{4,6}", pw):
        return "numeric_only", "banking_or_pin"
    if len(pw) < 6:
        return "very_short", "legacy_platform"
    if re.search(r"0[389]\d{8}", pw):
        return "vn_phone_in_pass", None
    if re.search(r"(19|20)\d{2}(0[1-9]|1[0-2])", pw):
        return "date_pattern", None
    return "plaintext_normal", None


def classify_identifier(ident: str) -> dict:
    ident = ident.strip()
    out = {
        "identifier": ident,
        "type": "unknown",
        "email_domain": None,
        "carrier": None,
    }
    if "@" in ident:
        domain = ident.rsplit("@", 1)[-1].lower().strip()
        out["email_domain"] = domain
        if domain in GENERIC_DOMAINS:
            out["type"] = "generic_email"
        else:
            out["type"] = "corporate_email"
        return out
    compact = re.sub(r"[\s\-()]", "", ident)
    if PHONE_VN.match(compact) or PHONE_INTL.match(compact) or PHONE_LOOSE.match(compact):
        out["type"] = "phone"
        out["carrier"] = identify_vn_carrier(compact)
        out["identifier"] = compact
        return out
    out["type"] = "unknown"
    return out


def parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    # CSV-ish: take first two fields if comma-separated and no colon password form
    if ":" in line:
        ident, pw = line.split(":", 1)
        return ident.strip(), pw.strip()
    if "," in line:
        parts = next(csv.reader([line]))
        if len(parts) >= 2:
            return parts[0].strip(), parts[1].strip()
    return None


def classify_file(path: Path, limit: int | None = None) -> dict:
    records = []
    skipped = 0
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if limit is not None and i >= limit:
                break
            parsed = parse_line(line)
            if not parsed:
                skipped += 1
                continue
            ident, pw = parsed
            row = classify_identifier(ident)
            pw_pat, hint = classify_password(pw)
            row["password_pattern"] = pw_pat
            row["platform_hint"] = hint
            row["password_len"] = len(pw)
            records.append(row)

    by_type = Counter(r["type"] for r in records)
    by_pw = Counter(r["password_pattern"] for r in records)
    by_hint = Counter(h for h in (r["platform_hint"] for r in records) if h)
    corp_domains = Counter(
        r["email_domain"] for r in records if r["type"] == "corporate_email" and r["email_domain"]
    )
    carriers = Counter(r["carrier"] for r in records if r["type"] == "phone" and r["carrier"])

    total = len(records) or 1
    summary = {
        "ok": True,
        "mode": "local-only",
        "file": path.name,
        "path": str(path),
        "lines_parsed": len(records),
        "lines_skipped": skipped,
        "by_type": {
            k: {"count": v, "pct": round(100.0 * v / total, 2)} for k, v in by_type.most_common()
        },
        "password_patterns": {
            k: {"count": v, "pct": round(100.0 * v / total, 2)} for k, v in by_pw.most_common()
        },
        "platform_hints": dict(by_hint.most_common()),
        "top_corporate_domains": corp_domains.most_common(15),
        "top_carriers": carriers.most_common(10),
        "note": "Không tra breach DB / không OSINT / không suy portal tấn.",
    }
    return {"summary": summary, "records": records}


def write_outputs(result: dict, out_dir: Path, stem: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / f"{stem}.summary.json"
    csv_path = out_dir / f"{stem}.classified.csv"
    buckets = defaultdict(list)
    for r in result["records"]:
        buckets[r["type"]].append(r)

    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(result["summary"], f, ensure_ascii=False, indent=2)

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "identifier",
                "type",
                "email_domain",
                "carrier",
                "password_pattern",
                "platform_hint",
                "password_len",
            ],
        )
        w.writeheader()
        w.writerows(result["records"])

    bucket_paths = {}
    for typ, rows in buckets.items():
        p = out_dir / f"{stem}.{typ}.txt"
        with p.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(r["identifier"] + "\n")
        bucket_paths[typ] = str(p)

    return {
        "summary": str(summary_path),
        "csv": str(csv_path),
        "buckets": bucket_paths,
    }


def format_telegram_report(summary: dict) -> str:
    lines = [
        f"Phân loại local: {summary.get('file')}",
        f"Parsed: {summary.get('lines_parsed')} · skipped: {summary.get('lines_skipped')}",
        "",
        "Theo loại:",
    ]
    for k, v in (summary.get("by_type") or {}).items():
        lines.append(f"· {k}: {v['count']} ({v['pct']}%)")
    lines.append("")
    lines.append("Password pattern:")
    for k, v in list((summary.get("password_patterns") or {}).items())[:8]:
        lines.append(f"· {k}: {v['count']} ({v['pct']}%)")
    top = summary.get("top_corporate_domains") or []
    if top:
        lines.append("")
        lines.append("Top corporate domains:")
        for dom, n in top[:8]:
            lines.append(f"· {dom}: {n}")
    carriers = summary.get("top_carriers") or []
    if carriers:
        lines.append("")
        lines.append("Nhà mạng:")
        for c, n in carriers[:6]:
            lines.append(f"· {c}: {n}")
    lines.append("")
    lines.append(summary.get("note") or "")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Local classify identifier:password files")
    ap.add_argument("file", type=Path)
    ap.add_argument("--out", type=Path, default=Path("reports/classify"))
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    if not args.file.is_file():
        print(f"missing file: {args.file}", flush=True)
        return 2
    result = classify_file(args.file, limit=args.limit)
    paths = write_outputs(result, args.out, args.file.stem)
    print(json.dumps({"summary": result["summary"], "outputs": paths}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
