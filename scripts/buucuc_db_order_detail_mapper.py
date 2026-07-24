#!/usr/bin/env python3
"""Mapper DB bưu cục → xem đơn hàng chi tiết.

Đọc SQLite:
  · reports/telegram-classify/buucuc_backend.db
  · reports/telegram-classify/kho_buucuc_pipe.db (ưu tiên field giàu hơn)

Xuất thẻ đơn chi tiết: kho · bưu cục · backend · HĐ · mã VĐ · nhận · địa chỉ · COD · NS · flow.

Owned-only · read-only SQL · mask SĐT dài.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
BUUCUC_DB = REPORTS / "buucuc_backend.db"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"
STATE_PATH = SECRETS / "buucuc_db_order_detail_mapper.state.json"

# Cột chi tiết — pipe thường đầy đủ hơn backend
DETAIL_COLS = (
    "order_key",
    "oms_id",
    "so_noi_bo",
    "van_tay",
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
    "customer_phone",
    "phone_class",
    "staff_creator",
    "staff_account",
    "staff_seller",
    "staff_care",
    "cod_amount",
    "source",
    "channel",
    "platform",
    "file",
    "flow_path",
    "picked_at",
    "delivered_at",
    "piped_at",
    "created_at",
    "synced_at",
    "event_at",
    "icon_chant",
    "icon_feedback",
)


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
    digits = re.sub(r"\D", "", s)
    if len(digits) < 7:
        return s
    if "*" in s:
        return s
    return digits[:3] + "***" + digits[-3:]


def table_cols(conn: sqlite3.Connection, table: str = "orders") -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.OperationalError:
        return set()


def open_db(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def select_sql(cols_avail: set[str], *, where: str, order: str, limit: int) -> str:
    cols = [c for c in DETAIL_COLS if c in cols_avail]
    if not cols:
        cols = ["order_key", "backend", "buucuc", "status"]
    return (
        f"SELECT {', '.join(cols)} FROM orders "
        f"WHERE {where} ORDER BY {order} LIMIT {int(limit)}"
    )


def row_to_detail(row: sqlite3.Row | dict, *, db: str) -> dict[str, Any]:
    d = dict(row) if not isinstance(row, dict) else dict(row)
    # mask phones
    for k in ("receiver_phone", "customer_phone"):
        if d.get(k):
            d[k] = mask_phone(str(d[k]))
    d["_db"] = db
    # completeness score
    rich = (
        "tracking_code",
        "receiver_name",
        "full_address",
        "province",
        "kho",
        "buucuc",
        "carrier",
        "status",
    )
    hit = sum(1 for k in rich if (d.get(k) not in (None, "", "(none)", "(null)")))
    d["_detail_score"] = hit
    d["_detail_max"] = len(rich)
    return d


def fetch_orders(
    *,
    backend: str | None = None,
    buucuc: str | None = None,
    shop_id: str | None = None,
    kho: str | None = None,
    q: str | None = None,
    tracking: str | None = None,
    status: str | None = None,
    with_tracking: bool = False,
    limit: int = 25,
    prefer_pipe: bool = True,
) -> dict[str, Any]:
    """Lấy đơn chi tiết; ưu tiên pipe DB nếu có field giàu hơn."""
    sources: list[tuple[str, Path]] = []
    if prefer_pipe and PIPE_DB.is_file():
        sources.append(("pipe", PIPE_DB))
    if BUUCUC_DB.is_file():
        sources.append(("buucuc", BUUCUC_DB))
    if not sources:
        return {"ok": False, "error": "không có buucuc_backend.db / kho_buucuc_pipe.db", "orders": []}

    used = None
    orders: list[dict[str, Any]] = []
    stats_src: dict[str, Any] = {}
    for label, path in sources:
        conn = open_db(path)
        if not conn:
            continue
        cols = table_cols(conn)
        if "orders" not in {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }:
            conn.close()
            continue

        clauses: list[str] = ["1=1"]
        params: list[Any] = []
        if backend and "backend" in cols:
            clauses.append("backend = ?")
            params.append(backend)
        if buucuc and "buucuc" in cols:
            clauses.append("buucuc = ?")
            params.append(buucuc)
        if shop_id and "shop_id" in cols:
            clauses.append("(shop_id = ? OR shop_id LIKE ?)")
            params.extend([shop_id, f"%{shop_id}%"])
        if kho and "kho" in cols:
            clauses.append("kho LIKE ?")
            params.append(f"%{kho}%")
        if tracking and "tracking_code" in cols:
            clauses.append("tracking_code LIKE ?")
            params.append(f"%{tracking}%")
        if status and "status" in cols:
            clauses.append("status LIKE ?")
            params.append(f"%{status}%")
        if with_tracking and "tracking_code" in cols:
            clauses.append("tracking_code IS NOT NULL AND tracking_code != ''")
        if q:
            q_parts: list[str] = []
            like = f"%{q}%"
            for col in (
                "order_key",
                "tracking_code",
                "so_noi_bo",
                "van_tay",
                "receiver_name",
                "shop_name",
            ):
                if col in cols:
                    q_parts.append(f"COALESCE({col},'') LIKE ?")
                    params.append(like)
            if q_parts:
                clauses.append("(" + " OR ".join(q_parts) + ")")

        where = " AND ".join(clauses)
        ord_bits: list[str] = []
        if "tracking_code" in cols:
            ord_bits.append(
                "CASE WHEN tracking_code IS NOT NULL AND tracking_code != '' THEN 0 ELSE 1 END"
            )
        if "full_address" in cols:
            ord_bits.append(
                "CASE WHEN full_address IS NOT NULL AND full_address != '' THEN 0 ELSE 1 END"
            )
        if "order_key" in cols:
            ord_bits.append("order_key DESC")
        ord_sql = ", ".join(ord_bits) if ord_bits else "rowid DESC"

        sql = select_sql(cols, where=where, order=ord_sql, limit=limit)
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            conn.close()
            stats_src[label] = {"error": str(e)[:120]}
            continue
        total = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        conn.close()
        orders = [row_to_detail(r, db=label) for r in rows]
        used = {"db": label, "path": str(path), "total_orders": total, "returned": len(orders)}
        stats_src[label] = used
        # nếu đã có đơn giàu (score>=4) thì dừng; không thì thử DB còn lại
        if orders and max(o.get("_detail_score") or 0 for o in orders) >= 4:
            break
        if orders:
            break

    # gắn HĐ theo shop_id từ buucuc DB
    contract_by_shop: dict[str, list[dict]] = {}
    cconn = open_db(BUUCUC_DB)
    if cconn and "contracts" in {
        r[0] for r in cconn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }:
        for r in cconn.execute(
            "SELECT shop_id, backend, buucuc, partner_name, account_name, account_id "
            "FROM contracts"
        ):
            sid = str(r["shop_id"] or "")
            contract_by_shop.setdefault(sid, []).append(dict(r))
        cconn.close()
    for o in orders:
        sid = str(o.get("shop_id") or "")
        o["contracts"] = contract_by_shop.get(sid, [])

    return {
        "ok": True,
        "source": used,
        "sources_tried": stats_src,
        "filters": {
            "backend": backend,
            "buucuc": buucuc,
            "shop_id": shop_id,
            "kho": kho,
            "q": q,
            "tracking": tracking,
            "status": status,
            "with_tracking": with_tracking,
            "limit": limit,
        },
        "orders": orders,
    }


def db_overview() -> dict[str, Any]:
    out: dict[str, Any] = {"dbs": {}}
    for label, path in (("buucuc", BUUCUC_DB), ("pipe", PIPE_DB)):
        conn = open_db(path)
        if not conn:
            out["dbs"][label] = {"exists": False, "path": str(path)}
            continue
        info: dict[str, Any] = {"exists": True, "path": str(path)}
        try:
            info["orders"] = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            info["by_backend"] = [
                {"backend": r[0], "n": r[1]}
                for r in conn.execute(
                    "SELECT backend, COUNT(*) FROM orders GROUP BY backend ORDER BY 2 DESC LIMIT 12"
                )
            ]
            info["by_buucuc"] = [
                {"buucuc": r[0], "n": r[1]}
                for r in conn.execute(
                    "SELECT buucuc, COUNT(*) FROM orders GROUP BY buucuc ORDER BY 2 DESC LIMIT 12"
                )
            ]
            info["with_tracking"] = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE tracking_code IS NOT NULL AND tracking_code != ''"
            ).fetchone()[0]
            info["with_address"] = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE full_address IS NOT NULL AND full_address != ''"
            ).fetchone()[0]
            cols = table_cols(conn)
            info["detail_cols_present"] = [c for c in DETAIL_COLS if c in cols]
            info["detail_cols_missing"] = [c for c in DETAIL_COLS if c not in cols]
        except sqlite3.OperationalError as e:
            info["error"] = str(e)[:160]
        if "contracts" in {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }:
            info["contracts"] = conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
        conn.close()
        out["dbs"][label] = info
    return out


def format_order_card(o: dict[str, Any], idx: int) -> list[str]:
    lines = [
        f"── Đơn #{idx} · score {o.get('_detail_score')}/{o.get('_detail_max')} · db={o.get('_db')} ──",
        f"🔑 key: {o.get('order_key') or '∅'}",
    ]
    if o.get("so_noi_bo") and o.get("so_noi_bo") != o.get("order_key"):
        lines.append(f"📎 số NB: {o.get('so_noi_bo')}")
    if o.get("van_tay"):
        lines.append(f"🧬 van_tay: {o.get('van_tay')}")
    lines.append(
        f"🏛 backend={o.get('backend') or '?'} · buucuc={o.get('buucuc') or '?'} · "
        f"carrier={o.get('carrier') or '∅'}"
    )
    lines.append(f"🏬 kho: {o.get('kho') or o.get('warehouse_display') or '∅'}")
    if o.get("warehouse_id"):
        lines.append(f"   warehouse_id={o.get('warehouse_id')}")
    lines.append(
        f"🛒 shop: {o.get('shop_name') or '∅'} [{o.get('shop_id') or '?'}]"
    )
    lines.append(f"📦 status: {o.get('status') or '∅'}")
    lines.append(f"🚚 mã VĐ: {o.get('tracking_code') or '∅'}")
    recv = o.get("receiver_name") or "∅"
    phone = o.get("receiver_phone") or o.get("customer_phone") or "∅"
    lines.append(f"👤 nhận: {recv} · SĐT={phone} · class={o.get('phone_class') or '?'}")
    geo = " / ".join(
        str(x)
        for x in (o.get("address_detail"), o.get("ward"), o.get("district"), o.get("province"))
        if x
    ) or o.get("full_address") or "∅"
    lines.append(f"📍 địa chỉ: {geo}")
    if o.get("cod_amount") not in (None, ""):
        lines.append(f"💰 COD: {o.get('cod_amount')}")
    staff = " · ".join(
        f"{k}={o.get(k)}"
        for k in ("staff_creator", "staff_account", "staff_seller", "staff_care")
        if o.get(k)
    )
    if staff:
        lines.append(f"👷 NS: {staff}")
    if o.get("contracts"):
        for c in o["contracts"][:3]:
            lines.append(
                f"📜 HĐ: {c.get('partner_name') or c.get('backend')} · "
                f"{c.get('account_name')} (shop {c.get('shop_id')})"
            )
    if o.get("flow_path"):
        lines.append(f"🌊 flow: {o.get('flow_path')}")
    meta = " · ".join(
        f"{k}={o.get(k)}"
        for k in ("source", "channel", "platform", "file")
        if o.get(k)
    )
    if meta:
        lines.append(f"🗂 {meta}")
    times = " · ".join(
        f"{k}={o.get(k)}"
        for k in ("picked_at", "delivered_at", "piped_at", "created_at", "synced_at")
        if o.get(k)
    )
    if times:
        lines.append(f"⏱ {times}")
    if o.get("icon_chant") or o.get("icon_feedback"):
        lines.append(f"✨ {o.get('icon_chant') or ''} {o.get('icon_feedback') or ''}".strip())
    return lines


def build_report(
    *,
    backend: str | None = None,
    buucuc: str | None = None,
    shop_id: str | None = None,
    kho: str | None = None,
    q: str | None = None,
    tracking: str | None = None,
    status: str | None = None,
    with_tracking: bool = True,
    limit: int = 15,
    prefer_pipe: bool = True,
) -> dict[str, Any]:
    overview = db_overview()
    fetched = fetch_orders(
        backend=backend,
        buucuc=buucuc,
        shop_id=shop_id,
        kho=kho,
        q=q,
        tracking=tracking,
        status=status,
        with_tracking=with_tracking,
        limit=limit,
        prefer_pipe=prefer_pipe,
    )
    orders = fetched.get("orders") or []
    by_be = Counter(o.get("backend") or "?" for o in orders)
    by_buu = Counter(o.get("buucuc") or "?" for o in orders)
    avg_score = (
        round(sum(o.get("_detail_score") or 0 for o in orders) / len(orders), 2)
        if orders
        else 0
    )

    # mặc định: nếu không filter mà rỗng vì with_tracking — thử lại không bắt tracking
    fallback = None
    if not orders and with_tracking and not any([q, tracking, backend, buucuc, shop_id]):
        fallback = fetch_orders(
            with_tracking=False, limit=limit, prefer_pipe=prefer_pipe
        )
        orders = fallback.get("orders") or []
        fetched = fallback
        by_be = Counter(o.get("backend") or "?" for o in orders)
        by_buu = Counter(o.get("buucuc") or "?" for o in orders)
        avg_score = (
            round(sum(o.get("_detail_score") or 0 for o in orders) / len(orders), 2)
            if orders
            else 0
        )

    pipe_n = (overview.get("dbs") or {}).get("pipe", {}).get("orders")
    buu_n = (overview.get("dbs") or {}).get("buucuc", {}).get("orders")
    src = (fetched.get("source") or {}).get("db") or "?"

    report: dict[str, Any] = {
        "ok": bool(fetched.get("ok")),
        "module": "buucuc_db_order_detail_mapper",
        "checked_at": utc_now(),
        "policy": "read-only SQLite · mask SĐT · no dump-login",
        "atlas": (
            "buucuc_backend.db / kho_buucuc_pipe.db → filter → thẻ đơn chi tiết "
            "(kho·BC·HĐ·VĐ·nhận·địa chỉ·COD·NS)"
        ),
        "overview": overview,
        "fetch": {
            k: v
            for k, v in fetched.items()
            if k != "orders"
        },
        "orders": orders,
        "stats": {
            "returned": len(orders),
            "avg_detail_score": avg_score,
            "by_backend": dict(by_be),
            "by_buucuc": dict(by_buu),
            "with_tracking_in_result": sum(
                1 for o in orders if o.get("tracking_code")
            ),
            "with_address_in_result": sum(
                1 for o in orders if o.get("full_address") or o.get("address_detail")
            ),
        },
        "verdict": (
            f"✅ DB BC xem đơn CT: {len(orders)} thẻ · score≈{avg_score}/8 · "
            f"src={src} · pipe={pipe_n} · buucuc_db={buu_n}"
        ),
        "next": [
            "python3 scripts/buucuc_db_order_detail_mapper.py --tracking SPX",
            "python3 scripts/buucuc_db_order_detail_mapper.py --backend SPX-local --limit 10",
            "python3 scripts/buucuc_db_order_detail_mapper.py --q 'ASUNMEE' --no-require-tracking",
            "Panel: 📋 Đơn CT·DB BC",
        ],
    }
    return report


def format_text(report: dict[str, Any], *, max_cards: int = 12) -> str:
    lines = [
        "📋 Mapper DB bưu cục · đơn hàng chi tiết",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
        f"Atlas: {report.get('atlas')}",
        "",
        "=== DB overview ===",
    ]
    for label, info in ((report.get("overview") or {}).get("dbs") or {}).items():
        if not info.get("exists"):
            lines.append(f"  · {label}: ❌ missing")
            continue
        lines.append(
            f"  · {label}: orders={info.get('orders')} · "
            f"track={info.get('with_tracking')} · addr={info.get('with_address')} · "
            f"contracts={info.get('contracts', '—')}"
        )
        for b in (info.get("by_backend") or [])[:5]:
            lines.append(f"      backend {b.get('backend')}: {b.get('n')}")
        miss = info.get("detail_cols_missing") or []
        if miss:
            lines.append(f"      thiếu cột: {', '.join(miss[:8])}")

    filt = (report.get("fetch") or {}).get("filters") or {}
    lines.append("")
    lines.append(
        "=== Filter === "
        + " · ".join(f"{k}={v}" for k, v in filt.items() if v not in (None, False, ""))
    )
    st = report.get("stats") or {}
    lines.append(
        f"=== Kết quả: {st.get('returned')} đơn · "
        f"track={st.get('with_tracking_in_result')} · "
        f"addr={st.get('with_address_in_result')} · "
        f"avg_score={st.get('avg_detail_score')}"
    )
    lines.append("")
    for i, o in enumerate((report.get("orders") or [])[:max_cards], 1):
        lines.extend(format_order_card(o, i))
        lines.append("")
    if (report.get("orders") or [])[max_cards:]:
        lines.append(f"… +{len(report['orders']) - max_cards} đơn nữa (xem JSON)")
    lines.append("")
    for n in report.get("next") or []:
        lines.append(f"Next: {n}")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    SECRETS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "buucuc_db_order_detail_mapper.json"
    tp = REPORTS / "buucuc_db_order_detail_mapper.txt"
    # JSON: giữ đủ orders nhưng có thể lớn — OK (gitignore reports)
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = format_text(report)
    tp.write_text(text + "\n", encoding="utf-8")
    STATE_PATH.write_text(
        json.dumps(
            {
                "updated_at": report.get("checked_at"),
                "verdict": report.get("verdict"),
                "returned": (report.get("stats") or {}).get("returned"),
                "source": ((report.get("fetch") or {}).get("source") or {}).get("db"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"json": str(jp), "txt": str(tp)}


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
    ap = argparse.ArgumentParser(description="Mapper DB bưu cục · xem đơn hàng chi tiết")
    ap.add_argument("--backend", default=None)
    ap.add_argument("--buucuc", default=None)
    ap.add_argument("--shop-id", default=None)
    ap.add_argument("--kho", default=None)
    ap.add_argument("--q", default=None, help="tìm order_key/VĐ/tên/shop/van_tay")
    ap.add_argument("--tracking", default=None)
    ap.add_argument("--status", default=None)
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument(
        "--no-require-tracking",
        action="store_true",
        help="không bắt buộc có mã VĐ",
    )
    ap.add_argument("--prefer-buucuc-db", action="store_true", help="ưu tiên buucuc_backend.db")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)

    report = build_report(
        backend=args.backend,
        buucuc=args.buucuc,
        shop_id=args.shop_id,
        kho=args.kho,
        q=args.q,
        tracking=args.tracking,
        status=args.status,
        with_tracking=not args.no_require_tracking,
        limit=args.limit,
        prefer_pipe=not args.prefer_buucuc_db,
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
    print(f"\nWrote: {paths['txt']}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
