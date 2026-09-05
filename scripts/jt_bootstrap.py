#!/usr/bin/env python3
"""JT bootstrap — tạo file thiếu, nạp Telegram, chạy pipeline J&T → KET_QUA."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SECRETS = ROOT / "secrets"
INBOX = ROOT / "quarantine" / "telegram"
REPORTS = ROOT / "reports" / "telegram-classify"

CUSTOMER_CODE_RE = re.compile(r"\b(\d{2,3}[Ll][Cc]\d{4,8})\b")
BILL_RE = re.compile(
    r"\b(84[0-9]{10,12}|85[0-9]{10,12}|JNTMP[0-9]{10,14}|JT[0-9A-Z]{10,16}|JO[0-9]{8,14}|JD[0-9]{8,14})\b",
    re.I,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chmod600(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def extract_customer_codes() -> list[str]:
    codes: set[str] = set()
    for p in sorted(INBOX.glob("*jt_parsed*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        items = data if isinstance(data, list) else []
        for e in items:
            if not isinstance(e, dict):
                continue
            for m in CUSTOMER_CODE_RE.findall(str(e.get("username") or "")):
                codes.add(m.upper().replace(" ", ""))
    return sorted(codes)


def harvest_bill_refs() -> list[str]:
    refs: set[str] = set()
    for root in (INBOX, REPORTS, ROOT / "uploads"):
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.stat().st_size > 2_000_000:
                continue
            if "jt_parsed" in p.name.lower():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")[:300_000]
            except OSError:
                continue
            for m in BILL_RE.findall(text):
                refs.add(m.upper())
    return sorted(refs)


def ensure_jt_api_env(codes: list[str]) -> dict:
    """Tạo secrets/jt_api.env nếu chưa có; gợi ý customerCode từ jt_parsed."""
    dest = SECRETS / "jt_api.env"
    example = SECRETS / "jt_api.env.example"
    created = False
    if not dest.is_file():
        if example.is_file():
            shutil.copy2(example, dest)
        else:
            dest.write_text(
                "# J&T Open Platform — owned credentials\nJT_API_REGION=vn\n"
                "JT_OPEN_BASE_URL=https://ylopenapi.jtexpress.vn/webopenplatformapi/api\n",
                encoding="utf-8",
            )
        created = True
        _chmod600(dest)

    lines = dest.read_text(encoding="utf-8", errors="replace").splitlines()
    hint = ", ".join(codes[:8]) if codes else ""
    out: list[str] = []
    has_customer = any(ln.startswith("JT_CUSTOMER_CODE=") and ln.split("=", 1)[1].strip() for ln in lines)
    hint_written = any("jt_parsed customer codes" in ln for ln in lines)

    for ln in lines:
        out.append(ln)
    if not has_customer and codes:
        out.append(f"JT_CUSTOMER_CODE=")
    if hint and not hint_written:
        out.append(f"# jt_parsed customer codes (portal — cần apiAccount/privateKey từ open.jtexpress.vn): {hint}")

    if created or not has_customer or (hint and not hint_written):
        dest.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
        _chmod600(dest)

    filled = {
        "JT_API_ACCOUNT": False,
        "JT_PRIVATE_KEY": False,
        "JT_CUSTOMER_CODE": False,
        "JT_PASSWORD": False,
    }
    for ln in dest.read_text(encoding="utf-8").splitlines():
        if "=" in ln and not ln.strip().startswith("#"):
            k, v = ln.split("=", 1)
            k = k.strip()
            if k in filled:
                filled[k] = bool(v.strip())
    return {"path": str(dest), "created": created, "fields": filled, "customer_codes_hint": codes[:12]}


def ensure_jt_tracking_refs(bills: list[str]) -> dict:
    dest = SECRETS / "jt_tracking_refs.txt"
    example = SECRETS / "jt_tracking_refs.txt.example"
    created = False
    if not dest.is_file():
        if example.is_file():
            shutil.copy2(example, dest)
        else:
            dest.write_text(
                "# Mỗi dòng: BILL hoặc BILL:1234 (4 số cuối SĐT)\n",
                encoding="utf-8",
            )
        created = True

    existing = {
        ln.strip().split(":")[0].split(",")[0].upper()
        for ln in dest.read_text(encoding="utf-8", errors="replace").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    }
    added: list[str] = []
    if bills:
        new_lines = list(dest.read_text(encoding="utf-8", errors="replace").splitlines())
        for bill in bills:
            if bill.upper() not in existing:
                new_lines.append(bill)
                added.append(bill)
                existing.add(bill.upper())
        if added:
            dest.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")

    return {"path": str(dest), "created": created, "added": added, "total_lines": len(existing)}


def ensure_jt_customer_codes(codes: list[str]) -> dict:
    dest = SECRETS / "jt_customer_codes.txt"
    if codes:
        body = "\n".join(
            [
                f"# J&T portal customer codes từ jt_parsed — {utc_now()}",
                "# Dùng để đối chiếu với JT_CUSTOMER_CODE trong jt_api.env (Open Platform)",
                *codes,
                "",
            ]
        )
        dest.write_text(body, encoding="utf-8")
    elif not dest.is_file():
        dest.write_text("# Chưa có — chạy lại sau khi có jt_parsed_data.json\n", encoding="utf-8")
    return {"path": str(dest), "count": len(codes)}


def import_from_inbox() -> list[str]:
    from jt_express_api import import_jt_files_from_inbox

    return import_jt_files_from_inbox()


def pull_telegram(wait: int = 12) -> dict:
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "telegram_inbox_today_mapper.py"), "--pull", "--wait", str(wait)],
            capture_output=True,
            text=True,
            timeout=max(60, wait * 3),
            cwd=str(ROOT),
        )
        return {"ok": r.returncode == 0, "exit": r.returncode}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}


def run_pipeline() -> dict:
    steps: dict = {}
    try:
        from jt_parsed_audit import audit_file

        p = next(iter(sorted(INBOX.glob("*jt_parsed*.json"), reverse=True)), None)
        steps["audit"] = audit_file(p) if p else {"ok": False, "error": "no_jt_parsed"}
    except Exception as e:  # noqa: BLE001
        steps["audit"] = {"ok": False, "error": str(e)[:80]}

    try:
        from jt_express_fetch import run_fetch

        steps["api_fetch"] = run_fetch(apply=True)
    except Exception as e:  # noqa: BLE001
        steps["api_fetch"] = {"ok": False, "error": str(e)[:80]}

    try:
        from jt_public_trace import run_batch

        steps["public_trace"] = run_batch()
    except Exception as e:  # noqa: BLE001
        steps["public_trace"] = {"ok": False, "error": str(e)[:80]}

    ket_qua = REPORTS / "KET_QUA_DON_CHI_TIET.csv"
    rows = 0
    if ket_qua.is_file() and ket_qua.stat().st_size > 0:
        rows = max(0, sum(1 for _ in ket_qua.open(encoding="utf-8")) - 1)
    return {"steps": steps, "ket_qua_rows": rows}


def ensure_all(*, pull: bool = False, wait: int = 12, run: bool = True) -> dict:
    codes = extract_customer_codes()
    bills = harvest_bill_refs()
    report: dict = {
        "ok": False,
        "module": "jt_bootstrap",
        "checked_at": utc_now(),
        "customer_codes": len(codes),
        "bill_refs_harvested": len(bills),
        "jt_api_env": ensure_jt_api_env(codes),
        "jt_tracking_refs": ensure_jt_tracking_refs(bills),
        "jt_customer_codes": ensure_jt_customer_codes(codes),
        "inbox_import": import_from_inbox(),
    }

    if pull:
        report["telegram_pull"] = pull_telegram(wait=wait)
        report["inbox_import"] = import_from_inbox()
        # re-harvest after pull
        bills2 = harvest_bill_refs()
        if len(bills2) > len(bills):
            report["jt_tracking_refs"] = ensure_jt_tracking_refs(bills2)

    api = report["jt_api_env"]["fields"]
    report["api_ready"] = all(api.get(k) for k in ("JT_API_ACCOUNT", "JT_PRIVATE_KEY", "JT_CUSTOMER_CODE", "JT_PASSWORD"))
    report["has_tracking_refs"] = report["jt_tracking_refs"].get("total_lines", 0) > 0

    if run:
        report["pipeline"] = run_pipeline()
        report["ket_qua_rows"] = report["pipeline"].get("ket_qua_rows", 0)
        report["ok"] = report["ket_qua_rows"] > 0
    else:
        report["ket_qua_rows"] = 0

    if not report["ok"]:
        blockers = []
        if not report["api_ready"]:
            blockers.append(
                "Điền secrets/jt_api.env (apiAccount/privateKey từ open.jtexpress.vn) hoặc gửi file qua Telegram"
            )
        if not report["has_tracking_refs"]:
            blockers.append("Thêm mã vận đơn vào secrets/jt_tracking_refs.txt (BILL:4_số_SĐT)")
        if codes and not report["api_ready"]:
            blockers.append(f"Gợi ý customerCode từ jt_parsed: {', '.join(codes[:5])}")
        report["blockers"] = blockers
        report["verdict"] = "JT bootstrap xong — chờ credential Open Platform hoặc billCode"
    else:
        report["verdict"] = f"JT OK · KET_QUA={report['ket_qua_rows']} đơn"

    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "jt_bootstrap.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="JT bootstrap — tạo file thiếu + pipeline")
    ap.add_argument("--pull", action="store_true", help="Kéo Telegram trước")
    ap.add_argument("--wait", type=int, default=12)
    ap.add_argument("--no-run", action="store_true", help="Chỉ tạo file, không fetch")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rep = ensure_all(pull=args.pull, wait=args.wait, run=not args.no_run)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(f"jt_bootstrap · ket_qua={rep.get('ket_qua_rows')} api_ready={rep.get('api_ready')}")
        print(f"  customer_codes={rep.get('customer_codes')} tracking_refs={rep.get('has_tracking_refs')}")
        for b in rep.get("blockers") or []:
            print(f"  · {b}")
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
