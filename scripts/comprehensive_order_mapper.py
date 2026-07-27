#!/usr/bin/env python3
"""Mapper mở rộng toàn diện: backend · kho · nhân sự · bưu cục · endpoint · ống dẫn.

Chỉ đọc local quarantine + reports đã có. Không login dump, không gọi third-party
trừ khi secrets owned đã cấu hình (keepalive/realtime riêng).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
Q = ROOT / "quarantine" / "telegram"
REPORTS = ROOT / "reports" / "telegram-classify"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def loadj(name: str):
    path = REPORTS / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def phone_class(ph: str | None) -> str:
    ph = (ph or "").strip()
    if not ph:
        return "MISSING"
    if "*" in ph or set(ph) <= {"*"}:
        return "MASKED"
    digits = re.sub(r"\D", "", ph)
    if len(digits) < 9:
        return "INVALID"
    return "OK"


def read_xlsx_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    z = zipfile.ZipFile(path)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            texts = [
                t.text or ""
                for t in si.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")
            ]
            shared.append("".join(texts))
    sheet_name = next(
        (n for n in z.namelist() if n.startswith("xl/worksheets/sheet")),
        None,
    )
    if not sheet_name:
        return []
    sheet = ET.fromstring(z.read(sheet_name))

    def col_row(ref: str) -> tuple[int, int]:
        m = re.match(r"([A-Z]+)(\d+)", ref)
        assert m
        col_s, row = m.group(1), int(m.group(2))
        n = 0
        for ch in col_s:
            n = n * 26 + (ord(ch) - 64)
        return n, row

    cells: dict[int, dict[int, str]] = defaultdict(dict)
    max_row = 0
    for c in sheet.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
        ref = c.get("r")
        if not ref:
            continue
        col, row = col_row(ref)
        max_row = max(max_row, row)
        v = c.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
        if v is None or v.text is None:
            continue
        cells[row][col] = shared[int(v.text)] if c.get("t") == "s" else v.text
    header = cells.get(1, {})
    names = {c: header[c] for c in sorted(header)}
    rows = []
    for r in range(2, max_row + 1):
        if r not in cells:
            continue
        rows.append({names.get(c, f"col{c}"): cells[r].get(c, "") for c in names})
    return rows


def inventory_quarantine() -> dict:
    files = []
    if Q.is_dir():
        for p in sorted(Q.iterdir()):
            if p.is_file():
                files.append({"name": p.name, "bytes": p.stat().st_size})
    return {"dir": str(Q), "files": files, "count": len(files)}


def scan_warehouses() -> dict:
    warehouses: Counter = Counter()
    detail: dict = {}
    shipments_empty = 0
    shipments_nonempty = 0
    assign_null = Counter()
    assign_present = Counter()
    accounts = Counter()
    pages = Counter()
    shops = Counter()

    for jf in sorted(Q.glob("orders_detailed_*.json")):
        try:
            orders = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(orders, list):
            continue
        for o in orders:
            p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
            wi = p.get("warehouse_info") if isinstance(p.get("warehouse_info"), dict) else {}
            wh_id = p.get("warehouse_id") or wi.get("id") or ""
            wh_name = wi.get("custom_id") or wi.get("name") or wh_id or "(none)"
            key = f"{wh_name}|{wh_id}"
            warehouses[key] += 1
            if key not in detail and (wi or wh_id):
                detail[key] = {
                    "custom_id": wi.get("custom_id"),
                    "name": wi.get("name"),
                    "address": wi.get("address"),
                    "warehouse_id": wh_id,
                }
            ships = p.get("shipments") or []
            if ships:
                shipments_nonempty += 1
            else:
                shipments_empty += 1
            for field in (
                "creator",
                "assigning_seller",
                "assigning_care",
                "account",
                "page_id",
                "warehouse_id",
            ):
                v = p.get(field)
                if v in (None, "", {}, []):
                    assign_null[field] += 1
                else:
                    assign_present[field] += 1
            if p.get("account") not in (None, ""):
                accounts[str(p.get("account"))] += 1
            if p.get("page_id"):
                pages[str(p.get("page_id"))] += 1
            shops[str(o.get("shop_id") or p.get("shop_id") or "")] += 1

    return {
        "counts": warehouses.most_common(),
        "detail": detail,
        "shipments_empty": shipments_empty,
        "shipments_nonempty": shipments_nonempty,
        "assign_null": dict(assign_null),
        "assign_present": dict(assign_present),
        "accounts": accounts.most_common(10),
        "pages": pages.most_common(10),
        "shops": shops.most_common(10),
    }


def scan_dang_giao() -> dict:
    path = Q / "orders_detailed_Dang_giao_20260512_120712.csv"
    if not path.is_file():
        return {"rows": 0}
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        rows = list(csv.DictReader(fh))
    by_source = {}
    phone = Counter()
    for r in rows:
        src = r.get("source") or "(empty)"
        b = by_source.setdefault(src, {"n": 0, "phone": Counter(), "name_missing": 0})
        b["n"] += 1
        pc = phone_class(r.get("customer_phone"))
        b["phone"][pc] += 1
        phone[pc] += 1
        if not (r.get("customer_name") or "").strip():
            b["name_missing"] += 1
    return {
        "file": path.name,
        "rows": len(rows),
        "phone": dict(phone),
        "by_source": {
            k: {"n": v["n"], "phone": dict(v["phone"]), "name_missing": v["name_missing"]}
            for k, v in sorted(by_source.items(), key=lambda x: -x[1]["n"])
        },
        "staff_columns": False,
        "warehouse_columns": False,
    }


def scan_ghn_topology() -> dict:
    path = Q / "Ghn.txt"
    if not path.is_file():
        return {"hosts": [], "paths_top": []}
    text = path.read_text(encoding="utf-8", errors="ignore")
    hosts: Counter = Counter()
    paths: Counter = Counter()
    for line in text.splitlines():
        m = re.search(r"https?://([^/\s:]+)(/[^\s:]*)?", line)
        if not m:
            continue
        host = m.group(1).lower()
        path_s = (m.group(2) or "/")[:100]
        if not re.search(r"ghn|giaohangnhanh", host):
            continue
        hosts[host] += 1
        if re.search(
            r"hub|truck|sso|5sao|hr|shiip|gateway|ontime|hopdong|khachhang|warehouse|buu",
            host + path_s,
            re.I,
        ):
            paths[f"{host}{path_s.split('?')[0]}"] += 1
    return {"hosts": hosts.most_common(25), "paths_top": paths.most_common(30)}


def scan_thanhcoong() -> dict:
    rows = read_xlsx_rows(Q / "thanhcoong.xlsx")
    if not rows:
        return {"rows": 0}
    return {
        "rows": len(rows),
        "order_creator": Counter((r.get("Order Creator") or "").strip() for r in rows).most_common(10),
        "account_id": Counter((r.get("Account ID") or "").strip() for r in rows).most_common(10),
        "tpl": Counter((r.get("3PL Name") or "").strip() for r in rows).most_common(10),
        "sender": Counter((r.get("Sender Name") or "").strip() for r in rows).most_common(10),
        "status": Counter((r.get("Tracking Status") or "").strip() for r in rows).most_common(10),
    }


def build_layers(prior: dict, live: dict) -> list[dict]:
    ep = prior.get("endpoint") or {}
    buu = prior.get("buucuc") or {}
    kho = prior.get("kho_ns_bc") or {}
    staff = prior.get("staff") or {}
    urls = prior.get("urls") or {}
    pipes_live = prior.get("keepalive") or {}
    dang = live.get("dang_giao") or {}
    wh = live.get("warehouses") or {}

    return [
        {
            "id": "L1-BACKEND",
            "name": "Backend pipes (keepalive)",
            "pipes": (pipes_live or {}).get("pipes")
            or [
                {"backend": b, "status": "unknown"}
                for b in ("Telegram", "Pancake", "GHN", "TPOS", "direct_api", "OMS-pipe-bus")
            ],
        },
        {
            "id": "L2-ENDPOINT",
            "name": "Endpoint catalog",
            "endpoint_count": ep.get("endpoint_count"),
            "totals": ep.get("totals"),
            "mapper_flows": ep.get("mapper_flows"),
            "url_mentions": ((urls or {}).get("totals") or {}).get("url_mentions"),
        },
        {
            "id": "L3-KHO",
            "name": "Kho / warehouse",
            "warehouses": wh.get("counts"),
            "detail": wh.get("detail"),
            "shipments_empty": wh.get("shipments_empty"),
            "shipments_nonempty": wh.get("shipments_nonempty"),
        },
        {
            "id": "L4-NHANSU",
            "name": "Nhân sự OMS / 3PL creator",
            "assign_null": wh.get("assign_null"),
            "assign_present": wh.get("assign_present"),
            "accounts": wh.get("accounts"),
            "staff_summary": (staff or {}).get("summary"),
            "thanhcoong": live.get("thanhcoong"),
        },
        {
            "id": "L5-BUUCUC",
            "name": "Bưu cục / 3PL / tracking",
            "buucuc_pipes": [
                {k: p.get(k) for k in ("id", "priority", "status") if k in p}
                for p in (buu or {}).get("pipes") or []
            ],
            "kho_ns_bc_pipes": [
                {k: p.get(k) for k in ("id", "priority", "status", "name") if k in p}
                for p in (kho or {}).get("pipes") or []
            ],
            "ghn_topology": live.get("ghn_topology"),
        },
        {
            "id": "L6-DONHANG",
            "name": "Đơn hàng ops (Đang giao)",
            "dang_giao": dang,
        },
    ]


def build_master_pipes(live: dict, prior: dict) -> list[dict]:
    dang = live.get("dang_giao") or {}
    wh = live.get("warehouses") or {}
    xlsx = live.get("thanhcoong") or {}
    keep = {p.get("backend"): p for p in ((prior.get("keepalive") or {}).get("pipes") or [])}

    def st(backend: str) -> str:
        return (keep.get(backend) or {}).get("status") or "unknown"

    return [
        {
            "id": "M-01",
            "priority": "P0",
            "name": "Pancake POS → Kho → Nhân sự → GHN/3PL → Tracking → OMS",
            "status": "blocked_missing_keys_and_staff_assign",
            "stages": [
                "pos.pancake.vn /shops/{shop}/orders",
                "warehouse_info (Kho HCM)",
                "assigning_seller / assigning_care / creator",
                "GHN shiip / SPX / partner",
                "tracking.aship.app",
                "realtime_order_sync + Đang giao",
            ],
            "deps": {"Pancake": st("Pancake"), "GHN": st("GHN")},
        },
        {
            "id": "M-02",
            "priority": "P0",
            "name": "Telegram upload / direct_api → OMS → CS (thiếu SĐT)",
            "status": "ops_blocker",
            "stages": [
                "multi_platform_telegram_upload / direct_api_orders_snapshot",
                "CSV flat thiếu kho + nhân sự",
                "MISSING/MASKED phone → bưu cục không liên hệ",
            ],
            "dang_giao_phone": dang.get("phone"),
            "deps": {"Telegram": st("Telegram"), "direct_api": st("direct_api")},
        },
        {
            "id": "M-03",
            "priority": "P1",
            "name": "Tracking public (aship) ← mã VĐ bưu cục",
            "status": "ready_no_auth",
            "stages": [
                "provider_code từ VĐ",
                "GET tracking.aship.app/order",
                "map status → OMS",
            ],
        },
        {
            "id": "M-04",
            "priority": "P1",
            "name": "Sender/Kho proxy → Order Creator → SPX",
            "status": "mapped_from_xlsx",
            "stages": [
                "thanhcoong Sender",
                "Order Creator email",
                "3PL SPX → Đã giao",
            ],
            "evidence": xlsx,
        },
        {
            "id": "M-05",
            "priority": "P2",
            "name": "VNPost file đối soát",
            "status": "file_only",
            "stages": ["vnpost_ok_*.txt", "đối soát local", "chưa nối OMS"],
        },
        {
            "id": "M-06",
            "priority": "P2",
            "name": "TPOS OData delivery view",
            "status": st("TPOS"),
            "stages": [
                "{tpos}/odata/FastSaleOrder/ODataService.GetViewDelivery",
                "Bearer token owned",
            ],
            "deps": {"TPOS": st("TPOS")},
        },
        {
            "id": "M-07",
            "priority": "P3",
            "name": "GHN SSO/5sao/hr/truck-hub — nhân sự bưu cục",
            "status": "topology_only",
            "stages": ["portal login nhân sự", "không đấu assigning_* OMS", "không dùng dump"],
            "hosts_top": (live.get("ghn_topology") or {}).get("hosts", [])[:12],
        },
        {
            "id": "M-08",
            "priority": "P1",
            "name": "OMS pipe-bus registry",
            "status": st("OMS-pipe-bus"),
            "stages": ["backend_pipe_keepalive", "state secrets/*.json", "Telegram notify"],
            "warehouse_bridge": wh.get("counts"),
        },
    ]


def mermaid_for(pipes: list[dict], wh: dict, dang: dict) -> str:
    wh_label = "Kho HCM"
    if wh.get("counts"):
        wh_label = (wh["counts"][0][0] or "Kho").split("|")[0]
    phone = dang.get("phone") or {}
    return f"""flowchart TB
  subgraph L1["L1 Backend"]
    TG[Telegram]
    PK[Pancake]
    GHN[GHN shiip]
    TPOS[TPOS]
    DA[direct_api]
    BUS[OMS pipe-bus]
  end
  subgraph L3["L3 Kho"]
    WH["{wh_label}"]
  end
  subgraph L4["L4 Nhân sự"]
    SEL[assigning_seller]
    CARE[assigning_care]
    CRE[creator/account]
    XCRE[Order Creator 3PL]
  end
  subgraph L5["L5 Bưu cục / 3PL"]
    HUB[GHN hub/SSO topology]
    SPX[SPX]
    TRK[tracking.aship]
    VNP[VNPost file]
  end
  subgraph L6["L6 Đơn ops"]
    DG["Đang giao\\nOK={phone.get('OK',0)} MISSING={phone.get('MISSING',0)} MASKED={phone.get('MASKED',0)}"]
  end
  PK --> WH --> SEL
  WH --> CARE
  CRE --> WH
  SEL --> GHN --> TRK --> DG
  XCRE --> SPX --> TRK
  TG --> DG
  DA --> DG
  HUB -.-> GHN
  VNP -.-> DG
  BUS --> DG
  TPOS -.-> TRK
"""


def build_report() -> dict:
    prior = {
        "endpoint": loadj("endpoint_mapper_deep.json"),
        "buucuc": loadj("buucuc_order_pipes_mapper.json"),
        "kho_ns_bc": loadj("kho_nhansu_buucuc_pipes.json"),
        "staff": loadj("staff_orders_audit_expanded.json"),
        "urls": loadj("url_paths_expanded.json"),
        "keepalive": loadj("backend_pipe_keepalive.json"),
        "issues": loadj("order_issues_outstanding_deep.json"),
        "backend_stats": loadj("backend_paths_stats_deep.json"),
    }
    live = {
        "quarantine": inventory_quarantine(),
        "warehouses": scan_warehouses(),
        "dang_giao": scan_dang_giao(),
        "ghn_topology": scan_ghn_topology(),
        "thanhcoong": scan_thanhcoong(),
    }
    layers = build_layers(prior, live)
    pipes = build_master_pipes(live, prior)
    mmd = mermaid_for(pipes, live["warehouses"], live["dang_giao"])

    p0 = [p["id"] for p in pipes if p["priority"] == "P0"]
    ready = [p["id"] for p in pipes if "ready" in p.get("status", "")]
    blocked = [p["id"] for p in pipes if "blocked" in p.get("status", "") or p.get("status") == "ops_blocker"]

    prior_counts = {
        "endpoint_count": (prior["endpoint"] or {}).get("endpoint_count"),
        "buucuc_pipes": len((prior["buucuc"] or {}).get("pipes") or []),
        "kho_ns_bc_pipes": len((prior["kho_ns_bc"] or {}).get("pipes") or []),
        "url_mentions": ((prior["urls"] or {}).get("totals") or {}).get("url_mentions"),
        "keepalive_pipes": len((prior["keepalive"] or {}).get("pipes") or []),
    }

    verdict = (
        f"Mapper toàn diện: {len(layers)} lớp · {len(pipes)} ống master · "
        f"EP={prior_counts['endpoint_count']} · URL mentions="
        f"{prior_counts['url_mentions']} · "
        f"kho={(live['warehouses'].get('counts') or [['?']])[0][0].split('|')[0]} · "
        f"Đang giao={live['dang_giao'].get('rows', 0)} "
        f"(phone {live['dang_giao'].get('phone')}). "
        f"P0 blocked/ops={blocked}; ready={ready}. "
        f"P0: refetch Pancake đủ kho+NS+shipments + GHN/Pancake keys owned."
    )

    return {
        "ok": True,
        "query": "Mapper mở rộng toàn diện",
        "checked_at": now_z(),
        "summary": {
            "layers": len(layers),
            "master_pipes": len(pipes),
            "p0": p0,
            "ready": ready,
            "blocked_or_ops": blocked,
            "prior_counts": prior_counts,
            "quarantine_files": live["quarantine"].get("count"),
            "dang_giao_rows": live["dang_giao"].get("rows"),
            "dang_giao_phone": live["dang_giao"].get("phone"),
            "warehouses": live["warehouses"].get("counts"),
        },
        "layers": layers,
        "master_pipes": pipes,
        "live": live,
        "prior_report_refs": {
            k: (REPORTS / f"{name}.json").name
            for k, name in [
                ("endpoint", "endpoint_mapper_deep"),
                ("buucuc", "buucuc_order_pipes_mapper"),
                ("kho_ns_bc", "kho_nhansu_buucuc_pipes"),
                ("staff", "staff_orders_audit_expanded"),
                ("urls", "url_paths_expanded"),
                ("keepalive", "backend_pipe_keepalive"),
                ("issues", "order_issues_outstanding_deep"),
                ("backend_stats", "backend_paths_stats_deep"),
            ]
            if (REPORTS / f"{name}.json").is_file()
        },
        "mermaid": mmd,
        "safety": {
            "no_dump_login": True,
            "no_password_exfiltration": True,
            "local_only": True,
            "secrets_path": "secrets/backend_pipes.env",
            "needed_owned_secrets": [
                "PANCAKE_POS_API_KEY",
                "GHN_API_TOKEN",
                "TPOS_BASE_URL",
                "TPOS_ACCESS_TOKEN",
            ],
        },
        "next_actions": [
            "Refetch Pancake shop 1530618 kèm warehouse_info + assigning_* + shipments",
            "Join Đang giao CSV ↔ payload theo remote_id để gắn kho + nhân sự",
            "Thêm PANCAKE_POS_API_KEY + GHN_API_TOKEN owned vào secrets/backend_pipes.env",
            "Wire tracking.aship vào realtime_order_sync",
            "Map Order Creator email → Pancake account; pipe SPX riêng",
            "Backfill phone MISSING/MASKED trước khi đẩy bưu cục",
            "Không login SSO/5sao/hr bằng dump",
        ],
        "verdict": verdict,
    }


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🗺 MAPPER MỞ RỘNG TOÀN DIỆN")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    s = report["summary"]
    L("=== Summary ===")
    L(f"· layers={s['layers']} master_pipes={s['master_pipes']}")
    L(f"· prior={s['prior_counts']}")
    L(f"· Đang giao={s['dang_giao_rows']} phone={s['dang_giao_phone']}")
    L(f"· kho={s['warehouses']}")
    L(f"· P0={s['p0']} blocked/ops={s['blocked_or_ops']} ready={s['ready']}")
    L("")
    L("=== Lớp ===")
    for layer in report["layers"]:
        L(f"▶ {layer['id']} · {layer['name']}")
    L("")
    L("=== Ống master ===")
    for p in report["master_pipes"]:
        L(f"▶ [{p['priority']}] {p['id']} · {p['status']}")
        L(f"  {p['name']}")
        for st in p.get("stages") or []:
            L(f"  → {st}")
    L("")
    L("=== Next ===")
    for a in report["next_actions"]:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    out_json = REPORTS / "comprehensive_mapper.json"
    out_txt = REPORTS / "comprehensive_mapper.txt"
    out_mmd = REPORTS / "comprehensive_mapper.mermaid.md"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_txt.write_text(format_text(report), encoding="utf-8")
    out_mmd.write_text(
        "# Mapper mở rộng toàn diện\n\n```mermaid\n" + report["mermaid"] + "\n```\n",
        encoding="utf-8",
    )
    return {"json": out_json, "txt": out_txt, "mermaid": out_mmd}


def main() -> int:
    parser = argparse.ArgumentParser(description="Mapper mở rộng toàn diện đơn hàng")
    parser.add_argument("--json", action="store_true", help="In JSON ra stdout")
    args = parser.parse_args()
    report = build_report()
    paths = write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_text(report))
        print("\nWROTE", {k: str(v) for k, v in paths.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
