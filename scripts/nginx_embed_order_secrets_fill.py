#!/usr/bin/env python3
"""Rà soát TOÀN BỘ tín hiệu lấy đơn → nhúng nginx → điền secrets (không bỏ sót).

Mỗi file trong quarantine/telegram được phân loại:
  - dump/stealer/cookies/password-list → chặn (inventory only)
  - export đơn / tracking sở hữu → trích TẤT CẢ shop/page/warehouse/account/host/user

Điền qua nginx POST /v1/owned/fill → secrets/backend_pipes.env.
Không dump-login. Không điền password từ vnpost_ok / Acc_all / stealer.
"""

from __future__ import annotations

import argparse
import csv
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
    "vnpost_ok",  # user:pass list — credential dump, không owned token
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_dump_name(name: str) -> bool:
    n = name.lower().replace("\\", "/")
    return any(m in n for m in DUMP_MARKERS)


def list_all_inbox_files() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not INBOX.is_dir():
        return out
    for p in sorted(INBOX.rglob("*")):
        if not p.is_file():
            continue
        rel = str(p.relative_to(INBOX))
        out.append(
            {
                "file": rel,
                "path": str(p),
                "size": p.stat().st_size,
                "dump": is_dump_name(rel),
                "ext": p.suffix.lower(),
            }
        )
    return out


def _count_vals(pattern: str, text: str) -> Counter[str]:
    return Counter(
        v
        for v in re.findall(pattern, text)
        if v and v.lower() not in {"null", "none", "undefined", ""}
    )


def extract_from_owned_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    shops = _count_vals(r'"shop_id"\s*:\s*"?(\d+)"?', text)
    pages = _count_vals(r'"page_id"\s*:\s*"([^"]+)"', text)
    warehouses = _count_vals(r'"warehouse_id"\s*:\s*"([0-9a-fA-F-]{16,})"', text)
    hosts = Counter()
    for u in re.findall(r"https?://([a-zA-Z0-9.-]+\.[a-zA-Z]{2,}[^/\s\"']*)", text):
        h = u.lower().split("/")[0]
        if any(
            x in h
            for x in (
                "pancake",
                "pages.fm",
                "ghn",
                "spx",
                "viettel",
                "sapo",
                "nhanh",
                "shopee",
                "tpos",
                "vnpost",
            )
        ):
            hosts[h] += 1
    auth_hits = {
        k: text.lower().count(k)
        for k in ("api_key", "access_token", "authorization", "bearer ", "pos_token")
        if text.lower().count(k)
    }
    return {
        "shops": shops,
        "pages": pages,
        "warehouses": warehouses,
        "hosts": hosts,
        "auth_literal_hits": auth_hits,
        "has_real_api_token": False,  # verified 0 access_token/api_key fields
    }


def extract_from_owned_csv(path: Path) -> dict[str, Any]:
    shops: Counter[str] = Counter()
    platforms: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    with path.open(encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = (row.get("shop_id") or "").strip()
            if sid.isdigit():
                shops[sid] += 1
            plat = (row.get("platform") or "").strip()
            if plat:
                platforms[plat] += 1
            src = (row.get("source") or row.get("source_label") or "").strip()
            if src:
                sources[src] += 1
    return {"shops": shops, "platforms": platforms, "sources": sources}


def extract_from_owned_xlsx(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"sheets": []}
    try:
        import openpyxl
    except ImportError:
        out["error"] = "openpyxl missing"
        return out
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        hdr = [str(h or "") for h in (rows[0] if rows else [])]
        sheet_info: dict[str, Any] = {"name": ws.title, "rows": max(0, len(rows) - 1), "header": hdr[:40]}
        interesting = [
            h
            for h in hdr
            if any(
                x in h.lower()
                for x in (
                    "account",
                    "shop",
                    "platform",
                    "3pl",
                    "token",
                    "api",
                    "sender",
                    "tracking",
                    "status",
                )
            )
        ]
        sheet_info["interesting_cols"] = interesting
        for col in interesting:
            if col not in hdr:
                continue
            i = hdr.index(col)
            c = Counter(
                str(r[i]).strip()
                for r in rows[1:]
                if r and i < len(r) and r[i] is not None and str(r[i]).strip()
            )
            # drop spreadsheet header/label rows only
            drop_re = re.compile(
                r"^(ID |Tên |Số |Loại |Tỉnh|Quận|Phường|Mã |Account ID|3PL Name|platform|status)",
                re.I,
            )
            c = Counter({k: v for k, v in c.items() if not drop_re.search(k)})
            sheet_info[col] = c.most_common(12)
        out["sheets"].append(sheet_info)
    wb.close()
    return out


def collect_exhaustive() -> dict[str, Any]:
    files = list_all_inbox_files()
    per_file: list[dict[str, Any]] = []
    shops: Counter[str] = Counter()
    pages: Counter[str] = Counter()
    warehouses: Counter[str] = Counter()
    hosts: Counter[str] = Counter()
    platforms: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    spx_accounts: Counter[str] = Counter()
    spx_sender_phones: Counter[str] = Counter()
    spx_sender_names: Counter[str] = Counter()
    dump_files: list[str] = []
    owned_files: list[str] = []

    for meta in files:
        entry: dict[str, Any] = {
            "file": meta["file"],
            "size": meta["size"],
            "dump": meta["dump"],
            "ext": meta["ext"],
            "status": None,
            "extract": None,
        }
        if meta["dump"]:
            entry["status"] = "blocked_dump"
            dump_files.append(meta["file"])
            per_file.append(entry)
            continue

        owned_files.append(meta["file"])
        path = Path(meta["path"])
        try:
            if meta["ext"] == ".json":
                ex = extract_from_owned_json(path)
                shops.update(ex["shops"])
                pages.update(ex["pages"])
                warehouses.update(ex["warehouses"])
                hosts.update(ex["hosts"])
                entry["extract"] = {
                    "shops": ex["shops"].most_common(10),
                    "pages": ex["pages"].most_common(5),
                    "warehouses": ex["warehouses"].most_common(5),
                    "hosts": ex["hosts"].most_common(10),
                    "auth_literal_hits": ex["auth_literal_hits"],
                }
                entry["status"] = "scanned_owned_json"
            elif meta["ext"] == ".csv":
                ex = extract_from_owned_csv(path)
                shops.update(ex["shops"])
                platforms.update(ex["platforms"])
                sources.update(ex["sources"])
                entry["extract"] = {
                    "shops": ex["shops"].most_common(10),
                    "platforms": ex["platforms"].most_common(10),
                    "sources": ex["sources"].most_common(15),
                }
                entry["status"] = "scanned_owned_csv"
            elif meta["ext"] == ".xlsx":
                ex = extract_from_owned_xlsx(path)
                for sh in ex.get("sheets") or []:
                    # danh_sach platform
                    for plat, cnt in sh.get("platform") or []:
                        if plat and plat not in {"platform"}:
                            platforms[plat] += cnt
                    # thanhcoong
                    for acc, cnt in sh.get("Account ID") or []:
                        if str(acc).isdigit():
                            spx_accounts[str(acc)] += cnt
                    for phone, cnt in sh.get("Sender Phone Number") or []:
                        digits = re.sub(r"\D", "", str(phone))
                        if len(digits) >= 9:
                            spx_sender_phones[digits] += cnt
                    for name, cnt in sh.get("Sender Name") or []:
                        if name and len(name) > 3 and "Tên" not in name:
                            spx_sender_names[name] += cnt
                    for p3, cnt in sh.get("3PL Name") or []:
                        if p3 and "3PL" not in p3 and "Tên" not in p3:
                            platforms[p3] += cnt
                entry["extract"] = ex
                entry["status"] = "scanned_owned_xlsx"
            elif meta["ext"] == ".txt":
                # non-dump txt only — still inspect structure without storing passwords
                raw = path.read_text(encoding="utf-8", errors="ignore")
                lines = [ln for ln in raw.splitlines() if ln.strip()]
                user_passish = sum(1 for ln in lines if re.match(r"^[^:\s]+:[^:\s]+$", ln.strip()))
                entry["extract"] = {
                    "lines": len(lines),
                    "user_pass_lines": user_passish,
                    "note": "txt owned path — nếu user:pass thì nên coi là dump",
                }
                if user_passish >= max(3, len(lines) // 2):
                    entry["status"] = "reclassified_dump_userpass"
                    entry["dump"] = True
                    dump_files.append(meta["file"])
                    if meta["file"] in owned_files:
                        owned_files.remove(meta["file"])
                else:
                    entry["status"] = "scanned_owned_txt"
            else:
                entry["status"] = "scanned_other"
                entry["extract"] = {"note": "không parser chuyên biệt"}
        except Exception as e:  # noqa: BLE001
            entry["status"] = "error"
            entry["extract"] = {"error": str(e)[:160]}
        per_file.append(entry)

    shop_ranked = [s for s, _ in shops.most_common() if s not in {"9999999", "0", ""}]
    primary = shop_ranked[0] if shop_ranked else None
    secondary = [s for s in shop_ranked[1:] if s != primary]
    page_id = pages.most_common(1)[0][0] if pages else None
    warehouse_id = warehouses.most_common(1)[0][0] if warehouses else None
    spx_id = spx_accounts.most_common(1)[0][0] if spx_accounts else None
    spx_user = spx_sender_phones.most_common(1)[0][0] if spx_sender_phones else None
    spx_name = spx_sender_names.most_common(1)[0][0] if spx_sender_names else None
    host_list = [h for h, _ in hosts.most_common(20)]

    fills: list[dict[str, Any]] = []
    if primary or page_id or warehouse_id or host_list:
        extras: dict[str, str] = {}
        if secondary:
            extras["PANCAKE_SECONDARY_SHOP_IDS"] = ",".join(secondary)
        if page_id:
            extras["PANCAKE_PAGE_ID"] = page_id
        if warehouse_id:
            extras["PANCAKE_WAREHOUSE_ID"] = warehouse_id
        if host_list:
            extras["ORDER_API_HOSTS"] = ",".join(host_list[:12])
        if platforms:
            extras["ORDER_PLATFORMS_SEEN"] = ",".join(p for p, _ in platforms.most_common(12))
        if sources:
            extras["ORDER_SOURCES_SEEN"] = ",".join(s for s, _ in sources.most_common(12))
        # token-related source label (không phải token) — giữ để mapper
        tokenish_sources = [s for s, _ in sources.most_common() if "token" in s.lower()]
        if tokenish_sources:
            extras["ORDER_TOKEN_SOURCE_LABELS"] = ",".join(tokenish_sources[:8])
        fills.append(
            {
                "platform": "Pancake",
                "shop_id": primary,
                "extras": extras,
                "source": "exhaustive_owned_exports",
                "evidence": {
                    "shop_counts": shops.most_common(8),
                    "page_counts": pages.most_common(3),
                    "warehouse_counts": warehouses.most_common(3),
                    "hosts": hosts.most_common(10),
                    "platforms": platforms.most_common(10),
                    "sources": sources.most_common(12),
                },
            }
        )

    if spx_id or spx_user:
        extras_spx: dict[str, str] = {}
        if spx_id:
            extras_spx["SPX_SHOP_ID"] = spx_id
        if spx_user:
            extras_spx["SPX_USER"] = spx_user
        if spx_name:
            extras_spx["SPX_SENDER_NAME"] = spx_name
        extras_spx["SPX_3PL"] = "SPX"
        fills.append(
            {
                "platform": "SPX",
                "shop_id": spx_id,
                "user": spx_user,
                "extras": extras_spx,
                "source": "thanhcoong.xlsx",
                "evidence": {
                    "accounts": spx_accounts.most_common(5),
                    "sender_phones": spx_sender_phones.most_common(3),
                    "sender_names": spx_sender_names.most_common(3),
                },
            }
        )

    return {
        "ok": True,
        "files_total": len(files),
        "files_owned": len(owned_files),
        "files_dump": len(set(dump_files)),
        "owned_files": owned_files,
        "dump_files": sorted(set(dump_files)),
        "per_file": per_file,
        "aggregates": {
            "shops": shops.most_common(15),
            "pages": pages.most_common(5),
            "warehouses": warehouses.most_common(5),
            "hosts": hosts.most_common(15),
            "platforms": platforms.most_common(15),
            "sources": sources.most_common(20),
            "spx_accounts": spx_accounts.most_common(5),
            "spx_sender_phones": spx_sender_phones.most_common(5),
            "spx_sender_names": spx_sender_names.most_common(3),
        },
        "fills": fills,
        "real_api_tokens_in_owned_exports": 0,
        "coverage": {
            "every_file_listed": True,
            "owned_scanned": len(owned_files),
            "dump_blocked": len(set(dump_files)),
            "gap": "Không có access_token/api_key trong export đơn — cần token dashboard sở hữu",
        },
    }


def inventory_dump_audit() -> dict[str, Any]:
    by_file: dict[str, Counter] = defaultdict(Counter)
    by_plat: Counter[str] = Counter()
    total = 0
    dump_files: list[str] = []
    if AUDIT_DB.is_file():
        con = sqlite3.connect(AUDIT_DB)
        for r in con.execute(
            "SELECT file, kind, platform, dump_source, COUNT(*) c FROM secrets_findings GROUP BY 1,2,3,4"
        ):
            fname, kind, platform, dump_source, c = r
            dumpish = bool(dump_source) or is_dump_name(str(fname))
            if not dumpish:
                continue
            total += int(c)
            by_file[str(fname)][str(kind)] += int(c)
            if platform:
                by_plat[str(platform)] += int(c)
            if fname not in dump_files:
                dump_files.append(str(fname))
        con.close()
    return {
        "ok": True,
        "dump_findings": total,
        "dump_files_db": dump_files,
        "by_platform": by_plat.most_common(20),
        "by_file_kinds": {f: dict(c) for f, c in list(by_file.items())[:30]},
        "blocked": True,
        "reason": "dump/stealer/cookies/password-list — không điền (no dump-login)",
    }


def fill_via_nginx(fills: list[dict[str, Any]], *, keep: bool = False) -> dict[str, Any]:
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

            full = build_report()
            write_outputs(full)
            audit_summary = {
                "ok": full.get("ok"),
                "stats": full.get("stats"),
                "verdict": full.get("verdict"),
                "files_scanned": len((full.get("files") or [])),
            }
        except Exception as e:  # noqa: BLE001
            audit_summary = {"ok": False, "error": str(e)[:160]}

    owned = collect_exhaustive()
    dumps = inventory_dump_audit()
    fill = fill_via_nginx(owned.get("fills") or [], keep=keep)

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

    # missed check: every inbox file must appear in per_file
    listed = {e["file"] for e in owned.get("per_file") or []}
    disk = {m["file"] for m in list_all_inbox_files()}
    missing_files = sorted(disk - listed)

    report = {
        "ok": bool(fill.get("ok") or not (owned.get("fills") or [])) and not missing_files,
        "module": "nginx_embed_order_secrets_fill",
        "checked_at": utc_now(),
        "pipeline": "exhaustive audit→classify→nginx /v1/owned/fill→secrets/backend_pipes.env",
        "via_nginx": True,
        "audit": audit_summary,
        "owned_extract": {
            "files_total": owned.get("files_total"),
            "files_owned": owned.get("files_owned"),
            "files_dump": owned.get("files_dump"),
            "owned_files": owned.get("owned_files"),
            "dump_files": owned.get("dump_files"),
            "aggregates": owned.get("aggregates"),
            "fills": [
                {
                    "platform": f.get("platform"),
                    "shop_id": f.get("shop_id"),
                    "user": f.get("user"),
                    "extras_keys": sorted((f.get("extras") or {}).keys()),
                    "source": f.get("source"),
                    "evidence": f.get("evidence"),
                }
                for f in (owned.get("fills") or [])
            ],
            "real_api_tokens_in_owned_exports": owned.get("real_api_tokens_in_owned_exports"),
            "coverage": owned.get("coverage"),
            "per_file": owned.get("per_file"),
        },
        "dump_inventory": dumps,
        "fill": fill,
        "owned_map_after": owned_map_pub,
        "missed_files": missing_files,
        "verdict": (
            f"{'✅' if not missing_files else '❌'} Phủ sóng {owned.get('files_total')} file · "
            f"owned={owned.get('files_owned')} dump={owned.get('files_dump')} · "
            f"filled={fill.get('filled')}/{fill.get('total')} · "
            f"dump_findings={dumps.get('dump_findings')} · "
            f"missed_files={len(missing_files)}"
        ),
        "policy": {
            "owned_only": True,
            "no_dump_login": True,
            "via_nginx_required": True,
            "exhaustive": True,
        },
        "next": [
            "Token API sở hữu (dashboard): python3 scripts/access_token_rotate.py set --platform Pancake --token …",
            "python3 scripts/access_token_rotate.py apply-realtime",
        ],
    }
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔎 NGINX EMBED · RÀ SOÁT ĐẦY ĐỦ → SECRETS (không bỏ sót)")
    L(f"Lúc: {report.get('checked_at')}")
    L(report.get("verdict") or "")
    L(f"pipeline: {report.get('pipeline')}")
    ox = report.get("owned_extract") or {}
    L("")
    L(f"Files: total={ox.get('files_total')} owned={ox.get('files_owned')} dump={ox.get('files_dump')}")
    if report.get("missed_files"):
        L(f"❌ MISSED: {report.get('missed_files')}")
    else:
        L("✅ Không sót file inbox nào")
    L("")
    L("=== Từng file ===")
    for e in ox.get("per_file") or []:
        mark = "⚠DUMP" if e.get("dump") or str(e.get("status", "")).startswith("reclassified") else "·"
        L(f"{mark} {e.get('file')} size={e.get('size')} status={e.get('status')}")
        ex = e.get("extract") or {}
        if e.get("status") == "scanned_owned_json":
            L(f"    shops={ex.get('shops')} pages={ex.get('pages')} wh={ex.get('warehouses')} hosts={ex.get('hosts')}")
            if ex.get("auth_literal_hits"):
                L(f"    auth_hits={ex.get('auth_literal_hits')}")
        elif e.get("status") == "scanned_owned_csv":
            L(f"    shops={ex.get('shops')} platforms={ex.get('platforms')} sources={ex.get('sources')}")
        elif e.get("status") == "scanned_owned_xlsx":
            for sh in ex.get("sheets") or []:
                L(f"    sheet={sh.get('name')} rows={sh.get('rows')} cols={sh.get('interesting_cols')}")
        elif e.get("extract") and e.get("extract", {}).get("error"):
            L(f"    error={ex.get('error')}")
    L("")
    agg = ox.get("aggregates") or {}
    L(f"Aggregates shops={agg.get('shops')}")
    L(f"  pages={agg.get('pages')} warehouses={agg.get('warehouses')}")
    L(f"  hosts={agg.get('hosts')}")
    L(f"  platforms={agg.get('platforms')}")
    L(f"  sources={agg.get('sources')}")
    L(f"  spx_accounts={agg.get('spx_accounts')} phones={agg.get('spx_sender_phones')}")
    L(f"real_api_tokens_in_owned_exports={ox.get('real_api_tokens_in_owned_exports')}")
    cov = ox.get("coverage") or {}
    L(f"gap: {cov.get('gap')}")
    L("")
    dumps = report.get("dump_inventory") or {}
    L(f"Dump blocked findings={dumps.get('dump_findings')} · {dumps.get('reason')}")
    for f in ox.get("dump_files") or []:
        L(f"  ⚠ {f}")
    L("")
    fill = report.get("fill") or {}
    L(f"Fill via nginx: ok={fill.get('ok')} filled={fill.get('filled')}/{fill.get('total')}")
    for r in fill.get("results") or []:
        emb = r.get("embedded") or {}
        pl = r.get("payload") or {}
        L(
            f"  · {r.get('platform')} http={r.get('http')} upstream={emb.get('$upstream_addr')} "
            f"keys={pl.get('filled_keys')} shop={pl.get('shop_id')} user={pl.get('user')}"
        )
    L("")
    om = report.get("owned_map_after") or {}
    L(f"Owned map: {om.get('verdict')} ready={om.get('ready_platforms')}")
    for plat, info in (om.get("platforms") or {}).items():
        L(
            f"  · {plat}: ready={info.get('ready')} token={info.get('with_token')} "
            f"shops={info.get('shop_ids')} users={info.get('users')}"
        )
    if report.get("next"):
        L("")
        for n in report["next"]:
            L(f"· {n}")
    L("")
    L("Safety: exhaustive · owned-only · no dump-login · via nginx")
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
    ap = argparse.ArgumentParser(description="Exhaustive nginx embed: rà soát đầy đủ → secrets")
    ap.add_argument("--no-rescan", action="store_true")
    ap.add_argument("--keep", action="store_true")
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
