#!/usr/bin/env python3
"""Mapper truy vấn chuỗi catalog bưu cục.

Chuỗi:
  Aship ShippingProviderConfigs (catalog ĐVVC)
    → owned ConfigId / secret slot
    → backends catalog tip
    → contracts (HĐ ĐVVC · shop · account)
    → buucuc_nodes
    → orders → kho → shop
    → [--continue] tracking → aship SSR → pipe_events/flow_path → next

CLI query:
  --q TEXT | --buucuc | --backend | --provider | --chain
  --continue | --expand | --live | --list-chains | --notify | --json

Policy: owned secrets only · no dump-login · reports gitignored.
"""

from __future__ import annotations

import argparse
import html as htmlmod
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
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
BUUCUC_DB = REPORTS / "buucuc_backend.db"

# Catalog provider (Aship) → pipe/backend tip
PROVIDER_TO_BACKEND = {
    "GHN": "GHN",
    "ViettelPost": "ViettelPost",
    "VNPost": "VNPost-local",
    "HCMPost": "VNPost-local",
    "ShipChung": "OMS-pipe-bus",
    "Best": "Best",
    "BEST": "Best",
    "J&T": "J&T",
    "JNT": "J&T",
    "GHTK": "GHTK",
    "SPX": "SPX-local",
    "Pancake": "Pancake",
}

BUUCUC_TO_BACKEND = {
    "GHN": "GHN",
    "J&T": "J&T",
    "JNT": "J&T",
    "SPX": "SPX-local",
    "ViettelPost": "ViettelPost",
    "VTP": "ViettelPost",
    "VNPost": "VNPost-local",
    "GHTK": "GHTK",
    "Best": "Best",
    "Pancake": "Pancake",
}

# Predefined chuỗi queries
CHAIN_QUERIES: dict[str, dict[str, str]] = {
    "all": {
        "title": "Toàn bộ chuỗi catalog → buucuc → đơn",
        "hint": "catalog_provider → backend → HĐ → node → orders",
    },
    "ghn": {
        "title": "Chuỗi GHN",
        "provider": "GHN",
        "backend": "GHN",
        "buucuc": "GHN",
    },
    "vtp": {
        "title": "Chuỗi ViettelPost / VTP",
        "provider": "ViettelPost",
        "backend": "ViettelPost",
        "buucuc": "ViettelPost",
    },
    "best": {
        "title": "Chuỗi Best Inc",
        "provider": "Best",
        "backend": "Best",
        "buucuc": "Best",
    },
    "jnt": {
        "title": "Chuỗi J&T",
        "provider": "J&T",
        "backend": "J&T",
        "buucuc": "J&T",
    },
    "spx": {
        "title": "Chuỗi SPX",
        "provider": "SPX",
        "backend": "SPX-local",
        "buucuc": "SPX",
    },
    "pancake": {
        "title": "Chuỗi Pancake hub",
        "backend": "Pancake",
        "buucuc": "Pancake",
    },
    "unassigned": {
        "title": "Chuỗi chưa gán vận đơn",
        "buucuc": "UNASSIGNED",
    },
    "gap_secret": {
        "title": "Chuỗi thiếu secret owned",
        "hint": "backend tip có secret key nhưng env trống",
    },
    "gap_orders": {
        "title": "Catalog/HĐ có tip nhưng 0 đơn pipe",
        "hint": "contracts/backends không khớp buucuc_nodes.orders",
    },
    "continue": {
        "title": "Tiếp tục chuỗi → tracking · SSR · flow · next",
        "hint": "hop sau orders/kho/shop: tracking_url → aship SSR → pipe_events → next CLI",
    },
    "expand": {
        "title": "Mở rộng chuỗi → nhánh pipe · live probe · shop/kho · events",
        "hint": "continue + branch paths + owned GHN/SSR probe + ghi pipe_events",
    },
    "catalog_ext": {
        "title": "Mở rộng catalog BC thống nhất (Aship + pipe + HĐ + ConfigId)",
        "hint": "public ShippingProviderConfigs ⋃ pipe carriers ⋃ contracts ⋃ owned ConfigId",
    },
    "full": {
        "title": "Chuỗi đầy đủ: catalog_ext → continue → expand → live → hub",
        "hint": "catalog thống nhất + tracking/SSR/flow + branch/shop + live probe + hub snapshot",
    },
}


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


def open_db(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def http_json(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> tuple[int, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "OMS-buucuc-catalog-chain",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return int(resp.status), json.loads(raw or "null")
            except json.JSONDecodeError:
                return int(resp.status), {"raw": raw[:200]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        try:
            return int(e.code), json.loads(raw or "null")
        except Exception:
            return int(e.code), {"raw": raw[:200]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)[:160]}


def fetch_aship_catalog(env: dict[str, str]) -> dict[str, Any]:
    base = (env.get("ASHIP_BASE_URL") or "https://aship.tpos.vn").rstrip("/")
    token = (env.get("ASHIP_TOKEN_SHIP") or env.get("TPOS_ACCESS_TOKEN") or "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    st, body = http_json(f"{base}/odata/ShippingProviderConfigs?$top=100", headers)
    providers: list[dict[str, Any]] = []
    if isinstance(body, dict):
        for r in body.get("value") or []:
            if not isinstance(r, dict):
                continue
            providers.append(
                {
                    "id": r.get("Id"),
                    "provider": r.get("Provider"),
                    "description": r.get("Description"),
                    "config_keys": [
                        c.get("Key")
                        for c in (r.get("Configs") or [])
                        if isinstance(c, dict)
                    ][:12],
                }
            )
    owned = {
        "user": (env.get("ASHIP_CONFIG_ID") or "").strip() or None,
        "vtp": (env.get("ASHIP_CARRIER_VTP_CONFIG_ID") or "").strip() or None,
        "best": (env.get("ASHIP_CARRIER_BEST_KONTUM_CONFIG_ID") or "").strip() or None,
    }
    in_catalog = {
        k: (bool(v) and any(str(p.get("id")) == v for p in providers))
        for k, v in owned.items()
        if v
    }
    return {
        "http": st,
        "base": base,
        "providers": providers,
        "owned_config_ids": owned,
        "owned_in_public_catalog": in_catalog,
        "ok": st == 200 and bool(providers),
    }


def build_extended_catalog(
    *,
    catalog: dict[str, Any],
    backends: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    env: dict[str, str],
) -> dict[str, Any]:
    """Catalog BC thống nhất: Aship public ⋃ pipe/HĐ ⋃ owned ConfigId shop."""
    be_by_id = {str(b.get("id")): b for b in backends}
    contracts_by_be: dict[str, list[dict]] = defaultdict(list)
    for c in contracts:
        contracts_by_be[str(c.get("backend") or "")].append(c)

    nodes_by_root: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        buu = str(n.get("buucuc") or "")
        root = buu.split("/")[0]
        nodes_by_root[root].append(n)
        if buu != root:
            nodes_by_root[buu].append(n)

    entries: dict[str, dict[str, Any]] = {}

    def upsert(key: str, **fields: Any) -> dict[str, Any]:
        row = entries.get(key) or {
            "key": key,
            "sources": [],
            "provider": None,
            "catalog_id": None,
            "config_keys": [],
            "backend": None,
            "buucuc": None,
            "in_aship_public": False,
            "owned_config_slot": None,
            "owned_config_id": None,
            "contracts_n": 0,
            "contracts": [],
            "orders_n": 0,
            "nodes": [],
            "secret": None,
            "secret_present": None,
            "status": "unknown",
            "chain": None,
            "next": [],
        }
        for k, v in fields.items():
            if k == "sources" and isinstance(v, str):
                if v not in row["sources"]:
                    row["sources"].append(v)
            elif k == "sources" and isinstance(v, list):
                for s in v:
                    if s not in row["sources"]:
                        row["sources"].append(s)
            elif k == "nodes" and isinstance(v, list):
                # merge unique by buucuc+backend
                seen = {(x.get("buucuc"), x.get("backend")) for x in row["nodes"]}
                for x in v:
                    sig = (x.get("buucuc"), x.get("backend"))
                    if sig not in seen:
                        row["nodes"].append(x)
                        seen.add(sig)
            elif k == "contracts" and isinstance(v, list):
                seen = {x.get("contract_id") for x in row["contracts"]}
                for x in v:
                    if x.get("contract_id") not in seen:
                        row["contracts"].append(x)
                        seen.add(x.get("contract_id"))
            elif k == "config_keys" and isinstance(v, list):
                for ck in v:
                    if ck not in row["config_keys"]:
                        row["config_keys"].append(ck)
            elif v is not None and (row.get(k) in (None, [], 0, False, "") or k in {
                "status", "chain", "orders_n", "contracts_n", "secret", "secret_present",
                "backend", "buucuc", "provider", "catalog_id", "owned_config_slot",
                "owned_config_id", "in_aship_public",
            }):
                if k == "orders_n":
                    # sum unique node orders if nodes present later; keep max of explicit values as floor
                    row[k] = max(int(row.get(k) or 0), int(v or 0))
                elif k == "in_aship_public":
                    row[k] = bool(row.get(k) or v)
                else:
                    row[k] = v
        entries[key] = row
        return row

    def unique_nodes(node_list: list[dict]) -> list[dict]:
        seen: set[tuple] = set()
        out: list[dict] = []
        for n in node_list:
            sig = (n.get("buucuc"), n.get("backend"))
            if sig in seen:
                continue
            seen.add(sig)
            out.append(n)
        return out

    def orders_of(node_list: list[dict]) -> int:
        return sum(int(n.get("orders") or 0) for n in unique_nodes(node_list))

    # 1) Aship public
    for p in catalog.get("providers") or []:
        prov = str(p.get("provider") or "")
        be_id = PROVIDER_TO_BACKEND.get(prov) or PROVIDER_TO_BACKEND.get(prov.upper()) or prov
        be = be_by_id.get(be_id)
        confs = contracts_by_be.get(be_id) or []
        node_list = unique_nodes(
            nodes_by_root.get(prov) or nodes_by_root.get(be_id) or []
        )
        if prov == "ViettelPost" and not node_list:
            node_list = unique_nodes(nodes_by_root.get("VTP") or [])
        orders_n = orders_of(node_list)
        secret = (be or {}).get("secret")
        secret_present = (be or {}).get("secret_present")
        if secret is None and be_id == "ViettelPost":
            secret = "VIETTELPOST_TOKEN"
            secret_present = bool((env.get(secret) or "").strip())
        status = "ok"
        if secret and not secret_present:
            status = "missing_secret"
        elif orders_n == 0 and confs:
            status = "contract_no_orders"
        elif orders_n == 0 and not confs:
            status = "catalog_only"
        upsert(
            f"aship:{prov}",
            sources="aship_public",
            provider=prov,
            catalog_id=p.get("id"),
            config_keys=p.get("config_keys") or [],
            backend=be_id,
            buucuc=prov,
            in_aship_public=True,
            contracts_n=len(confs),
            contracts=[
                {
                    "contract_id": c.get("contract_id"),
                    "shop_id": c.get("shop_id"),
                    "shop_name": c.get("shop_name"),
                    "account_name": c.get("account_name"),
                }
                for c in confs[:5]
            ],
            orders_n=orders_n,
            nodes=[
                {
                    "buucuc": n.get("buucuc"),
                    "backend": n.get("backend"),
                    "orders": n.get("orders"),
                }
                for n in node_list[:5]
            ],
            secret=secret,
            secret_present=secret_present,
            status=status,
        )

    # 2) Owned ConfigId shop-side slots
    owned = catalog.get("owned_config_ids") or {}
    owned_map = {
        "user": ("ASHIP_CONFIG_ID", None, "shop_config"),
        "vtp": ("ASHIP_CARRIER_VTP_CONFIG_ID", "ViettelPost", "carrier_config"),
        "best": ("ASHIP_CARRIER_BEST_KONTUM_CONFIG_ID", "Best", "carrier_config"),
    }
    in_pub = catalog.get("owned_in_public_catalog") or {}
    for slot, (env_key, prov_hint, kind) in owned_map.items():
        cid = owned.get(slot)
        if not cid:
            continue
        be_id = PROVIDER_TO_BACKEND.get(prov_hint or "") if prov_hint else None
        upsert(
            f"owned_cfg:{slot}",
            sources="owned_config_id",
            provider=prov_hint or "AshipShop",
            catalog_id=cid,
            backend=be_id or "aship_shop",
            buucuc=prov_hint,
            owned_config_slot=slot,
            owned_config_id=cid,
            in_aship_public=bool(in_pub.get(slot)),
            status="shop_config_slot" if not in_pub.get(slot) else "ok",
            secret="ASHIP_TOKEN_SHIP",
            secret_present=bool((env.get("ASHIP_TOKEN_SHIP") or "").strip()),
        )

    # 3) Pipe / contract carriers not in Aship public
    aship_names = {str(p.get("provider") or "") for p in (catalog.get("providers") or [])}
    # from contracts
    for c in contracts:
        be_id = str(c.get("backend") or "")
        buu = str(c.get("buucuc") or be_id)
        prov = str(c.get("carrier") or c.get("partner_name") or buu)
        if prov in aship_names or be_id in {"GHN", "ViettelPost"} and prov in {"GHN", "VTP", "ViettelPost"}:
            # already covered via aship key — still enrich
            key = f"aship:{'ViettelPost' if prov in {'VTP', 'ViettelPost'} else prov if prov in aship_names else be_id}"
            if key not in entries:
                key = f"pipe:{be_id or buu}"
        else:
            key = f"pipe:{be_id or buu}"
        be = be_by_id.get(be_id)
        node_list = unique_nodes(nodes_by_root.get(buu) or nodes_by_root.get(be_id) or [])
        orders_n = orders_of(node_list)
        secret = c.get("secret") or (be or {}).get("secret")
        secret_present = (
            bool((env.get(str(secret)) or "").strip())
            if secret
            else (be or {}).get("secret_present")
        )
        status = "ok" if orders_n > 0 else "contract_no_orders"
        if secret and not secret_present:
            status = "missing_secret"
        upsert(
            key,
            sources="contract",
            provider=prov,
            backend=be_id,
            buucuc=buu,
            contracts_n=1,
            contracts=[
                {
                    "contract_id": c.get("contract_id"),
                    "shop_id": c.get("shop_id"),
                    "shop_name": c.get("shop_name"),
                    "account_name": c.get("account_name"),
                }
            ],
            orders_n=orders_n,
            nodes=[
                {
                    "buucuc": n.get("buucuc"),
                    "backend": n.get("backend"),
                    "orders": n.get("orders"),
                }
                for n in node_list[:5]
            ],
            secret=secret,
            secret_present=secret_present,
            status=status,
        )

    # from nodes (Pancake, J&T, UNASSIGNED…)
    for n in nodes:
        buu = str(n.get("buucuc") or "")
        root = buu.split("/")[0]
        if root in aship_names or root in {"VTP"}:
            continue
        be_pipe = str(n.get("backend") or "")
        be_id = BUUCUC_TO_BACKEND.get(root) or be_pipe or root
        key = f"pipe:{root}"
        be = be_by_id.get(be_id)
        status = "ok"
        if root.startswith("UNASSIGNED") or root.startswith("UNKNOWN"):
            status = "unassigned_or_unknown"
        upsert(
            key,
            sources="buucuc_node",
            provider=root,
            backend=be_id,
            buucuc=root,
            orders_n=int(n.get("orders") or 0),
            nodes=[
                {
                    "buucuc": n.get("buucuc"),
                    "backend": n.get("backend"),
                    "orders": n.get("orders"),
                }
            ],
            secret=(be or {}).get("secret"),
            secret_present=(be or {}).get("secret_present"),
            status=status,
            in_aship_public=False,
        )

    # finalize chain + next
    out_list: list[dict[str, Any]] = []
    for row in entries.values():
        row["contracts_n"] = len(row.get("contracts") or []) or int(row.get("contracts_n") or 0)
        # recompute orders from unique nodes (avoid double-count)
        node_orders = 0
        seen_nb: set[tuple] = set()
        for n in row.get("nodes") or []:
            sig = (n.get("buucuc"), n.get("backend"))
            if sig in seen_nb:
                continue
            seen_nb.add(sig)
            node_orders += int(n.get("orders") or 0)
        if node_orders:
            row["orders_n"] = node_orders
        elif not row.get("orders_n"):
            row["orders_n"] = 0
        row["chain"] = chain_text(
            [
                "+".join(row.get("sources") or []) or "catalog",
                f"provider:{row.get('provider')}",
                f"cfg:{row.get('catalog_id')}" if row.get("catalog_id") else None,
                f"backend:{row.get('backend')}",
                f"HĐ×{row.get('contracts_n')}",
                f"orders={row.get('orders_n')}",
            ]
        )
        acts: list[str] = []
        st = row.get("status")
        if st == "missing_secret":
            acts.append(f"Điền {row.get('secret')} owned")
        if st == "catalog_only":
            acts.append("Catalog public chưa có đơn/HĐ — chờ ship qua Aship hoặc bỏ qua")
        if st == "contract_no_orders":
            acts.append("HĐ có tip nhưng 0 đơn — scan/remap carrier")
        if st == "shop_config_slot":
            acts.append("ConfigId shop-side — dùng tokenShip/Aship API tạo đơn, không khớp public Id")
        if st == "unassigned_or_unknown":
            acts.append("Gán ĐVVC / quét BC remote")
        if row.get("in_aship_public") and int(row.get("orders_n") or 0) > 0:
            acts.append("Chuỗi catalog→pipe OK — keepalive")
        if not acts:
            acts.append("python3 scripts/buucuc_catalog_chain_query_mapper.py --chain catalog_ext --notify")
        row["next"] = acts[:4]
        out_list.append(row)

    out_list.sort(
        key=lambda r: (
            0 if r.get("in_aship_public") else 1,
            0 if r.get("owned_config_slot") else 1,
            -int(r.get("orders_n") or 0),
            str(r.get("provider") or ""),
        )
    )
    by_status = dict(Counter(r.get("status") for r in out_list))
    by_source = Counter()
    for r in out_list:
        for s in r.get("sources") or []:
            by_source[s] += 1
    return {
        "ok": True,
        "entries_n": len(out_list),
        "entries": out_list,
        "by_status": by_status,
        "by_source": dict(by_source),
        "aship_public_n": sum(1 for r in out_list if r.get("in_aship_public")),
        "pipe_only_n": sum(
            1
            for r in out_list
            if not r.get("in_aship_public") and "owned_config_id" not in (r.get("sources") or [])
            and not r.get("owned_config_slot")
        ),
        "owned_slots_n": sum(1 for r in out_list if r.get("owned_config_slot")),
    }


def load_backends(env: dict[str, str]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in (PIPE_DB, BUUCUC_DB):
        conn = open_db(path)
        if not conn:
            continue
        try:
            for r in conn.execute("SELECT * FROM backends"):
                d = dict(r)
                bid = str(d.get("id") or "")
                if not bid:
                    continue
                secret = d.get("secret")
                present = bool(secret and (env.get(str(secret)) or "").strip()) if secret else None
                d["secret_present"] = present
                d["source_db"] = path.name
                # prefer pipe tip if exists, else keep richer mirror
                if bid not in rows or path == PIPE_DB:
                    rows[bid] = d
                else:
                    # merge query_hint if missing
                    if not rows[bid].get("query_hint") and d.get("query_hint"):
                        rows[bid]["query_hint"] = d["query_hint"]
                    if rows[bid].get("secret_present") is None:
                        rows[bid]["secret_present"] = present
        except sqlite3.OperationalError:
            pass
        conn.close()
    return sorted(rows.values(), key=lambda x: str(x.get("id") or ""))


def load_contracts() -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for path in (PIPE_DB, BUUCUC_DB):
        conn = open_db(path)
        if not conn:
            continue
        try:
            for r in conn.execute("SELECT * FROM contracts"):
                d = dict(r)
                cid = str(d.get("contract_id") or "")
                if cid and cid not in seen:
                    d["source_db"] = path.name
                    seen[cid] = d
        except sqlite3.OperationalError:
            pass
        conn.close()
    return list(seen.values())


def load_buucuc_nodes() -> list[dict[str, Any]]:
    conn = open_db(PIPE_DB)
    if not conn:
        return []
    rows = [dict(r) for r in conn.execute("SELECT * FROM buucuc_nodes ORDER BY orders DESC")]
    conn.close()
    return rows


def order_chain_stats(buucuc: str | None = None, backend: str | None = None) -> dict[str, Any]:
    conn = open_db(PIPE_DB)
    if not conn:
        return {"orders": 0}
    where: list[str] = []
    args: list[Any] = []
    if buucuc:
        where.append("upper(coalesce(buucuc,'')) LIKE ?")
        args.append(f"%{buucuc.upper()}%")
    if backend:
        where.append("upper(coalesce(backend,'')) = ?")
        args.append(backend.upper())
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    orders = int(conn.execute(f"SELECT COUNT(*) FROM orders {clause}", args).fetchone()[0])
    with_track = int(
        conn.execute(
            f"SELECT COUNT(*) FROM orders {clause} "
            f"{'AND' if where else 'WHERE'} tracking_code IS NOT NULL AND TRIM(tracking_code) != ''",
            args,
        ).fetchone()[0]
    )
    kho = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT COALESCE(kho,'(none)') kho, COUNT(*) n
            FROM orders {clause}
            GROUP BY 1 ORDER BY n DESC LIMIT 8
            """,
            args,
        )
    ]
    shops = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT COALESCE(shop_name, shop_id, '(no_shop)') shop, COUNT(*) n
            FROM orders {clause}
            GROUP BY 1 ORDER BY n DESC LIMIT 8
            """,
            args,
        )
    ]
    pipes = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT COALESCE(pipe_source,'(none)') pipe_source, COUNT(*) n
            FROM orders {clause}
            GROUP BY 1 ORDER BY n DESC LIMIT 8
            """,
            args,
        )
    ]
    samples = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT van_tay, so_noi_bo, tracking_code, status, kho, shop_name, carrier
            FROM orders {clause}
            ORDER BY piped_at DESC
            LIMIT 5
            """,
            args,
        )
    ]
    conn.close()
    return {
        "orders": orders,
        "with_tracking": with_track,
        "kho_top": kho,
        "shops_top": shops,
        "pipe_sources": pipes,
        "samples": samples,
    }


def chain_text(parts: list[str | None]) -> str:
    clean = [p for p in parts if p]
    return " → ".join(clean) if clean else "(empty)"


def build_chains(
    *,
    catalog: dict[str, Any],
    backends: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    env: dict[str, str],
) -> list[dict[str, Any]]:
    be_by_id = {str(b.get("id")): b for b in backends}
    contracts_by_be: dict[str, list[dict]] = defaultdict(list)
    contracts_by_buu: dict[str, list[dict]] = defaultdict(list)
    for c in contracts:
        contracts_by_be[str(c.get("backend") or "")].append(c)
        contracts_by_buu[str(c.get("buucuc") or "")].append(c)

    nodes_by_buu: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        nodes_by_buu[str(n.get("buucuc") or "")].append(n)

    chains: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    # 1) from Aship catalog providers
    for p in catalog.get("providers") or []:
        prov = str(p.get("provider") or "")
        be_id = PROVIDER_TO_BACKEND.get(prov) or PROVIDER_TO_BACKEND.get(prov.upper()) or prov
        be = be_by_id.get(be_id)
        confs = contracts_by_be.get(be_id) or contracts_by_buu.get(prov) or []
        node_list = nodes_by_buu.get(prov) or nodes_by_buu.get(be_id) or []
        # VTP alias
        if prov == "ViettelPost" and not node_list:
            node_list = nodes_by_buu.get("VTP") or []
        orders_n = sum(int(n.get("orders") or 0) for n in node_list)
        secret = (be or {}).get("secret")
        secret_present = (be or {}).get("secret_present")
        if secret is None and be_id == "ViettelPost":
            secret = "VIETTELPOST_TOKEN"
            secret_present = bool((env.get(secret) or "").strip())
        status = "ok"
        if not confs and orders_n == 0:
            status = "catalog_only"
        elif secret and not secret_present:
            status = "missing_secret"
        elif orders_n == 0 and confs:
            status = "contract_no_orders"
        key = f"catalog:{prov}:{be_id}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        chains.append(
            {
                "key": key,
                "layer": "catalog→backend→HĐ→node",
                "provider": prov,
                "catalog_id": p.get("id"),
                "catalog_keys": p.get("config_keys") or [],
                "backend": be_id,
                "backend_role": (be or {}).get("role"),
                "oms": (be or {}).get("oms"),
                "secret": secret,
                "secret_present": secret_present,
                "contracts_n": len(confs),
                "contracts": [
                    {
                        "contract_id": c.get("contract_id"),
                        "shop_id": c.get("shop_id"),
                        "shop_name": c.get("shop_name"),
                        "account_name": c.get("account_name"),
                        "partner_name": c.get("partner_name"),
                    }
                    for c in confs[:5]
                ],
                "buucuc_nodes": [
                    {
                        "buucuc": n.get("buucuc"),
                        "backend": n.get("backend"),
                        "orders": n.get("orders"),
                        "kho_n": n.get("kho_n"),
                    }
                    for n in node_list[:5]
                ],
                "orders_n": orders_n,
                "status": status,
                "chain": chain_text(
                    [
                        f"catalog:{prov}",
                        f"cfg:{p.get('id')}",
                        f"backend:{be_id}",
                        f"HĐ×{len(confs)}",
                        f"node×{len(node_list)}({orders_n})",
                    ]
                ),
            }
        )

    # 2) from buucuc_nodes not covered by catalog provider name
    catalog_names = {str(p.get("provider") or "") for p in (catalog.get("providers") or [])}
    for n in nodes:
        buu = str(n.get("buucuc") or "")
        root = buu.split("/")[0]
        if root in catalog_names or buu in catalog_names:
            continue
        be_id = (
            BUUCUC_TO_BACKEND.get(root)
            or BUUCUC_TO_BACKEND.get(buu)
            or str(n.get("backend") or "OMS-pipe-bus")
        )
        be = be_by_id.get(be_id)
        confs = contracts_by_be.get(be_id) or contracts_by_buu.get(root) or []
        key = f"node:{buu}:{n.get('backend')}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        status = "ok"
        if buu.startswith("UNASSIGNED") or buu.startswith("UNKNOWN"):
            status = "unassigned_or_unknown"
        elif int(n.get("orders") or 0) == 0:
            status = "empty_node"
        chains.append(
            {
                "key": key,
                "layer": "node→backend→HĐ",
                "provider": None,
                "catalog_id": None,
                "catalog_keys": [],
                "backend": be_id,
                "backend_role": (be or {}).get("role") or n.get("backend"),
                "oms": (be or {}).get("oms"),
                "secret": (be or {}).get("secret"),
                "secret_present": (be or {}).get("secret_present"),
                "contracts_n": len(confs),
                "contracts": [
                    {
                        "contract_id": c.get("contract_id"),
                        "shop_id": c.get("shop_id"),
                        "shop_name": c.get("shop_name"),
                        "account_name": c.get("account_name"),
                        "partner_name": c.get("partner_name"),
                    }
                    for c in confs[:5]
                ],
                "buucuc_nodes": [
                    {
                        "buucuc": n.get("buucuc"),
                        "backend": n.get("backend"),
                        "orders": n.get("orders"),
                        "kho_n": n.get("kho_n"),
                    }
                ],
                "orders_n": int(n.get("orders") or 0),
                "status": status,
                "chain": chain_text(
                    [
                        f"buucuc:{buu}",
                        f"pipe_backend:{n.get('backend')}",
                        f"catalog_backend:{be_id}",
                        f"HĐ×{len(confs)}",
                        f"orders={n.get('orders')}",
                    ]
                ),
            }
        )

    # 3) from contracts without matching node orders
    for c in contracts:
        be_id = str(c.get("backend") or "")
        buu = str(c.get("buucuc") or "")
        key = f"contract:{c.get('contract_id')}"
        if key in seen_keys:
            continue
        # skip if already represented via catalog/node with same backend+buucuc
        if any(
            ch.get("backend") == be_id
            and any(x.get("buucuc") == buu for x in (ch.get("buucuc_nodes") or []))
            for ch in chains
        ):
            continue
        seen_keys.add(key)
        node_list = nodes_by_buu.get(buu) or []
        orders_n = sum(int(n.get("orders") or 0) for n in node_list)
        be = be_by_id.get(be_id)
        chains.append(
            {
                "key": key,
                "layer": "HĐ→backend→node",
                "provider": c.get("carrier") or c.get("partner_name"),
                "catalog_id": None,
                "catalog_keys": [],
                "backend": be_id,
                "backend_role": (be or {}).get("role") or c.get("role"),
                "oms": (be or {}).get("oms") or c.get("oms"),
                "secret": c.get("secret") or (be or {}).get("secret"),
                "secret_present": (
                    bool((env.get(str(c.get("secret"))) or "").strip())
                    if c.get("secret")
                    else (be or {}).get("secret_present")
                ),
                "contracts_n": 1,
                "contracts": [
                    {
                        "contract_id": c.get("contract_id"),
                        "shop_id": c.get("shop_id"),
                        "shop_name": c.get("shop_name"),
                        "account_name": c.get("account_name"),
                        "partner_name": c.get("partner_name"),
                    }
                ],
                "buucuc_nodes": [
                    {
                        "buucuc": n.get("buucuc"),
                        "backend": n.get("backend"),
                        "orders": n.get("orders"),
                        "kho_n": n.get("kho_n"),
                    }
                    for n in node_list[:5]
                ],
                "orders_n": orders_n,
                "status": "contract_no_orders" if orders_n == 0 else "ok",
                "chain": chain_text(
                    [
                        f"HĐ:{c.get('partner_name') or c.get('contract_id')}",
                        f"shop:{c.get('shop_id')}",
                        f"backend:{be_id}",
                        f"buucuc:{buu}",
                        f"orders={orders_n}",
                    ]
                ),
            }
        )

    chains.sort(key=lambda x: (-int(x.get("orders_n") or 0), str(x.get("backend") or "")))
    return chains


def filter_chains(
    chains: list[dict[str, Any]],
    *,
    q: str | None = None,
    buucuc: str | None = None,
    backend: str | None = None,
    provider: str | None = None,
    chain_id: str | None = None,
) -> list[dict[str, Any]]:
    out = chains
    if chain_id and chain_id in CHAIN_QUERIES and chain_id not in {"all", "gap_secret", "gap_orders"}:
        meta = CHAIN_QUERIES[chain_id]
        buucuc = buucuc or meta.get("buucuc")
        backend = backend or meta.get("backend")
        provider = provider or meta.get("provider")
    if chain_id == "gap_secret":
        return [c for c in out if c.get("secret") and not c.get("secret_present")]
    if chain_id == "gap_orders":
        return [
            c
            for c in out
            if c.get("status") in {"contract_no_orders", "catalog_only"}
            or (int(c.get("contracts_n") or 0) > 0 and int(c.get("orders_n") or 0) == 0)
        ]
    if chain_id == "unassigned" or (buucuc and buucuc.upper().startswith("UNASSIGNED")):
        return [
            c
            for c in out
            if c.get("status") == "unassigned_or_unknown"
            or any(
                str(n.get("buucuc") or "").upper().startswith("UNASSIGNED")
                or str(n.get("buucuc") or "").upper().startswith("UNKNOWN")
                for n in (c.get("buucuc_nodes") or [])
            )
        ]

    def match(c: dict[str, Any], needle: str, fields: list[str]) -> bool:
        n = needle.lower()
        blob_parts = [str(c.get(f) or "") for f in fields]
        for node in c.get("buucuc_nodes") or []:
            blob_parts.extend(str(node.get(k) or "") for k in ("buucuc", "backend"))
        for ct in c.get("contracts") or []:
            blob_parts.extend(
                str(ct.get(k) or "")
                for k in ("shop_name", "account_name", "partner_name", "shop_id")
            )
        blob_parts.append(str(c.get("chain") or ""))
        return n in " ".join(blob_parts).lower()

    if provider:
        out = [c for c in out if match(c, provider, ["provider", "backend"])]
    if backend:
        out = [c for c in out if match(c, backend, ["backend"])]
    if buucuc:
        out = [c for c in out if match(c, buucuc, ["provider", "backend"])]
    if q:
        # free text across chain
        out = [c for c in out if match(c, q, ["provider", "backend", "oms", "secret", "status", "key"])]
    return out


def enrich_order_stats(chains: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    enriched = []
    for i, c in enumerate(chains):
        row = dict(c)
        if i < limit:
            buu = None
            nodes = c.get("buucuc_nodes") or []
            if nodes:
                buu = str(nodes[0].get("buucuc") or "") or None
            # for catalog-only use provider name as buucuc hint
            if not buu and c.get("provider"):
                buu = str(c.get("provider"))
            stats = order_chain_stats(buucuc=buu if buu and not buu.startswith("UNKNOWN") else None)
            # if UNASSIGNED, query specifically
            if buu and (buu.startswith("UNASSIGNED") or buu.startswith("UNKNOWN")):
                stats = order_chain_stats(buucuc=buu.split("/")[0] if "/" in buu else buu)
            row["order_stats"] = stats
        enriched.append(row)
    return enriched


def _primary_buucuc(c: dict[str, Any]) -> str | None:
    nodes = c.get("buucuc_nodes") or []
    if nodes:
        return str(nodes[0].get("buucuc") or "") or None
    if c.get("provider"):
        return str(c.get("provider"))
    return None


def hop_tracking(buucuc: str | None, backend: str | None) -> dict[str, Any]:
    conn = open_db(PIPE_DB)
    if not conn:
        return {"ok": False, "error": "no_db"}
    where: list[str] = []
    args: list[Any] = []
    if buucuc:
        where.append("upper(coalesce(buucuc,'')) LIKE ?")
        args.append(f"%{buucuc.upper().split('/')[0]}%")
    if backend and backend not in {"OMS-pipe-bus", "direct_api"}:
        # soft: also accept when backend on row matches catalog backend tip
        pass
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = int(conn.execute(f"SELECT COUNT(*) FROM orders {clause}", args).fetchone()[0])
    with_code = int(
        conn.execute(
            f"SELECT COUNT(*) FROM orders {clause} "
            f"{'AND' if where else 'WHERE'} tracking_code IS NOT NULL AND TRIM(tracking_code) != ''",
            args,
        ).fetchone()[0]
    )
    with_url = int(
        conn.execute(
            f"SELECT COUNT(*) FROM orders {clause} "
            f"{'AND' if where else 'WHERE'} tracking_url IS NOT NULL AND TRIM(tracking_url) != ''",
            args,
        ).fetchone()[0]
    )
    aship_n = int(
        conn.execute(
            f"SELECT COUNT(*) FROM orders {clause} "
            f"{'AND' if where else 'WHERE'} tracking_url LIKE '%aship%'",
            args,
        ).fetchone()[0]
    )
    provs = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT COALESCE(tracking_provider,'(none)') p, COUNT(*) n
            FROM orders {clause}
            GROUP BY 1 ORDER BY n DESC LIMIT 8
            """,
            args,
        )
    ]
    samples_where = list(where) + [
        "tracking_code IS NOT NULL",
        "TRIM(tracking_code) != ''",
    ]
    samples_clause = "WHERE " + " AND ".join(samples_where)
    samples = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT tracking_code, tracking_provider, tracking_url, status, carrier
            FROM orders {samples_clause}
            ORDER BY piped_at DESC LIMIT 5
            """,
            args,
        )
    ]
    conn.close()
    return {
        "ok": True,
        "orders": total,
        "with_tracking_code": with_code,
        "with_tracking_url": with_url,
        "aship_url": aship_n,
        "providers": provs,
        "samples": samples,
        "coverage_pct": round(100.0 * with_code / total, 1) if total else 0.0,
    }


def hop_ssr_events(buucuc: str | None) -> dict[str, Any]:
    conn = open_db(PIPE_DB)
    if not conn:
        return {"ok": False}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
    where: list[str] = []
    args: list[Any] = []
    if buucuc:
        where.append("upper(coalesce(buucuc,'')) LIKE ?")
        args.append(f"%{buucuc.upper().split('/')[0]}%")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    ssr_n = 0
    ssr_by: list[dict[str, Any]] = []
    if "ssr_scraped_at" in cols:
        ssr_conds = list(where) + [
            "ssr_scraped_at IS NOT NULL",
            "ssr_scraped_at != ''",
        ]
        ssr_clause = "WHERE " + " AND ".join(ssr_conds)
        ssr_n = int(conn.execute(f"SELECT COUNT(*) FROM orders {ssr_clause}", args).fetchone()[0])
        if "ssr_status" in cols:
            ssr_by = [
                dict(r)
                for r in conn.execute(
                    f"""
                    SELECT COALESCE(ssr_status,'(none)') s, COUNT(*) n
                    FROM orders {ssr_clause}
                    GROUP BY 1 ORDER BY n DESC LIMIT 8
                    """,
                    args,
                )
            ]
    # recent related events (global SSR + buucuc remap)
    events = [
        dict(r)
        for r in conn.execute(
            """
            SELECT event, COUNT(*) n, MAX(at) last_at
            FROM pipe_events
            WHERE event LIKE 'tpos_ssr%'
               OR event IN ('aship_url_sync','carrier_buucuc_remap','upsert')
            GROUP BY 1 ORDER BY n DESC LIMIT 10
            """
        )
    ]
    conn.close()
    return {"ok": True, "ssr_rows": ssr_n, "ssr_status": ssr_by, "pipe_events_head": events}


def hop_flow_samples(buucuc: str | None, *, limit: int = 5) -> list[dict[str, Any]]:
    conn = open_db(PIPE_DB)
    if not conn:
        return []
    where = "WHERE flow_path IS NOT NULL AND TRIM(flow_path) != ''"
    args: list[Any] = []
    if buucuc:
        where += " AND upper(coalesce(buucuc,'')) LIKE ?"
        args.append(f"%{buucuc.upper().split('/')[0]}%")
    rows = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT flow_path, COUNT(*) n
            FROM orders {where}
            GROUP BY 1 ORDER BY n DESC LIMIT ?
            """,
            [*args, limit],
        )
    ]
    conn.close()
    return rows


def hop_branch_paths(buucuc: str | None, *, limit: int = 8) -> dict[str, Any]:
    """Nhánh pipe_source → channel → backend → buucuc → kho."""
    conn = open_db(PIPE_DB)
    if not conn:
        return {"ok": False, "branches": []}
    where = ""
    args: list[Any] = []
    if buucuc:
        where = "WHERE upper(coalesce(buucuc,'')) LIKE ?"
        args.append(f"%{buucuc.upper().split('/')[0]}%")
    rows = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT
              COALESCE(pipe_source,'(null)') pipe_source,
              COALESCE(channel,'(null)') channel,
              COALESCE(backend,'(null)') backend,
              COALESCE(buucuc,'(null)') buucuc,
              COALESCE(kho,'(null)') kho,
              COUNT(*) orders,
              COUNT(DISTINCT shop_id) shop_n,
              SUM(CASE WHEN tracking_code IS NOT NULL AND TRIM(tracking_code) != '' THEN 1 ELSE 0 END)
                with_tracking
            FROM orders
            {where}
            GROUP BY 1,2,3,4,5
            ORDER BY orders DESC
            LIMIT ?
            """,
            [*args, limit],
        )
    ]
    conn.close()
    for r in rows:
        r["path"] = (
            f"{r['pipe_source']} → {r['channel']} → {r['backend']} → "
            f"buucuc:{r['buucuc']} → kho:{r['kho']} ×{r['orders']}"
        )
    return {
        "ok": True,
        "branch_n": len(rows),
        "branches": rows,
        "pipe_sources": dict(Counter(r["pipe_source"] for r in rows)),
    }


def hop_shop_kho_matrix(buucuc: str | None, *, limit: int = 8) -> dict[str, Any]:
    conn = open_db(PIPE_DB)
    if not conn:
        return {"ok": False}
    where = ""
    args: list[Any] = []
    if buucuc:
        where = "WHERE upper(coalesce(buucuc,'')) LIKE ?"
        args.append(f"%{buucuc.upper().split('/')[0]}%")
    shops = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT COALESCE(shop_name, shop_id, '(no_shop)') shop, COUNT(*) n,
                   COUNT(DISTINCT kho) kho_n
            FROM orders {where}
            GROUP BY 1 ORDER BY n DESC LIMIT ?
            """,
            [*args, limit],
        )
    ]
    matrix = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT COALESCE(kho,'(none)') kho,
                   COALESCE(shop_name, shop_id, '(no_shop)') shop,
                   COUNT(*) n
            FROM orders {where}
            GROUP BY 1,2 ORDER BY n DESC LIMIT ?
            """,
            [*args, limit],
        )
    ]
    conn.close()
    return {"ok": True, "shops_top": shops, "kho_shop": matrix}


def hop_live_probe(
    c: dict[str, Any],
    tracking: dict[str, Any],
    env: dict[str, str],
    *,
    enabled: bool,
) -> dict[str, Any]:
    """Probe nhẹ owned: GHN token ping · Aship SSR 1 mã mẫu."""
    if not enabled:
        return {"ok": False, "skipped": True, "reason": "live_disabled"}
    be = str(c.get("backend") or "")
    probes: list[dict[str, Any]] = []

    # GHN owned ping
    if be == "GHN" and (env.get("GHN_API_TOKEN") or env.get("GHN_TOKEN") or "").strip():
        token = (env.get("GHN_API_TOKEN") or env.get("GHN_TOKEN") or "").strip()
        url = "https://online-gateway.ghn.vn/shiip/public-api/v2/shop/all"
        st, body = http_json(
            url,
            headers={"Token": token, "Content-Type": "application/json"},
        )
        n_shops = None
        if isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, list):
                n_shops = len(data)
            elif isinstance(data, dict) and isinstance(data.get("shops"), list):
                n_shops = len(data["shops"])
        probes.append(
            {
                "id": "ghn_shop_all",
                "http": st,
                "status": "ok" if 200 <= st < 300 else ("auth_fail" if st in (401, 403) else f"http_{st}"),
                "shops": n_shops,
            }
        )

    # Aship SSR sample from tracking codes
    samples = tracking.get("samples") or []
    ssr_probe = None
    for s in samples[:3]:
        code = str(s.get("tracking_code") or "").strip()
        prov = str(s.get("tracking_provider") or "").strip().lower()
        if not code:
            continue
        # Prefer TPO / viettelpost / best; else use resolved provider
        if code.upper().startswith("TPO"):
            prov_q = "ViettelPost"
        elif prov in {"viettelpost", "vtp", "best", "spx", "ghn", "jnt"}:
            prov_q = {
                "viettelpost": "ViettelPost",
                "vtp": "ViettelPost",
                "best": "BEST",
            }.get(prov, prov)
        else:
            continue
        url = "https://tracking.aship.app/order?" + urllib.parse.urlencode(
            {"provider_code": code, "provider": prov_q}
        )
        st = 0
        body = ""
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "OMS-bc-chain-expand", "Accept": "text/html"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                st = int(resp.status)
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            st = int(e.code)
            body = e.read().decode("utf-8", "replace") if e.fp else ""
        except Exception as e:  # noqa: BLE001
            ssr_probe = {"ok": False, "code": code, "provider": prov_q, "error": str(e)[:120]}
            break
        not_found = bool(re.search(r"(?i)không tồn tại|not found", body))
        texts = [
            htmlmod.unescape(t.strip())
            for t in re.findall(r">([^<]{2,80})<", body)
            if t.strip() and not re.search(r"[{}]|function|https?:", t)
        ]
        order_code = None
        for i, t in enumerate(texts):
            if t == "Mã đơn hàng:" and i + 1 < len(texts):
                order_code = texts[i + 1]
                break
        ssr_probe = {
            "ok": st == 200 and bool(order_code) and not not_found,
            "http": st,
            "code": code,
            "provider": prov_q,
            "order_code": order_code,
            "not_found": not_found,
            "url": url,
        }
        probes.append({"id": "aship_ssr_sample", **ssr_probe})
        break

    # Seed SSR if no sample but VTP/Best chain
    if ssr_probe is None and be in {"ViettelPost", "Best"}:
        code = "TPO1408375976"
        prov_q = "ViettelPost" if be == "ViettelPost" else "BEST"
        url = "https://tracking.aship.app/order?" + urllib.parse.urlencode(
            {"provider_code": code, "provider": prov_q}
        )
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "OMS-bc-chain-expand", "Accept": "text/html"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                st = int(resp.status)
                body = resp.read().decode("utf-8", "replace")
            texts = [
                htmlmod.unescape(t.strip())
                for t in re.findall(r">([^<]{2,80})<", body)
                if t.strip() and not re.search(r"[{}]|function|https?:", t)
            ]
            order_code = None
            for i, t in enumerate(texts):
                if t == "Mã đơn hàng:" and i + 1 < len(texts):
                    order_code = texts[i + 1]
                    break
            ssr_probe = {
                "ok": st == 200 and bool(order_code),
                "http": st,
                "code": code,
                "provider": prov_q,
                "order_code": order_code,
                "seed": True,
                "url": url,
            }
            probes.append({"id": "aship_ssr_seed", **ssr_probe})
        except Exception as e:  # noqa: BLE001
            probes.append({"id": "aship_ssr_seed", "ok": False, "error": str(e)[:120]})

    ok_n = sum(1 for p in probes if p.get("ok") or p.get("status") == "ok")
    return {
        "ok": bool(probes),
        "skipped": False,
        "probes": probes,
        "ok_n": ok_n,
        "probe_n": len(probes),
    }


def record_expand_events(chains: list[dict[str, Any]], *, limit: int = 20) -> int:
    """Ghi pipe_events bc_chain_expand cho các chuỗi đã mở rộng."""
    conn = open_db(PIPE_DB)
    if not conn:
        return 0
    n = 0
    now = utc_now()
    for c in chains[:limit]:
        hops = c.get("continue_hops") or {}
        if not hops:
            continue
        detail = json.dumps(
            {
                "key": c.get("key"),
                "status": c.get("status"),
                "backend": c.get("backend"),
                "orders_n": c.get("orders_n"),
                "continued_chain": hops.get("continued_chain"),
                "tracking_cov": (hops.get("tracking") or {}).get("coverage_pct"),
                "branch_n": (hops.get("branches") or {}).get("branch_n"),
                "live_ok": (hops.get("live") or {}).get("ok_n"),
                "next": (hops.get("next_actions") or [])[:3],
            },
            ensure_ascii=False,
        )[:500]
        conn.execute(
            """
            INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail)
            VALUES (?, 'bc_chain_expand', NULL, ?, ?)
            """,
            (now, str(c.get("backend") or c.get("provider") or "")[:80], detail),
        )
        n += 1
    conn.commit()
    conn.close()
    return n


def next_actions_for_chain(c: dict[str, Any], hops: dict[str, Any]) -> list[str]:
    st = c.get("status")
    be = str(c.get("backend") or "")
    buu = _primary_buucuc(c) or ""
    acts: list[str] = []
    if st == "missing_secret" or (c.get("secret") and not c.get("secret_present")):
        acts.append(f"Điền owned secret {c.get('secret')} vào secrets/backend_pipes.env")
        if be == "ViettelPost":
            acts.append("Sau khi có VIETTELPOST_TOKEN: python3 scripts/scan_buucuc_orders.py --backend ViettelPost --notify")
            acts.append("SSR TPO seed: python3 scripts/tpos_ssr_pipe.py --code TPO1408375976 --provider viettelpost --notify")
    if st in {"contract_no_orders", "catalog_only"}:
        acts.append(f"HĐ/catalog {be} chưa đổ đơn — kiểm tra partner scan hoặc remap carrier→buucuc")
        if be in {"Best", "GHTK", "J&T"}:
            acts.append("python3 scripts/contract_buucuc_backend_mapper.py --notify")
        if be == "ViettelPost":
            acts.append("python3 scripts/aship_tpos_ship_mapper.py --notify")
    if st == "unassigned_or_unknown":
        acts.append("python3 scripts/scan_buucuc_orders.py --days 3 --notify")
        acts.append("python3 scripts/order_pipe_reverse_query.py --continue-flow")
        acts.append("python3 scripts/pipe_branch_mapper.py --notify")
    if int(c.get("orders_n") or 0) > 0:
        track = hops.get("tracking") or {}
        if int(track.get("with_tracking_url") or 0) < int(track.get("with_tracking_code") or 0):
            acts.append("python3 scripts/tracking_aship.py --notify  # gắn tracking_url thiếu")
        if be in {"GHN", "SPX-local", "J&T", "Pancake"} and buu:
            acts.append(
                f"python3 scripts/buucuc_catalog_chain_query_mapper.py --buucuc {buu.split('/')[0]} --expand --live"
            )
        if any(
            str(p.get("p") or "").lower() in {"viettelpost", "best", "vtp"}
            for p in (track.get("providers") or [])
        ) or be in {"ViettelPost", "Best"}:
            acts.append("python3 scripts/tpos_ssr_pipe.py --providers viettelpost,best --limit 40 --notify")
        live = hops.get("live") or {}
        if live.get("ok_n"):
            acts.append("Live probe OK — giữ keepalive / scan định kỳ")
        elif live.get("probes") and not live.get("skipped"):
            acts.append("Live probe fail — kiểm tra token/SSR mã mẫu")
    if not acts:
        acts.append("python3 scripts/buucuc_catalog_chain_query_mapper.py --expand --live --notify")
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for a in acts:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out[:7]


def continue_chain_hops(
    chains: list[dict[str, Any]],
    *,
    limit: int = 15,
    expand: bool = False,
    live: bool = False,
    env: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Tiếp tục / mở rộng mỗi chuỗi: tracking → SSR → flow → [branch·shop·live] → next."""
    env = env or load_env()
    out: list[dict[str, Any]] = []
    for i, c in enumerate(chains[:limit]):
        buu = _primary_buucuc(c)
        be = str(c.get("backend") or "") if c.get("backend") else None
        tracking = hop_tracking(buu, be)
        ssr = hop_ssr_events(buu)
        flows = hop_flow_samples(buu, limit=4)
        hops: dict[str, Any] = {
            "tracking": tracking,
            "ssr_events": ssr,
            "flow_samples": flows,
        }
        parts = [
            c.get("chain"),
            f"track:{tracking.get('with_tracking_code')}/{tracking.get('orders')}({tracking.get('coverage_pct')}%)",
            f"aship_url:{tracking.get('aship_url')}",
            f"ssr:{ssr.get('ssr_rows')}",
            f"flow×{len(flows)}",
        ]
        if expand:
            branches = hop_branch_paths(buu, limit=8)
            shop_kho = hop_shop_kho_matrix(buu, limit=8)
            live_p = hop_live_probe(c, tracking, env, enabled=live)
            hops["branches"] = branches
            hops["shop_kho"] = shop_kho
            hops["live"] = live_p
            parts.append(f"branch×{branches.get('branch_n')}")
            parts.append(f"shop×{len((shop_kho.get('shops_top') or []))}")
            if not live_p.get("skipped"):
                parts.append(f"live:{live_p.get('ok_n')}/{live_p.get('probe_n')}")
        hops["next_actions"] = next_actions_for_chain(c, hops)
        hops["continued_chain"] = chain_text(parts)
        row = dict(c)
        row["continue_hops"] = hops
        out.append(row)
    for c in chains[limit:]:
        out.append(c)
    return out


def build_continue_priority(chains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ưu tiên gap → unassigned → live có đơn."""
    rank = {
        "missing_secret": 0,
        "contract_no_orders": 1,
        "catalog_only": 2,
        "unassigned_or_unknown": 3,
        "empty_node": 4,
        "ok": 5,
    }
    return sorted(
        chains,
        key=lambda c: (
            rank.get(str(c.get("status")), 9),
            -int(c.get("orders_n") or 0),
        ),
    )


def attach_hops_to_extended(
    extended: dict[str, Any],
    *,
    limit: int = 12,
    expand: bool = True,
    live: bool = True,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Gắn continue/expand hops vào từng entry catalog mở rộng."""
    env = env or load_env()
    entries = list(extended.get("entries") or [])
    # prioritize gaps then live volume
    rank = {
        "missing_secret": 0,
        "contract_no_orders": 1,
        "catalog_only": 2,
        "shop_config_slot": 3,
        "unassigned_or_unknown": 4,
        "ok": 5,
    }
    entries.sort(
        key=lambda e: (
            rank.get(str(e.get("status")), 9),
            -int(e.get("orders_n") or 0),
        )
    )
    enriched: list[dict[str, Any]] = []
    for i, e in enumerate(entries):
        row = dict(e)
        if i < limit:
            # synthesize a mini-chain row for hop helpers
            fake = {
                "key": e.get("key"),
                "status": e.get("status"),
                "backend": e.get("backend"),
                "provider": e.get("provider"),
                "secret": e.get("secret"),
                "secret_present": e.get("secret_present"),
                "contracts_n": e.get("contracts_n"),
                "orders_n": e.get("orders_n"),
                "chain": e.get("chain"),
                "buucuc_nodes": e.get("nodes") or (
                    [{"buucuc": e.get("buucuc"), "backend": e.get("backend"), "orders": e.get("orders_n")}]
                    if e.get("buucuc")
                    else []
                ),
                "contracts": e.get("contracts") or [],
            }
            hopped = continue_chain_hops(
                [fake],
                limit=1,
                expand=expand,
                live=live,
                env=env,
            )
            if hopped and hopped[0].get("continue_hops"):
                row["continue_hops"] = hopped[0]["continue_hops"]
                # merge next actions
                nxt = list(e.get("next") or [])
                for a in (row["continue_hops"].get("next_actions") or []):
                    if a not in nxt:
                        nxt.append(a)
                row["next"] = nxt[:7]
        enriched.append(row)
    out = dict(extended)
    out["entries"] = enriched
    out["hops_attached"] = sum(1 for e in enriched if e.get("continue_hops"))
    return out


def load_hub_snapshot() -> dict[str, Any]:
    """Snapshot backend·từng BC (per-hub mapper)."""
    try:
        from buucuc_backend_per_hub_mapper import build_report as hub_report

        rep = hub_report(prefer_pipe=True)
        hubs = rep.get("hubs") or []
        sample: list[dict[str, Any]] = []
        for h in hubs[:12]:
            if not isinstance(h, dict):
                continue
            oms = h.get("oms") if isinstance(h.get("oms"), dict) else {}
            contracts = h.get("contracts")
            sample.append(
                {
                    "buucuc": h.get("buucuc") or h.get("name"),
                    "primary_backend": h.get("primary_backend") or h.get("backend"),
                    "kind": h.get("kind"),
                    "pipe_status": h.get("pipe_status"),
                    "orders": h.get("orders") or h.get("orders_n"),
                    "oms": oms.get("status") or oms.get("channel") or h.get("oms_status"),
                    "contracts_n": len(contracts)
                    if isinstance(contracts, list)
                    else h.get("contracts_n"),
                    "shop_n": h.get("shop_n"),
                    "kho_n": h.get("kho_n"),
                }
            )
        return {
            "ok": bool(rep.get("ok", True)),
            "stats": rep.get("stats") or {},
            "verdict": rep.get("verdict"),
            "hubs_sample": sample,
            "hubs_n": len(hubs),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:160]}


def record_full_events(extended: dict[str, Any], *, limit: int = 20) -> int:
    conn = open_db(PIPE_DB)
    if not conn:
        return 0
    n = 0
    now = utc_now()
    for e in (extended.get("entries") or [])[:limit]:
        hops = e.get("continue_hops") or {}
        detail = json.dumps(
            {
                "key": e.get("key"),
                "status": e.get("status"),
                "provider": e.get("provider"),
                "backend": e.get("backend"),
                "orders_n": e.get("orders_n"),
                "sources": e.get("sources"),
                "continued_chain": hops.get("continued_chain"),
                "live_ok": (hops.get("live") or {}).get("ok_n"),
                "branch_n": (hops.get("branches") or {}).get("branch_n"),
                "next": (e.get("next") or [])[:3],
            },
            ensure_ascii=False,
        )[:500]
        conn.execute(
            """
            INSERT INTO pipe_events(at, event, van_tay, so_noi_bo, detail)
            VALUES (?, 'bc_chain_full', NULL, ?, ?)
            """,
            (now, str(e.get("backend") or e.get("provider") or "")[:80], detail),
        )
        n += 1
    conn.commit()
    conn.close()
    return n


def build_report(
    *,
    q: str | None = None,
    buucuc: str | None = None,
    backend: str | None = None,
    provider: str | None = None,
    chain_id: str | None = None,
    enrich_limit: int = 12,
    continue_chain: bool = False,
    expand: bool = False,
    live: bool = False,
    write_events: bool = True,
    catalog_extend: bool = False,
) -> dict[str, Any]:
    env = load_env()
    if not PIPE_DB.is_file():
        return {"ok": False, "error": f"missing {PIPE_DB}", "checked_at": utc_now()}

    # presets
    if chain_id == "full":
        catalog_extend = True
        continue_chain = True
        expand = True
        live = True
        chain_id = "all"
    if chain_id == "catalog_ext":
        catalog_extend = True
        chain_id = "all"
    if chain_id == "expand":
        expand = True
        continue_chain = True
        live = True
        catalog_extend = True
        chain_id = "all"
    elif chain_id == "continue":
        continue_chain = True
        chain_id = "all"
    if expand:
        continue_chain = True
        catalog_extend = True

    full_mode = catalog_extend and continue_chain and expand and live

    catalog = fetch_aship_catalog(env)
    backends = load_backends(env)
    contracts = load_contracts()
    nodes = load_buucuc_nodes()
    extended = build_extended_catalog(
        catalog=catalog,
        backends=backends,
        contracts=contracts,
        nodes=nodes,
        env=env,
    )
    if full_mode or (catalog_extend and expand):
        extended = attach_hops_to_extended(
            extended,
            limit=max(enrich_limit, 12),
            expand=expand,
            live=live,
            env=env,
        )

    hub_snap: dict[str, Any] = {}
    if full_mode:
        hub_snap = load_hub_snapshot()

    all_chains = build_chains(
        catalog=catalog, backends=backends, contracts=contracts, nodes=nodes, env=env
    )
    matched = filter_chains(
        all_chains,
        q=q,
        buucuc=buucuc,
        backend=backend,
        provider=provider,
        chain_id=chain_id or "all",
    )
    if continue_chain:
        matched = build_continue_priority(matched)
    matched = enrich_order_stats(matched, limit=enrich_limit)
    if continue_chain:
        matched = continue_chain_hops(
            matched,
            limit=max(enrich_limit, 15),
            expand=expand,
            live=live,
            env=env,
        )

    events_n = 0
    if expand and write_events and not full_mode:
        events_n = record_expand_events(matched, limit=max(enrich_limit, 15))
    full_events_n = 0
    if full_mode and write_events:
        full_events_n = record_full_events(extended, limit=max(enrich_limit, 15))
        events_n = full_events_n

    status_c = Counter(c.get("status") for c in all_chains)
    gap_secret = [c for c in all_chains if c.get("secret") and not c.get("secret_present")]
    gap_orders = [
        c
        for c in all_chains
        if int(c.get("contracts_n") or 0) > 0 and int(c.get("orders_n") or 0) == 0
    ]

    pipe_orders = 0
    conn = open_db(PIPE_DB)
    if conn:
        pipe_orders = int(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
        conn.close()

    if full_mode:
        mode = "full"
    elif catalog_extend and not expand and not continue_chain:
        mode = "catalog_ext"
    elif expand:
        mode = "expand"
    elif continue_chain:
        mode = "continue"
    else:
        mode = chain_id or "all"
    query_meta = {
        "q": q,
        "buucuc": buucuc,
        "backend": backend,
        "provider": provider,
        "chain": mode,
        "continue": continue_chain,
        "expand": expand,
        "live": live,
        "catalog_extend": catalog_extend,
        "full": full_mode,
        "chain_title": (
            CHAIN_QUERIES["full"]["title"]
            if full_mode
            else (
                CHAIN_QUERIES["catalog_ext"]["title"]
                if mode == "catalog_ext"
                else (
                    CHAIN_QUERIES["expand"]["title"]
                    if expand
                    else (
                        CHAIN_QUERIES["continue"]["title"]
                        if continue_chain
                        else (CHAIN_QUERIES.get(chain_id or "all") or {}).get("title")
                    )
                )
            )
        ),
    }

    atlas = (
        "ShippingProviderConfigs → ConfigId/secret → backends → contracts → "
        "buucuc_nodes → orders → kho/shop"
    )
    mermaid = (
        "flowchart LR\n"
        "  CAT[Aship ShippingProviderConfigs] --> CFG[ConfigId / secret]\n"
        "  CFG --> BE[backends catalog]\n"
        "  BE --> HD[contracts HĐ]\n"
        "  HD --> NODE[buucuc_nodes]\n"
        "  NODE --> ORD[orders]\n"
        "  ORD --> KHO[kho]\n"
        "  ORD --> SHOP[shop]\n"
    )
    if full_mode:
        atlas = (
            "catalog_ext ⋃ owned ConfigId ⋃ pipe/HĐ → tracking → SSR/flow → "
            "branch/shop → live probe → hub backend → next"
        )
        mermaid = (
            "flowchart LR\n"
            "  EXT[catalog_ext] --> HOP[continue hops]\n"
            "  HOP --> BR[branch/shop]\n"
            "  BR --> LIVE[live probe]\n"
            "  LIVE --> HUB[per-hub backend]\n"
            "  HUB --> NEXT[next CLI]\n"
        )
    elif catalog_extend:
        atlas = (
            "Aship public ⋃ owned ConfigId ⋃ pipe/HĐ carriers → backend → "
            "buucuc_nodes → orders → kho/shop"
        )
        mermaid = (
            "flowchart LR\n"
            "  AP[Aship public] --> EXT[extended catalog BC]\n"
            "  OWN[owned ConfigId] --> EXT\n"
            "  PIPE[pipe nodes / HĐ] --> EXT\n"
            "  EXT --> BE[backends]\n"
            "  BE --> NODE[buucuc_nodes]\n"
            "  NODE --> ORD[orders]\n"
        )
    if continue_chain and not full_mode:
        atlas += " → tracking → aship SSR → pipe_events/flow → next"
        mermaid += (
            "  ORD --> TRK[tracking_code/url]\n"
            "  TRK --> SSR[tracking.aship SSR]\n"
            "  SSR --> EV[pipe_events]\n"
            "  ORD --> FLOW[flow_path]\n"
            "  EV --> NEXT[next CLI]\n"
            "  FLOW --> NEXT\n"
        )
    if expand and not full_mode:
        atlas += " → branch · live probe · shop/kho · bc_chain_expand"
        mermaid += (
            "  ORD --> BR[pipe_source branch]\n"
            "  BR --> LIVE[owned live probe]\n"
            "  LIVE --> NEXT\n"
            "  SHOP --> NEXT\n"
        )

    cont_summary: dict[str, Any] = {}
    if continue_chain:
        with_hops = [c for c in matched if c.get("continue_hops")]
        act_c: Counter[str] = Counter()
        live_ok = 0
        branch_n = 0
        for c in with_hops:
            hops = c.get("continue_hops") or {}
            for a in hops.get("next_actions") or []:
                act_c[a] += 1
            live_p = hops.get("live") or {}
            if int(live_p.get("ok_n") or 0) > 0:
                live_ok += 1
            branch_n += int((hops.get("branches") or {}).get("branch_n") or 0)
        # also count from extended catalog hops in full mode
        ext_hops_n = 0
        if full_mode:
            for e in extended.get("entries") or []:
                hops = e.get("continue_hops") or {}
                if not hops:
                    continue
                ext_hops_n += 1
                for a in hops.get("next_actions") or []:
                    act_c[a] += 1
                if int((hops.get("live") or {}).get("ok_n") or 0) > 0:
                    live_ok += 1
                branch_n += int((hops.get("branches") or {}).get("branch_n") or 0)
        cont_summary = {
            "chains_continued": len(with_hops),
            "ext_hops_attached": ext_hops_n if full_mode else extended.get("hops_attached"),
            "actions_n": sum(act_c.values()),
            "expand": expand,
            "live": live,
            "full": full_mode,
            "live_ok_chains": live_ok,
            "branch_rows": branch_n,
            "events_written": events_n,
            "hub_ok": hub_snap.get("ok") if full_mode else None,
            "hubs_n": hub_snap.get("hubs_n") if full_mode else None,
            "tracking_coverages": [
                {
                    "backend": c.get("backend"),
                    "buucuc": _primary_buucuc(c),
                    "coverage_pct": ((c.get("continue_hops") or {}).get("tracking") or {}).get(
                        "coverage_pct"
                    ),
                    "aship_url": ((c.get("continue_hops") or {}).get("tracking") or {}).get(
                        "aship_url"
                    ),
                    "branch_n": ((c.get("continue_hops") or {}).get("branches") or {}).get(
                        "branch_n"
                    ),
                    "live_ok": ((c.get("continue_hops") or {}).get("live") or {}).get("ok_n"),
                }
                for c in with_hops[:12]
            ],
            "top_next": [{"action": a, "n": n} for a, n in act_c.most_common(12)],
        }

    tag = ""
    if full_mode:
        tag = " · FULL"
    elif mode == "catalog_ext":
        tag = " · CATALOG MỞ RỘNG"
    elif expand:
        tag = " · MỞ RỘNG"
    elif continue_chain:
        tag = " · TIẾP TỤC"

    report: dict[str, Any] = {
        "ok": True,
        "module": "buucuc_catalog_chain_query_mapper",
        "checked_at": utc_now(),
        "policy": "owned secrets only · no dump-login · reports gitignored",
        "atlas": atlas,
        "mermaid": mermaid,
        "query": query_meta,
        "chain_presets": {
            k: {"title": v.get("title"), "hint": v.get("hint")}
            for k, v in CHAIN_QUERIES.items()
        },
        "catalog": {
            "http": catalog.get("http"),
            "ok": catalog.get("ok"),
            "providers_n": len(catalog.get("providers") or []),
            "providers": catalog.get("providers"),
            "owned_config_ids": catalog.get("owned_config_ids"),
            "owned_in_public_catalog": catalog.get("owned_in_public_catalog"),
        },
        "catalog_extended": extended if catalog_extend else None,
        "hub": hub_snap if full_mode else None,
        "stats": {
            "pipe_orders": pipe_orders,
            "backends": len(backends),
            "contracts": len(contracts),
            "buucuc_nodes": len(nodes),
            "chains_total": len(all_chains),
            "chains_matched": len(matched),
            "by_status": dict(status_c),
            "gap_secret_n": len(gap_secret),
            "gap_orders_n": len(gap_orders),
            "continue": continue_chain,
            "expand": expand,
            "live": live,
            "full": full_mode,
            "catalog_extend": catalog_extend,
            "catalog_extended_n": extended.get("entries_n") if catalog_extend else 0,
            "ext_hops_attached": extended.get("hops_attached") if catalog_extend else 0,
            "events_written": events_n,
            "continue_summary": cont_summary,
        },
        "backends": backends,
        "contracts_sample": contracts[:12],
        "buucuc_nodes": nodes,
        "chains": matched,
        "continue": cont_summary if continue_chain else None,
        "gaps": {
            "missing_secret": [
                {"backend": c.get("backend"), "secret": c.get("secret"), "chain": c.get("chain")}
                for c in gap_secret[:10]
            ],
            "contract_no_orders": [
                {
                    "backend": c.get("backend"),
                    "provider": c.get("provider"),
                    "contracts_n": c.get("contracts_n"),
                    "chain": c.get("chain"),
                }
                for c in gap_orders[:10]
            ],
        },
        "verdict": (
            f"🏷 Catalog BC chuỗi{tag}: matched={len(matched)}/{len(all_chains)} · "
            f"aship={len(catalog.get('providers') or [])} · "
            f"ext={extended.get('entries_n') if catalog_extend else 0} · "
            f"HĐ={len(contracts)} · nodes={len(nodes)} · "
            f"gap_secret={len(gap_secret)} · gap_0đơn={len(gap_orders)}"
            + (
                f" · hops={cont_summary.get('chains_continued')} "
                f"ext_hops={cont_summary.get('ext_hops_attached')} "
                f"actions={cont_summary.get('actions_n')}"
                + (
                    f" · branches={cont_summary.get('branch_rows')} "
                    f"live_ok={cont_summary.get('live_ok_chains')} "
                    f"hubs={cont_summary.get('hubs_n')} "
                    f"events={events_n}"
                    if expand or full_mode
                    else ""
                )
                if continue_chain
                else ""
            )
        ),
        "next": [
            "python3 scripts/buucuc_catalog_chain_query_mapper.py --chain full --notify",
            "python3 scripts/buucuc_catalog_chain_query_mapper.py --full --notify",
            "python3 scripts/buucuc_catalog_chain_query_mapper.py --chain catalog_ext --notify",
            "python3 scripts/buucuc_backend_per_hub_mapper.py --notify",
            "python3 scripts/pipe_branch_mapper.py --notify",
            "python3 scripts/scan_buucuc_orders.py --days 3 --notify",
        ],
    }
    return report


def format_text(report: dict[str, Any]) -> str:
    if not report.get("ok"):
        return f"❌ {report.get('error')}"
    L: list[str] = []
    A = L.append
    st = report.get("stats") or {}
    q = report.get("query") or {}
    A("🏷 MAPPER TRUY VẤN CHUỖI CATALOG BƯU CỤC")
    A(f"Lúc: {report.get('checked_at')}")
    A(f"Verdict: {report.get('verdict')}")
    A(f"Atlas: {report.get('atlas')}")
    A(
        f"Query: chain={q.get('chain')} · full={q.get('full')} · continue={q.get('continue')} · "
        f"expand={q.get('expand')} · live={q.get('live')} · catalog_extend={q.get('catalog_extend')} · "
        f"q={q.get('q')} · buucuc={q.get('buucuc')} · backend={q.get('backend')}"
    )
    if q.get("chain_title"):
        A(f"Preset: {q.get('chain_title')}")
    A(
        f"Stats: pipe={st.get('pipe_orders')} · backends={st.get('backends')} · "
        f"HĐ={st.get('contracts')} · nodes={st.get('buucuc_nodes')} · "
        f"chains={st.get('chains_matched')}/{st.get('chains_total')} · "
        f"gap_secret={st.get('gap_secret_n')} gap_0đơn={st.get('gap_orders_n')}"
    )
    cont = report.get("continue") or st.get("continue_summary") or {}
    if cont:
        A(
            f"Continue: hops={cont.get('chains_continued')} ext_hops={cont.get('ext_hops_attached')} "
            f"actions={cont.get('actions_n')}"
            + (
                f" · branches={cont.get('branch_rows')} live_ok={cont.get('live_ok_chains')} "
                f"hubs={cont.get('hubs_n')} events={cont.get('events_written')}"
                if cont.get("expand") or cont.get("full")
                else ""
            )
        )
    A("")
    cat = report.get("catalog") or {}
    A(
        f"=== Catalog Aship === http={cat.get('http')} ok={cat.get('ok')} "
        f"providers={cat.get('providers_n')} · owned_in_catalog={cat.get('owned_in_public_catalog')}"
    )
    for p in cat.get("providers") or []:
        A(f"  · {p.get('provider')} id={p.get('id')} keys={p.get('config_keys')}")
    ext = report.get("catalog_extended") or {}
    if ext.get("entries"):
        A("")
        A(
            f"=== Catalog BC mở rộng (thống nhất) === entries={ext.get('entries_n')} · "
            f"aship_public={ext.get('aship_public_n')} · pipe_only={ext.get('pipe_only_n')} · "
            f"owned_slots={ext.get('owned_slots_n')} · by_status={ext.get('by_status')} · "
            f"by_source={ext.get('by_source')}"
        )
        icon_e = {
            "ok": "✅",
            "catalog_only": "⚪",
            "missing_secret": "❌",
            "contract_no_orders": "🟠",
            "unassigned_or_unknown": "🟠",
            "shop_config_slot": "🟡",
            "unknown": "·",
        }
        for e in (ext.get("entries") or [])[:25]:
            ic = icon_e.get(str(e.get("status")), "·")
            A(
                f"  {ic} [{e.get('status')}] {e.get('key')} · src={'+'.join(e.get('sources') or [])}"
            )
            A(f"      {e.get('chain')}")
            if e.get("owned_config_slot"):
                A(
                    f"      owned_slot={e.get('owned_config_slot')} "
                    f"id={e.get('owned_config_id')} in_public={e.get('in_aship_public')}"
                )
            if e.get("secret"):
                A(f"      secret={e.get('secret')} present={e.get('secret_present')}")
            hops = e.get("continue_hops") or {}
            if hops:
                A(f"      → hop: {hops.get('continued_chain')}")
                live_p = hops.get("live") or {}
                if live_p and not live_p.get("skipped"):
                    A(f"      live: ok={live_p.get('ok_n')}/{live_p.get('probe_n')}")
                    for p in (live_p.get("probes") or [])[:2]:
                        A(
                            f"        · {p.get('id')} ok={p.get('ok', p.get('status'))} "
                            f"http={p.get('http')} order={p.get('order_code')}"
                        )
                br = hops.get("branches") or {}
                if br.get("branch_n"):
                    A(f"      branches×{br.get('branch_n')}: {br.get('pipe_sources')}")
            for a in (e.get("next") or [])[:2]:
                A(f"      next▸ {a}")
    hub = report.get("hub") or {}
    if hub:
        A("")
        A(
            f"=== Hub backend·từng BC === ok={hub.get('ok')} hubs={hub.get('hubs_n')} · "
            f"{hub.get('verdict')}"
        )
        for h in (hub.get("hubs_sample") or [])[:10]:
            A(
                f"  · {h.get('buucuc')} → {h.get('primary_backend')} · "
                f"orders={h.get('orders')} oms={h.get('oms')} HĐ={h.get('contracts_n')}"
            )
    A("")
    mode_label = ""
    if q.get("full"):
        mode_label = " · FULL"
    elif q.get("expand"):
        mode_label = " · MỞ RỘNG"
    elif q.get("continue"):
        mode_label = " · TIẾP TỤC"
    elif q.get("catalog_extend"):
        mode_label = " · CATALOG EXT"
    A("=== Chuỗi khớp" + mode_label + " ===")
    icon = {
        "ok": "✅",
        "catalog_only": "⚪",
        "missing_secret": "❌",
        "contract_no_orders": "🟠",
        "unassigned_or_unknown": "🟠",
        "empty_node": "⚪",
    }
    for c in (report.get("chains") or [])[:20]:
        ic = icon.get(str(c.get("status")), "·")
        hops = c.get("continue_hops") or {}
        A(f"  {ic} [{c.get('status')}] {c.get('chain')}")
        A(
            f"      backend={c.get('backend')} secret={c.get('secret')} "
            f"present={c.get('secret_present')} HĐ={c.get('contracts_n')} "
            f"orders={c.get('orders_n')}"
        )
        for ct in (c.get("contracts") or [])[:2]:
            A(
                f"      HĐ shop={ct.get('shop_id')} {ct.get('shop_name')} · "
                f"acc={ct.get('account_name')} · partner={ct.get('partner_name')}"
            )
        os_ = c.get("order_stats") or {}
        if os_:
            A(
                f"      order_stats: n={os_.get('orders')} track={os_.get('with_tracking')} · "
                f"kho={[(x.get('kho'), x.get('n')) for x in (os_.get('kho_top') or [])[:3]]}"
            )
        if hops:
            tr = hops.get("tracking") or {}
            A(f"      → continue: {hops.get('continued_chain')}")
            A(
                f"      track: code={tr.get('with_tracking_code')} url={tr.get('with_tracking_url')} "
                f"aship={tr.get('aship_url')} cov={tr.get('coverage_pct')}% · "
                f"prov={[(x.get('p'), x.get('n')) for x in (tr.get('providers') or [])[:4]]}"
            )
            ssr = hops.get("ssr_events") or {}
            A(
                f"      ssr_rows={ssr.get('ssr_rows')} · "
                f"events_head={[(e.get('event'), e.get('n')) for e in (ssr.get('pipe_events_head') or [])[:4]]}"
            )
            for fl in (hops.get("flow_samples") or [])[:2]:
                A(f"      flow×{fl.get('n')}: {(fl.get('flow_path') or '')[:140]}")
            br = hops.get("branches") or {}
            if br.get("branches"):
                A(f"      branches×{br.get('branch_n')}: pipes={br.get('pipe_sources')}")
                for b in (br.get("branches") or [])[:3]:
                    A(f"        · {b.get('path')}")
            sk = hops.get("shop_kho") or {}
            if sk.get("shops_top"):
                A(
                    f"      shops={[(x.get('shop'), x.get('n')) for x in (sk.get('shops_top') or [])[:4]]}"
                )
            live_p = hops.get("live") or {}
            if live_p and not live_p.get("skipped"):
                A(f"      live: ok={live_p.get('ok_n')}/{live_p.get('probe_n')}")
                for p in (live_p.get("probes") or [])[:3]:
                    A(
                        f"        · {p.get('id')} ok={p.get('ok', p.get('status'))} "
                        f"http={p.get('http')} code={p.get('code')} order={p.get('order_code')}"
                    )
            for a in (hops.get("next_actions") or [])[:3]:
                A(f"      next▸ {a}")
    A("")
    if cont.get("top_next"):
        A("=== Next ưu tiên (gộp) ===")
        for a in cont["top_next"][:8]:
            A(f"  · ×{a.get('n')} {a.get('action')}")
        A("")
    gaps = report.get("gaps") or {}
    if gaps.get("missing_secret"):
        A("=== Gap secret ===")
        for g in gaps["missing_secret"][:8]:
            A(f"  ❌ {g.get('backend')} · {g.get('secret')} · {g.get('chain')}")
    if gaps.get("contract_no_orders"):
        A("=== Gap HĐ→0 đơn ===")
        for g in gaps["contract_no_orders"][:8]:
            A(f"  🟠 {g.get('backend')}/{g.get('provider')} HĐ={g.get('contracts_n')} · {g.get('chain')}")
    A("")
    A("=== Presets --chain ===")
    for k, v in (report.get("chain_presets") or {}).items():
        A(f"  · {k}: {v.get('title')}")
    A("")
    for n in report.get("next") or []:
        A(f"Next: {n}")
    return "\n".join(L)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "buucuc_catalog_chain_query_mapper.json"
    tp = REPORTS / "buucuc_catalog_chain_query_mapper.txt"
    mp = REPORTS / "buucuc_catalog_chain_query_mapper.mermaid.md"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    mp.write_text("```mermaid\n" + (report.get("mermaid") or "") + "```\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp), "mermaid": str(mp)}


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
    ap = argparse.ArgumentParser(description="Mapper truy vấn chuỗi catalog bưu cục")
    ap.add_argument("--q", help="Free-text query across chain")
    ap.add_argument("--buucuc", help="Filter by buucuc name")
    ap.add_argument("--backend", help="Filter by backend id")
    ap.add_argument("--provider", help="Filter by Aship/catalog provider")
    ap.add_argument(
        "--chain",
        choices=sorted(CHAIN_QUERIES.keys()),
        default="all",
        help="Preset chuỗi query",
    )
    ap.add_argument(
        "--continue",
        dest="continue_chain",
        action="store_true",
        help="Tiếp tục chuỗi: tracking → SSR → flow → next actions",
    )
    ap.add_argument(
        "--expand",
        action="store_true",
        help="Mở rộng: continue + nhánh pipe + shop/kho + ghi events (+ --live)",
    )
    ap.add_argument(
        "--catalog-extend",
        action="store_true",
        help="Mở rộng catalog thống nhất Aship+pipe+HĐ+ConfigId",
    )
    ap.add_argument(
        "--full",
        action="store_true",
        help="Chuỗi đầy đủ: catalog_ext + continue + expand + live + hub",
    )
    ap.add_argument(
        "--live",
        action="store_true",
        help="Trong --expand/--full: probe GHN/SSR owned nhẹ",
    )
    ap.add_argument("--no-events", action="store_true", help="Không ghi pipe_events khi expand/full")
    ap.add_argument("--list-chains", action="store_true")
    ap.add_argument("--enrich-limit", type=int, default=12)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)

    if args.list_chains:
        for k, v in CHAIN_QUERIES.items():
            print(f"{k:14} {v.get('title')} · {v.get('hint') or ''}")
        return 0

    full = bool(args.full) or args.chain == "full"
    report = build_report(
        q=args.q,
        buucuc=args.buucuc,
        backend=args.backend,
        provider=args.provider,
        chain_id="full" if full else args.chain,
        enrich_limit=args.enrich_limit,
        continue_chain=full
        or bool(args.continue_chain)
        or args.chain in {"continue", "expand", "full"},
        expand=full or bool(args.expand) or args.chain in {"expand", "full"},
        live=full or bool(args.live) or args.chain in {"expand", "full"},
        write_events=not args.no_events,
        catalog_extend=full
        or bool(args.catalog_extend)
        or args.chain in {"catalog_ext", "expand", "full"}
        or bool(args.expand),
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
