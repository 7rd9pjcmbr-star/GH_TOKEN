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
  --continue | --list-chains | --notify | --json

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
    "continue": {
        "title": "Tiếp tục chuỗi → tracking · SSR · flow · next",
        "hint": "hop sau orders/kho/shop: tracking_url → aship SSR → pipe_events → next CLI",
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
    if int(c.get("orders_n") or 0) > 0:
        track = hops.get("tracking") or {}
        if int(track.get("with_tracking_url") or 0) < int(track.get("with_tracking_code") or 0):
            acts.append("python3 scripts/tracking_aship.py --notify  # gắn tracking_url thiếu")
        if be in {"GHN", "SPX-local", "J&T", "Pancake"} and buu:
            acts.append(f"python3 scripts/buucuc_catalog_chain_query_mapper.py --buucuc {buu.split('/')[0]} --continue")
        if any(
            str(p.get("p") or "").lower() in {"viettelpost", "best", "vtp"}
            for p in (track.get("providers") or [])
        ) or be in {"ViettelPost", "Best"}:
            acts.append("python3 scripts/tpos_ssr_pipe.py --providers viettelpost,best --limit 40 --notify")
    if not acts:
        acts.append("python3 scripts/buucuc_catalog_chain_query_mapper.py --chain all --continue --notify")
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for a in acts:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out[:6]


def continue_chain_hops(
    chains: list[dict[str, Any]],
    *,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Tiếp tục mỗi chuỗi: tracking → SSR/events → flow_path → next actions."""
    out: list[dict[str, Any]] = []
    for i, c in enumerate(chains[:limit]):
        buu = _primary_buucuc(c)
        be = str(c.get("backend") or "") if c.get("backend") else None
        tracking = hop_tracking(buu, be)
        ssr = hop_ssr_events(buu)
        flows = hop_flow_samples(buu, limit=4)
        hops = {
            "tracking": tracking,
            "ssr_events": ssr,
            "flow_samples": flows,
        }
        hops["next_actions"] = next_actions_for_chain(c, hops)
        hops["continued_chain"] = chain_text(
            [
                c.get("chain"),
                f"track:{tracking.get('with_tracking_code')}/{tracking.get('orders')}({tracking.get('coverage_pct')}%)",
                f"aship_url:{tracking.get('aship_url')}",
                f"ssr:{ssr.get('ssr_rows')}",
                f"flow×{len(flows)}",
            ]
        )
        row = dict(c)
        row["continue_hops"] = hops
        out.append(row)
    # keep remainder without deep hops
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


def build_report(
    *,
    q: str | None = None,
    buucuc: str | None = None,
    backend: str | None = None,
    provider: str | None = None,
    chain_id: str | None = None,
    enrich_limit: int = 12,
    continue_chain: bool = False,
) -> dict[str, Any]:
    env = load_env()
    if not PIPE_DB.is_file():
        return {"ok": False, "error": f"missing {PIPE_DB}", "checked_at": utc_now()}

    # preset "continue" ⇒ continue mode on all chains
    if chain_id == "continue":
        continue_chain = True
        chain_id = "all"

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
    if continue_chain:
        matched = build_continue_priority(matched)
    matched = enrich_order_stats(matched, limit=enrich_limit)
    if continue_chain:
        matched = continue_chain_hops(matched, limit=max(enrich_limit, 15))

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
        "chain": "continue" if continue_chain and (chain_id or "all") == "all" else (chain_id or "all"),
        "continue": continue_chain,
        "chain_title": (
            CHAIN_QUERIES["continue"]["title"]
            if continue_chain
            else (CHAIN_QUERIES.get(chain_id or "all") or {}).get("title")
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
    if continue_chain:
        atlas += " → tracking → aship SSR → pipe_events/flow → next"
        mermaid += (
            "  ORD --> TRK[tracking_code/url]\n"
            "  TRK --> SSR[tracking.aship SSR]\n"
            "  SSR --> EV[pipe_events]\n"
            "  ORD --> FLOW[flow_path]\n"
            "  EV --> NEXT[next CLI]\n"
            "  FLOW --> NEXT\n"
        )

    # continue summary
    cont_summary: dict[str, Any] = {}
    if continue_chain:
        with_hops = [c for c in matched if c.get("continue_hops")]
        cont_summary = {
            "chains_continued": len(with_hops),
            "actions_n": sum(
                len((c.get("continue_hops") or {}).get("next_actions") or []) for c in with_hops
            ),
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
                }
                for c in with_hops[:12]
            ],
            "top_next": [],
        }
        # flatten unique next actions by frequency
        act_c: Counter[str] = Counter()
        for c in with_hops:
            for a in (c.get("continue_hops") or {}).get("next_actions") or []:
                act_c[a] += 1
        cont_summary["top_next"] = [{"action": a, "n": n} for a, n in act_c.most_common(10)]

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
            f"🏷 Catalog BC chuỗi{(' · TIẾP TỤC' if continue_chain else '')}: "
            f"matched={len(matched)}/{len(all_chains)} · "
            f"catalog={len(catalog.get('providers') or [])} · "
            f"HĐ={len(contracts)} · nodes={len(nodes)} · "
            f"gap_secret={len(gap_secret)} · gap_0đơn={len(gap_orders)}"
            + (
                f" · hops={cont_summary.get('chains_continued')} "
                f"actions={cont_summary.get('actions_n')}"
                if continue_chain
                else ""
            )
        ),
        "next": [
            "python3 scripts/buucuc_catalog_chain_query_mapper.py --continue --notify",
            "python3 scripts/buucuc_catalog_chain_query_mapper.py --chain continue --notify",
            "python3 scripts/buucuc_catalog_chain_query_mapper.py --chain gap_secret --continue --notify",
            "python3 scripts/buucuc_catalog_chain_query_mapper.py --chain ghn --continue --notify",
            "python3 scripts/tpos_ssr_pipe.py --notify",
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
        f"Query: chain={q.get('chain')} · continue={q.get('continue')} · q={q.get('q')} · "
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
    cont = report.get("continue") or st.get("continue_summary") or {}
    if cont:
        A(
            f"Continue: hops={cont.get('chains_continued')} actions={cont.get('actions_n')}"
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
    A("=== Chuỗi khớp" + (" · TIẾP TỤC" if q.get("continue") else "") + " ===")
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
            A(
                f"      → continue: {hops.get('continued_chain')}"
            )
            A(
                f"      track: code={tr.get('with_tracking_code')} url={tr.get('with_tracking_url')} "
                f"aship={tr.get('aship_url')} cov={tr.get('coverage_pct')}% · "
                f"prov={[(x.get('p'), x.get('n')) for x in (tr.get('providers') or [])[:4]]}"
            )
            ssr = hops.get("ssr_events") or {}
            A(f"      ssr_rows={ssr.get('ssr_rows')} · events_head={[(e.get('event'), e.get('n')) for e in (ssr.get('pipe_events_head') or [])[:4]]}")
            for fl in (hops.get("flow_samples") or [])[:2]:
                A(f"      flow×{fl.get('n')}: {(fl.get('flow_path') or '')[:140]}")
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
        continue_chain=bool(args.continue_chain) or args.chain == "continue",
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
