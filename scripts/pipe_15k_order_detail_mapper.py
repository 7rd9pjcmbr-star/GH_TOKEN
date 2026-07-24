#!/usr/bin/env python3
"""Pipe 15k đơn chi tiết — toàn cảnh + export từ kho_buucuc_pipe.db.

Đọc toàn bộ orders (~15k) trong pipe DB →:
  · panorama completeness (track/addr/COD/NS/HĐ/flow)
  · CSV + JSONL chi tiết (SĐT mask)
  · mẫu thẻ đơn + thống kê backend×buucuc×kho×status

Owned-only · read-only · mask phones · reports gitignored.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"
OUT_DIR = REPORTS / "pipe_15k_detail"
STATE_PATH = SECRETS / "pipe_15k_order_detail_mapper.state.json"

EXPORT_COLS = [
    "van_tay",
    "so_noi_bo",
    "order_key",
    "oms_id",
    "backend",
    "buucuc",
    "kho",
    "warehouse_id",
    "warehouse_display",
    "shop_id",
    "shop_name",
    "carrier",
    "tracking_code",
    "status",
    "province",
    "district",
    "ward",
    "address_detail",
    "full_address",
    "postal_code",
    "receiver_name",
    "receiver_phone",
    "phone_class",
    "staff_creator",
    "cod_amount",
    "source",
    "channel",
    "file",
    "flow_path",
    "picked_at",
    "delivered_at",
    "piped_at",
    "created_at",
    "synced_at",
    "event_at",
    "pipe_source",
    "realtime_new",
    "icon_chant",
    "contract_backend",
    "contract_account",
    "contract_partner",
    "detail_score",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for path in (
        SECRETS / "order_session.env",
        SECRETS / "backend_pipes.env",
        SECRETS / "telegram.env",
        ROOT / ".env",
    ):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def mask_phone(ph: str | None) -> str | None:
    if not ph:
        return ph
    s = str(ph).strip()
    if "*" in s:
        return s
    digits = re.sub(r"\D", "", s)
    if len(digits) < 7:
        return s
    return digits[:3] + "***" + digits[-3:]


def filled(v: Any) -> bool:
    return v not in (None, "", "(none)", "(null)", "(csv_no_warehouse)")


def detail_score(row: dict[str, Any]) -> int:
    keys = (
        "tracking_code",
        "receiver_name",
        "full_address",
        "province",
        "kho",
        "buucuc",
        "carrier",
        "status",
    )
    return sum(1 for k in keys if filled(row.get(k)))


def load_contracts(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    by_shop: dict[str, list[dict]] = defaultdict(list)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "contracts" not in tables:
        return by_shop
    for r in conn.execute(
        "SELECT shop_id, backend, buucuc, partner_name, account_name, account_id "
        "FROM contracts"
    ):
        by_shop[str(r[0] or "")].append(
            {
                "backend": r[1],
                "buucuc": r[2],
                "partner_name": r[3],
                "account_name": r[4],
                "account_id": r[5],
            }
        )
    return by_shop


def iter_pipe_orders(
    *,
    backend: str | None = None,
    buucuc: str | None = None,
    shop_id: str | None = None,
    with_tracking: bool | None = None,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not PIPE_DB.is_file():
        return [], {"ok": False, "error": f"missing {PIPE_DB}"}

    conn = sqlite3.connect(str(PIPE_DB))
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()}
    contracts = load_contracts(conn)

    where = ["1=1"]
    params: list[Any] = []
    if backend:
        where.append("backend = ?")
        params.append(backend)
    if buucuc:
        where.append("buucuc = ?")
        params.append(buucuc)
    if shop_id:
        where.append("shop_id = ?")
        params.append(shop_id)
    if with_tracking is True:
        where.append("tracking_code IS NOT NULL AND tracking_code != ''")
    elif with_tracking is False:
        where.append("(tracking_code IS NULL OR tracking_code = '')")

    sql = f"SELECT * FROM orders WHERE {' AND '.join(where)}"
    if limit:
        sql += f" LIMIT {int(limit)}"

    rows: list[dict[str, Any]] = []
    for r in conn.execute(sql, params):
        d = {k: r[k] for k in r.keys()}
        if "receiver_phone" in d:
            d["receiver_phone"] = mask_phone(d.get("receiver_phone"))
        sid = str(d.get("shop_id") or "")
        cons = contracts.get(sid) or []
        d["contracts"] = cons
        if cons:
            d["contract_backend"] = cons[0].get("backend")
            d["contract_account"] = cons[0].get("account_name")
            d["contract_partner"] = cons[0].get("partner_name")
        else:
            d["contract_backend"] = None
            d["contract_account"] = None
            d["contract_partner"] = None
        d["detail_score"] = detail_score(d)
        # ensure export cols exist
        for c in EXPORT_COLS:
            d.setdefault(c, None)
        rows.append(d)

    meta = {
        "ok": True,
        "path": str(PIPE_DB),
        "cols_n": len(cols),
        "cols": sorted(cols),
        "total_in_db": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
        "returned": len(rows),
        "contracts_n": sum(len(v) for v in contracts.values()),
    }
    conn.close()
    return rows, meta


def panorama(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows) or 1
    flags = {
        "with_tracking": sum(1 for r in rows if filled(r.get("tracking_code"))),
        "with_address": sum(
            1 for r in rows if filled(r.get("full_address")) or filled(r.get("address_detail"))
        ),
        "with_receiver": sum(1 for r in rows if filled(r.get("receiver_name"))),
        "with_phone": sum(1 for r in rows if filled(r.get("receiver_phone"))),
        "with_cod": sum(1 for r in rows if filled(r.get("cod_amount"))),
        "with_staff": sum(1 for r in rows if filled(r.get("staff_creator"))),
        "with_flow": sum(1 for r in rows if filled(r.get("flow_path"))),
        "with_contract": sum(1 for r in rows if r.get("contracts")),
        "score_8": sum(1 for r in rows if (r.get("detail_score") or 0) >= 8),
        "score_ge6": sum(1 for r in rows if (r.get("detail_score") or 0) >= 6),
        "score_lt4": sum(1 for r in rows if (r.get("detail_score") or 0) < 4),
    }
    pct = {k: round(100.0 * v / n, 1) for k, v in flags.items()}

    by_backend = Counter(r.get("backend") or "?" for r in rows)
    by_buucuc = Counter(r.get("buucuc") or "?" for r in rows)
    by_kho = Counter(r.get("kho") or "?" for r in rows)
    by_status = Counter(r.get("status") or "?" for r in rows)
    by_shop = Counter(
        f"{r.get('shop_name') or '?'}[{r.get('shop_id') or '?'}]" for r in rows
    )
    by_score = Counter(r.get("detail_score") or 0 for r in rows)
    by_be_buu: Counter[str] = Counter()
    for r in rows:
        by_be_buu[f"{r.get('backend')}|{r.get('buucuc')}"] += 1

    # province top
    by_prov = Counter(r.get("province") or "?" for r in rows if filled(r.get("province")))

    return {
        "orders": len(rows),
        "flags": flags,
        "pct": pct,
        "avg_detail_score": round(
            sum(r.get("detail_score") or 0 for r in rows) / n, 2
        ),
        "by_backend": by_backend.most_common(20),
        "by_buucuc": by_buucuc.most_common(20),
        "by_kho": by_kho.most_common(20),
        "by_status": by_status.most_common(20),
        "by_shop": by_shop.most_common(20),
        "by_score": sorted(by_score.items()),
        "by_backend_buucuc": by_be_buu.most_common(25),
        "by_province": by_prov.most_common(15),
    }


def sample_cards(rows: list[dict[str, Any]], n: int = 8) -> list[dict[str, Any]]:
    """Ưu tiên đơn score cao + có tracking."""
    ranked = sorted(
        rows,
        key=lambda r: (
            -(r.get("detail_score") or 0),
            0 if filled(r.get("tracking_code")) else 1,
            str(r.get("order_key") or ""),
        ),
    )
    out = []
    for r in ranked[:n]:
        out.append(
            {
                "order_key": r.get("order_key"),
                "van_tay": r.get("van_tay"),
                "backend": r.get("backend"),
                "buucuc": r.get("buucuc"),
                "kho": r.get("kho"),
                "shop_id": r.get("shop_id"),
                "shop_name": r.get("shop_name"),
                "status": r.get("status"),
                "tracking_code": r.get("tracking_code"),
                "receiver_name": r.get("receiver_name"),
                "receiver_phone": r.get("receiver_phone"),
                "full_address": (r.get("full_address") or r.get("address_detail") or "")[:160],
                "cod_amount": r.get("cod_amount"),
                "staff_creator": r.get("staff_creator"),
                "contract_account": r.get("contract_account"),
                "flow_path": (r.get("flow_path") or "")[:220],
                "detail_score": r.get("detail_score"),
            }
        )
    return out


def write_exports(rows: list[dict[str, Any]]) -> dict[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "pipe_15k_orders_detail.csv"
    jsonl_path = OUT_DIR / "pipe_15k_orders_detail.jsonl"

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=EXPORT_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            row = {c: r.get(c) for c in EXPORT_COLS}
            # flatten list contracts already done via contract_* fields
            w.writerow(row)

    with jsonl_path.open("w", encoding="utf-8") as f:
        for r in rows:
            slim = {c: r.get(c) for c in EXPORT_COLS}
            # drop heavy icon text in jsonl optional — keep
            f.write(json.dumps(slim, ensure_ascii=False) + "\n")

    return {"csv": str(csv_path), "jsonl": str(jsonl_path)}


def build_report(
    *,
    export: bool = True,
    backend: str | None = None,
    buucuc: str | None = None,
    shop_id: str | None = None,
    with_tracking: bool | None = None,
    limit: int | None = None,
    sample_n: int = 8,
) -> dict[str, Any]:
    rows, meta = iter_pipe_orders(
        backend=backend,
        buucuc=buucuc,
        shop_id=shop_id,
        with_tracking=with_tracking,
        limit=limit,
    )
    pan = panorama(rows) if rows else {"orders": 0, "flags": {}, "pct": {}}
    samples = sample_cards(rows, n=sample_n) if rows else []
    exports: dict[str, str] = {}
    if export and rows:
        exports = write_exports(rows)

    n = pan.get("orders") or 0
    pct = pan.get("pct") or {}
    report: dict[str, Any] = {
        "ok": bool(meta.get("ok")) and n > 0,
        "module": "pipe_15k_order_detail_mapper",
        "checked_at": utc_now(),
        "policy": "read-only pipe DB · mask SĐT · full export gitignored under reports/",
        "atlas": (
            "kho_buucuc_pipe.db (~15k) → panorama completeness → "
            "CSV/JSONL chi tiết + mẫu thẻ"
        ),
        "db": meta,
        "filters": {
            "backend": backend,
            "buucuc": buucuc,
            "shop_id": shop_id,
            "with_tracking": with_tracking,
            "limit": limit,
        },
        "panorama": pan,
        "samples": samples,
        "exports": exports,
        # không nhét 15k vào JSON report — chỉ meta + samples
        "verdict": (
            f"✅ Pipe {n} đơn chi tiết · score≈{pan.get('avg_detail_score', 0)}/8 · "
            f"track={pct.get('with_tracking', 0)}% · addr={pct.get('with_address', 0)}% · "
            f"COD={pct.get('with_cod', 0)}% · HĐ={pct.get('with_contract', 0)}% · "
            f"score8={pct.get('score_8', 0)}%"
        ),
        "next": [
            f"CSV: {exports.get('csv') or OUT_DIR / 'pipe_15k_orders_detail.csv'}",
            f"JSONL: {exports.get('jsonl') or OUT_DIR / 'pipe_15k_orders_detail.jsonl'}",
            "python3 scripts/pipe_15k_order_detail_mapper.py --backend SPX-local",
            "Panel: 📦 Pipe·15k CT",
        ],
    }
    return report


def format_text(report: dict[str, Any]) -> str:
    pan = report.get("panorama") or {}
    pct = pan.get("pct") or {}
    flags = pan.get("flags") or {}
    lines = [
        "📦 Pipe 15k · đơn hàng chi tiết",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
        f"Atlas: {report.get('atlas')}",
        "",
        f"DB: {(report.get('db') or {}).get('path')} · "
        f"total_in_db={(report.get('db') or {}).get('total_in_db')} · "
        f"returned={pan.get('orders')}",
        "",
        "=== Completeness ===",
        f"  score TB: {pan.get('avg_detail_score')}/8",
        f"  tracking: {flags.get('with_tracking')} ({pct.get('with_tracking')}%)",
        f"  địa chỉ:  {flags.get('with_address')} ({pct.get('with_address')}%)",
        f"  người nhận: {flags.get('with_receiver')} ({pct.get('with_receiver')}%)",
        f"  SĐT: {flags.get('with_phone')} ({pct.get('with_phone')}%)",
        f"  COD: {flags.get('with_cod')} ({pct.get('with_cod')}%)",
        f"  NS: {flags.get('with_staff')} ({pct.get('with_staff')}%)",
        f"  flow: {flags.get('with_flow')} ({pct.get('with_flow')}%)",
        f"  HĐ gắn shop: {flags.get('with_contract')} ({pct.get('with_contract')}%)",
        f"  score=8: {flags.get('score_8')} · ≥6: {flags.get('score_ge6')} · <4: {flags.get('score_lt4')}",
        "",
        "=== Backend ===",
    ]
    for be, c in pan.get("by_backend") or []:
        lines.append(f"  · {be}: {c}")
    lines.append("")
    lines.append("=== Backend × Bưu cục (top) ===")
    for k, c in (pan.get("by_backend_buucuc") or [])[:12]:
        lines.append(f"  · {k}: {c}")
    lines.append("")
    lines.append("=== Kho (top) ===")
    for k, c in (pan.get("by_kho") or [])[:10]:
        lines.append(f"  · {k}: {c}")
    lines.append("")
    lines.append("=== Status (top) ===")
    for k, c in (pan.get("by_status") or [])[:10]:
        lines.append(f"  · {k}: {c}")
    lines.append("")
    lines.append("=== Shop (top) ===")
    for k, c in (pan.get("by_shop") or [])[:8]:
        lines.append(f"  · {k}: {c}")
    lines.append("")
    lines.append("=== Mẫu đơn chi tiết ===")
    for i, s in enumerate(report.get("samples") or [], 1):
        lines.append(
            f"  #{i} [{s.get('detail_score')}/8] {s.get('order_key')} · "
            f"{s.get('backend')}/{s.get('buucuc')} · VĐ={s.get('tracking_code') or '∅'}"
        )
        lines.append(
            f"      kho={s.get('kho')} · shop={s.get('shop_name')} · "
            f"status={s.get('status')} · COD={s.get('cod_amount') or '∅'}"
        )
        lines.append(
            f"      nhận={s.get('receiver_name')} · SĐT={s.get('receiver_phone')} · "
            f"addr={(s.get('full_address') or '')[:80]}"
        )
        if s.get("contract_account"):
            lines.append(f"      HĐ={s.get('contract_account')}")
    lines.append("")
    ex = report.get("exports") or {}
    if ex:
        lines.append("=== Export ===")
        lines.append(f"  CSV:   {ex.get('csv')}")
        lines.append(f"  JSONL: {ex.get('jsonl')}")
    lines.append("")
    for n in report.get("next") or []:
        lines.append(f"Next: {n}")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    SECRETS.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "pipe_15k_order_detail_mapper.json"
    tp = REPORTS / "pipe_15k_order_detail_mapper.txt"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = format_text(report)
    tp.write_text(text + "\n", encoding="utf-8")
    # copy text into out dir too
    (OUT_DIR / "panorama.txt").write_text(text + "\n", encoding="utf-8")
    STATE_PATH.write_text(
        json.dumps(
            {
                "updated_at": report.get("checked_at"),
                "verdict": report.get("verdict"),
                "orders": (report.get("panorama") or {}).get("orders"),
                "exports": report.get("exports"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    out = {"json": str(jp), "txt": str(tp)}
    out.update(report.get("exports") or {})
    return out


def notify_telegram(text: str) -> int | None:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_CHAT_ID") or ""
    if not token or not chat:
        return None
    body = json.dumps({"chat_id": chat, "text": text[:3500]}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pipe 15k đơn chi tiết — panorama + export")
    ap.add_argument("--backend", default=None)
    ap.add_argument("--buucuc", default=None)
    ap.add_argument("--shop-id", default=None)
    ap.add_argument("--with-tracking", action="store_true")
    ap.add_argument("--without-tracking", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="giới hạn (mặc định: tất cả)")
    ap.add_argument("--no-export", action="store_true")
    ap.add_argument("--sample", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)

    wt: bool | None = None
    if args.with_tracking:
        wt = True
    elif args.without_tracking:
        wt = False

    report = build_report(
        export=not args.no_export,
        backend=args.backend,
        buucuc=args.buucuc,
        shop_id=args.shop_id,
        with_tracking=wt,
        limit=args.limit,
        sample_n=args.sample,
    )
    paths = write_outputs(report)
    text = format_text(report)
    if args.notify:
        try:
            report["telegram"] = notify_telegram(text)
            write_outputs(report)
        except Exception as e:  # noqa: BLE001
            report["telegram_error"] = str(e)[:160]
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else text)
    print(f"\nWrote: {paths.get('txt')}")
    if paths.get("csv"):
        print(f"CSV:   {paths['csv']}")
        print(f"JSONL: {paths['jsonl']}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
