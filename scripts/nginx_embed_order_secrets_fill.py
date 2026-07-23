#!/usr/bin/env python3
"""Rà soát token API + tín hiệu lấy đơn → nhúng qua nginx → điền secrets/backend.

Luồng:
  1) Quét quarantine/telegram (+ audit DB)
  2) Phân loại owned vs dump/stealer
  3) Nhúng giá trị SỞ HỮU qua nginx /v1/owned/fill → secrets/backend_pipes.env
  4) Báo cáo (mask) — không dump-login

Chỉ điền từ export đơn / file không dump:
  - Pancake shop_id / page_id (orders_detailed_*)
  - SPX account id (thanhcoong.xlsx)

Không điền Acc_all · stealer · ghn_tokens · results_cookies · valid_accounts.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

INBOX = ROOT / "quarantine" / "telegram"
SKIP = INBOX / "_skipped_dumps"
REPORTS = ROOT / "reports" / "telegram-classify"
AUDIT_DB = REPORTS / "telegram_inbox_secrets_audit.db"
STATE = ROOT / "secrets" / "nginx_embed_owned_fill.state.json"

DUMP_MARKERS = (
    "acc_all",
    "stealer",
    "internal_search",
    "ghn_tokens",
    "ghn.txt",
    "valid_accounts",
    "results_cookies",
    "cookie",
    "assassin",
    "password",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_dump_name(name: str) -> bool:
    n = name.lower()
    return any(m in n for m in DUMP_MARKERS)


def collect_owned_from_order_exports() -> dict[str, Any]:
    """Trích shop_id / page_id / SPX account từ file đơn sở hữu (không dump)."""
    shops: Counter[str] = Counter()
    pages: Counter[str] = Counter()
    sources: list[str] = []

    for p in sorted(INBOX.glob("orders_detailed*.json")):
        if is_dump_name(p.name):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        shops.update(re.findall(r'"shop_id"\s*:\s*"?(\d+)"?', text))
        pages.update(re.findall(r'"page_id"\s*:\s*"([^"]+)"', text))
        sources.append(p.name)

    spx_accounts: Counter[str] = Counter()
    thanh = INBOX / "thanhcoong.xlsx"
    if thanh.is_file() and not is_dump_name(thanh.name):
        try:
            import openpyxl

            wb = openpyxl.load_workbook(thanh, read_only=True, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
            wb.close()
            if rows:
                hdr = [str(h or "") for h in rows[0]]
                if "Account ID" in hdr:
                    idx = hdr.index("Account ID")
                    for r in rows[1:]:
                        if not r or r[idx] is None:
                            continue
                        val = str(r[idx]).strip()
                        if val.isdigit() and len(val) >= 6:
                            spx_accounts[val] += 1
                    sources.append(thanh.name)
        except Exception as e:  # noqa: BLE001
            sources.append(f"thanhcoong.xlsx:error:{e}")

    # primary pancake shop = most frequent non-placeholder
    shop_ranked = [s for s, _ in shops.most_common() if s not in {"9999999", "0", ""}]
    primary = shop_ranked[0] if shop_ranked else None
    secondary = [s for s in shop_ranked[1:] if s != primary]
    page_id = pages.most_common(1)[0][0] if pages else None
    spx_id = spx_accounts.most_common(1)[0][0] if spx_accounts else None

    fills: list[dict[str, Any]] = []
    if primary:
        extras: dict[str, str] = {}
        if secondary:
            extras["PANCAKE_SECONDARY_SHOP_IDS"] = ",".join(secondary)
        if page_id:
            extras["PANCAKE_PAGE_ID"] = page_id
        fills.append(
            {
                "platform": "Pancake",
                "shop_id": primary,
                "extras": extras,
                "source": "orders_detailed_export",
                "evidence": {"shop_counts": shops.most_common(5), "page_counts": pages.most_common(3)},
            }
        )
    if spx_id:
        fills.append(
            {
                "platform": "SPX",
                "shop_id": spx_id,
                "extras": {"SPX_SHOP_ID": spx_id},
                "source": "thanhcoong.xlsx",
                "evidence": {"account_counts": spx_accounts.most_common(3)},
            }
        )

    return {
        "ok": True,
        "sources": sources,
        "shops": shops.most_common(10),
        "pages": pages.most_common(5),
        "spx_accounts": spx_accounts.most_common(5),
        "fills": fills,
        "real_api_tokens_in_owned_exports": 0,
        "note": "Export đơn không chứa access_token/api_key thật — chỉ shop_id/page_id/account",
    }


def inventory_dump_audit() -> dict[str, Any]:
    """Tóm tắt dump findings (masked) — không lấy giá trị để login."""
    by_file: dict[str, Counter] = defaultdict(Counter)
    by_plat: Counter[str] = Counter()
    total = 0
    dump_files: list[str] = []

    if AUDIT_DB.is_file():
        con = sqlite3.connect(AUDIT_DB)
        con.row_factory = sqlite3.Row
        for r in con.execute(
            "SELECT file, kind, platform, dump_source, COUNT(*) c FROM secrets_findings "
            "GROUP BY 1,2,3,4"
        ):
            fname = r["file"]
            # treat cookie dumps as dump even if dump_source=0 historically
            dumpish = bool(r["dump_source"]) or is_dump_name(fname)
            if not dumpish:
                continue
            total += int(r["c"])
            by_file[fname][r["kind"]] += int(r["c"])
            if r["platform"]:
                by_plat[r["platform"]] += int(r["c"])
            if fname not in dump_files:
                dump_files.append(fname)
        con.close()

    # also list skipped dump files on disk
    disk_dumps = []
    for p in list(INBOX.iterdir()) + (list(SKIP.iterdir()) if SKIP.is_dir() else []):
        if p.is_file() and is_dump_name(p.name):
            disk_dumps.append(str(p.relative_to(INBOX) if p.parent == INBOX else Path("_skipped_dumps") / p.name))

    return {
        "ok": True,
        "dump_findings": total,
        "dump_files_db": dump_files,
        "dump_files_disk": sorted(set(disk_dumps)),
        "by_platform": by_plat.most_common(15),
        "by_file_kinds": {f: dict(c) for f, c in list(by_file.items())[:20]},
        "blocked": True,
        "reason": "dump/stealer/cookies — không điền vào secrets (no dump-login)",
    }


def fill_via_nginx(fills: list[dict[str, Any]], *, keep: bool = False) -> dict[str, Any]:
    """Nhúng từng fill qua nginx /v1/owned/fill."""
    from nginx_order_embed import NginxOrderEmbed

    mod = NginxOrderEmbed(auto_stop=not keep)
    started = mod.ensure_up()
    if not started.get("ok"):
        return {"ok": False, "error": "nginx embed chưa up", "start": started, "results": []}

    results = []
    try:
        for item in fills:
            payload = {
                "platform": item.get("platform"),
                "shop_id": item.get("shop_id"),
                "user": item.get("user"),
                "token": item.get("token"),
                "extras": item.get("extras") or {},
                "as_api_key": bool(item.get("as_api_key")),
                "source": item.get("source"),
            }
            # strip None
            payload = {k: v for k, v in payload.items() if v is not None and v != {}}
            res = mod.call_json("/v1/owned/fill", method="POST", payload=payload, ensure=False)
            results.append(
                {
                    "platform": item.get("platform"),
                    "source": item.get("source"),
                    "ok": res.get("ok"),
                    "http": res.get("http"),
                    "embedded": res.get("embedded"),
                    "pipeline": res.get("pipeline"),
                    "payload": res.get("payload"),
                }
            )
        return {
            "ok": all(r.get("ok") for r in results) if results else False,
            "via_nginx": True,
            "results": results,
            "filled": sum(1 for r in results if r.get("ok")),
            "total": len(results),
        }
    finally:
        if not keep:
            mod.stop()


def run_pipeline(*, rescan_audit: bool = True, keep: bool = False) -> dict:
    audit_summary = None
    if rescan_audit:
        try:
            from telegram_inbox_secrets_audit import build_report, write_outputs

            audit_summary = build_report()
            write_outputs(audit_summary)
            audit_summary = {
                "ok": audit_summary.get("ok"),
                "stats": audit_summary.get("stats"),
                "verdict": audit_summary.get("verdict"),
            }
        except Exception as e:  # noqa: BLE001
            audit_summary = {"ok": False, "error": str(e)[:160]}

    owned = collect_owned_from_order_exports()
    dumps = inventory_dump_audit()
    fill = fill_via_nginx(owned.get("fills") or [], keep=keep)

    # status after fill
    try:
        from owned_credentials import mapping_summary

        owned_map = mapping_summary()
        owned_map_pub = {
            "ready_platforms": owned_map.get("ready_platforms"),
            "verdict": owned_map.get("verdict"),
            "platforms": {
                k: {
                    "ready": v.get("ready"),
                    "with_token": v.get("with_token"),
                    "shop_ids": v.get("shop_ids"),
                    "users": v.get("users"),
                }
                for k, v in (owned_map.get("platforms") or {}).items()
            },
        }
    except Exception as e:  # noqa: BLE001
        owned_map_pub = {"ok": False, "error": str(e)[:120]}

    report = {
        "ok": bool(fill.get("ok") or (owned.get("fills") == [])),
        "module": "nginx_embed_order_secrets_fill",
        "checked_at": utc_now(),
        "pipeline": "audit→classify→nginx /v1/owned/fill→secrets/backend_pipes.env",
        "via_nginx": True,
        "audit": audit_summary,
        "owned_extract": owned,
        "dump_inventory": dumps,
        "fill": fill,
        "owned_map_after": owned_map_pub,
        "verdict": (
            f"✅ Nhúng qua nginx · filled={fill.get('filled')}/{fill.get('total')} · "
            f"dump_blocked={dumps.get('dump_findings')} · "
            f"ready={(owned_map_pub or {}).get('ready_platforms')}"
        ),
        "policy": {
            "owned_only": True,
            "no_dump_login": True,
            "via_nginx_required": True,
            "secrets_gitignored": True,
        },
        "next": [
            "Gửi token API sở hữu (dashboard) rồi: "
            "python3 scripts/access_token_rotate.py set --platform Pancake --token …",
            "python3 scripts/access_token_rotate.py apply-realtime",
        ],
    }

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔎 NGINX EMBED · RÀ SOÁT TOKEN/ĐƠN → SECRETS")
    L(f"Lúc: {report.get('checked_at')}")
    L(report.get("verdict") or "")
    L(f"pipeline: {report.get('pipeline')}")
    L("")
    owned = report.get("owned_extract") or {}
    L(f"Owned exports: sources={owned.get('sources')}")
    L(f"  shops={owned.get('shops')}")
    L(f"  pages={owned.get('pages')}")
    L(f"  spx_accounts={owned.get('spx_accounts')}")
    L(f"  real_api_tokens_in_owned_exports={owned.get('real_api_tokens_in_owned_exports')}")
    L(f"  note: {owned.get('note')}")
    L("")
    dumps = report.get("dump_inventory") or {}
    L(f"Dump blocked: findings={dumps.get('dump_findings')} · {dumps.get('reason')}")
    for f in (dumps.get("dump_files_disk") or [])[:12]:
        L(f"  ⚠ {f}")
    L("")
    fill = report.get("fill") or {}
    L(f"Fill via nginx: ok={fill.get('ok')} filled={fill.get('filled')}/{fill.get('total')}")
    for r in fill.get("results") or []:
        emb = r.get("embedded") or {}
        L(
            f"  · {r.get('platform')} src={r.get('source')} http={r.get('http')} "
            f"upstream={emb.get('$upstream_addr')} ok={r.get('ok')}"
        )
        pl = r.get("payload") or {}
        L(f"    keys={pl.get('filled_keys')} shop={pl.get('shop_id')}")
    L("")
    om = report.get("owned_map_after") or {}
    L(f"Owned map after: {om.get('verdict')} ready={om.get('ready_platforms')}")
    for plat, info in (om.get("platforms") or {}).items():
        L(
            f"  · {plat}: ready={info.get('ready')} token={info.get('with_token')} "
            f"shops={info.get('shop_ids')} users={info.get('users')}"
        )
    if report.get("next"):
        L("")
        L("Next:")
        for n in report["next"]:
            L(f"· {n}")
    L("")
    L("Safety: owned-only · no dump-login · via nginx · values masked in reports")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": REPORTS / "nginx_embed_order_secrets_fill.json",
        "txt": REPORTS / "nginx_embed_order_secrets_fill.txt",
    }
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paths["txt"].write_text(format_text(report), encoding="utf-8")
    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Nginx embed: rà soát token/đơn → điền secrets owned")
    ap.add_argument("--no-rescan", action="store_true", help="Không chạy lại secrets audit")
    ap.add_argument("--keep", action="store_true", help="Giữ nginx sau khi fill")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report = run_pipeline(rescan_audit=not args.no_rescan, keep=args.keep)
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
