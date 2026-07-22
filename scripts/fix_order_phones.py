#!/usr/bin/env python3
"""
Khắc phục / gắn nhãn lỗi SĐT trên file đơn hàng (local-only).
- Phát hiện SĐT bị mask (*, x)
- Chuẩn hoá SĐT VN khi có thể
- Gắn phone_status + nguyên nhân + hành động khắc phục
Không đoán số đã che; không gọi API bên ngoài.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

PHONE_OK = re.compile(r"^0(?:3|5|7|8|9)\d{8}$")
MASK_CHARS = set("*xX#")


def digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def is_masked(raw: str) -> bool:
    s = str(raw or "")
    return any(c in s for c in MASK_CHARS)


def normalize_vn_phone(raw: str) -> tuple[str | None, str]:
    """Return (normalized|None, status)."""
    if raw is None:
        return None, "missing"
    s = str(raw).strip()
    if not s or s.lower() in {"none", "null", "nan", "n/a", "#n/a"}:
        return None, "missing"
    if is_masked(s):
        return None, "masked"
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    compact = re.sub(r"[\s\-().]", "", s)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if compact.startswith("+84"):
        compact = "0" + compact[3:]
    elif compact.startswith("84") and len(compact) >= 11:
        compact = "0" + compact[2:]
    if re.fullmatch(r"[35789]\d{8}", compact):
        compact = "0" + compact
    if PHONE_OK.match(compact):
        return compact, "ok"
    if re.fullmatch(r"\+?[0-9]{9,15}", compact):
        return compact, "ok_loose"
    return None, "invalid"


def remediation_for(status: str, source: str, platform: str) -> dict:
    src = source or ""
    if status == "ok":
        return {
            "action": "none",
            "priority": "P3",
            "fix": "SĐT hợp lệ — giữ nguyên.",
        }
    if status == "masked":
        return {
            "action": "fetch_unmasked_from_source_api",
            "priority": "P1",
            "fix": (
                "SĐT bị che (*, x) trong snapshot API. "
                "Tắt PII mask khi export nội bộ hoặc gọi lại API gốc (Pancake/POS) lấy customer_phone đầy đủ."
            ),
        }
    if status == "missing":
        if "pancake" in src.lower():
            return {
                "action": "fix_pancake_sync_mapping",
                "priority": "P0",
                "fix": (
                    "Nguồn Pancake không ghi customer_phone (chuỗi rỗng). "
                    "Kiểm tra quyền API / field billing_address.phone / shipping_address.phone "
                    "và map vào customer_phone trước khi ghi CSV."
                ),
            }
        if "telegram_upload" in src.lower() or platform.lower() == "telegram upload":
            return {
                "action": "require_phone_on_telegram_upload",
                "priority": "P0",
                "fix": (
                    "Upload Telegram thiếu SĐT và shop_id. "
                    "Bắt buộc cột customer_phone trong template; reject đơn thiếu SĐT."
                ),
            }
        return {
            "action": "backfill_from_oms",
            "priority": "P1",
            "fix": "customer_phone trống — backfill từ OMS/CRM theo remote_id/order_key.",
        }
    return {
        "action": "manual_validate",
        "priority": "P2",
        "fix": "SĐT không chuẩn và không phải mask — kiểm tra tay / form nhập.",
    }


def load_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, list) else []
    with path.open(encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def process(path: Path) -> dict:
    rows = load_rows(path)
    out_rows = []
    stats = Counter()
    cause = Counter()
    by_source = defaultdict(Counter)

    for r in rows:
        raw = r.get("customer_phone")
        normalized, status = normalize_vn_phone(raw)
        src = str(r.get("source") or "")
        plat = str(r.get("platform") or "")
        rem = remediation_for(status, src, plat)
        stats[status] += 1
        by_source[src][status] += 1
        cause[rem["action"]] += 1
        row = dict(r)
        row["customer_phone_raw"] = raw
        row["customer_phone_normalized"] = normalized or ""
        row["phone_status"] = status
        row["phone_fix_action"] = rem["action"]
        row["phone_fix_priority"] = rem["priority"]
        row["phone_fix_note"] = rem["fix"]
        # Prefer normalized when ok
        if normalized:
            row["customer_phone"] = normalized
        out_rows.append(row)

    return {
        "file": path.name,
        "records": len(rows),
        "stats": dict(stats),
        "fix_actions": dict(cause),
        "by_source": {k: dict(v) for k, v in by_source.items()},
        "rows": out_rows,
    }


def write_outputs(result: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(result["file"]).stem
    fixed_csv = out_dir / f"{stem}.phone_fixed.csv"
    summary_json = out_dir / f"{stem}.phone_fix.summary.json"
    todo_json = out_dir / f"{stem}.phone_fix.todo.json"

    fieldnames = list(result["rows"][0].keys()) if result["rows"] else []
    with fixed_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(result["rows"])

    summary = {
        "ok": True,
        "file": result["file"],
        "records": result["records"],
        "stats": result["stats"],
        "fix_actions": result["fix_actions"],
        "by_source": result["by_source"],
        "notes": [
            "masked = SĐT bị che bằng * / x — không khôi phục local được",
            "missing = chuỗi rỗng từ upstream",
            "ok = đã chuẩn hoá về 0xxxxxxxxx khi có thể",
        ],
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # compact todo list for ops
    todos = []
    for action, n in sorted(result["fix_actions"].items(), key=lambda x: -x[1]):
        if action == "none":
            continue
        sample = next((r for r in result["rows"] if r["phone_fix_action"] == action), None)
        todos.append(
            {
                "action": action,
                "count": n,
                "priority": sample["phone_fix_priority"] if sample else "P2",
                "note": sample["phone_fix_note"] if sample else "",
            }
        )
    todo_json.write_text(json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"fixed_csv": str(fixed_csv), "summary": str(summary_json), "todo": str(todo_json)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("reports/telegram-classify/phone-fix"))
    args = ap.parse_args()
    reports = []
    for f in args.files:
        result = process(f)
        paths = write_outputs(result, args.out)
        reports.append(
            {
                "file": result["file"],
                "stats": result["stats"],
                "fix_actions": result["fix_actions"],
                "by_source": result["by_source"],
                "outputs": paths,
            }
        )
        # drop rows from memory print
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
