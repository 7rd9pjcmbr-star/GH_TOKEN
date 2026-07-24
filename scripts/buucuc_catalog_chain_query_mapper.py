#!/usr/bin/env python3
"""Mapper truy vấn chuỗi catalog bưu cục.

Chuỗi:
  Aship ShippingProviderConfigs (catalog ĐVVC)
    → owned ConfigId / secret slot
    → backends catalog tip
    → contracts (HĐ ĐVVC · shop · account)
    → buucuc_nodes
    → orders → kho → shop

CLI query:
  --q TEXT | --buucuc | --backend | --provider | --chain
  --list-chains | --notify | --json

Policy: owned secrets only · no dump-login · reports gitignored.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
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


def build_report(
    *,
    q: str | None = None,
    buucuc: str | None = None,
    backend: str | None = None,
    provider: str | None = None,
    chain_id: str | None = None,
    enrich_limit: int = 12,
) -> dict[str, Any]:
    env = load_env()
    if not PIPE_DB.is_file():
        return {"ok": False, "error": f"missing {PIPE_DB}", "checked_at": utc_now()}

    catalog = fetch_aship_catalog(env)
    backends = load_backends(env)
    contracts = load_contracts()
    nodes = load_buucuc_nodes()
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
    matched = enrich_order_stats(matched, limit=enrich_limit)

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

    query_meta = {
        "q": q,
        "buucuc": buucuc,
        "backend": backend,
        "provider": provider,
        "chain": chain_id or "all",
        "chain_title": (CHAIN_QUERIES.get(chain_id or "all") or {}).get("title"),
    }

    report: dict[str, Any] = {
        "ok": True,
        "module": "buucuc_catalog_chain_query_mapper",
        "checked_at": utc_now(),
        "policy": "owned secrets only · no dump-login · reports gitignored",
        "atlas": (
            "ShippingProviderConfigs → ConfigId/secret → backends → contracts → "
            "buucuc_nodes → orders → kho/shop"
        ),
        "mermaid": (
            "flowchart LR\n"
            "  CAT[Aship ShippingProviderConfigs] --> CFG[ConfigId / secret]\n"
            "  CFG --> BE[backends catalog]\n"
            "  BE --> HD[contracts HĐ]\n"
            "  HD --> NODE[buucuc_nodes]\n"
            "  NODE --> ORD[orders]\n"
            "  ORD --> KHO[kho]\n"
            "  ORD --> SHOP[shop]\n"
        ),
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
        },
        "backends": backends,
        "contracts_sample": contracts[:12],
        "buucuc_nodes": nodes,
        "chains": matched,
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
            f"🏷 Catalog BC chuỗi: matched={len(matched)}/{len(all_chains)} · "
            f"catalog={len(catalog.get('providers') or [])} · "
            f"HĐ={len(contracts)} · nodes={len(nodes)} · "
            f"gap_secret={len(gap_secret)} · gap_0đơn={len(gap_orders)}"
        ),
        "next": [
            "python3 scripts/buucuc_catalog_chain_query_mapper.py --chain ghn --notify",
            "python3 scripts/buucuc_catalog_chain_query_mapper.py --q Viettel --notify",
            "python3 scripts/buucuc_catalog_chain_query_mapper.py --chain gap_secret --notify",
            "python3 scripts/buucuc_catalog_chain_query_mapper.py --buucuc J&T --notify",
            "python3 scripts/buucuc_catalog_chain_query_mapper.py --list-chains",
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
        f"Query: chain={q.get('chain')} · q={q.get('q')} · "
        f"buucuc={q.get('buucuc')} · backend={q.get('backend')} · provider={q.get('provider')}"
    )
    if q.get("chain_title"):
        A(f"Preset: {q.get('chain_title')}")
    A(
        f"Stats: pipe={st.get('pipe_orders')} · backends={st.get('backends')} · "
        f"HĐ={st.get('contracts')} · nodes={st.get('buucuc_nodes')} · "
        f"chains={st.get('chains_matched')}/{st.get('chains_total')} · "
        f"gap_secret={st.get('gap_secret_n')} gap_0đơn={st.get('gap_orders_n')}"
    )
    A("")
    cat = report.get("catalog") or {}
    A(
        f"=== Catalog Aship === http={cat.get('http')} ok={cat.get('ok')} "
        f"providers={cat.get('providers_n')} · owned_in_catalog={cat.get('owned_in_public_catalog')}"
    )
    for p in cat.get("providers") or []:
        A(f"  · {p.get('provider')} id={p.get('id')} keys={p.get('config_keys')}")
    A("")
    A("=== Chuỗi khớp ===")
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
    ap.add_argument("--list-chains", action="store_true")
    ap.add_argument("--enrich-limit", type=int, default=12)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)

    if args.list_chains:
        for k, v in CHAIN_QUERIES.items():
            print(f"{k:14} {v.get('title')} · {v.get('hint') or ''}")
        return 0

    report = build_report(
        q=args.q,
        buucuc=args.buucuc,
        backend=args.backend,
        provider=args.provider,
        chain_id=args.chain,
        enrich_limit=args.enrich_limit,
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
