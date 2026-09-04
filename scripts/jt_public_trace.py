#!/usr/bin/env python3
"""Tra cứu J&T công khai — jtexpress.vn/vi/tracking (billCode + 4 số cuối SĐT).

Không cần Open Platform credential. Cần mã vận đơn + 4 số cuối điện thoại người gửi/nhận.
Refs: secrets/jt_tracking_refs.txt — mỗi dòng: BILL hoặc BILL:PHONE4 hoặc BILL,PHONE4
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
INBOX = ROOT / "quarantine" / "telegram"

BILL_RE = re.compile(
    r"^\s*("
    r"84[0-9]{10,12}|85[0-9]{10,12}|58[0-9]{10,20}|"
    r"JNTMP[0-9]{10,14}|JT[0-9A-Z]{10,16}|"
    r"JO[0-9]{8,14}|JD[0-9]{8,14}|"
    r"[0-9]{10,20}"
    r")\s*$",
    re.I,
)
NOT_FOUND_RE = re.compile(r"Không tìm thấy dữ liệu", re.I)
PHONE_ERR_RE = re.compile(r"Số điện thoại không tồn tại", re.I)
STATUS_RE = re.compile(
    r"(Giao thành công|Đang giao|Đã lấy hàng|Đã tiếp nhận|Chờ lấy hàng|Hoàn thành|"
    r"Đang vận chuyển|Phát thất bại|Đang hoàn|Đã hoàn)",
    re.I,
)
ADDR_RE = re.compile(r"(?:địa chỉ|address|người nhận)[^<]{0,40}([^<]{10,120})", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_my_bill(bill: str) -> bool:
    b = bill.upper()
    return b.startswith("JNTMP") or (b.isdigit() and b.startswith("85"))


def trace_my(bill: str, phone_suffix: str = "", *, timeout: float = 20.0) -> dict[str, Any]:
    """jtexpress.my — JSON track (có thể cần captcha; parcelInfo rỗng = chưa lấy được)."""
    import http.cookiejar
    import json as _json

    bill = bill.strip().upper()
    phone_suffix = re.sub(r"\D", "", phone_suffix)[-4:]
    try:
        cj = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        op.open(
            urllib.request.Request(
                f"https://www.jtexpress.my/tracking?billcode={urllib.parse.quote(bill)}",
                headers={"User-Agent": "Mozilla/5.0 (jt-public-trace/1.2)"},
            ),
            timeout=timeout,
        )
        tok = _json.loads(
            op.open(
                urllib.request.Request("https://www.jtexpress.my/refresh-token", headers={"User-Agent": "Mozilla/5.0"}),
                timeout=timeout,
            ).read()
        )["token"]
        data = urllib.parse.urlencode({"_token": tok, "wb": bill, "randstr": "", "ticket": ""}).encode()
        body = op.open(
            urllib.request.Request(
                "https://www.jtexpress.my/track",
                data=data,
                method="POST",
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "X-CSRF-TOKEN": tok,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"https://www.jtexpress.my/tracking?billcode={bill}",
                },
            ),
            timeout=timeout,
        ).read().decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "bill": bill, "error": str(e)[:160], "region": "my"}

    if "information not found" in body.lower() or '"parcelInfo":"[]"' in body or '"parcelInfo": "[]"' in body:
        return {"ok": False, "bill": bill, "error": "not_found", "region": "my"}

    if body.strip().startswith("{"):
        try:
            j = _json.loads(body)
            pi = j.get("parcelInfo")
            if isinstance(pi, str):
                pi = _json.loads(pi) if pi.strip().startswith("[") else []
            if isinstance(pi, list) and pi:
                p0 = pi[0] if isinstance(pi[0], dict) else {}
                status = str(p0.get("status") or p0.get("scanType") or p0.get("statusName") or "traced")
                row = {
                    "order_key": bill,
                    "remote_id": bill,
                    "tracking_code": bill,
                    "status_normalized": status,
                    "status_raw": status,
                    "carrier": "J&T",
                    "platform": "J&T-MY-public",
                    "source": "jt_public_trace",
                    "channel": "jtexpress.my/track",
                }
                return {"ok": True, "bill": bill, "phone_suffix": phone_suffix, "row": row, "status": status, "region": "my"}
        except (_json.JSONDecodeError, TypeError):
            pass

    if re.search(r"delivered|dihantar|out for delivery|in transit", body, re.I):
        st = "traced"
        m = re.search(r"(delivered|dihantar|out for delivery|in transit)", body, re.I)
        if m:
            st = m.group(1)
        row = {
            "order_key": bill,
            "remote_id": bill,
            "tracking_code": bill,
            "status_normalized": st,
            "status_raw": st,
            "carrier": "J&T",
            "platform": "J&T-MY-public",
            "source": "jt_public_trace",
            "channel": "jtexpress.my/track",
        }
        return {"ok": True, "bill": bill, "row": row, "status": st, "region": "my"}

    return {"ok": False, "bill": bill, "error": "no_data", "region": "my"}


def trace_bill(bill: str, phone_suffix: str = "", *, timeout: float = 20.0) -> dict[str, Any]:
    """Tra VN hoặc MY theo prefix mã."""
    bill = bill.strip().upper()
    if _is_my_bill(bill):
        att = trace_my(bill, phone_suffix, timeout=timeout)
        if att.get("ok"):
            return att
        return trace_public(bill, phone_suffix, timeout=timeout)
    att = trace_public(bill, phone_suffix, timeout=timeout)
    if att.get("ok"):
        return att
    return trace_my(bill, phone_suffix, timeout=timeout)


def load_refs() -> list[tuple[str, str]]:
    """(bill_code, phone_suffix) — phone_suffix có thể rỗng."""
    refs: list[tuple[str, str]] = []
    default_phone = (os.environ.get("JT_PUBLIC_PHONE_SUFFIX") or "").strip()

    paths = [SECRETS / "jt_tracking_refs.txt", INBOX / "jt_tracking_refs.txt"]
    paths.extend(sorted(INBOX.glob("jt_tracking*.txt")))

    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            if t.lower().startswith("# stale"):
                continue
            if ":" in t:
                bill, phone = t.split(":", 1)
            elif "," in t:
                bill, phone = t.split(",", 1)
            else:
                bill, phone = t, default_phone
            bill = bill.strip().upper()
            phone = re.sub(r"\D", "", phone.strip())[-4:]
            if BILL_RE.match(bill):
                refs.append((bill, phone))

    raw = (os.environ.get("JT_TRACKING_REFS") or "").strip()
    if raw:
        for part in raw.replace("\n", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                b, p = part.split(":", 1)
            else:
                b, p = part, default_phone
            b = b.strip().upper()
            p = re.sub(r"\D", "", p.strip())[-4:]
            if BILL_RE.match(b):
                refs.append((b, p))

    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for bill, phone in refs:
        if bill in seen:
            continue
        seen.add(bill)
        out.append((bill, phone))
    return out


def _vn_tracking_html(bill: str, phone_suffix: str, *, timeout: float) -> str:
    """GET form-tracking — server render vào .result-tracking (không dùng /vi/tracking)."""
    phone_suffix = re.sub(r"\D", "", phone_suffix)[-4:]
    params = urllib.parse.urlencode(
        {"type": "track", "billcode": bill.strip(), "cellphone": phone_suffix}
    )
    url = f"https://jtexpress.vn/tracking?{params}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (jt-public-trace/1.1)", "Accept-Language": "vi"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _vn_result_empty(html: str) -> bool:
    m = re.search(r'class="result-tracking[^"]*"[^>]*>(.*?)</div>', html, re.S | re.I)
    block = m.group(1) if m else ""
    return "empty-vandon" in block or "Không tìm thấy dữ liệu" in block


def trace_public(bill: str, phone_suffix: str = "", *, timeout: float = 20.0) -> dict[str, Any]:
    phone_suffix = re.sub(r"\D", "", phone_suffix)[-4:]
    try:
        html = _vn_tracking_html(bill, phone_suffix, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "bill": bill, "error": str(e)[:160]}

    if PHONE_ERR_RE.search(html) and phone_suffix and "text-error" in html:
        te = re.search(r'id="text-error"[^>]*>([^<]+)', html, re.I)
        if te and PHONE_ERR_RE.search(te.group(1)):
            return {"ok": False, "bill": bill, "error": "phone_mismatch", "needs_phone": True}
    if _vn_result_empty(html):
        return {"ok": False, "bill": bill, "error": "not_found", "needs_phone": not phone_suffix}
    if NOT_FOUND_RE.search(html):
        return {"ok": False, "bill": bill, "error": "not_found", "needs_phone": not phone_suffix}
    if PHONE_ERR_RE.search(html) and not phone_suffix:
        return {"ok": False, "bill": bill, "error": "need_phone_suffix"}

    status = ""
    m = STATUS_RE.search(html)
    if m:
        status = m.group(1)
    addr = ""
    ma = ADDR_RE.search(html)
    if ma:
        addr = re.sub(r"\s+", " ", ma.group(1)).strip()

    if not status and bill not in html:
        return {"ok": False, "bill": bill, "error": "no_data", "needs_phone": not phone_suffix}

    row = {
        "order_key": bill,
        "remote_id": bill,
        "tracking_code": bill,
        "full_address": addr,
        "status_normalized": status or "traced",
        "status_raw": status or "",
        "carrier": "J&T",
        "platform": "J&T-VN-public",
        "source": "jt_public_trace",
        "channel": "jtexpress.vn/tracking",
    }
    return {"ok": True, "bill": bill, "phone_suffix": phone_suffix, "row": row, "status": status}


def run_batch(*, limit: int = 50) -> dict[str, Any]:
    refs = load_refs()
    report: dict[str, Any] = {
        "ok": False,
        "module": "jt_public_trace",
        "checked_at": utc_now(),
        "refs": len(refs),
        "mapped": 0,
        "rows": [],
        "attempts": [],
    }
    if not refs:
        report["blockers"] = [
            "Thiếu mã vận đơn — secrets/jt_tracking_refs.txt (BILL hoặc BILL:1234)",
            "jt_parsed_data.json chỉ có credential portal — không có billCode",
        ]
        report["verdict"] = "Cần danh sách billCode + 4 số cuối SĐT"
        _write_report(report)
        return report

    rows: list[dict] = []
    for bill, phone in refs[:limit]:
        att = trace_bill(bill, phone)
        report["attempts"].append(
            {"bill": bill, "ok": att.get("ok"), "error": att.get("error"), "needs_phone": att.get("needs_phone")}
        )
        if att.get("ok") and att.get("row"):
            rows.append(att["row"])

    report["mapped"] = len(rows)
    report["rows"] = rows[:20]
    if rows:
        report["ok"] = True
        report["verdict"] = f"J&T public trace: {len(rows)} đơn"
        try:
            from flex_local_ingest import dedupe_rows, write_exports
            from export_orders_detailed import CSV_FIELDS

            full = []
            for r in rows:
                base = {f: "" for f in CSV_FIELDS}
                base.update({k: str(v) if v is not None else "" for k, v in r.items()})
                full.append(base)
            rep = write_exports(dedupe_rows(full))
            report["ket_qua_rows"] = int(rep.get("ket_qua_rows") or 0)
        except Exception as e:  # noqa: BLE001
            report["ket_qua_error"] = str(e)[:120]
    else:
        report["verdict"] = "Có refs nhưng chưa tra được — kiểm tra billCode + 4 số cuối SĐT"
        report["blockers"] = [
            "Định dạng jt_tracking_refs.txt: 841234567890:1234 (bill:4_số_cuối_SĐT)",
            "Hoặc điền secrets/jt_api.env (Open Platform) → jt_express_fetch.py",
        ]

    _write_report(report)
    return report


def _write_report(report: dict) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    safe = {k: v for k, v in report.items() if k != "attempts" or len(v) <= 40}
    (REPORTS / "jt_public_trace.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="J&T public web trace → KET_QUA")
    ap.add_argument("--bill", type=str, default="", help="Tra 1 mã")
    ap.add_argument("--phone", type=str, default="", help="4 số cuối SĐT")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.bill:
        rep = trace_bill(args.bill.strip(), args.phone)
        if rep.get("ok") and rep.get("row"):
            try:
                from flex_local_ingest import dedupe_rows, write_exports
                from export_orders_detailed import CSV_FIELDS

                base = {f: "" for f in CSV_FIELDS}
                base.update({k: str(v) if v is not None else "" for k, v in rep["row"].items()})
                wrep = write_exports(dedupe_rows([base]))
                rep["ket_qua_rows"] = int(wrep.get("ket_qua_rows") or 0)
            except Exception as e:  # noqa: BLE001
                rep["ket_qua_error"] = str(e)[:120]
    else:
        rep = run_batch()

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        if args.bill:
            print(f"trace ok={rep.get('ok')} bill={rep.get('bill')} status={rep.get('status') or rep.get('error')}")
        else:
            print(f"jt_public_trace ok={rep.get('ok')} mapped={rep.get('mapped')} ket_qua={rep.get('ket_qua_rows', 0)}")
            for b in rep.get("blockers") or []:
                print(f"  · {b}")
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
