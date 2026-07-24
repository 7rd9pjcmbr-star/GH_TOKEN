#!/usr/bin/env python3
"""Rà soát mối nối ống → backend Aship OData · kho · bưu cục.

Atlas:
  owned secrets (tokenShip / ConfigId / TPOS_*)
    → aship.tpos.vn/odata (+ ShippingProviderConfigs)
    → aship-v2.tpos.app/api/v1 (probe)
    → tracking.aship.app SSR
    → OMS-pipe-bus / writers
    → kho_buucuc_pipe.db (kho_nodes · buucuc_nodes · backends · orders)
    → mirror buucuc_backend.db

Policy: owned only · no dump-login · mask tokens · reports gitignored.
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
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"
BUUCUC_DB = REPORTS / "buucuc_backend.db"
SHIP_ENV = SECRETS / "aship_tpos_ship.env"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for path in (
        SHIP_ENV,
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


def mask(s: str | None, keep: int = 4) -> str | None:
    if not s:
        return None
    s = str(s)
    if len(s) <= keep * 2:
        return "***"
    return s[:keep] + "…" + s[-2:]


def http(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: int = 20,
) -> tuple[int, Any, str]:
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            text = raw.decode("utf-8", "replace")
            try:
                return int(resp.status), json.loads(text or "null"), ct
            except json.JSONDecodeError:
                return int(resp.status), text, ct
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp else b""
        ct = (e.headers.get("Content-Type") or "").split(";")[0].strip() if e.headers else ""
        text = raw.decode("utf-8", "replace")
        try:
            return int(e.code), json.loads(text or "null"), ct
        except Exception:
            return int(e.code), text[:500], ct
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)[:200]}, ""


def classify_http(code: int, body: Any, ct: str) -> str:
    if code == 0:
        return "error"
    if code in (401, 403):
        return "auth_fail"
    if code == 402:
        return "expired_payment"
    if code == 404:
        return "not_found"
    if code >= 500:
        return "upstream_5xx"
    if 200 <= code < 300:
        if isinstance(body, str) and (
            body.lstrip().startswith("<!") or "text/html" in (ct or "")
        ):
            return "html_shell"
        if isinstance(body, dict):
            return "ok_json"
        return "ok"
    if code in (400, 405, 415):
        return "reachable"
    return f"http_{code}"


def joint(
    *,
    id: str,
    layer: str,
    target: str,
    feeds: str,
    status: str,
    http_code: int | None = None,
    detail: str = "",
    secret_keys: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "layer": layer,
        "target": target,
        "feeds": feeds,
        "status": status,
        "http": http_code,
        "detail": detail[:240],
        "secret_keys": secret_keys or [],
    }


def scrape_ssr_ok(code: str = "TPO1408375976", provider: str = "ViettelPost") -> dict[str, Any]:
    url = "https://tracking.aship.app/order?" + urllib.parse.urlencode(
        {"provider_code": code, "provider": provider}
    )
    st, body, ct = http(url, headers={"User-Agent": "OMS-joint-audit", "Accept": "text/html"})
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    texts = [
        htmlmod.unescape(t.strip())
        for t in re.findall(r">([^<]{2,120})<", text)
        if t.strip() and not re.search(r"[{}]|function|https?:", t)
    ]
    not_found = any(re.search(r"(?i)không tồn tại|not found", t) for t in texts)
    order = None
    for i, t in enumerate(texts):
        if t == "Mã đơn hàng:" and i + 1 < len(texts):
            order = texts[i + 1]
            break
    ok = st == 200 and bool(order) and not not_found
    return {
        "ok": ok,
        "http": st,
        "url": url,
        "order_code": order,
        "not_found": not_found,
        "status": "ok" if ok else ("not_found" if not_found else classify_http(st, body, ct)),
        "snippet": texts[:6],
    }


def probe_aship_odata(env: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    base = (env.get("ASHIP_BASE_URL") or env.get("TPOS_BASE_URL") or "https://aship.tpos.vn").rstrip("/")
    token = (env.get("ASHIP_TOKEN_SHIP") or env.get("TPOS_ACCESS_TOKEN") or "").strip()
    cfg = (env.get("ASHIP_CONFIG_ID") or env.get("TPOS_SHOP_ID") or "").strip()
    vtp_cfg = (env.get("ASHIP_CARRIER_VTP_CONFIG_ID") or "").strip()
    best_cfg = (env.get("ASHIP_CARRIER_BEST_KONTUM_CONFIG_ID") or "").strip()
    headers = {
        "Accept": "application/json",
        "User-Agent": "OMS-joint-audit",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    }

    joints: list[dict[str, Any]] = []
    st, body, ct = http(f"{base}/odata", headers=headers)
    ents = []
    if isinstance(body, dict):
        ents = [x.get("name") for x in (body.get("value") or []) if isinstance(x, dict)]
    joints.append(
        joint(
            id="aship_odata_catalog",
            layer="aship_odata",
            target=f"{base}/odata",
            feeds="entity catalog → ShippingProviderConfigs / public sets",
            status=classify_http(st, body, ct),
            http_code=st,
            detail=f"entities={len(ents)}: {', '.join(ents[:8])}{'…' if len(ents)>8 else ''}",
            secret_keys=["ASHIP_BASE_URL", "ASHIP_TOKEN_SHIP"],
        )
    )

    entity_results: list[dict[str, Any]] = []
    json_ok: list[str] = []
    html_shell: list[str] = []
    for name in ents:
        st_e, body_e, ct_e = http(f"{base}/odata/{name}?$top=1", headers=headers)
        st_label = classify_http(st_e, body_e, ct_e)
        n = None
        if isinstance(body_e, dict) and isinstance(body_e.get("value"), list):
            n = len(body_e["value"])
        entity_results.append({"name": name, "http": st_e, "status": st_label, "rows_top1": n})
        if st_label == "ok_json":
            json_ok.append(name)
        elif st_label == "html_shell":
            html_shell.append(name)

    joints.append(
        joint(
            id="aship_odata_entities_json",
            layer="aship_odata",
            target=f"{base}/odata/{{Entity}}",
            feeds="live JSON entity sets (public catalog surface)",
            status="ok" if json_ok else "blocked",
            http_code=200 if json_ok else None,
            detail=f"json_ok={json_ok} · html_shell={len(html_shell)}/{len(ents)}",
            secret_keys=["ASHIP_TOKEN_SHIP"],
        )
    )

    # ShippingProviderConfigs deep
    st_c, body_c, ct_c = http(
        f"{base}/odata/ShippingProviderConfigs?$top=100", headers=headers
    )
    providers: list[dict[str, Any]] = []
    if isinstance(body_c, dict):
        for r in body_c.get("value") or []:
            if isinstance(r, dict):
                providers.append(
                    {
                        "id": r.get("Id"),
                        "provider": r.get("Provider"),
                        "description": r.get("Description"),
                    }
                )
    known = {k: v for k, v in {
        cfg: "ASHIP_CONFIG_ID(user)",
        vtp_cfg: "ASHIP_CARRIER_VTP_CONFIG_ID",
        best_cfg: "ASHIP_CARRIER_BEST_KONTUM_CONFIG_ID",
    }.items() if k}
    in_catalog = {kid: any(str(p.get("id")) == kid for p in providers) for kid in known}
    joints.append(
        joint(
            id="aship_shipping_provider_configs",
            layer="aship_odata",
            target=f"{base}/odata/ShippingProviderConfigs",
            feeds="carrier catalog → map ConfigId → buucuc tip (VTP/BEST/GHN…)",
            status=classify_http(st_c, body_c, ct_c),
            http_code=st_c,
            detail=(
                f"public={len(providers)} providers={dict(Counter(p.get('provider') for p in providers))} · "
                f"owned_cfg_in_catalog={in_catalog}"
            ),
            secret_keys=[
                "ASHIP_CONFIG_ID",
                "ASHIP_CARRIER_VTP_CONFIG_ID",
                "ASHIP_CARRIER_BEST_KONTUM_CONFIG_ID",
            ],
        )
    )

    meta = {
        "base": base,
        "token_set": bool(token),
        "token_masked": mask(token),
        "config_id": cfg or None,
        "entities": ents,
        "entity_probes": entity_results,
        "providers": providers,
        "owned_config_in_catalog": in_catalog,
        "json_entity_sets": json_ok,
        "html_shell_sets": html_shell,
    }
    return joints, meta


def probe_aship_v2(env: dict[str, str]) -> list[dict[str, Any]]:
    v2 = (env.get("ASHIP_V2_API") or "https://aship-v2.tpos.app/api/v1").rstrip("/")
    token = (env.get("ASHIP_TOKEN_SHIP") or env.get("TPOS_ACCESS_TOKEN") or "").strip()
    cfg = (env.get("ASHIP_CONFIG_ID") or "").strip()
    headers = {
        "Accept": "application/json",
        "User-Agent": "OMS-joint-audit",
        "Content-Type": "application/json",
        **({"Authorization": f"Bearer {token}"} if token else {}),
        **({"ConfigId": cfg} if cfg else {}),
    }
    out: list[dict[str, Any]] = []
    for path in ("/app-user/init", "/shop", "/shops"):
        st, body, ct = http(f"{v2}{path}", headers=headers)
        out.append(
            joint(
                id=f"aship_v2{path.replace('/', '_')}",
                layer="aship_v2",
                target=f"{v2}{path}",
                feeds="tenant/shop init → ConfigId shop context (not public OData)",
                status=classify_http(st, body, ct),
                http_code=st,
                detail=(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))[:160],
                secret_keys=["ASHIP_TOKEN_SHIP", "ASHIP_CONFIG_ID", "ASHIP_V2_API"],
            )
        )
    return out


def probe_tpos_tenant(env: dict[str, str]) -> list[dict[str, Any]]:
    joints: list[dict[str, Any]] = []
    # owned TPOS_BASE_URL; fall back to Aship host when same tenant surface
    base = (env.get("TPOS_BASE_URL") or env.get("ASHIP_BASE_URL") or "").rstrip("/")
    token = (env.get("TPOS_ACCESS_TOKEN") or env.get("ASHIP_TOKEN_SHIP") or "").strip()
    base_src = (
        "TPOS_BASE_URL"
        if (env.get("TPOS_BASE_URL") or "").strip()
        else ("ASHIP_BASE_URL" if base else "missing")
    )
    if base:
        headers = {
            "Accept": "application/json",
            "User-Agent": "OMS-joint-audit",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        }
        for path, feeds in (
            ("/odata", "TPOS/Aship OData root"),
            ("/odata/FastSaleOrder/ODataService.GetViewDelivery", "delivery view → OMS ingest → kho"),
            ("/odata/FastSaleOrder", "sale orders → OMS-pipe-bus"),
        ):
            st, body, ct = http(f"{base}{path}", headers=headers)
            joints.append(
                joint(
                    id=f"tpos{path.replace('/', '_').replace('.', '_')}"[:64],
                    layer="tpos_odata",
                    target=f"{base}{path}",
                    feeds=feeds,
                    status=classify_http(st, body, ct),
                    http_code=st,
                    detail=(
                        f"base_from={base_src} · keys={list(body.keys())[:8]}"
                        if isinstance(body, dict)
                        else f"base_from={base_src} · {str(body)[:120]}"
                    ),
                    secret_keys=["TPOS_BASE_URL", "TPOS_ACCESS_TOKEN", "ASHIP_BASE_URL", "ASHIP_TOKEN_SHIP"],
                )
            )
    else:
        joints.append(
            joint(
                id="tpos_base_missing",
                layer="tpos_odata",
                target="TPOS_BASE_URL|ASHIP_BASE_URL",
                feeds="delivery → OMS → kho/buucuc",
                status="missing_cred",
                detail="Thiếu TPOS_BASE_URL và ASHIP_BASE_URL",
                secret_keys=["TPOS_BASE_URL", "TPOS_ACCESS_TOKEN"],
            )
        )

    st_ph, body_ph, ct_ph = http(
        "https://phamthuhoa.tpos.vn/odata",
        headers={"Accept": "application/json", "User-Agent": "OMS-joint-audit"},
    )
    joints.append(
        joint(
            id="phamthuhoa_tpos_odata",
            layer="tpos_tenant",
            target="https://phamthuhoa.tpos.vn/odata",
            feeds="shop tenant locus (inventory only) → không login dump",
            status=classify_http(st_ph, body_ph, ct_ph),
            http_code=st_ph,
            detail=(body_ph if isinstance(body_ph, str) else json.dumps(body_ph, ensure_ascii=False))[:160],
        )
    )
    return joints


def probe_tracking() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    st_root, body_root, ct_root = http(
        "https://tracking.aship.app/",
        headers={"User-Agent": "OMS-joint-audit", "Accept": "text/html"},
    )
    ssr = scrape_ssr_ok()
    joints = [
        joint(
            id="tracking_aship_root",
            layer="tracking_ssr",
            target="https://tracking.aship.app/",
            feeds="public SSR host (root may 404; /order is the joint)",
            status=classify_http(st_root, body_root, ct_root),
            http_code=st_root,
            detail="root ping — expect 404; order path is the real joint",
        ),
        joint(
            id="tracking_aship_order_ssr",
            layer="tracking_ssr",
            target=ssr.get("url") or "https://tracking.aship.app/order",
            feeds="provider_code + provider → pipe ssr_* / pipe_events → status",
            status=ssr.get("status") or "error",
            http_code=ssr.get("http"),
            detail=f"seed TPO1408375976 → order={ssr.get('order_code')} ok={ssr.get('ok')}",
        ),
    ]
    return joints, ssr


def load_kho_buucuc_joints() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    joints: list[dict[str, Any]] = []
    atlas: dict[str, Any] = {"ok": False}

    if not PIPE_DB.is_file():
        joints.append(
            joint(
                id="pipe_db_missing",
                layer="kho_buucuc",
                target=str(PIPE_DB),
                feeds="orders · kho_nodes · buucuc_nodes",
                status="missing",
                detail="kho_buucuc_pipe.db absent",
            )
        )
        return joints, atlas

    conn = sqlite3.connect(str(PIPE_DB))
    conn.row_factory = sqlite3.Row
    orders = int(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
    tpo = int(
        conn.execute(
            "SELECT COUNT(*) FROM orders WHERE tracking_code LIKE 'TPO%'"
        ).fetchone()[0]
    )
    with_track = int(
        conn.execute(
            "SELECT COUNT(*) FROM orders WHERE tracking_code IS NOT NULL AND TRIM(tracking_code) != ''"
        ).fetchone()[0]
    )
    aship_url = int(
        conn.execute(
            "SELECT COUNT(*) FROM orders WHERE tracking_url LIKE '%aship%'"
        ).fetchone()[0]
    )
    ssr_n = 0
    cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
    if "ssr_scraped_at" in cols:
        ssr_n = int(
            conn.execute(
                "SELECT COUNT(*) FROM orders WHERE ssr_scraped_at IS NOT NULL AND ssr_scraped_at != ''"
            ).fetchone()[0]
        )

    pipe_sources = [
        dict(r)
        for r in conn.execute(
            "SELECT COALESCE(pipe_source,'(none)') s, COUNT(*) n FROM orders GROUP BY 1 ORDER BY n DESC"
        )
    ]
    backends_tip = [
        dict(r)
        for r in conn.execute(
            "SELECT COALESCE(backend,'(none)') b, COUNT(*) n FROM orders GROUP BY 1 ORDER BY n DESC"
        )
    ]
    kho_rows = [dict(r) for r in conn.execute("SELECT * FROM kho_nodes ORDER BY orders DESC")]
    buu_rows = [dict(r) for r in conn.execute("SELECT * FROM buucuc_nodes ORDER BY orders DESC")]
    be_catalog = [dict(r) for r in conn.execute("SELECT * FROM backends")]
    meta = {r[0]: r[1] for r in conn.execute("SELECT key, value FROM meta")}

    # secret presence for backend tips
    env = load_env()
    backend_secret_status: list[dict[str, Any]] = []
    for b in be_catalog:
        secret = b.get("secret")
        present = bool(secret and (env.get(secret) or "").strip()) if secret else None
        backend_secret_status.append(
            {
                "id": b.get("id"),
                "oms": b.get("oms"),
                "secret": secret,
                "secret_present": present,
                "query_hint": b.get("query_hint"),
            }
        )

    joints.append(
        joint(
            id="pipe_db_orders",
            layer="kho_buucuc",
            target=str(PIPE_DB.name),
            feeds="N nguồn → writers → fingerprints/orders",
            status="ok",
            detail=(
                f"orders={orders} tracking={with_track} aship_url={aship_url} "
                f"TPO*={tpo} ssr_rows={ssr_n} · sources={ {r['s']: r['n'] for r in pipe_sources[:6]} }"
            ),
        )
    )
    joints.append(
        joint(
            id="kho_nodes",
            layer="kho_buucuc",
            target="kho_nodes",
            feeds="warehouse tip ← OMS warehouse_display / upload",
            status="ok" if kho_rows else "empty",
            detail=" · ".join(
                f"{(r.get('kho_name') or r.get('warehouse_display') or r.get('kho_id') or '?')}={r.get('orders')}"
                for r in kho_rows[:8]
            ),
        )
    )
    joints.append(
        joint(
            id="buucuc_nodes",
            layer="kho_buucuc",
            target="buucuc_nodes",
            feeds="carrier hub tip ← scan / pancake / OMS",
            status="ok" if buu_rows else "empty",
            detail=" · ".join(
                f"{r.get('buucuc')}[{r.get('backend')}]={r.get('orders')}"
                for r in buu_rows[:8]
            ),
        )
    )
    joints.append(
        joint(
            id="backends_catalog",
            layer="kho_buucuc",
            target="backends",
            feeds="HĐ tip → owned secret → remote scan",
            status="ok" if be_catalog else "empty",
            detail=" · ".join(
                f"{b['id']}:{'Y' if b['secret_present'] else ('—' if b['secret_present'] is None else 'N')}"
                for b in backend_secret_status
            ),
        )
    )

    # Aship → buucuc planned joints (VTP/BEST) vs live counts
    vtp_n = int(
        conn.execute(
            "SELECT COUNT(*) FROM orders WHERE upper(coalesce(buucuc,'')) LIKE '%VIETTEL%' "
            "OR upper(coalesce(carrier,'')) LIKE '%VIETTEL%' OR upper(coalesce(buucuc,''))='VTP'"
        ).fetchone()[0]
    )
    best_n = int(
        conn.execute(
            "SELECT COUNT(*) FROM orders WHERE upper(coalesce(buucuc,'')) LIKE '%BEST%' "
            "OR upper(coalesce(carrier,'')) LIKE '%BEST%'"
        ).fetchone()[0]
    )
    joints.append(
        joint(
            id="aship_to_buucuc_vtp_best",
            layer="aship→buucuc",
            target="ConfigId VTP/BEST → buucuc tip",
            feeds="Aship ship create → tracking TPO* → pipe buucuc ViettelPost|BEST",
            status="gap" if (tpo == 0 and vtp_n == 0 and best_n == 0) else "partial",
            detail=f"pipe TPO*={tpo} VTP-like={vtp_n} BEST-like={best_n} · ingest Aship delivery chưa đổ vào pipe",
            secret_keys=[
                "ASHIP_TOKEN_SHIP",
                "ASHIP_CARRIER_VTP_CONFIG_ID",
                "ASHIP_CARRIER_BEST_KONTUM_CONFIG_ID",
            ],
        )
    )

    # mirror
    mirror_n = 0
    if BUUCUC_DB.is_file():
        c2 = sqlite3.connect(str(BUUCUC_DB))
        mirror_n = int(c2.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
        c2.close()
    joints.append(
        joint(
            id="buucuc_backend_mirror",
            layer="kho_buucuc",
            target=BUUCUC_DB.name,
            feeds="mirror từ OMS ingest / writers (subset)",
            status="ok" if mirror_n else "empty",
            detail=f"orders={mirror_n} (pipe={orders})",
        )
    )

    # script joints (code-level connectors)
    script_joints = [
        ("aship_tpos_ship_mapper.py", "q:aship_tpos", "tokenShip → OData/v2/SSR → buucuc map"),
        ("tpos_kho_buucuc_mapper.py", "q:tpos_kho", "TPOS OData → OMS → kho/BC atlas"),
        ("tpos_ssr_pipe.py", "q:tpos_ssr", "SSR HTML → ssr_* / pipe_events"),
        ("tracking_aship.py", "q:aship", "build tracking_url provider_code"),
        ("order_pipe_kho_buucuc_db.py", "q:pipe_fp", "writers → pipe DB + mirror"),
        ("pipe_kho_san_shop_mapper.py", "q:pipe_ksc", "pipe_source→sàn→backend→ĐVVC→kho→shop"),
        ("buucuc_backend_per_hub_mapper.py", "q:bc_hub", "từng buucuc → primary backend"),
        ("scan_buucuc_orders.py", "q:bc_scan", "remote 3PL scan → pipe"),
    ]
    for script, panel, feeds in script_joints:
        path = ROOT / "scripts" / script
        joints.append(
            joint(
                id=f"script_{Path(script).stem}",
                layer="code_joint",
                target=script,
                feeds=feeds,
                status="ok" if path.is_file() else "missing",
                detail=f"panel={panel} · exists={path.is_file()}",
            )
        )

    conn.close()
    atlas = {
        "ok": True,
        "orders": orders,
        "with_tracking": with_track,
        "aship_urls": aship_url,
        "tpo": tpo,
        "ssr_rows": ssr_n,
        "vtp_like": vtp_n,
        "best_like": best_n,
        "pipe_sources": pipe_sources,
        "backends": backends_tip,
        "kho_nodes": kho_rows,
        "buucuc_nodes": buu_rows,
        "backend_secrets": backend_secret_status,
        "meta": meta,
        "mirror_orders": mirror_n,
    }
    return joints, atlas


def summarize(joints: list[dict[str, Any]]) -> dict[str, Any]:
    c = Counter(j.get("status") for j in joints)
    okish = sum(1 for j in joints if j.get("status") in {"ok", "ok_json", "reachable", "partial"})
    blocked = sum(
        1
        for j in joints
        if j.get("status")
        in {
            "missing_cred",
            "auth_fail",
            "expired_payment",
            "upstream_5xx",
            "gap",
            "missing",
            "blocked",
            "error",
        }
    )
    return {
        "total": len(joints),
        "okish": okish,
        "blocked_or_gap": blocked,
        "by_status": dict(c),
        "by_layer": dict(Counter(j.get("layer") for j in joints)),
    }


def build_report() -> dict[str, Any]:
    env = load_env()
    joints: list[dict[str, Any]] = []

    # secrets layer
    secret_map = {
        "ASHIP_BASE_URL": bool((env.get("ASHIP_BASE_URL") or "").strip()),
        "ASHIP_TOKEN_SHIP": bool((env.get("ASHIP_TOKEN_SHIP") or "").strip()),
        "ASHIP_CONFIG_ID": bool((env.get("ASHIP_CONFIG_ID") or "").strip()),
        "ASHIP_CARRIER_VTP_CONFIG_ID": bool(
            (env.get("ASHIP_CARRIER_VTP_CONFIG_ID") or "").strip()
        ),
        "ASHIP_CARRIER_BEST_KONTUM_CONFIG_ID": bool(
            (env.get("ASHIP_CARRIER_BEST_KONTUM_CONFIG_ID") or "").strip()
        ),
        "ASHIP_V2_API": bool((env.get("ASHIP_V2_API") or "").strip()),
        "TPOS_BASE_URL": bool((env.get("TPOS_BASE_URL") or "").strip()),
        "TPOS_ACCESS_TOKEN": bool((env.get("TPOS_ACCESS_TOKEN") or "").strip()),
        "VIETTELPOST_TOKEN": bool((env.get("VIETTELPOST_TOKEN") or "").strip()),
        "GHN_API_TOKEN": bool((env.get("GHN_API_TOKEN") or "").strip()),
        "env_file_aship": SHIP_ENV.is_file(),
    }
    joints.append(
        joint(
            id="secrets_aship_tpos",
            layer="secrets",
            target="secrets/aship_tpos_ship.env + backend_pipes.env",
            feeds="auth headers cho mọi joint Aship/TPOS",
            status="ok" if secret_map["ASHIP_TOKEN_SHIP"] and secret_map["ASHIP_BASE_URL"] else "missing_cred",
            detail=" · ".join(f"{k}={'Y' if v else 'N'}" for k, v in secret_map.items()),
            secret_keys=list(secret_map.keys()),
        )
    )

    odata_joints, odata_meta = probe_aship_odata(env)
    joints.extend(odata_joints)
    joints.extend(probe_aship_v2(env))
    joints.extend(probe_tpos_tenant(env))
    track_joints, ssr = probe_tracking()
    joints.extend(track_joints)
    kho_joints, kho_atlas = load_kho_buucuc_joints()
    joints.extend(kho_joints)

    stats = summarize(joints)
    gaps = [
        j
        for j in joints
        if j.get("status")
        in {
            "missing_cred",
            "auth_fail",
            "expired_payment",
            "upstream_5xx",
            "gap",
            "missing",
            "blocked",
            "html_shell",
        }
    ]

    report: dict[str, Any] = {
        "ok": True,
        "module": "aship_odata_kho_buucuc_joint_audit",
        "checked_at": utc_now(),
        "policy": "owned only · no dump-login · mask tokens · reports gitignored",
        "atlas": (
            "tokenShip/ConfigId → aship.tpos.vn/odata → (v2?) → tracking.aship SSR → "
            "OMS-pipe-bus → kho_buucuc_pipe.db → buucuc_backend.db"
        ),
        "mermaid": (
            "flowchart LR\n"
            "  S[secrets tokenShip/ConfigId] --> O[aship.tpos.vn/odata]\n"
            "  O --> SPC[ShippingProviderConfigs]\n"
            "  S --> V2[aship-v2 /api/v1]\n"
            "  SPC -.ConfigId shop.-> BC[buucuc VTP/BEST tips]\n"
            "  O --> SSR[tracking.aship.app SSR]\n"
            "  SSR --> PIPE[kho_buucuc_pipe.db]\n"
            "  OMS[OMS/Pancake/SPX writers] --> PIPE\n"
            "  PIPE --> KHO[kho_nodes]\n"
            "  PIPE --> BUU[buucuc_nodes]\n"
            "  PIPE --> MIR[buucuc_backend.db]\n"
        ),
        "secrets_present": secret_map,
        "odata": odata_meta,
        "tracking_ssr": ssr,
        "kho_buucuc": kho_atlas,
        "joints": joints,
        "gaps": gaps,
        "stats": stats,
        "verdict": (
            f"🔗 Joints Aship×kho×BC: total={stats['total']} · okish={stats['okish']} · "
            f"gap/block={stats['blocked_or_gap']} · "
            f"OData JSON={odata_meta.get('json_entity_sets')} · "
            f"SSR={'OK' if ssr.get('ok') else 'FAIL'} · "
            f"pipe TPO*={(kho_atlas or {}).get('tpo', 0)}"
        ),
        "next": [
            "Aship OData public chỉ JSON: ApplicationUsers / ShippingProviderConfigs / Settings — không có Shipments/Orders entity",
            "FastSaleOrder/GetViewDelivery trên aship.tpos.vn = 404/html_shell — không phải joint delivery OMS",
            "ConfigId user/VTP/BEST là id shop-side — không nằm catalog public; cần API tạo đơn/tenant đúng",
            "aship-v2 đang 500 — joint shop/init chưa mở",
            "phamthuhoa.tpos.vn = expired 402 — không dùng làm ingress",
            "Pipe thiếu TPO*/VTP/BEST — joint Aship→buucuc chưa đổ đơn (chỉ SSR seed)",
            "VIETTELPOST_TOKEN trống → joint buucuc ViettelPost remote scan blocked",
            "Điền TPOS_BASE_URL/TPOS_ACCESS_TOKEN tenant sống (hoặc alias từ ASHIP_*) nếu cần GetViewDelivery riêng",
            "python3 scripts/aship_odata_kho_buucuc_joint_audit.py --notify",
            "python3 scripts/tpos_ssr_pipe.py --notify",
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
    A("🔗 RÀ SOÁT MỐI NỐI · ASHIP ODATA × KHO × BƯU CỤC")
    A(f"Lúc: {report.get('checked_at')}")
    A(f"Verdict: {report.get('verdict')}")
    A(f"Atlas: {report.get('atlas')}")
    A(
        f"Stats: total={st.get('total')} okish={st.get('okish')} "
        f"gap/block={st.get('blocked_or_gap')} · by_status={st.get('by_status')}"
    )
    A("")
    A("=== Secrets ===")
    for k, v in (report.get("secrets_present") or {}).items():
        A(f"  {'✅' if v else '❌'} {k}")
    A("")
    A("=== Joints (theo lớp) ===")
    by_layer: dict[str, list] = {}
    for j in report.get("joints") or []:
        by_layer.setdefault(j.get("layer") or "?", []).append(j)
    icon = {
        "ok": "✅",
        "ok_json": "✅",
        "reachable": "🟡",
        "partial": "🟡",
        "html_shell": "⚪",
        "not_found": "⚪",
        "gap": "🟠",
        "missing_cred": "❌",
        "auth_fail": "❌",
        "expired_payment": "❌",
        "upstream_5xx": "❌",
        "missing": "❌",
        "blocked": "❌",
        "error": "❌",
        "empty": "⚪",
    }
    for layer, items in by_layer.items():
        A(f"— {layer} ({len(items)})")
        for j in items:
            ic = icon.get(j.get("status") or "", "·")
            A(
                f"  {ic} [{j.get('status')}] {j.get('id')} · "
                f"http={j.get('http')} · {j.get('feeds')}"
            )
            if j.get("detail"):
                A(f"      {j.get('detail')}")
    A("")
    A("=== Gaps ưu tiên ===")
    for g in (report.get("gaps") or [])[:12]:
        A(f"  🟠 {g.get('id')}: {g.get('status')} — {g.get('detail') or g.get('feeds')}")
    A("")
    kho = report.get("kho_buucuc") or {}
    if kho.get("ok"):
        A(
            f"=== Pipe DB === orders={kho.get('orders')} track={kho.get('with_tracking')} "
            f"aship_url={kho.get('aship_urls')} TPO*={kho.get('tpo')} "
            f"VTP={kho.get('vtp_like')} BEST={kho.get('best_like')} mirror={kho.get('mirror_orders')}"
        )
    A("")
    for n in report.get("next") or []:
        A(f"Next: {n}")
    return "\n".join(L)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "aship_odata_kho_buucuc_joint_audit.json"
    tp = REPORTS / "aship_odata_kho_buucuc_joint_audit.txt"
    mp = REPORTS / "aship_odata_kho_buucuc_joint_audit.mermaid.md"
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
    ap = argparse.ArgumentParser(description="Rà soát mối nối Aship OData × kho × bưu cục")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)

    report = build_report()
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
