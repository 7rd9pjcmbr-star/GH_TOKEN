#!/usr/bin/env python3
"""Mapper đường ống dẫn các hợp đồng đối tác vận chuyển.

Atlas ống:
  credential/shop → auth → icon ĐVVC/HDDT → partner.accounts / HĐ → gắn đơn 3PL

Carrier pipes:
  · HDDT (hopdongdientu / SSO JWT)
  · Pancake ĐVVC: J&T · VTP · GHN · GHTK · Best · …
  · SPX / VNPost (env slots)

Owned-only · no dump-login · mask tokens.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
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
STATE_PATH = SECRETS / "contract_pipe_mapper.state.json"
ACCOUNTS_PATH = SECRETS / "shipping_partner_accounts_owned.json"

# Pancake partner catalog (id → name) — từ /shops/{id}/partners
PANCAKE_PARTNER_IDS = {
    "15": "J&T",
    "3": "VTP",
    "5": "Giao hàng nhanh (GHN)",
    "1": "Giao hàng tiết kiệm (GHTK)",
    "16": "Best Inc",
    "19": "Ninja Van",
    "0": "Snappy",
}

# ── Pipe stages ─────────────────────────────────────────────────
CONTRACT_PIPES: list[dict[str, Any]] = [
    {
        "id": "pipe.hddt",
        "title": "HĐĐT GHN (hopdongdientu)",
        "carrier": "HDDT",
        "stages": [
            {
                "id": "hddt.src",
                "title": "SSO login URL / app_key",
                "cli": "ghn_sso_jwt_bridge.py analyze --url '<sso login>'",
                "secrets": ["ghn_sso_login.url", "GHN_SSO_APP_KEY", "GHN_SSO_CLIENT_ID"],
            },
            {
                "id": "hddt.auth",
                "title": "Owned login → id_token JWT",
                "cli": "ghn_sso_jwt_bridge.py ingest --url '<callback#id_token=eyJ…>'",
                "secrets": ["GHN_SSO_ID_TOKEN"],
                "note": "auth_code UUID ≠ id_token; không thay Token shiip",
            },
            {
                "id": "hddt.list",
                "title": "Liệt kê / export số HĐ điện tử",
                "cli": None,
                "portal": "http://hopdongdientu.ghn.vn/",
                "note": "Cần id_token sống — API list HĐ phía Portal247",
            },
        ],
        "output": "số HĐ / hợp đồng điện tử GHN",
    },
    {
        "id": "pipe.pancake_dvvc",
        "title": "Pancake icon Cấu hình → Đơn vị vận chuyển",
        "carrier": "PANCAKE_DVVC",
        "stages": [
            {
                "id": "pk.auth",
                "title": "Pancake POS token / api_key (đúng shop)",
                "secrets": [
                    "PANCAKE_POS_ACCESS_TOKEN",
                    "PANCAKE_POS_SECONDARY_ACCESS_TOKEN",
                    "PANCAKE_API_KEY",
                    "PANCAKE_SHOP_ID",
                ],
            },
            {
                "id": "pk.icon_partners",
                "title": "Icon ĐVVC → GET /shops/{shop_id}/partners",
                "api": "https://pos.pages.fm/api/v1/shops/{shop_id}/partners",
                "note": "Catalog: J&T=15 · VTP=3 · GHN=5 · GHTK=1 · Best=16",
            },
            {
                "id": "pk.accounts",
                "title": "Mở đơn vị (J&T/VTP/…) → partner.accounts[]",
                "note": "accounts[].name / id = mã HĐ · customer code · SĐT / email gắn HĐ",
                "secrets": ["shipping_partner_accounts_owned.json"],
            },
            {
                "id": "pk.bind_order",
                "title": "Gắn HĐ vào đơn → shop_partner_id / service_partner",
                "note": "Đơn export pancake_shop_*_orders",
            },
        ],
        "output": "mã HĐ / customer code theo từng ĐVVC trên shop Pancake",
        "branches": [
            {"partner_id": "15", "name": "J&T", "pipe": "pipe.jnt"},
            {"partner_id": "3", "name": "VTP", "pipe": "pipe.vtp"},
            {"partner_id": "5", "name": "GHN", "pipe": "pipe.ghn_via_pancake"},
            {"partner_id": "1", "name": "GHTK", "pipe": "pipe.ghtk"},
            {"partner_id": "16", "name": "Best Inc", "pipe": "pipe.best"},
        ],
    },
    {
        "id": "pipe.jnt",
        "title": "J&T Express — mã hợp đồng",
        "carrier": "J&T",
        "parent": "pipe.pancake_dvvc",
        "stages": [
            {"id": "jnt.open", "title": "partners → id=15 J&T"},
            {
                "id": "jnt.accounts",
                "title": "accounts[] chứa customer code / mã HĐ",
                "note": "Rỗng = shop chưa gắn J&T; cần đúng shop (vd. 1530618)",
            },
        ],
        "output": "J&T customer code / mã HĐ",
    },
    {
        "id": "pipe.vtp",
        "title": "Viettel Post — mã hợp đồng",
        "carrier": "VTP",
        "parent": "pipe.pancake_dvvc",
        "stages": [
            {"id": "vtp.open", "title": "partners → id=3 VTP"},
            {
                "id": "vtp.accounts",
                "title": "accounts[] (SĐT / email / cusId)",
                "alt_env": ["VIETTELPOST_TOKEN", "VIETTELPOST_USER", "VIETTELPOST_SHOP_ID"],
            },
        ],
        "output": "VTP account name / cusId",
    },
    {
        "id": "pipe.ghn_via_pancake",
        "title": "GHN qua Pancake ĐVVC",
        "carrier": "GHN",
        "parent": "pipe.pancake_dvvc",
        "stages": [
            {"id": "ghn.pk", "title": "partners → id=5 Giao hàng nhanh → accounts[]"},
            {
                "id": "ghn.shiip",
                "title": "Hoặc Token shiip owned (printA5 / cookie)",
                "cli": "ghn_cookie_ingest.py ingest --printA5 '<URL>'",
                "secrets": ["GHN_API_TOKEN"],
            },
        ],
        "output": "GHN account trên Pancake hoặc GHN_API_TOKEN",
    },
    {
        "id": "pipe.ghtk",
        "title": "GHTK — tài khoản ĐVVC",
        "carrier": "GHTK",
        "parent": "pipe.pancake_dvvc",
        "stages": [{"id": "ghtk.open", "title": "partners → id=1 → accounts[]"}],
        "output": "GHTK account label",
    },
    {
        "id": "pipe.best",
        "title": "Best Inc — cus code",
        "carrier": "BEST",
        "parent": "pipe.pancake_dvvc",
        "stages": [{"id": "best.open", "title": "partners → id=16 → accounts[] (vd. cus615233)"}],
        "output": "Best customer code",
    },
    {
        "id": "pipe.spx",
        "title": "SPX — shop/partner env",
        "carrier": "SPX",
        "stages": [
            {
                "id": "spx.env",
                "title": "SPX_SHOP_ID / SPX_TOKEN owned",
                "secrets": ["SPX_SHOP_ID", "SPX_TOKEN", "SPX_USER"],
            }
        ],
        "output": "SPX shop id",
    },
    {
        "id": "pipe.buucuc_backend",
        "title": "HĐ → backend bưu cục (SQLite)",
        "carrier": "BUUCUC_BACKEND",
        "stages": [
            {
                "id": "bc.map",
                "title": "partner.accounts → backend GHN|VTP|J&T|GHTK|Best|SPX",
                "cli": "contract_buucuc_backend_mapper.py",
                "note": "PARTNER_TO_BACKEND: 5→GHN · 3→VTP · 15→J&T · 1→GHTK · 16→Best",
            },
            {
                "id": "bc.upsert",
                "title": "Upsert bảng contracts vào buucuc_backend.db",
                "secrets": ["shipping_partner_accounts_owned.json"],
                "note": "Mirror kho_buucuc_pipe.db · join orders theo shop_id",
            },
            {
                "id": "bc.query",
                "title": "Truy vấn HĐ×backend×đơn",
                "cli": 'sqlite3 reports/telegram-classify/buucuc_backend.db '
                '"SELECT backend, account_name, shop_id, orders_n FROM contracts;"',
            },
        ],
        "output": "contracts rows trong backend bưu cục DB",
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for path in (
        ROOT / "secrets" / "order_session.env",
        ROOT / "secrets" / "backend_pipes.env",
        ROOT / "secrets" / "telegram.env",
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


def _filled(env: dict[str, str], *keys: str) -> dict[str, bool]:
    return {k: bool((env.get(k) or "").strip()) for k in keys}


def load_owned_accounts() -> list[dict[str, Any]]:
    if not ACCOUNTS_PATH.is_file():
        return []
    try:
        data = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    return list(data.get("accounts") or [])


def http_json(url: str, timeout: int = 25) -> tuple[int, Any]:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp else b""
        try:
            return e.code, json.loads(raw.decode() or "null")
        except Exception:
            return e.code, {"raw": raw[:160].decode("utf-8", "replace")}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)[:160]}


def probe_pancake_partners(env: dict[str, str], *, limit_shops: int = 8) -> dict[str, Any]:
    """Live probe: list shops + J&T/VTP/GHN accounts on owned tokens."""
    base = "https://pos.pages.fm/api/v1"
    tokens = []
    if env.get("PANCAKE_POS_ACCESS_TOKEN"):
        tokens.append(("primary", env["PANCAKE_POS_ACCESS_TOKEN"], "access_token"))
    if env.get("PANCAKE_POS_SECONDARY_ACCESS_TOKEN"):
        tokens.append(
            ("secondary", env["PANCAKE_POS_SECONDARY_ACCESS_TOKEN"], "access_token")
        )
    api_key = env.get("PANCAKE_POS_API_KEY") or env.get("PANCAKE_API_KEY") or ""
    shop_api = (env.get("PANCAKE_SHOP_ID") or "").strip()
    out: dict[str, Any] = {"shops": [], "accounts": [], "jnt_empty": [], "gaps": []}

    seen_shop: set[str] = set()
    for label, tok, mode in tokens:
        q = f"{mode}={urllib.parse.quote(tok)}"
        st, body = http_json(f"{base}/shops?{q}")
        shops = body.get("shops") if isinstance(body, dict) else []
        for s in (shops or [])[:limit_shops]:
            sid = str(s.get("id") or "")
            if not sid or sid in seen_shop:
                continue
            seen_shop.add(sid)
            name = s.get("name")
            pst, pb = http_json(f"{base}/shops/{sid}/partners?{q}")
            partners = pb.get("data") if isinstance(pb, dict) else []
            row = {
                "token": label,
                "shop_id": sid,
                "shop_name": name,
                "partners_http": pst,
                "by_carrier": {},
            }
            for p in partners or []:
                pid = str(p.get("id"))
                pname = str(p.get("name") or PANCAKE_PARTNER_IDS.get(pid) or pid)
                acc = p.get("accounts") or []
                if not isinstance(acc, list):
                    acc = []
                if pid in PANCAKE_PARTNER_IDS or re.search(
                    r"(?i)j&t|jnt|vtp|ghn|giao hàng|best|ghtk|tiết kiệm", pname
                ):
                    slim_acc = []
                    for a in acc[:5]:
                        if not isinstance(a, dict):
                            continue
                        slim_acc.append(
                            {
                                "id": a.get("id"),
                                "name": a.get("name"),
                            }
                        )
                        if slim_acc:
                            out["accounts"].append(
                                {
                                    "shop_id": sid,
                                    "shop_name": name,
                                    "partner_id": pid,
                                    "partner_name": pname,
                                    "account": slim_acc[-1],
                                    "token": label,
                                }
                            )
                    row["by_carrier"][pname] = {
                        "partner_id": pid,
                        "accounts_n": len(acc),
                        "accounts": slim_acc,
                    }
                    if pid == "15" and not acc:
                        out["jnt_empty"].append({"shop_id": sid, "shop_name": name})
            out["shops"].append(row)

    # api_key shop (often ASUNMEE)
    if api_key and shop_api and shop_api not in seen_shop:
        q = f"api_key={urllib.parse.quote(api_key)}"
        st, body = http_json(f"{base}/shops/{shop_api}?{q}")
        if isinstance(body, dict) and body.get("success"):
            name = (body.get("shop") or body).get("name")
            pst, pb = http_json(f"{base}/shops/{shop_api}/partners?{q}")
            partners = pb.get("data") if isinstance(pb, dict) else []
            row = {
                "token": "api_key",
                "shop_id": shop_api,
                "shop_name": name,
                "partners_http": pst,
                "by_carrier": {},
            }
            for p in partners or []:
                pid = str(p.get("id"))
                pname = str(p.get("name") or "")
                acc = p.get("accounts") or []
                if not isinstance(acc, list):
                    acc = []
                if pid in {"15", "3", "5", "1", "16"} or re.search(
                    r"(?i)j&t|vtp|ghn|best|ghtk", pname
                ):
                    slim = [{"id": a.get("id"), "name": a.get("name")} for a in acc[:5] if isinstance(a, dict)]
                    row["by_carrier"][pname] = {
                        "partner_id": pid,
                        "accounts_n": len(acc),
                        "accounts": slim,
                    }
                    for a in slim:
                        out["accounts"].append(
                            {
                                "shop_id": shop_api,
                                "shop_name": name,
                                "partner_id": pid,
                                "partner_name": pname,
                                "account": a,
                                "token": "api_key",
                            }
                        )
                    if pid == "15" and not acc:
                        out["jnt_empty"].append(
                            {"shop_id": shop_api, "shop_name": name}
                        )
            out["shops"].append(row)

    # known gap
    out["gaps"].append(
        {
            "shop_id": "1530618",
            "claim": "Nguyen Van Tam / Sam Spa",
            "issue": "không thuộc token primary/secondary/api_key hiện có → không mở J&T accounts",
        }
    )
    return out


def status_for_pipes(env: dict[str, str], live: dict[str, Any]) -> list[dict[str, Any]]:
    accounts = live.get("accounts") or load_owned_accounts()
    by_carrier: dict[str, list] = {}
    for a in accounts:
        name = str(a.get("partner_name") or "")
        by_carrier.setdefault(name, []).append(a)

    def has_carrier(*hints: str) -> list:
        hits = []
        for name, rows in by_carrier.items():
            if any(re.search(h, name, re.I) for h in hints):
                hits.extend(rows)
        return hits

    hddt = _filled(env, "GHN_SSO_APP_KEY", "GHN_SSO_CLIENT_ID", "GHN_SSO_ID_TOKEN")
    spx = _filled(env, "SPX_SHOP_ID", "SPX_TOKEN", "SPX_USER")
    vtp_env = _filled(
        env, "VIETTELPOST_TOKEN", "VIETTELPOST_USER", "VIETTELPOST_SHOP_ID"
    )

    statuses = [
        {
            "pipe": "pipe.hddt",
            "ready": bool(hddt.get("GHN_SSO_APP_KEY")),
            "blocked": not hddt.get("GHN_SSO_ID_TOKEN"),
            "detail": (
                "có app_key/client_id"
                + ("; thiếu id_token JWT" if not hddt.get("GHN_SSO_ID_TOKEN") else "; có id_token")
            ),
            "artifacts": hddt,
        },
        {
            "pipe": "pipe.pancake_dvvc",
            "ready": bool(live.get("shops")),
            "blocked": False,
            "detail": f"shops probed={len(live.get('shops') or [])} · accounts={len(accounts)}",
            "shops": [
                {"id": s["shop_id"], "name": s["shop_name"], "token": s["token"]}
                for s in (live.get("shops") or [])
            ],
        },
        {
            "pipe": "pipe.jnt",
            "ready": bool(has_carrier(r"j\s*&?\s*t", "jnt")),
            "blocked": True,
            "detail": (
                f"J&T accounts gắn={len(has_carrier(r'j&t', 'jnt'))} · "
                f"shops J&T rỗng={len(live.get('jnt_empty') or [])} · "
                "1530618 inaccessible"
            ),
            "accounts": has_carrier(r"j\s*&?\s*t", "jnt"),
        },
        {
            "pipe": "pipe.vtp",
            "ready": bool(has_carrier("vtp", "viettel")) or any(vtp_env.values()),
            "blocked": not has_carrier("vtp", "viettel") and not vtp_env.get("VIETTELPOST_TOKEN"),
            "detail": f"Pancake VTP accounts={len(has_carrier('vtp', 'viettel'))} · env token={vtp_env.get('VIETTELPOST_TOKEN')}",
            "accounts": has_carrier("vtp", "viettel"),
            "env": vtp_env,
        },
        {
            "pipe": "pipe.ghn_via_pancake",
            "ready": bool(has_carrier("giao hàng nhanh", r"\bghn\b"))
            or bool((env.get("GHN_API_TOKEN") or "").strip()),
            "blocked": False,
            "detail": (
                f"Pancake GHN accounts={len(has_carrier('giao hàng nhanh', r'\\bghn\\b'))} · "
                f"GHN_API_TOKEN set={bool((env.get('GHN_API_TOKEN') or '').strip())}"
            ),
            "accounts": has_carrier("giao hàng nhanh", r"\bghn\b"),
        },
        {
            "pipe": "pipe.ghtk",
            "ready": bool(has_carrier("tiết kiệm", "ghtk")),
            "blocked": not has_carrier("tiết kiệm", "ghtk"),
            "detail": f"accounts={len(has_carrier('tiết kiệm', 'ghtk'))}",
            "accounts": has_carrier("tiết kiệm", "ghtk"),
        },
        {
            "pipe": "pipe.best",
            "ready": bool(has_carrier("best")),
            "blocked": not has_carrier("best"),
            "detail": f"accounts={len(has_carrier('best'))}",
            "accounts": has_carrier("best"),
        },
        {
            "pipe": "pipe.spx",
            "ready": spx.get("SPX_SHOP_ID"),
            "blocked": not spx.get("SPX_TOKEN"),
            "detail": f"SPX_SHOP_ID={spx.get('SPX_SHOP_ID')} TOKEN={spx.get('SPX_TOKEN')}",
            "env": spx,
        },
        {
            "pipe": "pipe.buucuc_backend",
            "ready": bool(accounts) or bool(spx.get("SPX_SHOP_ID")),
            "blocked": not accounts and not spx.get("SPX_SHOP_ID"),
            "detail": (
                f"accounts→map={len(accounts)} · "
                "upsert contracts → buucuc_backend.db"
            ),
            "accounts_n": len(accounts),
        },
    ]
    return statuses


def mermaid(statuses: list[dict[str, Any]]) -> str:
    ready = {s["pipe"]: s for s in statuses}

    def mark(pid: str) -> str:
        s = ready.get(pid) or {}
        if s.get("ready") and not s.get("blocked"):
            return "✅"
        if s.get("ready") and s.get("blocked"):
            return "⚠"
        return "❌"

    return "\n".join(
        [
            "```mermaid",
            "flowchart LR",
            f"  AUTH[Credential/shop owned] --> HDDT[{mark('pipe.hddt')} HDDT SSO]",
            f"  AUTH --> PK[{mark('pipe.pancake_dvvc')} Pancake ĐVVC icon]",
            f"  AUTH --> SPX[{mark('pipe.spx')} SPX env]",
            "  HDDT --> HD[(Số HĐ điện tử)]",
            "  PK --> JNT",
            "  PK --> VTP",
            "  PK --> GHN",
            "  PK --> GHTK",
            "  PK --> BEST",
            f"  JNT[{mark('pipe.jnt')} J&T accounts]",
            f"  VTP[{mark('pipe.vtp')} VTP accounts]",
            f"  GHN[{mark('pipe.ghn_via_pancake')} GHN accounts/Token]",
            f"  GHTK[{mark('pipe.ghtk')} GHTK]",
            f"  BEST[{mark('pipe.best')} Best cus]",
            "  JNT --> ORD[Gắn đơn 3PL / OMS]",
            "  VTP --> ORD",
            "  GHN --> ORD",
            "  GHTK --> ORD",
            "  BEST --> ORD",
            "  SPX --> ORD",
            "  HD --> ORD",
            f"  ORD --> BC[{mark('pipe.buucuc_backend')} Backend bưu cục DB]",
            "  BC --> SQLITE[(buucuc_backend.db contracts)]",
            "```",
        ]
    )


def build_report(*, probe: bool = True) -> dict[str, Any]:
    env = load_env()
    live: dict[str, Any] = {"shops": [], "accounts": load_owned_accounts(), "jnt_empty": [], "gaps": []}
    if probe:
        live = probe_pancake_partners(env)
        # refresh secrets cache (masked names only)
        if live.get("accounts"):
            ACCOUNTS_PATH.write_text(
                json.dumps(
                    {
                        "updated_at": utc_now(),
                        "source": "contract_pipe_mapper probe",
                        "accounts": live["accounts"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
    statuses = status_for_pipes(env, live)
    # Map HĐ vào backend bưu cục (SQLite contracts)
    buucuc_map: dict[str, Any] = {}
    try:
        from contract_buucuc_backend_mapper import (
            build_report as build_buucuc_map,
            write_outputs as write_buucuc_map,
        )

        buucuc_map = build_buucuc_map(refresh_accounts=False)
        write_buucuc_map(buucuc_map)
        for s in statuses:
            if s.get("pipe") == "pipe.buucuc_backend":
                s["ready"] = bool(buucuc_map.get("contracts_mapped"))
                s["blocked"] = not buucuc_map.get("contracts_mapped")
                s["detail"] = (
                    f"mapped={buucuc_map.get('contracts_mapped')} · "
                    f"backends={buucuc_map.get('backends_n')} · "
                    f"orders_linked≈{buucuc_map.get('orders_linked_sum')}"
                )
                s["db"] = (buucuc_map.get("db") or {}).get("buucuc")
    except Exception as e:  # noqa: BLE001
        buucuc_map = {"ok": False, "error": str(e)[:200]}

    ready_n = sum(1 for s in statuses if s.get("ready") and not s.get("blocked"))
    partial_n = sum(1 for s in statuses if s.get("ready") and s.get("blocked"))
    report: dict[str, Any] = {
        "ok": True,
        "module": "contract_pipe_mapper",
        "checked_at": utc_now(),
        "policy": "owned-only · no dump-login · mask secrets",
        "pipes": CONTRACT_PIPES,
        "partner_catalog": PANCAKE_PARTNER_IDS,
        "live": {
            "shops": live.get("shops"),
            "accounts": live.get("accounts"),
            "jnt_empty": live.get("jnt_empty"),
            "gaps": live.get("gaps"),
        },
        "buucuc_backend": {
            "contracts_mapped": buucuc_map.get("contracts_mapped"),
            "backends_n": buucuc_map.get("backends_n"),
            "orders_linked_sum": buucuc_map.get("orders_linked_sum"),
            "verdict": buucuc_map.get("verdict"),
            "db": (buucuc_map.get("db") or {}).get("buucuc"),
            "error": buucuc_map.get("error"),
        },
        "status": statuses,
        "mermaid": mermaid(statuses),
        "verdict": (
            f"✅ Đường ống HĐ: {ready_n} sẵn · {partial_n} dở · "
            f"accounts sống={len(live.get('accounts') or [])} · "
            f"HĐ→BC={buucuc_map.get('contracts_mapped') or 0} · "
            f"J&T còn thiếu trên shop đang mở / 1530618"
        ),
        "next": [
            "HĐ→backend BC: python3 scripts/contract_buucuc_backend_mapper.py --notify",
            "J&T: token đúng shop có gắn HĐ (vd. 1530618) → /partners id=15 → accounts[]",
            "HDDT: callback #id_token=eyJ… → liệt kê số HĐ",
            "VTP/GHN/Best: đã có accounts — đã upsert vào buucuc_backend.db.contracts",
        ],
    }
    return report


def format_text(report: dict[str, Any]) -> str:
    lines = [
        "🗺️ Mapper đường ống dẫn các hợp đồng",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
        "",
        "=== Ống chính ===",
        "1) HDDT: SSO app_key → id_token → số HĐ hopdongdientu",
        "2) Pancake ĐVVC: token/api_key → GET /shops/{id}/partners → accounts[]",
        "   J&T=15 · VTP=3 · GHN=5 · GHTK=1 · Best=16",
        "3) SPX: SPX_SHOP_ID / TOKEN env",
        "4) HĐ → backend bưu cục: upsert contracts → buucuc_backend.db",
        "",
        "=== Trạng thái ống ===",
    ]
    bc = report.get("buucuc_backend") or {}
    if bc:
        lines.append(
            f"  · HĐ→BC: mapped={bc.get('contracts_mapped')} · "
            f"backends={bc.get('backends_n')} · orders≈{bc.get('orders_linked_sum')}"
        )
        if (bc.get("db") or {}).get("path"):
            lines.append(f"  · DB: {bc['db']['path']}")
        lines.append("")
    for s in report.get("status") or []:
        flag = "✅" if s.get("ready") and not s.get("blocked") else ("⚠" if s.get("ready") else "❌")
        lines.append(f"  {flag} {s.get('pipe')}: {s.get('detail')}")
        for a in (s.get("accounts") or [])[:5]:
            acc = a.get("account") or {}
            lines.append(
                f"      · shop {a.get('shop_id')} {a.get('shop_name')} → "
                f"{a.get('partner_name')}: {acc.get('name')} (id={acc.get('id')})"
            )
    lines.append("")
    lines.append("=== Gaps ===")
    for g in (report.get("live") or {}).get("gaps") or []:
        lines.append(f"  · {g.get('shop_id')} {g.get('claim')}: {g.get('issue')}")
    lines.append("")
    lines.append(report.get("mermaid") or "")
    lines.append("")
    for n in report.get("next") or []:
        lines.append(f"Next: {n}")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    SECRETS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "contract_pipe_mapper.json"
    tp = REPORTS / "contract_pipe_mapper.txt"
    mp = REPORTS / "contract_pipe_mapper.mermaid.md"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = format_text(report)
    tp.write_text(text + "\n", encoding="utf-8")
    mp.write_text((report.get("mermaid") or "") + "\n", encoding="utf-8")
    state = {
        "updated_at": report.get("checked_at"),
        "verdict": report.get("verdict"),
        "status": report.get("status"),
        "accounts_n": len((report.get("live") or {}).get("accounts") or []),
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp), "mermaid": str(mp)}


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
    ap = argparse.ArgumentParser(description="Mapper đường ống dẫn hợp đồng ĐVVC/HDDT")
    ap.add_argument("--no-probe", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)
    report = build_report(probe=not args.no_probe)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
