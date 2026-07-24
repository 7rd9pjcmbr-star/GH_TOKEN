#!/usr/bin/env python3
"""Đổi / refresh access token để gọi đơn hàng realtime (owned-only).

Luồng mặc định:
  client → nginx:18080 ($upstream_*) → upstream → access_token_rotate → realtime orders

Hỗ trợ:
  - set / refresh / ensure / apply-realtime — mặc định qua nginx
  - --direct: bỏ qua nginx (chỉ debug / upstream nội bộ)

Không đọc Acc_all/stealer dumps. Không dump-login.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SECRETS = ROOT / "secrets"
ENV_PATH = SECRETS / "backend_pipes.env"
STATE_PATH = SECRETS / "access_tokens.state.json"
REPORTS = ROOT / "reports" / "telegram-classify"

# platform → env keys
TOKEN_KEYS = {
    "Pancake": "PANCAKE_POS_ACCESS_TOKEN",
    "GHN": "GHN_API_TOKEN",
    "ViettelPost": "VIETTELPOST_TOKEN",
    "TPOS": "TPOS_ACCESS_TOKEN",
    "Sapo": "SAPO_ACCESS_TOKEN",
    "Nhanh": "NHANH_API_KEY",
    "Shopee": "SHOPEE_ACCESS_TOKEN",
    "SPX": "SPX_TOKEN",
    "VNPost": "VNPOST_TOKEN",
}
USER_KEYS = {
    "Pancake": "PANCAKE_USER",
    "GHN": "GHN_USER",
    "ViettelPost": "VIETTELPOST_USER",
    "TPOS": "TPOS_USER",
    "Sapo": "SAPO_USER",
    "Nhanh": "NHANH_USER",
    "Shopee": "SHOPEE_USER",
    "SPX": "SPX_USER",
    "VNPost": "VNPOST_USER",
}
SHOP_KEYS = {
    "Pancake": "PANCAKE_SHOP_ID",
    "GHN": "GHN_SHOP_ID",
    "ViettelPost": "VIETTELPOST_SHOP_ID",
    "TPOS": "TPOS_SHOP_ID",
    "Sapo": "SAPO_STORE",
    "Nhanh": "NHANH_BUSINESS_ID",
    "Shopee": "SHOPEE_SHOP_ID",
    "SPX": "SPX_SHOP_ID",
    "VNPost": "VNPOST_CUSTOMER_CODE",
}
# alias API key cho Pancake (một số shop dùng api_key 32 ký tự thay Bearer)
ALT_TOKEN_KEYS = {
    "Pancake": ("PANCAKE_POS_API_KEY", "PANCAKE_API_KEY"),
}

PLATFORM_ALIASES = {
    "pancake": "Pancake",
    "ghn": "GHN",
    "vtp": "ViettelPost",
    "viettelpost": "ViettelPost",
    "tpos": "TPOS",
    "sapo": "Sapo",
    "nhanh": "Nhanh",
    "shopee": "Shopee",
    "spx": "SPX",
    "vnpost": "VNPost",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mask(token: str | None) -> str | None:
    if not token:
        return None
    t = str(token)
    if len(t) <= 8:
        return "*" * len(t)
    return f"{t[:4]}…{t[-4:]}(len={len(t)})"


def normalize_platform(name: str) -> str:
    n = (name or "").strip()
    if n in TOKEN_KEYS:
        return n
    return PLATFORM_ALIASES.get(n.lower(), n)


def load_env() -> dict[str, str]:
    from owned_credentials import env_overlay_from_owned, load_env as base_load

    return env_overlay_from_owned(base_load())


def load_state() -> dict:
    if STATE_PATH.is_file():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"rotations": [], "platforms": {}}


def save_state(state: dict) -> None:
    SECRETS.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_env_file() -> Path:
    from owned_credentials import ensure_env_file as ensure

    return ensure()


def upsert_env_values(updates: dict[str, str], *, path: Path | None = None) -> Path:
    """Ghi/ cập nhật key=value trong secrets/backend_pipes.env (giữ comment/thứ tự)."""
    path = path or ensure_env_file()
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines()
    keys_done = set()
    out: list[str] = []
    for line in lines:
        raw = line
        t = line.strip()
        if t and not t.startswith("#") and "=" in t:
            k = t.split("=", 1)[0].strip()
            if k in updates:
                val = updates[k]
                # quote if spaces/special
                if re.search(r'[\s#"\']', val):
                    val = json.dumps(val, ensure_ascii=False)
                out.append(f"{k}={val}")
                keys_done.add(k)
                continue
        out.append(raw)
    for k, v in updates.items():
        if k in keys_done:
            continue
        val = v
        if re.search(r'[\s#"\']', val):
            val = json.dumps(val, ensure_ascii=False)
        out.append(f"{k}={val}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    body: bytes | None = None,
    timeout: int = 25,
) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
        raw = e.read() if e.fp else b""
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)[:200]}
    try:
        return code, json.loads(raw.decode("utf-8", errors="replace") or "null")
    except json.JSONDecodeError:
        return code, {"raw": raw[:200].decode("utf-8", errors="replace")}


def extract_token_from_body(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    # common shapes
    for k in ("token", "Token", "access_token", "accessToken", "data"):
        v = body.get(k)
        if isinstance(v, str) and len(v) >= 16:
            return v.strip()
        if isinstance(v, dict):
            for kk in ("token", "Token", "access_token", "accessToken"):
                vv = v.get(kk)
                if isinstance(vv, str) and len(vv) >= 16:
                    return vv.strip()
    return None


# —— set / rotate ————————————————————————————


def set_access_token(
    platform: str,
    token: str,
    *,
    user: str | None = None,
    shop_id: str | None = None,
    as_api_key: bool = False,
) -> dict:
    """Đổi access token sở hữu → secrets/backend_pipes.env."""
    plat = normalize_platform(platform)
    if plat not in TOKEN_KEYS:
        return {"ok": False, "error": f"platform không hỗ trợ: {platform}", "supported": list(TOKEN_KEYS)}
    token = (token or "").strip()
    if not token:
        return {"ok": False, "error": "token trống"}

    updates: dict[str, str] = {}
    if as_api_key and plat == "Pancake":
        updates["PANCAKE_POS_API_KEY"] = token
    else:
        updates[TOKEN_KEYS[plat]] = token
        # pancake: cũng set ACCESS_TOKEN canonical
        if plat == "Pancake":
            updates["PANCAKE_POS_ACCESS_TOKEN"] = token

    if user:
        updates[USER_KEYS[plat]] = user.strip()
    if shop_id:
        updates[SHOP_KEYS[plat]] = str(shop_id).strip()

    path = upsert_env_values(updates)
    st = load_state()
    st.setdefault("platforms", {})[plat] = {
        "token_masked": mask(token),
        "user": user,
        "shop_id": shop_id,
        "updated_at": utc_now(),
        "source": "set_access_token",
    }
    st.setdefault("rotations", []).append(
        {
            "at": utc_now(),
            "platform": plat,
            "action": "set",
            "token_masked": mask(token),
        }
    )
    st["rotations"] = st["rotations"][-50:]
    save_state(st)
    return {
        "ok": True,
        "platform": plat,
        "env_file": str(path),
        "token_key": TOKEN_KEYS[plat],
        "token_masked": mask(token),
        "user": user,
        "shop_id": shop_id,
        "checked_at": utc_now(),
        "verdict": f"✅ Đã đổi access token {plat} → {path.name}",
        "next": [
            "python3 scripts/realtime_order_sync.py --once",
            "python3 scripts/access_token_rotate.py status",
        ],
    }


# —— refresh per platform ————————————————————


def refresh_ghn(
    env: dict[str, str] | None = None,
    *,
    fetch_orders: bool = False,
    days: int = 3,
    limit: int = 50,
    printA5: str | None = None,
) -> dict:
    """Lấy/duy trì GHN_API_TOKEN (owned) → resolve shop → (optional) gọi đơn.

    GHN public API không có login USER/PASSWORD như ViettelPost — token lấy từ
    dashboard / printA5 / cookie session owned, rồi gọi header Token.
    """
    env = env or load_env()
    from ghn_access_token_orders import get_token_and_fetch_orders, resolve_access_token, resolve_shop_id

    if printA5 and str(printA5).strip():
        report = get_token_and_fetch_orders(
            days=days,
            limit=limit,
            try_pending=True,
            resolve_shop=True,
            printA5=str(printA5).strip(),
            force_printA5=True,
        )
        return {
            "ok": bool(report.get("ok")),
            "platform": "GHN",
            "checked_at": utc_now(),
            "printA5": True,
            "token_masked": (report.get("token") or {}).get("token_masked"),
            "shop_id": report.get("shop_id"),
            "orders": report.get("orders"),
            "roles": report.get("roles"),
            "ingest": report.get("ingest"),
            "verdict": report.get("verdict"),
            "next": report.get("next"),
            "refresh": {
                "step_used": "printA5→GHN_API_TOKEN→orders",
                "token_masked": (report.get("token") or {}).get("token_masked"),
            },
            "source_report": {
                "ensure": (report.get("token") or {}).get("ensure"),
                "shop": report.get("shop"),
                "ingest": report.get("ingest"),
            },
        }

    if fetch_orders:
        report = get_token_and_fetch_orders(days=days, limit=limit, try_pending=True, resolve_shop=True)
        return {
            "ok": bool(report.get("ok")),
            "platform": "GHN",
            "checked_at": utc_now(),
            "token_masked": (report.get("token") or {}).get("token_masked"),
            "shop_id": report.get("shop_id"),
            "orders": report.get("orders"),
            "roles": report.get("roles"),
            "verdict": report.get("verdict"),
            "next": report.get("next"),
            "refresh": {
                "step_used": "ensure_ghn_session+shop_all+orders",
                "token_masked": (report.get("token") or {}).get("token_masked"),
            },
            "source_report": {
                "ensure": (report.get("token") or {}).get("ensure"),
                "shop": report.get("shop"),
            },
        }

    tok = resolve_access_token(try_pending=True)
    token = tok.get("token") or ""
    if not token or not tok.get("alive"):
        return {
            "ok": False,
            "platform": "GHN",
            "error": "Thiếu GHN_API_TOKEN sống (owned)",
            "hint": "Đặt printA5/cookie vào secrets/ghn_session.raw hoặc set --token",
            "ensure": tok.get("ensure"),
            "checked_at": utc_now(),
            "verdict": (tok.get("ensure") or {}).get("verdict") or "❌ GHN token missing/dead",
        }

    shop = resolve_shop_id(token, shop_id=tok.get("shop_id"), persist=True)
    # re-apply env key via set_access_token so access_tokens.state tracks rotation
    set_report = set_access_token("GHN", token, shop_id=shop.get("shop_id"))
    return {
        "ok": True,
        "platform": "GHN",
        "checked_at": utc_now(),
        "token_masked": tok.get("token_masked") or set_report.get("token_masked"),
        "shop_id": shop.get("shop_id"),
        "shop": shop,
        "ensure": tok.get("ensure"),
        "set": {"ok": set_report.get("ok"), "env_file": set_report.get("env_file")},
        "refresh": {
            "step_used": "ensure_ghn_session+shop_all",
            "token_masked": tok.get("token_masked"),
            "alive": True,
        },
        "verdict": (
            f"✅ Refresh GHN access token · {tok.get('token_masked')} · "
            f"shop={shop.get('shop_id') or '—'}"
        ),
        "next": [
            "python3 scripts/ghn_access_token_orders.py run --days 3 --limit 50",
            "python3 scripts/access_token_rotate.py apply-realtime --direct",
        ],
    }


def refresh_viettelpost(env: dict[str, str] | None = None) -> dict:
    """Login owned USER/PASSWORD → lấy token mới → ghi env."""
    env = env or load_env()
    user = (env.get("VIETTELPOST_USER") or "").strip()
    password = (env.get("VIETTELPOST_PASSWORD") or "").strip()
    if not user or not password:
        return {
            "ok": False,
            "platform": "ViettelPost",
            "error": "Thiếu VIETTELPOST_USER + VIETTELPOST_PASSWORD sở hữu",
            "hint": "Điền secrets/backend_pipes.env rồi chạy lại refresh",
        }

    # 1) Login → token tạm
    code, body = http_json(
        "https://partner.viettelpost.vn/v2/user/Login",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=json.dumps({"USERNAME": user, "PASSWORD": password}).encode(),
    )
    token = extract_token_from_body(body)
    if code not in (200, 201) or not token:
        return {
            "ok": False,
            "platform": "ViettelPost",
            "step": "Login",
            "http": code,
            "error": "Login không trả token",
            "body_keys": list(body.keys()) if isinstance(body, dict) else type(body).__name__,
        }

    # 2) ownerconnect → token dài hạn (nếu API hỗ trợ)
    code2, body2 = http_json(
        "https://partner.viettelpost.vn/v2/user/ownerconnect",
        method="POST",
        headers={"Content-Type": "application/json", "Token": token},
        body=json.dumps({"USERNAME": user, "PASSWORD": password}).encode(),
    )
    long_token = extract_token_from_body(body2) or token
    used_step = "ownerconnect" if extract_token_from_body(body2) else "Login"

    result = set_access_token("ViettelPost", long_token, user=user)
    result["refresh"] = {
        "login_http": code,
        "ownerconnect_http": code2,
        "step_used": used_step,
        "token_masked": mask(long_token),
    }
    result["verdict"] = f"✅ Refresh ViettelPost token via {used_step}"
    return result


def probe_token(platform: str, env: dict[str, str] | None = None) -> dict:
    """Probe nhẹ token hiện tại (không đổi)."""
    env = env or load_env()
    plat = normalize_platform(platform)
    if plat == "GHN":
        token = (env.get("GHN_API_TOKEN") or env.get("GHN_TOKEN") or "").strip()
        if not token:
            return {"ok": False, "platform": plat, "status": "missing_cred"}
        # province GET = probe chuẩn (available-services + {} dễ 400 dù token sống)
        from ghn_cookie_ingest import probe_token as ghn_probe

        probe = ghn_probe(token)
        http = int(probe.get("http") or 0)
        if probe.get("success"):
            status = "ok"
        elif http in (401, 403) or (
            isinstance(probe.get("message"), str)
            and "Unauthorized" in (probe.get("message") or "")
        ):
            status = "auth_fail"
        else:
            status = "error" if http else "error"
        return {
            "ok": status == "ok",
            "platform": plat,
            "status": status,
            "http": http,
            "probe_url": probe.get("url"),
            "provinces_n": probe.get("provinces_n"),
        }
    if plat == "ViettelPost":
        token = (env.get("VIETTELPOST_TOKEN") or "").strip()
        if not token:
            return {"ok": False, "platform": plat, "status": "missing_cred"}
        code, _ = http_json(
            "https://partner.viettelpost.vn/v2/order/trackingOrder",
            method="POST",
            headers={"Token": token, "Content-Type": "application/json"},
            body=json.dumps({"orderNumber": "OMS-PING"}).encode(),
        )
        status = "auth_fail" if code in (401, 403) else ("ok" if code else "error")
        return {"ok": status == "ok", "platform": plat, "status": status, "http": code}
    if plat == "Pancake":
        from pancake_pos_client import auth_ready, fetch_shops, resolve_credentials

        creds = resolve_credentials(
            api_key=env.get("PANCAKE_POS_API_KEY") or "",
            access_token=env.get("PANCAKE_POS_ACCESS_TOKEN") or "",
        )
        if not auth_ready(creds):
            return {"ok": False, "platform": plat, "status": "missing_cred"}
        try:
            shops, base = fetch_shops(creds, timeout=15)
            return {
                "ok": True,
                "platform": plat,
                "status": "ok",
                "shops": len(shops),
                "base": base,
            }
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            status = "auth_fail" if any(x in msg for x in ("401", "403", "Unauthorized")) else "error"
            return {"ok": False, "platform": plat, "status": status, "error": msg[:160]}
    if plat == "TPOS":
        base = (env.get("TPOS_BASE_URL") or "").rstrip("/")
        token = (env.get("TPOS_ACCESS_TOKEN") or "").strip()
        if not base or not token:
            return {"ok": False, "platform": plat, "status": "missing_cred"}
        code, _ = http_json(f"{base}/odata", headers={"Authorization": f"Bearer {token}"})
        status = "auth_fail" if code in (401, 403) else ("ok" if code else "error")
        return {"ok": status == "ok", "platform": plat, "status": status, "http": code}
    token_key = TOKEN_KEYS.get(plat)
    has = bool(token_key and (env.get(token_key) or "").strip())
    return {
        "ok": has,
        "platform": plat,
        "status": "present" if has else "missing_cred",
        "note": "Chưa có probe realtime — dùng set thủ công",
    }


def ensure_tokens(
    *,
    platforms: list[str] | None = None,
    auto_refresh_vtp: bool = True,
    auto_refresh_ghn: bool = True,
) -> dict:
    """Đảm bảo token sẵn sàng trước khi sync realtime."""
    env = load_env()
    plats = platforms or ["Pancake", "GHN", "ViettelPost", "TPOS"]
    results = []
    refreshed = []
    for p in plats:
        plat = normalize_platform(p)
        probe = probe_token(plat, env)
        entry = {"platform": plat, "probe": probe}
        if (
            auto_refresh_vtp
            and plat == "ViettelPost"
            and probe.get("status") in {"auth_fail", "missing_cred"}
            and (env.get("VIETTELPOST_USER") and env.get("VIETTELPOST_PASSWORD"))
        ):
            ref = refresh_viettelpost(env)
            entry["refresh"] = {
                "ok": ref.get("ok"),
                "verdict": ref.get("verdict") or ref.get("error"),
                "token_masked": (ref.get("refresh") or {}).get("token_masked") or ref.get("token_masked"),
            }
            if ref.get("ok"):
                refreshed.append(plat)
                env = load_env()  # reload
                entry["probe_after"] = probe_token(plat, env)
        if (
            auto_refresh_ghn
            and plat == "GHN"
            and probe.get("status") in {"auth_fail", "missing_cred", "error"}
        ):
            ref = refresh_ghn(env, fetch_orders=False)
            entry["refresh"] = {
                "ok": ref.get("ok"),
                "verdict": ref.get("verdict") or ref.get("error"),
                "token_masked": (ref.get("refresh") or {}).get("token_masked") or ref.get("token_masked"),
                "shop_id": ref.get("shop_id"),
            }
            if ref.get("ok"):
                refreshed.append(plat)
                env = load_env()
                entry["probe_after"] = probe_token(plat, env)
        results.append(entry)

    ready = [
        r["platform"]
        for r in results
        if (r.get("probe_after") or r.get("probe") or {}).get("ok")
        or (r.get("probe_after") or r.get("probe") or {}).get("status") in {"ok", "present"}
    ]
    return {
        "ok": True,
        "checked_at": utc_now(),
        "results": results,
        "ready_platforms": ready,
        "refreshed": refreshed,
        "verdict": (
            f"✅ Token sẵn sàng: {', '.join(ready) or '(chưa)'}"
            + (f" · refreshed={refreshed}" if refreshed else "")
        ),
        "policy": {"owned_only": True, "no_dump_login": True},
    }


def apply_realtime(*, limit: int = 20, notify: bool = False, via_nginx: bool = True) -> dict:
    """ensure tokens → danh sách đơn realtime.

    Mặc định BẮT BUỘC qua nginx ($upstream_*) rồi mới nạp module đổi token / gọi API.
    Dùng via_nginx=False chỉ cho upstream nội bộ (tránh đệ quy).
    """
    if via_nginx:
        return apply_realtime_via_nginx(limit=limit, notify=notify)

    ensure = ensure_tokens()
    from realtime_order_sync import load_env as sync_load_env, run_cycle

    env = sync_load_env()
    cycle = run_cycle(env, limit=limit, notify=notify, notify_new_only=False)
    return {
        "ok": bool(cycle.get("ok")),
        "checked_at": utc_now(),
        "via_nginx": False,
        "ensure": ensure,
        "cycle": {
            "new_count": cycle.get("new_count"),
            "owned": cycle.get("owned"),
            "blocked": cycle.get("blocked"),
            "backends": [
                {
                    "backend": b.get("backend"),
                    "status": b.get("status"),
                    "detail": (b.get("detail") or "")[:120],
                }
                for b in (cycle.get("backends") or [])
            ],
        },
        "verdict": (
            f"✅ apply-realtime (direct) · new={cycle.get('new_count')} · "
            f"ready={ensure.get('ready_platforms')} · refreshed={ensure.get('refreshed')}"
        ),
    }


def apply_realtime_via_nginx(*, limit: int = 20, notify: bool = False, keep: bool = False) -> dict:
    """client → nginx → upstream → access_token_rotate → realtime order list."""
    from nginx_order_embed import NginxOrderEmbed

    mod = NginxOrderEmbed(auto_stop=not keep)
    report = mod.token_realtime_pipeline(limit=limit, notify=notify, auto_stop=not keep)
    # payload từ /v1/orders/realtime = apply_realtime(direct) + nginx_mock_orders
    rt = report.get("realtime") if isinstance(report.get("realtime"), dict) else {}
    ensure = report.get("ensure") if isinstance(report.get("ensure"), dict) else rt.get("ensure") or {}
    cycle = rt.get("cycle") if isinstance(rt.get("cycle"), dict) else {}
    report["ensure"] = ensure
    report["cycle"] = cycle or {
        "new_count": None,
        "blocked": [],
        "backends": [],
    }
    if rt.get("nginx_mock_orders") and not report.get("nginx_orders"):
        report["nginx_orders"] = rt.get("nginx_mock_orders")
    if not report.get("verdict"):
        report["verdict"] = (
            f"{'✅' if report.get('ok') else '❌'} nginx→token→realtime · "
            f"new={(report.get('cycle') or {}).get('new_count')}"
        )
    return report


def set_access_token_via_nginx(
    platform: str,
    token: str,
    *,
    user: str | None = None,
    shop_id: str | None = None,
    as_api_key: bool = False,
    keep: bool = False,
) -> dict:
    """Đổi token: bắt buộc nhúng qua nginx rồi mới nạp module."""
    from nginx_order_embed import NginxOrderEmbed

    mod = NginxOrderEmbed(auto_stop=not keep)
    extra: dict[str, Any] = {}
    if user:
        extra["user"] = user
    if shop_id:
        extra["shop_id"] = shop_id
    if as_api_key:
        extra["as_api_key"] = True
    started = mod.ensure_up()
    if not started.get("ok"):
        return {
            "ok": False,
            "error": "nginx embed chưa up — không nạp token",
            "start": started,
            "via_nginx": False,
            "checked_at": utc_now(),
        }
    try:
        res = mod.token_set(platform, token, **extra)
        payload = res.get("payload") if isinstance(res.get("payload"), dict) else {}
        out = dict(payload) if payload else {"ok": res.get("ok"), "error": res.get("error")}
        out["via_nginx"] = True
        out["embedded"] = res.get("embedded")
        out["pipeline"] = res.get("pipeline")
        out["http"] = res.get("http")
        out["checked_at"] = utc_now()
        if res.get("ok") and not out.get("verdict"):
            out["verdict"] = f"✅ Đã nạp token qua nginx → {platform}"
        elif not res.get("ok"):
            out["ok"] = False
            out["verdict"] = f"❌ Nạp token qua nginx thất bại · http={res.get('http')}"
        return out
    finally:
        if not keep:
            mod.stop()


def ensure_tokens_via_nginx(*, platforms: list[str] | None = None, keep: bool = False) -> dict:
    from nginx_order_embed import NginxOrderEmbed

    mod = NginxOrderEmbed(auto_stop=not keep)
    started = mod.ensure_up()
    if not started.get("ok"):
        return {"ok": False, "error": "nginx embed chưa up", "start": started, "via_nginx": False}
    try:
        res = mod.token_ensure(platforms)
        payload = res.get("payload") if isinstance(res.get("payload"), dict) else {}
        out = dict(payload) if payload else {"ok": False, "error": res}
        out["via_nginx"] = True
        out["embedded"] = res.get("embedded")
        out["pipeline"] = res.get("pipeline")
        out["checked_at"] = utc_now()
        return out
    finally:
        if not keep:
            mod.stop()


def status() -> dict:
    env = load_env()
    st = load_state()
    platforms = {}
    for plat, key in TOKEN_KEYS.items():
        tok = (env.get(key) or "").strip()
        user = (env.get(USER_KEYS[plat]) or "").strip() or None
        shop = (env.get(SHOP_KEYS[plat]) or "").strip() or None
        platforms[plat] = {
            "token_key": key,
            "token_set": bool(tok),
            "token_masked": mask(tok) if tok else None,
            "user": user,
            "shop_id": shop,
            "state": (st.get("platforms") or {}).get(plat),
        }
    ready = [p for p, info in platforms.items() if info["token_set"]]
    return {
        "ok": True,
        "module": "access_token_rotate",
        "checked_at": utc_now(),
        "env_file": str(ENV_PATH),
        "platforms": platforms,
        "recent_rotations": (st.get("rotations") or [])[-10:],
        "verdict": (
            f"✅ Có token: {', '.join(ready)}"
            if ready
            else "⚠ Chưa có access token — dùng set/refresh"
        ),
        "cli": {
            "set": "python3 scripts/access_token_rotate.py set --platform GHN --token YOUR_TOKEN",
            "refresh_ghn": "python3 scripts/access_token_rotate.py refresh --platform GHN --direct",
            "refresh_ghn_orders": "python3 scripts/access_token_rotate.py refresh --platform GHN --orders --direct",
            "refresh_vtp": "python3 scripts/access_token_rotate.py refresh --platform ViettelPost",
            "ghn_orders": "python3 scripts/ghn_access_token_orders.py run --days 3 --limit 50",
            "ensure": "python3 scripts/access_token_rotate.py ensure",
            "realtime": "python3 scripts/access_token_rotate.py apply-realtime",
            "pipeline": "client → nginx:18080 → upstream → access_token_rotate → realtime orders",
        },
        "via_nginx_required": True,
    }


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔑 ACCESS TOKEN ROTATE · GỌI ĐƠN REALTIME")
    L(f"Lúc: {report.get('checked_at') or utc_now()}")
    L(report.get("verdict") or "")
    if report.get("via_nginx") or report.get("pipeline"):
        L(f"via_nginx={report.get('via_nginx')} pipeline={report.get('pipeline')}")
    emb = report.get("embedded") or {}
    if emb:
        L("nginx $upstream_*:")
        for k, v in emb.items():
            if v is not None:
                L(f"  {k} = {v}")
    if report.get("platform"):
        L(f"platform={report.get('platform')} token={report.get('token_masked')}")
    if report.get("env_file"):
        L(f"env: {report.get('env_file')}")
    if report.get("platforms"):
        L("")
        for plat, info in report["platforms"].items():
            mark = "✅" if info.get("token_set") else "·"
            L(
                f"{mark} {plat}: token={info.get('token_masked')} "
                f"user={info.get('user')} shop={info.get('shop_id')}"
            )
    if report.get("results"):
        L("")
        for r in report["results"]:
            probe = r.get("probe_after") or r.get("probe") or {}
            L(f"· {r.get('platform')}: status={probe.get('status')} http={probe.get('http')}")
            if r.get("refresh"):
                L(f"  refresh: {r['refresh']}")
    if report.get("ensure") and isinstance(report["ensure"], dict):
        L("")
        L(f"ensure: {report['ensure'].get('verdict') or report['ensure']}")
    if report.get("cycle"):
        c = report["cycle"]
        L("")
        L(f"realtime new={c.get('new_count')} blocked={c.get('blocked')}")
        for b in c.get("backends") or []:
            L(f"  - {b.get('backend')}: {b.get('status')} · {b.get('detail')}")
    if report.get("orders"):
        o = report["orders"] if isinstance(report["orders"], dict) else {}
        L("")
        L(f"GHN orders: status={o.get('status')} fetched={o.get('fetched')} · {o.get('detail') or ''}")
        for row in (o.get("preview") or [])[:5]:
            if isinstance(row, dict):
                L(f"  · {row.get('order_id') or row.get('tracking_code')} · {row.get('status')}")
    if report.get("shop_id"):
        L(f"shop_id={report.get('shop_id')}")
    if report.get("nginx_orders"):
        L("")
        L(f"nginx mock orders: {len(report['nginx_orders'])}")
        for o in report["nginx_orders"][:5]:
            L(f"  · {o.get('order_id')} · {o.get('tracking_code')} · {o.get('status')}")
    if report.get("cli"):
        L("")
        L("CLI:")
        for k, v in report["cli"].items():
            L(f"· {k}: {v}")
    if report.get("next"):
        for n in report["next"]:
            L(f"· {n}")
    L("")
    L("Safety: owned-only · no dump-login · via nginx · secrets gitignored")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": REPORTS / "access_token_rotate.json",
        "txt": REPORTS / "access_token_rotate.txt",
    }
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paths["txt"].write_text(format_text(report), encoding="utf-8")
    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Đổi/refresh access token gọi đơn realtime (mặc định qua nginx)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_set = sub.add_parser("set", help="Nạp access token sở hữu (qua nginx)")
    p_set.add_argument("--platform", required=True)
    p_set.add_argument("--token", required=True)
    p_set.add_argument("--user", default="")
    p_set.add_argument("--shop-id", default="")
    p_set.add_argument("--as-api-key", action="store_true", help="Pancake: lưu vào PANCAKE_POS_API_KEY")
    p_set.add_argument("--direct", action="store_true", help="Ghi env thẳng, không qua nginx")
    p_set.add_argument("--keep", action="store_true", help="Giữ nginx sau khi set")

    p_ref = sub.add_parser("refresh", help="Refresh token (GHN ensure / ViettelPost Login owned)")
    p_ref.add_argument("--platform", default="ViettelPost")
    p_ref.add_argument("--direct", action="store_true")
    p_ref.add_argument("--keep", action="store_true")
    p_ref.add_argument("--orders", action="store_true", help="GHN: sau khi lấy token → gọi đơn")
    p_ref.add_argument(
        "--printA5",
        default="",
        help="GHN: URL printA5?token=UUID → access token → gọi đơn",
    )
    p_ref.add_argument("--days", type=int, default=3, help="GHN orders window")
    p_ref.add_argument("--limit", type=int, default=50, help="GHN orders limit")

    p_ens = sub.add_parser("ensure", help="Probe + auto-refresh VTP/GHN (qua nginx)")
    p_ens.add_argument("--direct", action="store_true")
    p_ens.add_argument("--keep", action="store_true")

    sub.add_parser("status", help="Trạng thái token trong env")

    p_rt = sub.add_parser("apply-realtime", help="nginx → ensure → danh sách đơn realtime")
    p_rt.add_argument("--limit", type=int, default=20)
    p_rt.add_argument("--notify", action="store_true")
    p_rt.add_argument("--direct", action="store_true", help="Bỏ qua nginx (debug)")
    p_rt.add_argument("--keep", action="store_true", help="Giữ nginx sau pipeline")

    p_probe = sub.add_parser("probe", help="Probe một platform")
    p_probe.add_argument("--platform", required=True)

    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "set":
        if args.direct:
            report = set_access_token(
                args.platform,
                args.token,
                user=args.user or None,
                shop_id=args.shop_id or None,
                as_api_key=args.as_api_key,
            )
            report["via_nginx"] = False
        else:
            report = set_access_token_via_nginx(
                args.platform,
                args.token,
                user=args.user or None,
                shop_id=args.shop_id or None,
                as_api_key=args.as_api_key,
                keep=args.keep,
            )
    elif args.cmd == "refresh":
        plat = normalize_platform(args.platform)
        if plat not in {"ViettelPost", "GHN"}:
            report = {
                "ok": False,
                "error": f"refresh tự động hỗ trợ ViettelPost|GHN; {plat} dùng: set --token",
                "checked_at": utc_now(),
            }
        elif args.direct:
            if plat == "GHN":
                report = refresh_ghn(
                    fetch_orders=bool(args.orders) or bool(args.printA5),
                    days=int(args.days),
                    limit=int(args.limit),
                    printA5=(args.printA5 or None),
                )
            else:
                report = refresh_viettelpost()
            report["via_nginx"] = False
        else:
            from nginx_order_embed import NginxOrderEmbed

            mod = NginxOrderEmbed(auto_stop=not args.keep)
            started = mod.ensure_up()
            if not started.get("ok"):
                report = {"ok": False, "error": "nginx embed chưa up", "start": started}
            else:
                try:
                    # GHN --orders / --printA5: chạy trực tiếp sau nginx ensure
                    if plat == "GHN" and (args.orders or args.printA5):
                        report = refresh_ghn(
                            fetch_orders=True,
                            days=int(args.days),
                            limit=int(args.limit),
                            printA5=(args.printA5 or None),
                        )
                        report["via_nginx"] = True
                        report["pipeline"] = (
                            "nginx→up→printA5→orders" if args.printA5 else "nginx→up→refresh_ghn+orders"
                        )
                    else:
                        res = mod.token_refresh(plat)
                        payload = res.get("payload") if isinstance(res.get("payload"), dict) else {}
                        report = dict(payload) if payload else {"ok": False, "error": res}
                        report["via_nginx"] = True
                        report["embedded"] = res.get("embedded")
                        report["pipeline"] = res.get("pipeline")
                    report["checked_at"] = utc_now()
                finally:
                    if not args.keep:
                        mod.stop()
    elif args.cmd == "ensure":
        if args.direct:
            report = ensure_tokens()
            report["via_nginx"] = False
        else:
            report = ensure_tokens_via_nginx(keep=args.keep)
    elif args.cmd == "apply-realtime":
        if args.direct:
            report = apply_realtime(limit=args.limit, notify=args.notify, via_nginx=False)
        else:
            report = apply_realtime_via_nginx(limit=args.limit, notify=args.notify, keep=args.keep)
    elif args.cmd == "probe":
        report = probe_token(args.platform)
        report["checked_at"] = utc_now()
        report["verdict"] = f"probe {report.get('platform')}: {report.get('status')}"
    else:
        report = status()

    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
