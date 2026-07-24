#!/usr/bin/env python3
"""Tracking.aship — dựng URL theo provider_code={ref}&provider=…

Mẫu:
  https://tracking.aship.app/order?provider_code={ref}&provider=spx
  https://tracking.aship.app/order?provider_code={ref}&provider=ghn
  https://tracking.aship.app/order?provider_code={ref}&provider=viettelpost

Secrets-only probe. Không dump login.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
OUT = REPORTS / "realtime"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"
DG_DB = REPORTS / "dang_giao_chi_tiet.db"

ASHIP_BASE = "https://tracking.aship.app/order"
ASHIP_TEMPLATE = "https://tracking.aship.app/order?provider_code={ref}&provider={provider}"

# buucuc / carrier / backend → provider query param
PROVIDER_MAP = {
    "SPX": "spx",
    "SPX-local": "spx",
    "spx": "spx",
    "spx_local": "spx",
    "GHN": "ghn",
    "ghn": "ghn",
    "GIAOHANGNHANH": "ghn",
    "ViettelPost": "viettelpost",
    "VIETTELPOST": "viettelpost",
    "viettelpost": "viettelpost",
    "VTP": "viettelpost",
    "vtp": "viettelpost",
    "VNPost": "vnpost",
    "VNPost-local": "vnpost",
    "vnpost": "vnpost",
    "vnpost_local": "vnpost",
    "JT": "jnt",
    "J&T": "jnt",
    "BEST": "best",
    "Ninja": "ninjavan",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_provider(
    *,
    carrier: str | None = None,
    buucuc: str | None = None,
    backend: str | None = None,
    tracking_code: str | None = None,
    explicit: str | None = None,
) -> str | None:
    if explicit:
        return explicit.strip().lower()
    for raw in (carrier, buucuc, backend):
        if not raw:
            continue
        key = str(raw).strip()
        if key in PROVIDER_MAP:
            return PROVIDER_MAP[key]
        up = key.upper()
        if up in PROVIDER_MAP:
            return PROVIDER_MAP[up]
        for k, v in PROVIDER_MAP.items():
            if k.upper() in up or up in k.upper():
                return v
    track = (tracking_code or "").upper()
    if track.startswith("SPX"):
        return "spx"
    # SPX Express thường: 26 + 12 ký tự (vd 260724FBQYQM5X) — dù buucuc=Pancake
    if re.fullmatch(r"26[0-9A-Z]{12}", track):
        return "spx"
    if track.startswith(("GHN", "GHA")):
        return "ghn"
    # TPOS / Aship ViettelPost mã VĐ
    if track.startswith("TPO"):
        return "viettelpost"
    if track.startswith("BEST") or re.match(r"^BX\d+", track):
        return "best"
    if re.match(r"^V\d+", track) or "VTP" in track:
        return "viettelpost"
    return None


def build_tracking_url(
    ref: str | None,
    *,
    provider: str | None = None,
    carrier: str | None = None,
    buucuc: str | None = None,
    backend: str | None = None,
    tracking_code: str | None = None,
) -> str | None:
    """Dựng URL aship. {ref} = mã VĐ / so_noi_bo / provider_code."""
    code = (ref or tracking_code or "").strip()
    if not code:
        return None
    prov = resolve_provider(
        carrier=carrier,
        buucuc=buucuc,
        backend=backend,
        tracking_code=tracking_code or code,
        explicit=provider,
    )
    if not prov:
        # vẫn dựng với provider trống → site báo lỗi; trả template gợi ý
        return ASHIP_TEMPLATE.format(ref=quote(code, safe=""), provider="{provider}")
    qs = urlencode({"provider_code": code, "provider": prov})
    return f"{ASHIP_BASE}?{qs}"


def attach_tracking_urls(row: dict) -> dict:
    """Gắn tracking_url + tracking_ref vào dict đơn.

    Chỉ resolve URL khi có mã VĐ thật hoặc suy ra được provider.
    """
    out = dict(row)
    track = (out.get("tracking_code") or "").strip()
    ref = track or (out.get("so_noi_bo") or out.get("order_key") or out.get("remote_id") or "")
    ref = str(ref).strip() if ref else ""
    prov = resolve_provider(
        carrier=out.get("carrier"),
        buucuc=out.get("buucuc"),
        backend=out.get("backend"),
        tracking_code=track or None,
    )
    out["tracking_ref"] = ref or None
    out["tracking_provider"] = prov
    if track and prov:
        out["tracking_url"] = build_tracking_url(
            track, provider=prov, tracking_code=track, carrier=out.get("carrier"), buucuc=out.get("buucuc"), backend=out.get("backend")
        )
    elif ref and prov:
        out["tracking_url"] = build_tracking_url(
            ref, provider=prov, tracking_code=track or None, carrier=out.get("carrier"), buucuc=out.get("buucuc"), backend=out.get("backend")
        )
    else:
        out["tracking_url"] = None
    return out


def probe_url(url: str, timeout: float = 12.0) -> dict:
    """HEAD/GET nhẹ — chỉ status, không dump body lớn."""
    try:
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"User-Agent": "OMS-pipe-bus/aship-probe"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(400).decode("utf-8", errors="replace")
            return {
                "ok": True,
                "http": getattr(resp, "status", None) or resp.getcode(),
                "final_url": resp.geturl(),
                "snippet": body[:160].replace("\n", " "),
            }
    except urllib.error.HTTPError as e:
        return {"ok": False, "http": e.code, "final_url": url, "snippet": str(e)[:120]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "http": 0, "final_url": url, "snippet": str(e)[:120]}


def load_orders_with_codes(limit: int = 200) -> list[dict]:
    rows: list[dict] = []
    if PIPE_DB.is_file():
        conn = sqlite3.connect(str(PIPE_DB))
        conn.row_factory = sqlite3.Row
        for r in conn.execute(
            """
            SELECT van_tay, so_noi_bo, order_key, tracking_code, carrier, buucuc, backend, kho, status
            FROM orders
            WHERE (tracking_code IS NOT NULL AND tracking_code != '')
               OR buucuc = 'SPX'
            ORDER BY tracking_code DESC
            LIMIT ?
            """,
            (limit,),
        ):
            rows.append(attach_tracking_urls({k: r[k] for k in r.keys()}))
        conn.close()
    return rows


def patch_dang_giao_urls() -> dict:
    """Thêm/cập nhật cột tracking_url trên bảng đang giao nếu có DB."""
    if not DG_DB.is_file():
        return {"ok": False, "error": "missing dang_giao_chi_tiet.db"}
    conn = sqlite3.connect(str(DG_DB))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(don_dang_giao)")}
    for col in ("tracking_ref", "tracking_provider", "tracking_url"):
        if col not in cols:
            conn.execute(f"ALTER TABLE don_dang_giao ADD COLUMN {col} TEXT")
    updated = 0
    for r in conn.execute(
        "SELECT row_id, tracking_code, so_noi_bo, order_key, carrier, buucuc, backend FROM don_dang_giao"
    ):
        row = {
            "tracking_code": r[1],
            "so_noi_bo": r[2],
            "order_key": r[3],
            "carrier": r[4],
            "buucuc": r[5],
            "backend": r[6],
        }
        attached = attach_tracking_urls(row)
        conn.execute(
            """
            UPDATE don_dang_giao
            SET tracking_ref=?, tracking_provider=?, tracking_url=?
            WHERE row_id=?
            """,
            (
                attached.get("tracking_ref"),
                attached.get("tracking_provider"),
                attached.get("tracking_url"),
                r[0],
            ),
        )
        updated += 1
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES ('aship_patched_at', ?)",
        (utc_now(),),
    )
    conn.commit()
    with_url = conn.execute(
        "SELECT COUNT(*) FROM don_dang_giao WHERE tracking_url IS NOT NULL AND tracking_url NOT LIKE '%{provider}%'"
    ).fetchone()[0]
    conn.close()
    return {"ok": True, "updated": updated, "with_resolved_provider": with_url}


def patch_pipe_urls() -> dict:
    if not PIPE_DB.is_file():
        return {"ok": False, "error": "missing pipe db"}
    conn = sqlite3.connect(str(PIPE_DB))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
    for col in ("tracking_ref", "tracking_provider", "tracking_url"):
        if col not in cols:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {col} TEXT")
    updated = 0
    for r in conn.execute(
        "SELECT van_tay, tracking_code, so_noi_bo, order_key, carrier, buucuc, backend FROM orders"
    ):
        attached = attach_tracking_urls(
            {
                "tracking_code": r[1],
                "so_noi_bo": r[2],
                "order_key": r[3],
                "carrier": r[4],
                "buucuc": r[5],
                "backend": r[6],
            }
        )
        conn.execute(
            "UPDATE orders SET tracking_ref=?, tracking_provider=?, tracking_url=? WHERE van_tay=?",
            (
                attached.get("tracking_ref"),
                attached.get("tracking_provider"),
                attached.get("tracking_url"),
                r[0],
            ),
        )
        updated += 1
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES ('aship_patched_at', ?)",
        (utc_now(),),
    )
    conn.commit()
    resolved = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE tracking_provider IS NOT NULL AND tracking_provider != ''"
    ).fetchone()[0]
    conn.close()
    return {"ok": True, "updated": updated, "with_provider": resolved}


def build_report(*, probe: bool = False, probe_limit: int = 5) -> dict:
    from realtime_icon_feedback_mapper import chant, feedback_line

    dg = patch_dang_giao_urls()
    pipe = patch_pipe_urls()
    samples = load_orders_with_codes(limit=40)
    probes = []
    if probe:
        for s in samples[: max(0, probe_limit)]:
            url = s.get("tracking_url")
            if not url or "{provider}" in url:
                continue
            probes.append({"ref": s.get("tracking_ref"), "provider": s.get("tracking_provider"), "url": url, **probe_url(url)})

    icons = ["compass", "network", "hash", "monitor"]
    top_fb = feedback_line(
        icons,
        f"aship provider_code={{ref}} · pipe_urls={pipe.get('with_provider')} · "
        f"dg_patched={dg.get('updated')} · samples={len(samples)}",
    )

    return {
        "ok": True,
        "query": "Wire tracking.aship.app/order?provider_code={ref}&provider=…",
        "checked_at": utc_now(),
        "template": ASHIP_TEMPLATE,
        "provider_map": PROVIDER_MAP,
        "pipe_patch": pipe,
        "dang_giao_patch": dg,
        "samples": [
            {
                "ref": s.get("tracking_ref"),
                "provider": s.get("tracking_provider"),
                "url": s.get("tracking_url"),
                "buucuc": s.get("buucuc"),
                "kho": s.get("kho"),
                "status": s.get("status"),
                "van_tay": s.get("van_tay"),
            }
            for s in samples[:24]
        ],
        "probes": probes,
        "summary": {
            "template": ASHIP_TEMPLATE,
            "samples": len(samples),
            "pipe_with_provider": pipe.get("with_provider"),
            "icon_chant": chant(icons),
            "feedback": top_fb,
        },
        "verdict": top_fb,
        "next_actions": [
            ASHIP_TEMPLATE,
            "python3 scripts/tracking_aship.py --ref SPXVN067431106264 --provider spx",
            "python3 scripts/tracking_aship.py --probe",
            "SQL pipe: SELECT tracking_code, tracking_url FROM orders WHERE tracking_url IS NOT NULL LIMIT 20",
            "SQL DG: SELECT so_noi_bo, tracking_url FROM don_dang_giao WHERE tracking_provider IS NOT NULL LIMIT 20",
        ],
        "safety": {"secrets_only": True, "public_track_only": True, "no_dump_login": True},
    }


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🧭 TRACKING.ASHIP · provider_code={ref}&provider=…")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    L(f"Template: {report.get('template')}")
    L(f"Pipe patch: {report.get('pipe_patch')}")
    L(f"Đang giao patch: {report.get('dang_giao_patch')}")
    L("")
    L("=== URL mẫu ===")
    for s in report.get("samples") or []:
        L(f"· [{s.get('provider')}] {s.get('ref')}")
        L(f"  {s.get('url')}")
        L(f"  {s.get('kho')} / {s.get('buucuc')} · {s.get('status')}")
    if report.get("probes"):
        L("")
        L("=== Probe HTTP ===")
        for p in report["probes"]:
            mark = "✅" if p.get("ok") else "○"
            L(f"{mark} http={p.get('http')} {p.get('ref')} · {p.get('snippet')}")
    L("")
    L("Next:")
    for a in report["next_actions"]:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    text = format_text(report)
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=list)
    paths = {
        "json": REPORTS / "tracking_aship.json",
        "txt": REPORTS / "tracking_aship.txt",
        "rt_json": OUT / "tracking_aship.json",
        "rt_txt": OUT / "tracking_aship.txt",
    }
    paths["json"].write_text(payload, encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    paths["rt_json"].write_text(payload, encoding="utf-8")
    paths["rt_txt"].write_text(text, encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Tracking.aship provider_code URL builder")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ref", help="provider_code / mã VĐ")
    ap.add_argument("--provider", help="spx|ghn|viettelpost|vnpost|…")
    ap.add_argument("--probe", action="store_true", help="Probe vài URL SPX mẫu")
    ap.add_argument("--probe-limit", type=int, default=5)
    args = ap.parse_args()

    if args.ref:
        url = build_tracking_url(args.ref, provider=args.provider, tracking_code=args.ref)
        out = {"ref": args.ref, "provider": args.provider, "url": url}
        if args.probe and url and "{provider}" not in url:
            out["probe"] = probe_url(url)
        print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else f"{url}")
        if not args.json and out.get("probe"):
            print(out["probe"])
        return 0

    report = build_report(probe=args.probe, probe_limit=args.probe_limit)
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=list))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
