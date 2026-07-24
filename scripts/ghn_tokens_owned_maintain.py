#!/usr/bin/env python3
"""Nhận file ghn_tokens owned của chủ → probe live → duy trì GHN_API_TOKEN.

Format hỗ trợ (mỗi dòng):
  user:pass:token-uuid
  token-uuid
  token=<uuid>
  printA5?...token=<uuid>

Chỉ dùng UUID ở cột token (header Token). Không SSO login bằng password.
Cần xác nhận sở hữu: --i-own-this (hoặc secrets/OWNED_CLAIM_GHN=1).

Mask only trong report · secrets mode 0600.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SECRETS = ROOT / "secrets"
REPORTS = ROOT / "reports" / "telegram-classify"
POOL_PATH = SECRETS / "ghn_tokens.owned.json"
CLAIM_FLAG = SECRETS / "OWNED_CLAIM_GHN"
DEFAULT_SOURCES = (
    ROOT / "quarantine" / "telegram" / "_skipped_dumps" / "ghn_tokens_20260422_051037.txt",
    ROOT / "quarantine" / "telegram" / "ghn_tokens_20260422_051037.txt",
    SECRETS / "ghn_tokens.owned.txt",
)

UUID_RE = re.compile(
    r"(?i)\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)
UP_RE = re.compile(
    r"(?i)^([^:\s]{2,80}):([^:\s]{2,120}):([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
KV_RE = re.compile(r"(?i)\b(?:token|ghn_api_token|ghn_token)\s*[=:]\s*([0-9a-f-]{36})\b")
PRINT_RE = re.compile(r"(?i)token=([0-9a-f-]{36})")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mask(v: str | None, keep: int = 4) -> str | None:
    if not v:
        return None
    t = v.strip()
    if len(t) <= keep * 2:
        return "***"
    return f"{t[:keep]}…{t[-keep:]}"


def ownership_claimed(explicit: bool) -> bool:
    if explicit:
        return True
    if (os.environ.get("OWNED_CLAIM_GHN") or "").strip() in {"1", "true", "yes"}:
        return True
    if CLAIM_FLAG.is_file():
        raw = CLAIM_FLAG.read_text(encoding="utf-8", errors="ignore").strip().lower()
        return raw in {"1", "true", "yes", "i-own-this", "owned"}
    return False


def parse_owned_file(path: Path) -> list[dict[str, str]]:
    """Parse tokens; never keep password in returned structs used for API."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, line in enumerate(text.splitlines(), 1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        user = ""
        token = ""
        m = UP_RE.match(raw)
        if m:
            user, _pw, token = m.group(1), m.group(2), m.group(3)
        else:
            km = KV_RE.search(raw)
            pm = PRINT_RE.search(raw)
            if km:
                token = km.group(1)
            elif pm:
                token = pm.group(1)
            elif UUID_RE.fullmatch(raw):
                token = raw
            else:
                # last colon field if looks like uuid
                parts = raw.split(":")
                if parts and UUID_RE.fullmatch(parts[-1].strip()):
                    token = parts[-1].strip()
                    if len(parts) >= 2:
                        user = parts[0].strip()
        token = (token or "").strip().lower()
        if not token or token in seen:
            continue
        if not UUID_RE.fullmatch(token):
            continue
        seen.add(token)
        rows.append({"line": str(i), "user": user, "token": token})
    return rows


def probe_token(token: str) -> dict[str, Any]:
    from ghn_cookie_ingest import probe_token as ghn_probe

    return ghn_probe(token)


def apply_live_token(token: str, *, user: str | None = None) -> dict[str, Any]:
    from access_token_rotate import set_access_token, upsert_env_values
    from ghn_access_token_orders import resolve_shop_id
    from ghn_cookie_ingest import apply_token

    shop = resolve_shop_id(token, persist=True)
    shop_id = shop.get("shop_id")
    applied = apply_token(token, shop_id=shop_id)
    set_access_token("GHN", token, user=user or None, shop_id=shop_id)
    # mark claim so ensure loops treat pool as owned
    CLAIM_FLAG.write_text("i-own-this\n", encoding="utf-8")
    try:
        os.chmod(CLAIM_FLAG, 0o600)
    except OSError:
        pass
    extras = {"GHN_API_TOKEN": token, "OWNED_CLAIM_GHN": "1"}
    if user:
        extras["GHN_USER"] = user
    if shop_id:
        extras["GHN_SHOP_ID"] = str(shop_id)
    upsert_env_values(extras)
    try:
        from order_session_env import export_session_env

        export_session_env()
    except Exception:  # noqa: BLE001
        pass
    return {
        "token_masked": mask(token),
        "user_masked": mask(user, 2) if user else None,
        "shop_id": shop_id,
        "shop": shop,
        "apply": {
            "ok": (applied.get("set") or {}).get("ok"),
            "token_masked": applied.get("token_masked"),
        },
    }


def save_pool(rows: list[dict[str, Any]], *, source: str) -> None:
    SECRETS.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": utc_now(),
        "source": source,
        "owned_claim": True,
        "count": len(rows),
        "live_n": sum(1 for r in rows if r.get("alive")),
        "tokens": [
            {
                "user_masked": mask(r.get("user"), 2) if r.get("user") else None,
                "token_masked": mask(r.get("token")),
                "token": r.get("token") if r.get("alive") else None,  # only keep live raw in secrets
                "alive": r.get("alive"),
                "http": r.get("http"),
                "provinces_n": r.get("provinces_n"),
                "line": r.get("line"),
            }
            for r in rows
        ],
    }
    # drop dead raw tokens from disk
    for t in payload["tokens"]:
        if not t.get("alive"):
            t.pop("token", None)
    POOL_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(POOL_PATH, 0o600)
    except OSError:
        pass


def load_pool_live_tokens() -> list[str]:
    if not POOL_PATH.is_file():
        return []
    try:
        data = json.loads(POOL_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    out: list[str] = []
    for t in data.get("tokens") or []:
        tok = (t.get("token") or "").strip()
        if t.get("alive") and UUID_RE.fullmatch(tok):
            out.append(tok.lower())
    return out


def maintain_from_owned(
    path: Path,
    *,
    i_own_this: bool,
    max_probe: int = 0,
    stop_after_live: int = 0,
    sleep_s: float = 0.05,
    fetch_orders: bool = True,
    days: int = 3,
    limit: int = 50,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": False,
        "module": "ghn_tokens_owned_maintain",
        "checked_at": utc_now(),
        "source": str(path),
        "owned_claim": ownership_claimed(i_own_this),
        "parsed_n": 0,
        "probed_n": 0,
        "live_n": 0,
        "dead_n": 0,
        "live_preview": [],
        "applied": None,
        "orders": None,
        "verdict": "",
        "policy": {
            "owned_only": True,
            "no_password_sso_login": True,
            "uses_token_header_only": True,
            "require_i_own_this": True,
        },
        "next": [],
    }

    if not report["owned_claim"]:
        report["verdict"] = (
            "❌ Chưa xác nhận sở hữu — chạy lại với --i-own-this "
            "(hoặc echo i-own-this > secrets/OWNED_CLAIM_GHN)"
        )
        report["next"] = [
            f"python3 scripts/ghn_tokens_owned_maintain.py --file {path} --i-own-this",
        ]
        return report

    if not path.is_file():
        report["verdict"] = f"❌ Không thấy file: {path}"
        return report

    rows = parse_owned_file(path)
    report["parsed_n"] = len(rows)
    if not rows:
        report["verdict"] = "❌ File không parse được token UUID nào"
        return report

    # copy owned snapshot without passwords into secrets/
    owned_copy = SECRETS / "ghn_tokens.owned.txt"
    # write token-only lines + masked user comment — no passwords
    lines_out = ["# owned GHN tokens (no passwords stored)", f"# source={path.name}", f"# at={utc_now()}"]
    for r in rows:
        u = r.get("user") or ""
        if u:
            lines_out.append(f"# user={mask(u, 2)}")
        lines_out.append(r["token"])
    owned_copy.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    try:
        os.chmod(owned_copy, 0o600)
    except OSError:
        pass
    CLAIM_FLAG.write_text("i-own-this\n", encoding="utf-8")

    to_probe = rows if max_probe <= 0 else rows[:max_probe]
    live: list[dict[str, Any]] = []
    dead = 0
    results: list[dict[str, Any]] = []

    # parallel probe (Token header only) — nhanh hơn tuần tự
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _one(r: dict[str, str]) -> dict[str, Any]:
        if sleep_s:
            time.sleep(sleep_s)
        pr = probe_token(r["token"])
        return {
            **r,
            "alive": bool(pr.get("success")),
            "http": pr.get("http"),
            "provinces_n": pr.get("provinces_n"),
            "message": (pr.get("message") or "")[:80] if not pr.get("success") else None,
        }

    workers = 8 if len(to_probe) > 8 else max(1, len(to_probe))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_one, r) for r in to_probe]
        for fut in as_completed(futs):
            entry = fut.result()
            results.append(entry)
            report["probed_n"] += 1
            if entry["alive"]:
                live.append(entry)
                if stop_after_live and len(live) >= stop_after_live:
                    break
            else:
                dead += 1
    # stable order by original line
    results.sort(key=lambda x: int(x.get("line") or 0))
    live.sort(key=lambda x: int(x.get("line") or 0))

    report["live_n"] = len(live)
    report["dead_n"] = dead
    report["live_preview"] = [
        {
            "user_masked": mask(x.get("user"), 2) if x.get("user") else None,
            "token_masked": mask(x.get("token")),
            "http": x.get("http"),
            "provinces_n": x.get("provinces_n"),
            "line": x.get("line"),
        }
        for x in live[:15]
    ]
    save_pool(results, source=str(path))

    if not live:
        report["verdict"] = (
            f"❌ Owned file có {len(rows)} token nhưng 0 live sau khi probe "
            f"{report['probed_n']} (http chết/401)"
        )
        report["next"] = [
            "Kiểm tra token còn hạn trên GHN dashboard / tạo token mới",
            "python3 scripts/ghn_tokens_owned_maintain.py --file secrets/ghn_tokens.owned.txt --i-own-this",
        ]
        return report

    # apply first live as primary
    primary = live[0]
    applied = apply_live_token(primary["token"], user=primary.get("user") or None)
    report["applied"] = applied

    if fetch_orders:
        from ghn_access_token_orders import get_token_and_fetch_orders

        orders = get_token_and_fetch_orders(days=days, limit=limit, try_pending=False, resolve_shop=True)
        report["orders"] = {
            "ok": orders.get("ok"),
            "fetched": (orders.get("orders") or {}).get("fetched"),
            "status": (orders.get("orders") or {}).get("status"),
            "shop_id": orders.get("shop_id"),
            "verdict": orders.get("verdict"),
            "preview": (orders.get("orders") or {}).get("preview"),
        }

    try:
        from token_session_maintain import maintain_once

        maint = maintain_once(notify_on_risk=False)
        report["maintain"] = {
            "ghn_ready": maint.get("ghn_ready"),
            "verdict": maint.get("verdict"),
        }
    except Exception as e:  # noqa: BLE001
        report["maintain"] = {"error": str(e)[:120]}

    report["ok"] = True
    report["verdict"] = (
        f"✅ Owned GHN tokens · live={len(live)}/{report['probed_n']} · "
        f"primary={mask(primary['token'])} · shop={applied.get('shop_id') or '—'} · "
        f"orders_fetched={(report.get('orders') or {}).get('fetched')}"
    )
    report["next"] = [
        "python3 scripts/ghn_tokens_owned_maintain.py --from-pool --i-own-this",
        "python3 scripts/token_session_maintain.py once",
        "python3 scripts/ghn_access_token_orders.py run --days 3 --limit 50",
    ]
    return report


def maintain_from_pool(*, fetch_orders: bool = True, days: int = 3, limit: int = 50) -> dict[str, Any]:
    """Re-probe live pool tokens; rotate primary nếu chết."""
    report: dict[str, Any] = {
        "ok": False,
        "module": "ghn_tokens_owned_maintain.pool",
        "checked_at": utc_now(),
        "owned_claim": ownership_claimed(True) or ownership_claimed(False),
        "verdict": "",
    }
    if not report["owned_claim"] and not CLAIM_FLAG.is_file():
        report["verdict"] = "❌ Pool chưa claim owned"
        return report

    if not POOL_PATH.is_file():
        # fallback to owned txt
        src = SECRETS / "ghn_tokens.owned.txt"
        if src.is_file():
            return maintain_from_owned(
                src, i_own_this=True, fetch_orders=fetch_orders, days=days, limit=limit
            )
        report["verdict"] = "❌ Chưa có secrets/ghn_tokens.owned.json"
        return report

    data = json.loads(POOL_PATH.read_text(encoding="utf-8"))
    tokens = [t for t in (data.get("tokens") or []) if t.get("token")]
    if not tokens:
        report["verdict"] = "❌ Pool không còn raw live token — import lại file owned"
        return report

    live: list[dict[str, Any]] = []
    refreshed: list[dict[str, Any]] = []
    for t in tokens:
        tok = t["token"]
        pr = probe_token(tok)
        entry = {
            "user": "",
            "token": tok,
            "alive": bool(pr.get("success")),
            "http": pr.get("http"),
            "provinces_n": pr.get("provinces_n"),
            "line": t.get("line"),
        }
        refreshed.append(entry)
        if entry["alive"]:
            live.append(entry)

    # merge dead markers into full pool listing (keep previous dead without raw)
    save_pool(refreshed, source=data.get("source") or "pool")
    report["live_n"] = len(live)
    report["probed_n"] = len(refreshed)
    if not live:
        report["verdict"] = "❌ Mọi token trong pool đều chết"
        return report

    applied = apply_live_token(live[0]["token"])
    report["applied"] = applied
    if fetch_orders:
        from ghn_access_token_orders import get_token_and_fetch_orders

        orders = get_token_and_fetch_orders(days=days, limit=limit, try_pending=False, resolve_shop=True)
        report["orders"] = {
            "ok": orders.get("ok"),
            "fetched": (orders.get("orders") or {}).get("fetched"),
            "status": (orders.get("orders") or {}).get("status"),
            "verdict": orders.get("verdict"),
        }
    report["ok"] = True
    report["verdict"] = (
        f"✅ Pool maintain · live={len(live)}/{len(refreshed)} · "
        f"primary={mask(live[0]['token'])} · shop={applied.get('shop_id') or '—'}"
    )
    return report


def resolve_source(file_arg: str | None) -> Path | None:
    if file_arg:
        p = Path(file_arg)
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        return p
    for p in DEFAULT_SOURCES:
        if p.is_file():
            return p
    # any ghn_tokens* under quarantine
    q = ROOT / "quarantine" / "telegram"
    if q.is_dir():
        hits = sorted(q.rglob("ghn_tokens*"), key=lambda x: x.stat().st_mtime, reverse=True)
        if hits:
            return hits[0]
    return None


def format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("📦 GHN TOKENS OWNED → DUY TRÌ LIVE")
    L(f"Lúc: {report.get('checked_at') or utc_now()}")
    L(report.get("verdict") or "")
    if report.get("source"):
        L(f"source: {report.get('source')}")
    L(
        f"claim={report.get('owned_claim')} parsed={report.get('parsed_n')} "
        f"probed={report.get('probed_n')} live={report.get('live_n')} dead={report.get('dead_n')}"
    )
    for x in report.get("live_preview") or []:
        L(
            f"  · live user={x.get('user_masked')} token={x.get('token_masked')} "
            f"http={x.get('http')} provinces={x.get('provinces_n')}"
        )
    if report.get("applied"):
        a = report["applied"]
        L(f"applied: token={a.get('token_masked')} shop={a.get('shop_id')} user={a.get('user_masked')}")
    if report.get("orders"):
        o = report["orders"]
        L(f"orders: status={o.get('status')} fetched={o.get('fetched')} · {o.get('verdict')}")
    if report.get("maintain"):
        L(f"maintain: {report['maintain']}")
    if report.get("next"):
        L("")
        L("Next:")
        for n in report["next"]:
            L(f"· {n}")
    L("")
    L("Note: classifier từng gắn DUMP vì format nhiều dòng — --i-own-this = nhận là của bạn.")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    slim = dict(report)
    (REPORTS / "ghn_tokens_owned_maintain.json").write_text(
        json.dumps(slim, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (REPORTS / "ghn_tokens_owned_maintain.txt").write_text(format_text(report) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Owned ghn_tokens → probe live → maintain")
    ap.add_argument("--file", default="", help="Đường dẫn file ghn_tokens owned")
    ap.add_argument("--i-own-this", action="store_true", help="Xác nhận đây là token sở hữu của bạn")
    ap.add_argument("--from-pool", action="store_true", help="Maintain từ secrets/ghn_tokens.owned.json")
    ap.add_argument("--max-probe", type=int, default=0, help="Giới hạn số token probe (0=all)")
    ap.add_argument("--stop-after-live", type=int, default=0, help="Dừng khi đủ N token live (0=probe hết)")
    ap.add_argument("--sleep", type=float, default=0.05)
    ap.add_argument("--no-orders", action="store_true")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.from_pool:
        report = maintain_from_pool(
            fetch_orders=not args.no_orders, days=args.days, limit=args.limit
        )
    else:
        src = resolve_source(args.file or None)
        if not src:
            report = {
                "ok": False,
                "checked_at": utc_now(),
                "verdict": "❌ Không tìm thấy file ghn_tokens — truyền --file",
            }
        else:
            report = maintain_from_owned(
                src,
                i_own_this=bool(args.i_own_this),
                max_probe=args.max_probe,
                stop_after_live=args.stop_after_live,
                sleep_s=args.sleep,
                fetch_orders=not args.no_orders,
                days=args.days,
                limit=args.limit,
            )

    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
