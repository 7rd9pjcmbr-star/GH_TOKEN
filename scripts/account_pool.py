#!/usr/bin/env python3
"""Account pool — hồ chứa tài khoản SỞ HỮU cho gọi đơn đa nền tảng.

Quản lý một "pool" các tài khoản owned (theo nền tảng) và chọn/luân phiên khi chạy:
  • Nạp account từ owned_credentials (env / secrets/*.env / OWNED_ACCOUNTS_JSON / OWNED_MAP_*).
  • Theo dõi trạng thái mỗi account: use_count, last_used, cooldown, disabled, last_error.
  • Chọn account đủ điều kiện theo chiến lược: lru | least_used | first.
  • acquire/release/mark_bad + async acquire (asyncio.Lock) cho worker bất đồng bộ.

Chính sách (owned-only):
  • Chỉ tài khoản SỞ HỮU. Không dump-login / stealer. Không tự đăng nhập.
  • KHÔNG lưu secret trong pool state — credential nằm ở owned_credentials/session_store.
    Pool chỉ giữ metadata sử dụng + sức khoẻ (mask-only report).

Lưu trữ: secrets/account_pool.json (gitignored, chmod 600).
Override: ACCOUNT_POOL_PATH (dùng cho test).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SECRETS = ROOT / "secrets"
DEFAULT_POOL = SECRETS / "account_pool.json"

DEFAULT_COOLDOWN_S = 300
STRATEGIES = ("lru", "least_used", "first")

_SYNC_LOCK = threading.Lock()
_ASYNC_LOCK: asyncio.Lock | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def pool_path() -> Path:
    override = os.environ.get("ACCOUNT_POOL_PATH")
    return Path(override) if override else DEFAULT_POOL


# --------------------------------------------------------------------------- accounts


def account_key(acc: Any) -> str:
    """Stable identity for an OwnedAccount within the pool."""
    tail = acc.label or acc.user or acc.shop_id or "default"
    return f"{acc.platform}:{tail}"


def load_accounts(env: dict[str, str] | None = None) -> list[Any]:
    from owned_credentials import owned_accounts

    return owned_accounts(env)


# --------------------------------------------------------------------------- state io


def _skeleton() -> dict[str, Any]:
    return {"version": 1, "updated_at": utc_now(), "accounts": {}}


def load_state() -> dict[str, Any]:
    p = pool_path()
    if not p.is_file():
        return _skeleton()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return _skeleton()
    if not isinstance(data, dict) or "accounts" not in data:
        return _skeleton()
    return data


def save_state(state: dict[str, Any]) -> Path:
    p = pool_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def _entry(state: dict[str, Any], key: str) -> dict[str, Any]:
    return state["accounts"].setdefault(
        key,
        {"status": "active", "use_count": 0, "last_used_at": None, "cooldown_until": 0, "last_error": None},
    )


# --------------------------------------------------------------------------- eligibility / selection


def _eligible(acc: Any, entry: dict[str, Any], *, now: int, require_ready: bool = True) -> bool:
    if require_ready and not acc.ready:
        return False
    if entry.get("status") != "active":
        return False
    if int(entry.get("cooldown_until") or 0) > now:
        return False
    return True


def _select(candidates: list[tuple[Any, dict[str, Any]]], strategy: str) -> tuple[Any, dict[str, Any]] | None:
    if not candidates:
        return None
    if strategy == "first":
        return candidates[0]
    if strategy == "least_used":
        return min(candidates, key=lambda c: int(c[1].get("use_count") or 0))
    # default lru: oldest last_used_at first (None = never used = highest priority)
    def _lru_key(c: tuple[Any, dict[str, Any]]):
        lu = c[1].get("last_used_at")
        return (lu is not None, lu or "")

    return min(candidates, key=_lru_key)


def _acquire_locked(
    platform: str, *, strategy: str = "lru", env: dict[str, str] | None = None
) -> dict[str, Any] | None:
    from owned_credentials import normalize_platform

    plat = normalize_platform(platform) or platform
    accounts = [a for a in load_accounts(env) if a.platform == plat]
    if not accounts:
        return None
    state = load_state()
    now = _now_epoch()
    candidates = [(a, _entry(state, account_key(a))) for a in accounts]
    eligible = [(a, e) for a, e in candidates if _eligible(a, e, now=now)]
    chosen = _select(eligible, strategy)
    if not chosen:
        save_state(state)  # persist any newly-created entries
        return None
    acc, entry = chosen
    entry["use_count"] = int(entry.get("use_count") or 0) + 1
    entry["last_used_at"] = utc_now()
    save_state(state)
    return {"key": account_key(acc), "account": acc, "public": acc.public_dict()}


def acquire(platform: str, *, strategy: str = "lru", env: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Thread-safe: chọn một account owned đủ điều kiện cho `platform`."""
    with _SYNC_LOCK:
        return _acquire_locked(platform, strategy=strategy, env=env)


def _get_async_lock() -> asyncio.Lock:
    global _ASYNC_LOCK
    if _ASYNC_LOCK is None:
        _ASYNC_LOCK = asyncio.Lock()
    return _ASYNC_LOCK


async def acquire_async(
    platform: str, *, strategy: str = "lru", env: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """Async-safe acquire cho worker bất đồng bộ (asyncio.Lock)."""
    async with _get_async_lock():
        return await asyncio.to_thread(_acquire_locked, platform, strategy=strategy, env=env)


def mark_bad(key: str, *, reason: str = "", cooldown_s: int = DEFAULT_COOLDOWN_S) -> dict[str, Any]:
    with _SYNC_LOCK:
        state = load_state()
        entry = _entry(state, key)
        entry["cooldown_until"] = _now_epoch() + int(cooldown_s)
        entry["last_error"] = reason[:200] or "marked_bad"
        save_state(state)
        return {"ok": True, "key": key, "cooldown_until": entry["cooldown_until"]}


def disable(key: str) -> dict[str, Any]:
    with _SYNC_LOCK:
        state = load_state()
        _entry(state, key)["status"] = "disabled"
        save_state(state)
        return {"ok": True, "key": key, "status": "disabled"}


def reset(key: str | None = None) -> dict[str, Any]:
    with _SYNC_LOCK:
        state = load_state()
        if key is None:
            state["accounts"] = {}
        else:
            state["accounts"].pop(key, None)
        save_state(state)
        return {"ok": True, "reset": key or "ALL"}


# --------------------------------------------------------------------------- report


def status_report(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Mask-only overview of the pool (never returns raw tokens)."""
    accounts = load_accounts(env)
    state = load_state()
    now = _now_epoch()
    by_platform: dict[str, Any] = {}
    totals = {"total": 0, "ready": 0, "eligible": 0, "cooldown": 0, "disabled": 0}
    for acc in accounts:
        key = account_key(acc)
        entry = _entry(state, key)
        elig = _eligible(acc, entry, now=now)
        pub = acc.public_dict()  # token already masked by owned_credentials
        rec = {
            "key": key,
            "user": pub.get("user"),
            "shop_id": pub.get("shop_id"),
            "token_masked": pub.get("token_masked"),
            "ready": pub.get("ready"),
            "status": entry.get("status"),
            "use_count": entry.get("use_count"),
            "last_used_at": entry.get("last_used_at"),
            "cooldown_remaining_s": max(0, int(entry.get("cooldown_until") or 0) - now),
            "eligible": elig,
            "source": pub.get("source"),
        }
        by_platform.setdefault(acc.platform, []).append(rec)
        totals["total"] += 1
        totals["ready"] += 1 if pub.get("ready") else 0
        totals["eligible"] += 1 if elig else 0
        totals["cooldown"] += 1 if rec["cooldown_remaining_s"] > 0 else 0
        totals["disabled"] += 1 if entry.get("status") == "disabled" else 0
    # persist any newly created entries
    save_state(state)
    return {
        "ok": True,
        "module": "account_pool.status",
        "checked_at": utc_now(),
        "pool_path": str(pool_path()),
        "totals": totals,
        "platforms": by_platform,
        "policy": "owned-only · no dump-login · no auto-login · mask-only · secrets not stored in pool",
    }


def _fmt_status(rep: dict[str, Any]) -> str:
    t = rep.get("totals", {})
    lines = [
        "🗂  ACCOUNT POOL — hồ chứa tài khoản (owned-only)",
        f"Lúc: {rep.get('checked_at')} · state: {rep.get('pool_path')}",
        f"Tổng: {t.get('total')} · ready={t.get('ready')} eligible={t.get('eligible')} "
        f"cooldown={t.get('cooldown')} disabled={t.get('disabled')}",
    ]
    for plat, recs in (rep.get("platforms") or {}).items():
        lines.append(f"\n• {plat} ({len(recs)} account)")
        for r in recs:
            lines.append(
                f"    [{'✓' if r['eligible'] else '×'}] {r['key']} user={r.get('user') or '-'} "
                f"shop={r.get('shop_id') or '-'} token={r.get('token_masked') or '-'} "
                f"used={r.get('use_count')} status={r.get('status')} "
                f"cooldown={r.get('cooldown_remaining_s')}s"
            )
    lines.append(f"\nPolicy: {rep.get('policy')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Account pool — hồ chứa tài khoản owned đa nền tảng")
    ap.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=["status", "list", "acquire", "mark-bad", "disable", "reset"],
    )
    ap.add_argument("--platform", help="Nền tảng (Pancake, GHN, ...)")
    ap.add_argument("--strategy", default="lru", choices=list(STRATEGIES))
    ap.add_argument("--key", help="Account key (platform:label) cho mark-bad/disable/reset")
    ap.add_argument("--reason", default="", help="Lý do cho mark-bad")
    ap.add_argument("--cooldown", type=int, default=DEFAULT_COOLDOWN_S, help="Giây cooldown cho mark-bad")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.command in ("status", "list"):
        rep = status_report()
        print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else _fmt_status(rep))
        return 0

    if args.command == "acquire":
        if not args.platform:
            print("--platform bắt buộc cho 'acquire'", file=sys.stderr)
            return 2
        got = acquire(args.platform, strategy=args.strategy)
        if not got:
            print(f"⚠ Không có account owned đủ điều kiện cho {args.platform}")
            return 1
        pub = got["public"]
        # mask-only: never print the raw token
        print(
            json.dumps({"key": got["key"], **pub}, ensure_ascii=False)
            if args.json
            else f"✅ acquired {got['key']} user={pub.get('user') or '-'} shop={pub.get('shop_id') or '-'} "
            f"token={pub.get('token_masked') or '-'}"
        )
        return 0

    if args.command == "mark-bad":
        if not args.key:
            print("--key bắt buộc", file=sys.stderr)
            return 2
        print(json.dumps(mark_bad(args.key, reason=args.reason, cooldown_s=args.cooldown), ensure_ascii=False))
        return 0

    if args.command == "disable":
        if not args.key:
            print("--key bắt buộc", file=sys.stderr)
            return 2
        print(json.dumps(disable(args.key), ensure_ascii=False))
        return 0

    if args.command == "reset":
        print(json.dumps(reset(args.key), ensure_ascii=False))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
