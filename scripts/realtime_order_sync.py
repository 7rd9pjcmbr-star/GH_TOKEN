#!/usr/bin/env python3
"""
Cập nhật đơn hàng gần thời gian thực theo từng backend.

Backends:
  - Pancake POS (API key / Bearer trong secrets)
  - Telegram inbox (file mới trong quarantine/telegram)
  - direct_api snapshot (theo dõi file orders_detailed_*)
  - GHN / TPOS (nếu có token — probe nhẹ, không dump)

Chỉ secrets/. Không đọc Acc_all/Ghn dumps. Không auto-login mật khẩu.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from pancake_pos_client import (  # noqa: E402
    auth_ready,
    fetch_shop_orders,
    fetch_shops,
    resolve_credentials,
)

SECRETS = ROOT / "secrets"
INBOX = ROOT / "quarantine" / "telegram"
OUT = ROOT / "reports" / "telegram-classify" / "realtime"
STATE_FILE = SECRETS / "realtime_orders.state.json"
ENV_FILES = (
    SECRETS / "telegram.env",
    SECRETS / "backend_pipes.env",
    SECRETS / "pancake.env",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    # Overlay owned user/token mapping → canonical keys cho sync
    try:
        from owned_credentials import env_overlay_from_owned

        env = env_overlay_from_owned(env)
    except Exception:  # noqa: BLE001
        pass
    return env


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"backends": {}, "seen_orders": {}, "inbox_files": {}}


def save_state(state: dict) -> None:
    SECRETS.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def send_telegram(env: dict[str, str], text: str) -> None:
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_CHAT_ID") or ""
    if not token or not chat:
        return
    payload = json.dumps(
        {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def order_fingerprint(backend: str, order: dict) -> str:
    oid = str(
        order.get("id")
        or order.get("order_id")
        or order.get("order_key")
        or order.get("remote_id")
        or ""
    )
    shop = str(order.get("shop_id") or order.get("shopId") or "")
    raw = f"{backend}|{shop}|{oid}|{order.get('status_name') or order.get('status_normalized') or ''}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def summarize_order(backend: str, order: dict) -> str:
    oid = order.get("id") or order.get("order_key") or order.get("remote_id") or "?"
    status = order.get("status_name") or order.get("status_normalized") or order.get("status_raw") or "?"
    cust = order.get("customer") if isinstance(order.get("customer"), dict) else {}
    name = (
        order.get("bill_full_name")
        or order.get("customer_name")
        or cust.get("name")
        or "(no name)"
    )
    phone = order.get("bill_phone_number") or order.get("customer_phone") or ""
    phone_s = "(masked)" if phone and "*" in str(phone) else ("(missing)" if not phone else "ok")
    amount = order.get("total_price") or order.get("amount") or order.get("cod_amount") or ""
    return f"[{backend}] #{oid} · {status} · {name} · phone={phone_s} · amt={amount}"


def sync_pancake(env: dict[str, str], state: dict, limit: int) -> dict[str, Any]:
    creds = resolve_credentials()
    result: dict[str, Any] = {
        "backend": "Pancake",
        "status": "skipped",
        "new_orders": [],
        "fetched": 0,
        "detail": "",
    }
    if not auth_ready(creds):
        result["status"] = "missing_cred"
        result["detail"] = "Cần PANCAKE_POS_API_KEY hoặc Bearer trong secrets/"
        state.setdefault("backends", {})["Pancake"] = {
            "status": "missing_cred",
            "checked_at": utc_now(),
            "detail": result["detail"],
        }
        return result

    shop_ids = [
        s.strip()
        for s in (env.get("PANCAKE_POS_SHOP_IDS") or env.get("PANCAKE_SHOP_IDS") or "1530618").split(",")
        if s.strip()
    ]
    base_env = (env.get("PANCAKE_POS_BASE_URL") or "").strip()
    new_orders: list[dict] = []
    fetched = 0
    try:
        if not shop_ids:
            shops, base = fetch_shops(creds)
            shop_ids = [str(s.get("id")) for s in shops if s.get("id")]
            used_base = base
        else:
            used_base = base_env or "https://pos.pancake.vn/api/v1"

        for shop_id in shop_ids:
            orders = fetch_shop_orders(
                creds,
                shop_id,
                used_base,
                params={"limit": limit, "page_number": 1, "page": 1},
            )
            fetched += len(orders)
            for order in orders:
                order = dict(order)
                order["shop_id"] = shop_id
                order["_backend"] = "Pancake"
                fp = order_fingerprint("Pancake", order)
                if fp in state.setdefault("seen_orders", {}):
                    continue
                state["seen_orders"][fp] = {"at": utc_now(), "backend": "Pancake", "shop_id": shop_id}
                new_orders.append(order)
        result["status"] = "ok"
        result["detail"] = f"shops={','.join(shop_ids)} base={used_base}"
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["detail"] = str(exc)[:200]

    result["new_orders"] = new_orders
    result["fetched"] = fetched
    state.setdefault("backends", {})["Pancake"] = {
        "status": result["status"],
        "checked_at": utc_now(),
        "fetched": fetched,
        "new": len(new_orders),
        "detail": result["detail"],
    }
    return result


def sync_inbox_files(state: dict) -> dict[str, Any]:
    """Telegram upload / direct_api: phát hiện file inbox mới hoặc đổi nội dung."""
    result: dict[str, Any] = {
        "backend": "Telegram+direct_api",
        "status": "ok",
        "new_orders": [],
        "new_files": [],
        "detail": "",
    }
    if not INBOX.is_dir():
        result["status"] = "missing_inbox"
        result["detail"] = "quarantine/telegram trống"
        return result

    known = state.setdefault("inbox_files", {})
    new_files = []
    for path in sorted(INBOX.iterdir()):
        if not path.is_file():
            continue
        name_l = path.name.lower()
        if not (
            path.name.startswith("orders_detailed_")
            or path.name.lower() == "thanhcoong.xlsx"
            or ("don" in name_l and path.suffix.lower() in {".csv", ".json", ".xlsx"})
        ):
            continue
        st = path.stat()
        meta = {"mtime": st.st_mtime, "size": st.st_size}
        prev = known.get(path.name)
        if prev and prev.get("mtime") == meta["mtime"] and prev.get("size") == meta["size"]:
            continue
        known[path.name] = meta
        new_files.append(path.name)

        # Parse new/changed order files for new order keys
        try:
            rows: list[dict] = []
            if path.suffix.lower() == ".csv":
                with path.open(encoding="utf-8", errors="replace", newline="") as f:
                    rows = list(csv.DictReader(f))
            elif path.suffix.lower() == ".json":
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, list):
                    rows = [r for r in data if isinstance(r, dict)]
                elif isinstance(data, dict):
                    for k in ("orders", "data", "items"):
                        if isinstance(data.get(k), list):
                            rows = [r for r in data[k] if isinstance(r, dict)]
                            break
            backend = "direct_api"
            for r in rows[:5000]:
                src = str(r.get("source") or "").lower()
                if "telegram" in src:
                    backend = "Telegram"
                elif "pancake" in src:
                    backend = "Pancake-file"
                elif "direct_api" in src:
                    backend = "direct_api"
                elif "sample" in src:
                    continue
                fp = order_fingerprint(backend, r)
                if fp in state.setdefault("seen_orders", {}):
                    continue
                state["seen_orders"][fp] = {"at": utc_now(), "backend": backend, "file": path.name}
                r = dict(r)
                r["_backend"] = backend
                r["_file"] = path.name
                result["new_orders"].append(r)
        except Exception as exc:  # noqa: BLE001
            result["detail"] += f" parse:{path.name}:{exc};"

    result["new_files"] = new_files
    result["detail"] = f"new_files={len(new_files)} new_orders={len(result['new_orders'])}"
    state.setdefault("backends", {})["Telegram+direct_api"] = {
        "status": result["status"],
        "checked_at": utc_now(),
        "new_files": len(new_files),
        "new_orders": len(result["new_orders"]),
        "detail": result["detail"],
    }
    return result


def sync_ghn_probe(env: dict[str, str], state: dict) -> dict[str, Any]:
    """GHN: chỉ giữ ống sống (province). Đơn GHN chi tiết cần ShopId+API riêng."""
    token = (env.get("GHN_API_TOKEN") or "").strip()
    result = {"backend": "GHN", "status": "missing_cred", "new_orders": [], "detail": ""}
    if not token:
        result["detail"] = "Thiếu GHN_API_TOKEN — không cập nhật đơn GHN realtime"
        state.setdefault("backends", {})["GHN"] = {
            "status": "missing_cred",
            "checked_at": utc_now(),
            "detail": result["detail"],
        }
        return result
    try:
        resp = __import__("requests").post(
            "https://dev-online-gateway.ghn.vn/shiip/public-api/master-data/province",
            headers={"Token": token, "Content-Type": "application/json"},
            json={},
            timeout=20,
        )
        result["status"] = "ok" if resp.ok else "auth_fail"
        result["detail"] = f"province http={resp.status_code} (pipe sống; order stream cần shop API)"
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["detail"] = str(exc)[:160]
    state.setdefault("backends", {})["GHN"] = {
        "status": result["status"],
        "checked_at": utc_now(),
        "detail": result["detail"],
    }
    return result


def sync_buucuc_remote(env: dict[str, str], state: dict, limit: int) -> dict[str, Any]:
    """Quét đơn bưu cục remote (GHN/VTP/SPX/VNPost/Pancake) — không đọc danh_sach."""
    result: dict[str, Any] = {
        "backend": "Buucuc-scan",
        "status": "error",
        "new_orders": [],
        "fetched": 0,
        "detail": "",
    }
    try:
        from scan_buucuc_orders import build_report

        report = build_report(days=3, limit=limit, pipe=True, write_cache=True, notify=False)
        orders = report.get("orders") or []
        new_orders: list[dict] = []
        for o in orders:
            fp = order_fingerprint(
                str(o.get("backend") or "Buucuc"),
                {"id": o.get("order_id"), "order_id": o.get("order_id"), "shop_id": o.get("shop_id"), "status_name": o.get("status")},
            )
            if fp in state.setdefault("seen_orders", {}):
                continue
            state["seen_orders"][fp] = {"at": utc_now(), "backend": o.get("backend"), "buucuc": o.get("buucuc")}
            new_orders.append(o)
        result["status"] = "ok" if not report.get("blockers") or orders else "missing_cred"
        if report.get("blockers") and not orders:
            result["status"] = "missing_cred"
        result["fetched"] = len(orders)
        result["new_orders"] = new_orders
        result["detail"] = report.get("verdict") or f"scanned={len(orders)} new={len(new_orders)}"
        result["blockers"] = report.get("blockers") or []
        result["by_buucuc"] = report.get("by_buucuc") or {}
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["detail"] = str(exc)[:200]
    state.setdefault("backends", {})["Buucuc-scan"] = {
        "status": result["status"],
        "checked_at": utc_now(),
        "detail": result["detail"],
        "fetched": result.get("fetched"),
    }
    return result

def sync_tpos_probe(env: dict[str, str], state: dict) -> dict[str, Any]:
    base = (env.get("TPOS_BASE_URL") or "").rstrip("/")
    token = (env.get("TPOS_ACCESS_TOKEN") or "").strip()
    result = {"backend": "TPOS", "status": "missing_cred", "new_orders": [], "detail": ""}
    if not base or not token:
        result["detail"] = "Thiếu TPOS_BASE_URL + TPOS_ACCESS_TOKEN"
        state.setdefault("backends", {})["TPOS"] = {
            "status": "missing_cred",
            "checked_at": utc_now(),
            "detail": result["detail"],
        }
        return result
    try:
        resp = __import__("requests").get(
            f"{base}/odata",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        )
        result["status"] = "ok" if resp.status_code < 500 else "error"
        result["detail"] = f"odata http={resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["detail"] = str(exc)[:160]
    state.setdefault("backends", {})["TPOS"] = {
        "status": result["status"],
        "checked_at": utc_now(),
        "detail": result["detail"],
    }
    return result


def sync_spx_local(state: dict) -> dict[str, Any]:
    """SPX 3PL local (thanhcoong.xlsx) — mở rộng realtime theo Tracking No."""
    from oms_interconnect import normalize_from_thanhcoong, read_xlsx_rows

    result: dict[str, Any] = {
        "backend": "SPX-local",
        "status": "ok",
        "new_orders": [],
        "fetched": 0,
        "detail": "",
    }
    path = INBOX / "thanhcoong.xlsx"
    if not path.is_file():
        result["status"] = "missing_file"
        result["detail"] = "thiếu quarantine/telegram/thanhcoong.xlsx"
        state.setdefault("backends", {})["SPX-local"] = {
            "status": result["status"],
            "checked_at": utc_now(),
            "detail": result["detail"],
        }
        return result

    st = path.stat()
    meta = {"mtime": st.st_mtime, "size": st.st_size}
    known = state.setdefault("inbox_files", {})
    file_changed = True
    prev = known.get(path.name)
    if prev and prev.get("mtime") == meta["mtime"] and prev.get("size") == meta["size"]:
        file_changed = False
    known[path.name] = meta

    try:
        rows = read_xlsx_rows(path)
        new_orders: list[dict] = []
        for r in rows:
            rec = normalize_from_thanhcoong(r)
            if not rec:
                continue
            result["fetched"] += 1
            # fingerprint by tracking
            oid = rec.get("tracking_code") or rec.get("order_key") or ""
            fp = order_fingerprint("SPX-local", {"order_key": oid, "id": oid, "shop_id": rec.get("shop_id")})
            if fp in state.setdefault("seen_orders", {}):
                continue
            state["seen_orders"][fp] = {
                "at": utc_now(),
                "backend": "SPX-local",
                "file": path.name,
                "tracking": oid,
            }
            oo = dict(rec)
            oo["_backend"] = "SPX-local"
            oo["_file"] = path.name
            oo["id"] = oid
            oo["_realtime_new"] = True
            # enrich times from raw sheet when present
            oo["created_at"] = r.get("Create Time") or r.get("Thời gian tạo")
            oo["delivered_at"] = r.get("Delivered Time") or r.get("Thời gian giao")
            oo["picked_at"] = r.get("Actual Pickup/Drop Off Time")
            new_orders.append(oo)
        result["new_orders"] = new_orders
        result["detail"] = (
            f"file={path.name} changed={file_changed} rows={result['fetched']} "
            f"new={len(new_orders)}"
        )
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["detail"] = str(exc)[:200]

    state.setdefault("backends", {})["SPX-local"] = {
        "status": result["status"],
        "checked_at": utc_now(),
        "fetched": result.get("fetched") or 0,
        "new": len(result.get("new_orders") or []),
        "detail": result["detail"],
    }
    return result


def sync_vnpost_local(state: dict) -> dict[str, Any]:
    """VNPost file đối soát — theo dõi file mới (chưa parse đơn đầy đủ)."""
    result: dict[str, Any] = {
        "backend": "VNPost-local",
        "status": "ok",
        "new_orders": [],
        "new_files": [],
        "detail": "",
    }
    if not INBOX.is_dir():
        result["status"] = "missing_inbox"
        result["detail"] = "quarantine/telegram trống"
        return result
    known = state.setdefault("inbox_files", {})
    new_files = []
    for path in sorted(INBOX.iterdir()):
        if not path.is_file():
            continue
        if not path.name.lower().startswith("vnpost"):
            continue
        st = path.stat()
        meta = {"mtime": st.st_mtime, "size": st.st_size}
        prev = known.get(path.name)
        if prev and prev.get("mtime") == meta["mtime"] and prev.get("size") == meta["size"]:
            continue
        known[path.name] = meta
        new_files.append(path.name)
    result["new_files"] = new_files
    result["detail"] = f"vnpost_files_new={len(new_files)} (topology/đối soát; chưa map order rows)"
    state.setdefault("backends", {})["VNPost-local"] = {
        "status": result["status"],
        "checked_at": utc_now(),
        "new_files": len(new_files),
        "detail": result["detail"],
    }
    return result


def write_snapshot(cycle: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "realtime_latest.json"
    path.write_text(json.dumps(cycle, ensure_ascii=False, indent=2), encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    (OUT / f"realtime_{stamp}.json").write_text(
        json.dumps(cycle, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # trim old stamped files keep 20
    stamped = sorted(OUT.glob("realtime_2*.json"))
    for old in stamped[:-20]:
        try:
            old.unlink()
        except OSError:
            pass
    return path


def format_cycle(cycle: dict) -> str:
    lines = [
        "⏱ REALTIME ĐƠN HÀNG — từng backend",
        f"Lúc: {cycle.get('checked_at')}",
        "",
    ]
    owned = cycle.get("owned") or {}
    if owned:
        lines.append(f"Owned env: {owned.get('verdict')}")
        lines.append(f"ready_platforms={owned.get('ready_platforms')} accounts={owned.get('total_accounts')}")
        lines.append("")
    te = cycle.get("token_ensure") or {}
    if te:
        lines.append(f"Access token: {te.get('verdict')}")
        if te.get("refreshed"):
            lines.append(f"refreshed={te.get('refreshed')}")
        lines.append("")
    for b in cycle.get("backends") or []:
        lines.append(
            f"· {b['backend']}: {b.get('status')} · new={len(b.get('new_orders') or [])} · {b.get('detail','')[:100]}"
        )
    news = cycle.get("all_new_orders") or []
    lines.append("")
    lines.append(f"Đơn mới phát hiện: {len(news)}")
    for o in news[:12]:
        own = f" · owned={o.get('owned_user')}" if o.get("owned_user") else ""
        lines.append(f"  - {summarize_order(o.get('_backend') or '?', o)}{own}")
    if len(news) > 12:
        lines.append(f"  … +{len(news)-12} đơn nữa")
    if cycle.get("blocked"):
        lines.append("")
        lines.append("Chưa realtime API được:")
        for x in cycle["blocked"]:
            lines.append(f"  ⚠ {x}")
    return "\n".join(lines)


def run_cycle(env: dict[str, str], limit: int, notify: bool, notify_new_only: bool) -> dict:
    token_ensure: dict = {"ok": False, "skipped": True}
    try:
        from access_token_rotate import ensure_tokens

        # Probe + auto-refresh ViettelPost khi auth_fail/missing (owned USER/PASSWORD).
        token_ensure = ensure_tokens(auto_refresh_vtp=True)
        env = load_env()  # reload sau refresh
    except Exception as e:  # noqa: BLE001
        token_ensure = {"ok": False, "error": str(e)[:160]}

    try:
        from owned_credentials import apply_owned_mapping, mapping_summary

        owned = mapping_summary(env)
    except Exception as e:  # noqa: BLE001
        owned = {"ok": False, "error": str(e)[:120], "ready_platforms": []}
        apply_owned_mapping = None  # type: ignore

    state = load_state()
    # prune seen_orders if huge
    seen = state.setdefault("seen_orders", {})
    if len(seen) > 20000:
        # keep newest 10000 by at
        items = sorted(seen.items(), key=lambda kv: kv[1].get("at") or "", reverse=True)[:10000]
        state["seen_orders"] = dict(items)

    backends = [
        sync_pancake(env, state, limit=limit),
        sync_inbox_files(state),
        sync_spx_local(state),
        sync_vnpost_local(state),
        sync_ghn_probe(env, state),
        sync_tpos_probe(env, state),
        sync_buucuc_remote(env, state, limit=limit),
    ]
    all_new: list[dict] = []
    for b in backends:
        for o in b.get("new_orders") or []:
            if apply_owned_mapping:
                try:
                    o = apply_owned_mapping(o, env)
                except Exception:  # noqa: BLE001
                    pass
            all_new.append(o)
        if apply_owned_mapping:
            b["new_orders"] = [
                apply_owned_mapping(o, env) if isinstance(o, dict) else o
                for o in (b.get("new_orders") or [])
            ]

    blocked = []
    for b in backends:
        if b.get("status") in {"missing_cred", "auth_fail"}:
            blocked.append(f"{b['backend']}: {b.get('detail')}")

    cycle = {
        "ok": True,
        "checked_at": utc_now(),
        "token_ensure": {
            "ready_platforms": token_ensure.get("ready_platforms") or [],
            "refreshed": token_ensure.get("refreshed") or [],
            "verdict": token_ensure.get("verdict") or token_ensure.get("error"),
        },
        "owned": {
            "ready_platforms": owned.get("ready_platforms") or [],
            "total_accounts": owned.get("total_accounts"),
            "verdict": owned.get("verdict"),
        },
        "backends": [
            {
                "backend": b["backend"],
                "status": b.get("status"),
                "detail": b.get("detail"),
                "fetched": b.get("fetched"),
                "new_files": b.get("new_files"),
                "new_orders": [
                    {
                        "id": o.get("id") or o.get("order_key") or o.get("remote_id"),
                        "shop_id": o.get("shop_id"),
                        "_backend": o.get("_backend"),
                        "_file": o.get("_file"),
                        "status": o.get("status_name") or o.get("status_normalized"),
                        "owned_user": o.get("owned_user"),
                        "owned_ready": o.get("owned_ready"),
                        "customer_phone_status": (
                            "masked"
                            if "*" in str(o.get("customer_phone") or o.get("bill_phone_number") or "")
                            else (
                                "missing"
                                if not (o.get("customer_phone") or o.get("bill_phone_number"))
                                else "ok"
                            )
                        ),
                    }
                    for o in (b.get("new_orders") or [])[:50]
                ],
            }
            for b in backends
        ],
        "all_new_orders": all_new[:100],
        "new_count": len(all_new),
        "blocked": blocked,
        "policy": "secrets-only realtime; owned user/token env mapping; no credential dumps",
    }
    save_state(state)
    write_snapshot(cycle)
    text = format_cycle(cycle)
    (OUT / "realtime_latest.txt").write_text(text, encoding="utf-8")
    if notify and (not notify_new_only or all_new or blocked):
        send_telegram(env, text)
    return cycle


def main() -> int:
    ap = argparse.ArgumentParser(description="Realtime order updates per backend")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=60, help="Seconds between polls")
    ap.add_argument("--limit", type=int, default=20, help="Pancake orders per shop per poll")
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--notify-new-only", action="store_true", help="Telegram chỉ khi có đơn mới / blocker")
    args = ap.parse_args()
    env = load_env()

    if args.loop:
        while True:
            cycle = run_cycle(env, limit=max(1, args.limit), notify=args.notify, notify_new_only=args.notify_new_only)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "new": cycle["new_count"],
                        "at": cycle["checked_at"],
                        "blocked": len(cycle.get("blocked") or []),
                    },
                    ensure_ascii=False,
                )
            )
            time.sleep(max(15, args.interval))
    else:
        cycle = run_cycle(
            env,
            limit=max(1, args.limit),
            notify=bool(args.notify),
            notify_new_only=bool(args.notify_new_only),
        )
        print(json.dumps(cycle, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
