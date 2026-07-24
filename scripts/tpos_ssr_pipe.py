#!/usr/bin/env python3
"""Pipe TPOS / Aship SSR — scrape tracking.aship.app HTML → ghi pipe.

Ống:
  kho_buucuc_pipe.db
    → resolve provider (TPO*→ViettelPost, SPX/GHN/J&T/BEST…)
    → GET tracking.aship.app/order?provider_code=&provider=  (SSR, no API key)
    → parse trạng thái / lịch sử
    → patch tracking_url + pipe_events(ssr_*) · optional status/delivered_at

Secrets-only seed từ aship_tpos_ship.env. Không dump-login.
"""

from __future__ import annotations

import argparse
import html as htmlmod
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"

try:
    from tracking_aship import (
        attach_tracking_urls,
        build_tracking_url,
        resolve_provider,
    )
except Exception:  # noqa: BLE001
    attach_tracking_urls = None  # type: ignore
    build_tracking_url = None  # type: ignore
    resolve_provider = None  # type: ignore


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for path in (
        SECRETS / "aship_tpos_ship.env",
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


def resolve_prov(
    *,
    carrier: str | None,
    buucuc: str | None,
    backend: str | None,
    tracking_code: str | None,
    explicit: str | None = None,
) -> str | None:
    track = (tracking_code or "").strip().upper()
    if explicit:
        return explicit.strip().lower()
    # TPOS / Aship Viettel style
    if track.startswith("TPO"):
        return "viettelpost"
    if track.startswith("BEST") or re.match(r"^BX\d+", track):
        return "best"
    if resolve_provider:
        return resolve_provider(
            carrier=carrier,
            buucuc=buucuc,
            backend=backend,
            tracking_code=tracking_code,
        )
    return None


def scrape_ssr(provider_code: str, provider: str, *, timeout: int = 20) -> dict[str, Any]:
    # Aship SSR accepts ViettelPost or viettelpost — try canonical casing from docs
    prov_q = {
        "viettelpost": "ViettelPost",
        "best": "BEST",
        "spx": "spx",
        "ghn": "ghn",
        "jnt": "jnt",
        "vnpost": "vnpost",
    }.get((provider or "").lower(), provider)
    url = "https://tracking.aship.app/order?" + urllib.parse.urlencode(
        {"provider_code": provider_code, "provider": prov_q}
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 OMS-tpos-ssr-pipe",
            "Accept": "text/html",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            http = int(resp.status)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        http = int(e.code)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "http": 0, "url": url, "error": str(e)[:160]}

    texts = [
        htmlmod.unescape(t.strip())
        for t in re.findall(r">([^<]{2,120})<", raw)
        if t.strip() and not re.search(r"[{}]|function|var |https?:|font-awesome|bootstrap", t)
    ]
    order_code = carrier = status = None
    weight = None
    for i, t in enumerate(texts):
        if t == "Mã đơn hàng:" and i + 1 < len(texts):
            order_code = texts[i + 1]
        elif t == "Đối tác giao hàng:" and i + 1 < len(texts):
            carrier = texts[i + 1]
        elif t == "Mã vận đơn:" and i + 1 < len(texts):
            provider_code = texts[i + 1]
        elif t == "Trạng thái:" and i + 1 < len(texts):
            # often next block is sender info — prefer history head
            nxt = texts[i + 1]
            if nxt not in {"Thông tin người gửi", "Họ và tên:"}:
                status = nxt
        elif t.startswith("Khối lượng") and i + 1 < len(texts):
            weight = texts[i + 1]
    hist: list[str] = []
    if "Lịch sử vận đơn" in texts:
        i = texts.index("Lịch sử vận đơn")
        hist = [x for x in texts[i + 1 : i + 16] if x]
    # derive status from history
    status_from_hist = None
    for h in hist:
        if re.search(r"(?i)giao thành công|đã giao|delivered", h):
            status_from_hist = "delivered"
            break
        if re.search(r"(?i)đang giao", h):
            status_from_hist = "shipping"
            break
        if re.search(r"(?i)đang vận chuyển|lấy hàng|nhập kho", h):
            status_from_hist = "in_transit"
            break
    if not status and hist:
        status = hist[0]
    delivered = status_from_hist == "delivered" or bool(
        re.search(r"(?i)giao thành công|đã giao", status or "")
    )
    # find deliver datetime in hist
    delivered_at = None
    for i, h in enumerate(hist):
        if re.search(r"(?i)giao thành công|đã giao", h):
            if i + 1 < len(hist) and re.search(r"Ngày\s+\d", hist[i + 1]):
                delivered_at = hist[i + 1]
            break

    blob = " ".join(texts[:40])
    not_found = bool(
        re.search(r"(?i)không tồn tại|not found|không tìm thấy", blob)
    ) or (http == 200 and not (order_code or carrier or hist))
    ok = http == 200 and bool(order_code or carrier or hist) and not not_found
    return {
        "ok": ok,
        "not_found": not_found,
        "http": http,
        "url": url,
        "provider": prov_q,
        "provider_code": provider_code,
        "order_code": order_code,
        "carrier": carrier,
        "status_raw": status if not not_found else "not_found",
        "status_norm": status_from_hist or ("not_found" if not_found else None),
        "delivered": delivered,
        "delivered_at_text": delivered_at,
        "weight": weight,
        "history_head": hist[:10],
    }


def ensure_pipe_cols(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
    for col, decl in (
        ("ssr_status", "TEXT"),
        ("ssr_scraped_at", "TEXT"),
        ("ssr_order_code", "TEXT"),
        ("ssr_history", "TEXT"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {col} {decl}")


def select_candidates(
    conn: sqlite3.Connection, *, limit: int, providers: set[str] | None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    conn.row_factory = sqlite3.Row
    for r in conn.execute(
        """
        SELECT van_tay, so_noi_bo, tracking_code, carrier, buucuc, backend, kho,
               status, tracking_provider, tracking_url, shop_id, shop_name,
               delivered_at, ssr_scraped_at
        FROM orders
        WHERE tracking_code IS NOT NULL AND TRIM(tracking_code) != ''
          AND (ssr_scraped_at IS NULL OR ssr_scraped_at = '')
        ORDER BY
          CASE WHEN tracking_code LIKE 'TPO%' THEN 0
               WHEN lower(coalesce(tracking_provider,'')) IN ('viettelpost','best','vtp') THEN 1
               WHEN lower(coalesce(tracking_provider,'')) IN ('spx','ghn','jnt') THEN 3
               ELSE 2 END,
          CASE WHEN tracking_url IS NULL OR tracking_url = '' THEN 0 ELSE 1 END
        LIMIT ?
        """,
        (max(limit * 8, 80),),
    ):
        d = dict(r)
        prov = d.get("tracking_provider") or resolve_prov(
            carrier=d.get("carrier"),
            buucuc=d.get("buucuc"),
            backend=d.get("backend"),
            tracking_code=d.get("tracking_code"),
        )
        if not prov:
            continue
        if (
            providers
            and prov.lower() not in providers
            and not str(d.get("tracking_code")).upper().startswith("TPO")
        ):
            continue
        d["tracking_provider"] = prov
        if not d.get("tracking_url") and build_tracking_url:
            d["tracking_url"] = build_tracking_url(
                d["tracking_code"],
                provider=prov,
                tracking_code=d["tracking_code"],
                carrier=d.get("carrier"),
                buucuc=d.get("buucuc"),
                backend=d.get("backend"),
            )
        rows.append(d)
        if len(rows) >= limit:
            break
    return rows


def apply_ssr(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    ssr: dict[str, Any],
    *,
    write_status: bool,
) -> None:
    van = row.get("van_tay")
    code = row.get("tracking_code")
    now = utc_now()
    hist_json = json.dumps(ssr.get("history_head") or [], ensure_ascii=False)
    status_val = ssr.get("status_norm") or ssr.get("status_raw")
    if ssr.get("not_found"):
        status_val = "not_found"
    conn.execute(
        """
        UPDATE orders SET
          tracking_provider = COALESCE(?, tracking_provider),
          tracking_url = COALESCE(?, tracking_url),
          tracking_ref = COALESCE(tracking_code, tracking_ref),
          ssr_status = ?,
          ssr_scraped_at = ?,
          ssr_order_code = ?,
          ssr_history = ?
        WHERE van_tay = ? OR tracking_code = ?
        """,
        (
            ssr.get("provider") or row.get("tracking_provider"),
            ssr.get("url") or row.get("tracking_url"),
            status_val,
            now,
            ssr.get("order_code"),
            hist_json,
            van,
            code,
        ),
    )
    if write_status and ssr.get("delivered") and not ssr.get("not_found"):
        # only set delivered_at if empty
        conn.execute(
            """
            UPDATE orders SET
              status = CASE
                WHEN status IS NULL OR status IN ('','submitted','shipped','shipping')
                THEN 'delivered' ELSE status END,
              delivered_at = COALESCE(delivered_at, ?)
            WHERE (van_tay = ? OR tracking_code = ?)
              AND (delivered_at IS NULL OR delivered_at = '')
            """,
            (ssr.get("delivered_at_text") or now, van, code),
        )
    detail = json.dumps(
        {
            "provider": ssr.get("provider"),
            "provider_code": ssr.get("provider_code"),
            "status_norm": ssr.get("status_norm"),
            "status_raw": ssr.get("status_raw"),
            "order_code": ssr.get("order_code"),
            "delivered": ssr.get("delivered"),
            "not_found": ssr.get("not_found"),
            "url": ssr.get("url"),
            "http": ssr.get("http"),
        },
        ensure_ascii=False,
    )[:500]
    event = "tpos_ssr_not_found" if ssr.get("not_found") else "tpos_ssr_scrape"
    if not ssr.get("ok") and not ssr.get("not_found"):
        event = "tpos_ssr_fail"
    conn.execute(
        """
        INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail)
        VALUES (?, ?, ?, ?, ?)
        """,
        (now, event, van, row.get("so_noi_bo"), detail),
    )


def build_report(
    *,
    limit: int = 40,
    sleep_s: float = 0.15,
    write_status: bool = True,
    seed_codes: list[tuple[str, str]] | None = None,
    providers: list[str] | None = None,
) -> dict[str, Any]:
    if not PIPE_DB.is_file():
        return {"ok": False, "error": f"missing {PIPE_DB}", "checked_at": utc_now()}

    prov_set = {p.lower() for p in (providers or ["viettelpost", "best"])}
    conn = sqlite3.connect(str(PIPE_DB))
    ensure_pipe_cols(conn)
    conn.commit()

    candidates = select_candidates(conn, limit=limit, providers=prov_set)
    # seeds (TPOS samples) — upsert as synthetic if not in DB
    seeds = list(seed_codes or [])
    if not seeds:
        seeds = [("TPO1408375976", "viettelpost")]
    seed_results = []
    results = []
    scraped = 0
    delivered_n = 0
    fail_n = 0
    not_found_n = 0
    ok_n = 0

    for code, prov in seeds:
        ssr = scrape_ssr(code, prov)
        seed_results.append(ssr)
        scraped += 1
        if ssr.get("ok"):
            ok_n += 1
            # try patch matching tracking_code
            row = {"van_tay": None, "so_noi_bo": None, "tracking_code": code, "tracking_provider": prov}
            hit = conn.execute(
                "SELECT van_tay, so_noi_bo, tracking_code, carrier, buucuc, backend, "
                "kho, status, tracking_provider, tracking_url, shop_id, shop_name, delivered_at "
                "FROM orders WHERE tracking_code = ? LIMIT 1",
                (code,),
            ).fetchone()
            if hit:
                cols = [
                    "van_tay",
                    "so_noi_bo",
                    "tracking_code",
                    "carrier",
                    "buucuc",
                    "backend",
                    "kho",
                    "status",
                    "tracking_provider",
                    "tracking_url",
                    "shop_id",
                    "shop_name",
                    "delivered_at",
                ]
                row = dict(zip(cols, hit))
                apply_ssr(conn, row, ssr, write_status=write_status)
            else:
                # event-only seed
                conn.execute(
                    """
                    INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail)
                    VALUES (?, 'tpos_ssr_seed', NULL, ?, ?)
                    """,
                    (
                        utc_now(),
                        code,
                        json.dumps(
                            {
                                "provider": prov,
                                "ssr": {
                                    k: ssr.get(k)
                                    for k in (
                                        "ok",
                                        "order_code",
                                        "carrier",
                                        "status_norm",
                                        "status_raw",
                                        "delivered",
                                        "history_head",
                                        "url",
                                    )
                                },
                            },
                            ensure_ascii=False,
                        )[:500],
                    ),
                )
            if ssr.get("delivered"):
                delivered_n += 1
        elif ssr.get("not_found"):
            not_found_n += 1
            conn.execute(
                """
                INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail)
                VALUES (?, 'tpos_ssr_not_found', NULL, ?, ?)
                """,
                (
                    utc_now(),
                    code,
                    json.dumps({"provider": prov, "url": ssr.get("url")}, ensure_ascii=False)[:500],
                ),
            )
        else:
            fail_n += 1
        time.sleep(sleep_s)

    for row in candidates:
        code = str(row.get("tracking_code") or "")
        prov = str(row.get("tracking_provider") or "")
        if not code or not prov:
            continue
        # skip duplicate seed
        if any(code == s[0] for s in seeds):
            continue
        ssr = scrape_ssr(code, prov)
        scraped += 1
        # always write ssr_* (ok / not_found / fail) so we skip next run
        apply_ssr(conn, row, ssr, write_status=write_status)
        if ssr.get("ok"):
            ok_n += 1
            if ssr.get("delivered"):
                delivered_n += 1
            results.append(
                {
                    "tracking_code": code,
                    "provider": prov,
                    "shop_id": row.get("shop_id"),
                    "status_norm": ssr.get("status_norm"),
                    "status_raw": ssr.get("status_raw"),
                    "order_code": ssr.get("order_code"),
                    "delivered": ssr.get("delivered"),
                    "url": ssr.get("url"),
                }
            )
        elif ssr.get("not_found"):
            not_found_n += 1
            results.append(
                {
                    "tracking_code": code,
                    "provider": prov,
                    "ok": False,
                    "not_found": True,
                    "http": ssr.get("http"),
                }
            )
        else:
            fail_n += 1
            results.append(
                {
                    "tracking_code": code,
                    "provider": prov,
                    "ok": False,
                    "http": ssr.get("http"),
                    "error": ssr.get("error"),
                }
            )
        time.sleep(sleep_s)
        if scraped >= limit + len(seeds):
            break

    conn.commit()
    # stats
    ssr_n = int(
        conn.execute(
            "SELECT COUNT(*) FROM orders WHERE ssr_scraped_at IS NOT NULL AND ssr_scraped_at != ''"
        ).fetchone()[0]
    )
    ev_n = int(
        conn.execute(
            "SELECT COUNT(*) FROM pipe_events WHERE event IN "
            "('tpos_ssr_scrape','tpos_ssr_seed','tpos_ssr_not_found','tpos_ssr_fail')"
        ).fetchone()[0]
    )
    conn.close()

    report: dict[str, Any] = {
        "ok": True,
        "module": "tpos_ssr_pipe",
        "checked_at": utc_now(),
        "policy": "SSR public tracking.aship.app · no API key · owned seed codes ok",
        "atlas": "pipe tracking_code → aship SSR HTML → pipe_events/ssr_* (+ optional delivered)",
        "note": "Aship SSR chỉ có đơn ship qua TPOS (TPO*/ViettelPost/BEST). SPX/GHN/J&T pancake → thường not_found.",
        "stats": {
            "scraped": scraped,
            "candidates": len(candidates),
            "ok_results": ok_n,
            "delivered_hits": delivered_n,
            "not_found": not_found_n,
            "fail": fail_n,
            "orders_with_ssr": ssr_n,
            "pipe_events_ssr": ev_n,
            "providers_default": sorted(prov_set),
        },
        "seeds": seed_results,
        "results_sample": results[:30],
        "verdict": (
            f"🔌 Pipe TPOS SSR: scraped={scraped} · ok={ok_n} · delivered={delivered_n} · "
            f"not_found={not_found_n} · fail={fail_n} · orders_ssr={ssr_n} · events={ev_n}"
        ),
        "next": [
            "python3 scripts/tpos_ssr_pipe.py --limit 100 --notify",
            "python3 scripts/tpos_ssr_pipe.py --code TPO1408375976 --provider viettelpost --notify",
            "python3 scripts/tpos_ssr_pipe.py --providers spx,ghn,jnt,viettelpost,best --limit 20",
            "python3 scripts/aship_tpos_ship_mapper.py --notify",
        ],
    }
    return report


def format_text(report: dict[str, Any]) -> str:
    if not report.get("ok"):
        return f"❌ {report.get('error')}"
    L: list[str] = []
    A = L.append
    st = report.get("stats") or {}
    A("🔌 PIPE TPOS · ASHIP SSR")
    A(f"Lúc: {report.get('checked_at')}")
    A(f"Verdict: {report.get('verdict')}")
    A(f"Atlas: {report.get('atlas')}")
    A(
        f"Stats: scraped={st.get('scraped')} candidates={st.get('candidates')} · "
        f"ok={st.get('ok_results')} delivered={st.get('delivered_hits')} "
        f"not_found={st.get('not_found')} fail={st.get('fail')} · "
        f"orders_ssr={st.get('orders_with_ssr')} events={st.get('pipe_events_ssr')}"
    )
    if report.get("note"):
        A(f"Note: {report.get('note')}")
    A("")
    A("=== Seeds ===")
    for s in report.get("seeds") or []:
        A(
            f"  · ok={s.get('ok')} {s.get('provider_code')} / {s.get('provider')} · "
            f"order={s.get('order_code')} carrier={s.get('carrier')} · "
            f"norm={s.get('status_norm')} raw={s.get('status_raw')} delivered={s.get('delivered')}"
        )
        if s.get("history_head"):
            A(f"      hist: {s.get('history_head')[:4]}")
    A("")
    A("=== Sample scrapes ===")
    for r in (report.get("results_sample") or [])[:20]:
        if r.get("not_found"):
            A(f"  ○ not_found {r.get('tracking_code')} [{r.get('provider')}]")
        elif r.get("ok") is False:
            A(f"  ❌ {r.get('tracking_code')} {r.get('provider')} http={r.get('http')} {r.get('error')}")
        else:
            A(
                f"  · {r.get('tracking_code')} [{r.get('provider')}] "
                f"norm={r.get('status_norm')} order={r.get('order_code')} "
                f"deliv={r.get('delivered')} shop={r.get('shop_id')}"
            )
    A("")
    for n in report.get("next") or []:
        A(f"Next: {n}")
    return "\n".join(L)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "tpos_ssr_pipe.json"
    tp = REPORTS / "tpos_ssr_pipe.txt"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp)}


def notify_telegram(text: str) -> list[int]:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_CHAT_ID") or ""
    if not token or not chat:
        return []
    statuses: list[int] = []
    for i in range(0, min(len(text), 10500), 3500):
        chunk = text[i : i + 3500]
        body = json.dumps({"chat_id": chat, "text": chunk}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            statuses.append(resp.status)
    return statuses


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pipe TPOS / Aship SSR scrape → DB")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--sleep", type=float, default=0.12)
    ap.add_argument("--code", action="append", default=[], help="Seed provider_code (repeatable)")
    ap.add_argument("--provider", default="viettelpost", help="Provider for --code seeds")
    ap.add_argument(
        "--providers",
        default="viettelpost,best",
        help="Comma providers for pipe candidates (default: viettelpost,best)",
    )
    ap.add_argument("--no-status-write", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)

    seeds = [(c, args.provider) for c in (args.code or [])]
    if not seeds:
        seeds = [("TPO1408375976", "viettelpost")]

    provs = [p.strip() for p in (args.providers or "").split(",") if p.strip()]
    report = build_report(
        limit=args.limit,
        sleep_s=args.sleep,
        write_status=not args.no_status_write,
        seed_codes=seeds,
        providers=provs or None,
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
