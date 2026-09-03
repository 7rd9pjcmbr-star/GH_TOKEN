#!/usr/bin/env python3
"""Unified session store — persist owned tokens + cookie sessions and keep both alive.

Đồng thời:
  • LƯU  : token (Bearer / API key) + cookie session cho từng nền tảng
  • DUY TRÌ: kiểm tra hạn (JWT exp + cookie expires), refresh token owned, probe giữ ấm phiên

Chính sách (owned-only):
  • Chỉ credential SỞ HỮU — không dump-login, không paste cookie/stealer lạ.
  • KHÔNG tự đăng nhập (không điền form login). Chỉ tái sử dụng token/cookie đã có.
  • Report chỉ hiển thị token/cookie ở dạng MASK — không in raw secret.

Lưu trữ: secrets/session_store.json (gitignored, chmod 600).
Có thể override đường dẫn qua biến môi trường SESSION_STORE_PATH (dùng cho test).

CLI:
  python3 scripts/session_store.py status
  python3 scripts/session_store.py set --platform Pancake --from-env
  python3 scripts/session_store.py set --platform GHN --token GHN_API_TOKEN=... 
  python3 scripts/session_store.py import-state --platform Pancake --file pancake_storage_state.json
  python3 scripts/session_store.py apply          # export token → os.environ
  python3 scripts/session_store.py ensure         # keepalive 1 lần (refresh + probe)
  python3 scripts/session_store.py daemon --interval 300   # duy trì liên tục
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SECRETS = ROOT / "secrets"
DEFAULT_STORE = SECRETS / "session_store.json"

# Token env-key aliases per platform (reuse the same names the rest of the system reads).
PLATFORM_TOKEN_KEYS: dict[str, tuple[str, ...]] = {
    "Pancake": ("PANCAKE_POS_ACCESS_TOKEN", "PANCAKE_POS_API_KEY", "PANCAKE_API_KEY"),
    "GHN": ("GHN_API_TOKEN",),
    "ViettelPost": ("VIETTELPOST_TOKEN", "VIETTELPOST_USER", "VIETTELPOST_PASSWORD"),
    "TPOS": ("TPOS_ACCESS_TOKEN", "TPOS_BASE_URL"),
    "Sapo": ("SAPO_ACCESS_TOKEN", "SAPO_BASE_URL"),
    "Nhanh": ("NHANH_API_KEY", "NHANH_BUSINESS_ID"),
    "Shopee": ("SHOPEE_ACCESS_TOKEN", "SHOPEE_SHOP_ID"),
    "SPX": ("SPX_TOKEN", "SPX_SHOP_ID"),
    "VNPost": ("VNPOST_TOKEN", "VNPOST_CUSTOMER_CODE"),
    "Telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
}

# How long before expiry we flag a session as "expiring" (seconds).
EXPIRING_THRESHOLD = 3600


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mask(value: str | None, keep: int = 4) -> str | None:
    try:
        from owned_credentials import mask_secret

        return mask_secret(value, keep=keep)
    except Exception:  # noqa: BLE001
        if not value:
            return None
        return ("*" * max(0, len(value) - keep)) + value[-keep:]


# --------------------------------------------------------------------------- store io


def store_path() -> Path:
    override = os.environ.get("SESSION_STORE_PATH")
    return Path(override) if override else DEFAULT_STORE


def _skeleton() -> dict[str, Any]:
    return {"version": 1, "updated_at": utc_now(), "platforms": {}}


def load_store() -> dict[str, Any]:
    p = store_path()
    if not p.is_file():
        return _skeleton()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return _skeleton()
    if not isinstance(data, dict) or "platforms" not in data:
        return _skeleton()
    return data


def save_store(store: dict[str, Any]) -> Path:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    store["updated_at"] = utc_now()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


# --------------------------------------------------------------------------- mutations


def _entry(store: dict[str, Any], platform: str) -> dict[str, Any]:
    return store["platforms"].setdefault(
        platform,
        {"tokens": {}, "cookies": [], "meta": {"obtained_at": utc_now(), "source": "manual"}},
    )


def set_session(
    platform: str,
    *,
    tokens: dict[str, str] | None = None,
    cookies: list[dict[str, Any]] | None = None,
    source: str = "manual",
    store: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = store if store is not None else load_store()
    entry = _entry(store, platform)
    if tokens:
        entry["tokens"].update({k: v for k, v in tokens.items() if v})
    if cookies:
        # merge by (name, domain)
        index = {(c.get("name"), c.get("domain")): i for i, c in enumerate(entry["cookies"])}
        for c in cookies:
            key = (c.get("name"), c.get("domain"))
            if key in index:
                entry["cookies"][index[key]] = c
            else:
                entry["cookies"].append(c)
    entry["meta"]["obtained_at"] = utc_now()
    entry["meta"]["source"] = source
    save_store(store)
    return store


def collect_from_env(platform: str) -> dict[str, str]:
    keys = PLATFORM_TOKEN_KEYS.get(platform, ())
    out: dict[str, str] = {}
    for k in keys:
        v = (os.environ.get(k) or "").strip()
        if v:
            out[k] = v
    return out


def import_storage_state(platform: str, path: Path, *, store: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load a Playwright storage_state JSON ({"cookies":[...], "origins":[...]}) — owned only."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cookies = data.get("cookies") if isinstance(data, dict) else None
    if not isinstance(cookies, list):
        raise ValueError("storage_state không có mảng 'cookies'")
    norm = [
        {
            "name": c.get("name"),
            "value": c.get("value"),
            "domain": c.get("domain"),
            "path": c.get("path", "/"),
            "expires": c.get("expires", -1),
            "httpOnly": bool(c.get("httpOnly")),
            "secure": bool(c.get("secure")),
        }
        for c in cookies
        if isinstance(c, dict) and c.get("name")
    ]
    return set_session(platform, cookies=norm, source=f"storage_state:{Path(path).name}", store=store)


def apply_to_env(store: dict[str, Any] | None = None) -> dict[str, Any]:
    store = store if store is not None else load_store()
    applied: list[str] = []
    for _plat, entry in store.get("platforms", {}).items():
        for k, v in (entry.get("tokens") or {}).items():
            if v:
                os.environ[k] = v
                applied.append(k)
    return {"applied_count": len(applied), "keys": sorted(set(applied))}


def cookie_header(platform: str, *, store: dict[str, Any] | None = None, domain: str | None = None) -> str:
    store = store if store is not None else load_store()
    entry = store.get("platforms", {}).get(platform) or {}
    now = int(datetime.now(timezone.utc).timestamp())
    parts = []
    for c in entry.get("cookies", []):
        if domain and domain not in (c.get("domain") or ""):
            continue
        exp = c.get("expires")
        if isinstance(exp, (int, float)) and exp > 0 and now > exp:
            continue  # expired
        if c.get("name") and c.get("value") is not None:
            parts.append(f"{c['name']}={c['value']}")
    return "; ".join(parts)


# --------------------------------------------------------------------------- expiry / status


def _token_exp(token: str) -> dict[str, Any]:
    try:
        from pancake_cookie_ingest import decode_jwt, token_expiry_info

        return token_expiry_info(decode_jwt(token))
    except Exception:  # noqa: BLE001
        return {"has_exp": False, "expired": None}


def _status_from_exp(exp_epoch: int | None, *, threshold: int = EXPIRING_THRESHOLD) -> tuple[str, int | None]:
    if not exp_epoch:
        return ("unknown", None)
    now = int(datetime.now(timezone.utc).timestamp())
    left = int(exp_epoch) - now
    if left <= 0:
        return ("expired", left)
    if left <= threshold:
        return ("expiring", left)
    return ("ok", left)


def status_report(store: dict[str, Any] | None = None, *, threshold: int = EXPIRING_THRESHOLD) -> dict[str, Any]:
    """Mask-only view of the store — never returns raw token/cookie values."""
    store = store if store is not None else load_store()
    now = int(datetime.now(timezone.utc).timestamp())
    platforms: dict[str, Any] = {}
    worst = "ok"
    order = {"ok": 0, "unknown": 1, "expiring": 2, "expired": 3}
    for plat, entry in store.get("platforms", {}).items():
        toks = []
        for k, v in (entry.get("tokens") or {}).items():
            info = _token_exp(v) if "TOKEN" in k.upper() or "ACCESS" in k.upper() else {"has_exp": False}
            exp = info.get("exp") if info.get("has_exp") else None
            st, left = _status_from_exp(exp, threshold=threshold)
            if info.get("has_exp") is False:
                st = "ok"  # non-JWT key (api_key/base_url) — no expiry concept
            toks.append(
                {"key": k, "masked": _mask(v), "status": st, "exp_iso": info.get("exp_iso"), "seconds_left": left}
            )
            if order[st] > order[worst]:
                worst = st
        cookies = []
        for c in entry.get("cookies", []):
            exp = c.get("expires")
            exp_epoch = int(exp) if isinstance(exp, (int, float)) and exp > 0 else None
            st, left = _status_from_exp(exp_epoch, threshold=threshold)
            if exp_epoch is None:
                st = "session"  # session cookie, no expiry
            cookies.append(
                {
                    "name": c.get("name"),
                    "domain": c.get("domain"),
                    "masked": _mask(c.get("value")),
                    "expires_iso": datetime.fromtimestamp(exp_epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if exp_epoch
                    else None,
                    "status": st,
                    "seconds_left": left,
                }
            )
            if st in order and order[st] > order[worst]:
                worst = st
        platforms[plat] = {
            "tokens": toks,
            "cookies": cookies,
            "source": (entry.get("meta") or {}).get("source"),
            "obtained_at": (entry.get("meta") or {}).get("obtained_at"),
            "last_ok_at": (entry.get("meta") or {}).get("last_ok_at"),
        }
    return {
        "ok": True,
        "module": "session_store.status",
        "checked_at": utc_now(),
        "now_epoch": now,
        "store_path": str(store_path()),
        "platform_count": len(platforms),
        "platforms": platforms,
        "overall": worst,
        "policy": "owned-only · no dump-login · no auto-login · mask-only report",
    }


# --------------------------------------------------------------------------- keepalive


def keepalive(store: dict[str, Any] | None = None, *, refresh: bool = True, probe: bool = False) -> dict[str, Any]:
    """Duy trì phiên: refresh owned token (không login), tùy chọn probe giữ ấm, cập nhật last_ok_at."""
    store = store if store is not None else load_store()
    apply_to_env(store)

    refresh_report: dict[str, Any] = {"skipped": True}
    if refresh:
        try:
            from access_token_rotate import ensure_tokens

            refresh_report = ensure_tokens()
        except Exception as e:  # noqa: BLE001
            refresh_report = {"ok": False, "error": str(e)}

    probe_report: dict[str, Any] = {"skipped": True}
    if probe:
        try:
            from backend_pipe_keepalive import load_env as ka_load, run_once

            probe_report = run_once(ka_load(), notify=False)
        except Exception as e:  # noqa: BLE001
            probe_report = {"ok": False, "error": str(e)}

    stamp = utc_now()
    for _plat, entry in store.get("platforms", {}).items():
        entry.setdefault("meta", {})["last_ok_at"] = stamp
    save_store(store)

    rep = status_report(store)
    rep["module"] = "session_store.keepalive"
    rep["refresh_ok"] = bool(refresh_report.get("ok", True)) and "error" not in refresh_report
    rep["probe_ok"] = bool(probe_report.get("ok", True)) and "error" not in probe_report
    return rep


_STOP = {"flag": False}


def _handle_signal(signum, _frame):  # noqa: ANN001
    _STOP["flag"] = True


def run_daemon(*, interval: int = 300, iterations: int | None = None, probe: bool = False) -> dict[str, Any]:
    """Duy trì liên tục — keepalive mỗi `interval` giây. iterations dùng cho test (hữu hạn)."""
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    n = 0
    last: dict[str, Any] = {}
    while not _STOP["flag"]:
        last = keepalive(probe=probe)
        n += 1
        print(
            f"[{utc_now()}] session keepalive #{n} overall={last.get('overall')} "
            f"refresh_ok={last.get('refresh_ok')} probe_ok={last.get('probe_ok')}",
            flush=True,
        )
        if iterations is not None and n >= iterations:
            break
        # interruptible sleep
        for _ in range(int(interval)):
            if _STOP["flag"]:
                break
            time.sleep(1)
    return {"ok": True, "iterations": n, "last": last}


# --------------------------------------------------------------------------- CLI


def _parse_kv(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items or []:
        if "=" in it:
            k, v = it.split("=", 1)
            out[k.strip()] = v
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Unified token + cookie session store & keepalive (owned-only)")
    ap.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=["status", "set", "import-state", "apply", "ensure", "daemon", "export"],
    )
    ap.add_argument("--platform", help="Tên nền tảng (Pancake, GHN, ViettelPost, ...)")
    ap.add_argument("--from-env", action="store_true", help="Gom token owned từ os.environ theo nền tảng")
    ap.add_argument("--token", action="append", help="KEY=VALUE (lặp lại được)")
    ap.add_argument("--cookie", action="append", help="name=value (lặp lại được)")
    ap.add_argument("--domain", help="Domain cho cookie set / cookie-header")
    ap.add_argument("--file", help="Đường dẫn storage_state JSON (import-state)")
    ap.add_argument("--interval", type=int, default=300, help="Chu kỳ keepalive (giây) cho daemon")
    ap.add_argument("--iterations", type=int, help="Số vòng daemon (test)")
    ap.add_argument("--probe", action="store_true", help="Probe giữ ấm phiên khi ensure/daemon")
    ap.add_argument("--stdout", action="store_true", help="export: in raw Cookie header ra stdout (opt-in)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.command == "set":
        if not args.platform:
            print("--platform bắt buộc cho 'set'", file=sys.stderr)
            return 2
        tokens = _parse_kv(args.token)
        if args.from_env:
            tokens.update(collect_from_env(args.platform))
        cookies = None
        ck = _parse_kv(args.cookie)
        if ck:
            cookies = [
                {"name": n, "value": v, "domain": args.domain or "", "path": "/", "expires": -1}
                for n, v in ck.items()
            ]
        if not tokens and not cookies:
            print("Không có token/cookie nào để lưu (dùng --from-env / --token / --cookie)", file=sys.stderr)
            return 2
        set_session(args.platform, tokens=tokens or None, cookies=cookies, source="cli")
        print(f"✅ Đã lưu phiên cho {args.platform}: tokens={len(tokens)} cookies={len(cookies or [])}")
        return 0

    if args.command == "import-state":
        if not args.platform or not args.file:
            print("--platform và --file bắt buộc cho 'import-state'", file=sys.stderr)
            return 2
        import_storage_state(args.platform, Path(args.file))
        print(f"✅ Đã nạp cookie storage_state cho {args.platform} từ {args.file}")
        return 0

    if args.command == "apply":
        rep = apply_to_env()
        print(json.dumps(rep, ensure_ascii=False) if args.json else f"Applied {rep['applied_count']} token key(s) → env")
        return 0

    if args.command == "ensure":
        rep = keepalive(probe=args.probe)
        print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else _fmt_status(rep))
        return 0 if rep.get("ok") else 1

    if args.command == "daemon":
        return 0 if run_daemon(interval=args.interval, iterations=args.iterations, probe=args.probe).get("ok") else 1

    if args.command == "export":
        if not args.platform:
            print("--platform bắt buộc cho 'export'", file=sys.stderr)
            return 2
        hdr = cookie_header(args.platform, domain=args.domain)
        if args.stdout:
            # explicit opt-in: raw Cookie header for piping into a request
            print(hdr)
            return 0
        # default: write to a 600 file, print only path + masked preview (no raw secret to stdout)
        out = SECRETS / f"{args.platform.lower()}_cookie_header.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(hdr, encoding="utf-8")
        try:
            os.chmod(out, 0o600)
        except OSError:
            pass
        ncookies = len([c for c in hdr.split("; ") if c])
        print(f"Cookie header ({ncookies} cookie) → {out} (chmod 600). Dùng --stdout để in ra (không khuyến nghị).")
        return 0

    # default: status
    rep = status_report()
    print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else _fmt_status(rep))
    return 0


def _fmt_status(rep: dict[str, Any]) -> str:
    lines = [
        "🔐 SESSION STORE — token + cookie (owned-only)",
        f"Lúc: {rep.get('checked_at')} · store: {rep.get('store_path')}",
        f"Nền tảng: {rep.get('platform_count')} · tổng thể: {rep.get('overall')}",
    ]
    for plat, e in (rep.get("platforms") or {}).items():
        lines.append(f"\n• {plat}  (source={e.get('source')} last_ok={e.get('last_ok_at')})")
        for t in e.get("tokens", []):
            lines.append(f"    token {t['key']}={t['masked']} [{t['status']}] exp={t.get('exp_iso') or '-'}")
        for c in e.get("cookies", []):
            lines.append(
                f"    cookie {c['name']}@{c.get('domain')}={c['masked']} [{c['status']}] exp={c.get('expires_iso') or '-'}"
            )
    lines.append(f"\nPolicy: {rep.get('policy')}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
