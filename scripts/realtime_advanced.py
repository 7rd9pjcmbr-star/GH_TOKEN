#!/usr/bin/env python3
"""Realtime nâng cao — engine gọi đơn realtime bất đồng bộ, đa nguồn, thích ứng.

Nâng cấp realtime_order_sync (poll tuần tự, sync) thành một engine:
  • ASYNC — chạy nhiều nguồn (source) đồng thời qua asyncio (không tuần tự).
  • ADAPTIVE — chu kỳ poll tự điều chỉnh: nhanh khi có đơn mới, giãn khi rảnh,
    backoff luỹ thừa khi lỗi.
  • DEDUP — khử trùng đơn theo fingerprint (tái dùng realtime_order_sync.order_fingerprint).
  • HOOKS (soft) — session_store keepalive, account_pool, monitor_alert (nếu có).

Nguồn mặc định = realtime_order_sync.run_cycle (đa nền tảng, đã kiểm chứng) chạy
qua asyncio.to_thread để không chặn event loop. Có thể inject `sources` để test.

Chính sách: owned-only · no dump-login · no auto-login · mask-only.
State: secrets/realtime_advanced.state.json (gitignored, chmod 600).
Override: REALTIME_ADV_STATE_PATH (test).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

SECRETS = ROOT / "secrets"
DEFAULT_STATE = SECRETS / "realtime_advanced.state.json"

Source = Callable[[], Awaitable[dict[str, Any]]]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_path() -> Path:
    override = os.environ.get("REALTIME_ADV_STATE_PATH")
    return Path(override) if override else DEFAULT_STATE


# --------------------------------------------------------------------------- adaptive interval


def next_interval(
    *,
    had_new: bool,
    had_error: bool,
    current: int,
    min_i: int = 5,
    max_i: int = 300,
    base: int = 30,
    error_streak: int = 0,
) -> int:
    """Pure adaptive-polling policy.

    - error  → exponential backoff from current (2x), capped at max_i.
    - new    → snap to the fast floor (min_i).
    - idle   → ease back toward base, then grow 1.5x toward max_i.
    """
    if had_error:
        return max(min_i, min(max_i, max(current, base) * 2 if error_streak else base))
    if had_new:
        return min_i
    if current < base:
        return base
    return min(max_i, int(current * 1.5) or base)


# --------------------------------------------------------------------------- state / dedup


def _load_state() -> dict[str, Any]:
    p = state_path()
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                d.setdefault("fingerprints", [])
                return d
        except Exception:  # noqa: BLE001
            pass
    return {"version": 1, "updated_at": utc_now(), "fingerprints": [], "cycles": 0}


def _save_state(state: dict[str, Any]) -> None:
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    # cap fingerprint history so the file doesn't grow unbounded
    if len(state.get("fingerprints", [])) > 5000:
        state["fingerprints"] = state["fingerprints"][-5000:]
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _fingerprint(backend: str, order: dict[str, Any]) -> str:
    try:
        from realtime_order_sync import order_fingerprint

        return order_fingerprint(backend, order)
    except Exception:  # noqa: BLE001
        import hashlib

        key = f"{backend}:{order.get('id') or order.get('order_key') or json.dumps(order, sort_keys=True)}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- engine


@dataclass
class EngineConfig:
    limit: int = 20
    min_interval: int = 5
    max_interval: int = 300
    base_interval: int = 30
    notify: bool = False
    ensure_sessions: bool = False


@dataclass
class EngineStats:
    cycles: int = 0
    total_new: int = 0
    error_streak: int = 0
    last: dict[str, Any] = field(default_factory=dict)


def _default_source(cfg: EngineConfig) -> Source:
    """Wrap realtime_order_sync.run_cycle (sync, multi-platform) as an async source."""

    async def _src() -> dict[str, Any]:
        from realtime_order_sync import load_env, run_cycle

        env = load_env()
        cycle = await asyncio.to_thread(run_cycle, env, cfg.limit, cfg.notify, True)
        # normalise: extract new orders as (backend, order) pairs when present
        orders: list[tuple[str, dict]] = []
        for pipe in cycle.get("pipes", cycle.get("sources", []) or []):
            backend = pipe.get("backend") or pipe.get("id") or "src"
            for o in pipe.get("new_orders", []) or []:
                orders.append((backend, o))
        return {"orders": orders, "raw": {"new": cycle.get("new_total") or cycle.get("new")}}

    return _src


class RealtimeEngine:
    def __init__(self, cfg: EngineConfig | None = None, *, sources: list[Source] | None = None):
        self.cfg = cfg or EngineConfig()
        self.sources = sources if sources is not None else [_default_source(self.cfg)]
        self.stats = EngineStats()
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    async def _maybe_keepalive(self) -> None:
        if not self.cfg.ensure_sessions:
            return
        try:
            import session_store

            await session_store.keepalive_async(refresh=True)
        except Exception:  # noqa: BLE001
            pass  # soft — module/creds may be absent

    async def tick(self) -> dict[str, Any]:
        """Run all sources CONCURRENTLY, dedup by fingerprint, persist new fingerprints."""
        await self._maybe_keepalive()
        t0 = time.perf_counter()
        results = await asyncio.gather(*(s() for s in self.sources), return_exceptions=True)
        state = _load_state()
        seen = set(state.get("fingerprints", []))
        new_items: list[dict[str, Any]] = []
        errors: list[str] = []
        for r in results:
            if isinstance(r, Exception):
                errors.append(str(r)[:160])
                continue
            for backend, order in r.get("orders", []) or []:
                fp = _fingerprint(backend, order)
                if fp in seen:
                    continue
                seen.add(fp)
                new_items.append({"backend": backend, "fp": fp})
        state["fingerprints"] = list(seen)
        state["cycles"] = int(state.get("cycles", 0)) + 1
        _save_state(state)

        self.stats.cycles += 1
        self.stats.total_new += len(new_items)
        self.stats.error_streak = self.stats.error_streak + 1 if errors else 0
        cycle = {
            "checked_at": utc_now(),
            "new_count": len(new_items),
            "sources": len(self.sources),
            "errors": errors,
            "duration_ms": round((time.perf_counter() - t0) * 1000),
        }
        self.stats.last = cycle
        return cycle

    async def run(self, *, iterations: int | None = None, start_interval: int | None = None) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.request_stop)
            except (NotImplementedError, RuntimeError):
                pass
        interval = start_interval if start_interval is not None else self.cfg.base_interval
        n = 0
        while not self._stop:
            cycle = await self.tick()
            n += 1
            interval = next_interval(
                had_new=cycle["new_count"] > 0,
                had_error=bool(cycle["errors"]),
                current=interval,
                min_i=self.cfg.min_interval,
                max_i=self.cfg.max_interval,
                base=self.cfg.base_interval,
                error_streak=self.stats.error_streak,
            )
            print(
                f"[{utc_now()}] rt-adv #{n} new={cycle['new_count']} "
                f"errors={len(cycle['errors'])} next={interval}s ({cycle['duration_ms']}ms)",
                flush=True,
            )
            if iterations is not None and n >= iterations:
                break
            slept = 0
            while slept < interval and not self._stop:
                await asyncio.sleep(1)
                slept += 1
        return {"ok": True, "cycles": n, "total_new": self.stats.total_new, "last": self.stats.last}


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Realtime nâng cao — async adaptive multi-source order engine")
    ap.add_argument("command", nargs="?", default="once", choices=["once", "run", "status"])
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--min-interval", type=int, default=5)
    ap.add_argument("--max-interval", type=int, default=300)
    ap.add_argument("--base-interval", type=int, default=30)
    ap.add_argument("--iterations", type=int, help="Số vòng (test)")
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--ensure-sessions", action="store_true", help="keepalive session_store mỗi tick")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.command == "status":
        print(json.dumps(_load_state(), ensure_ascii=False, indent=2))
        return 0

    cfg = EngineConfig(
        limit=args.limit,
        min_interval=args.min_interval,
        max_interval=args.max_interval,
        base_interval=args.base_interval,
        notify=args.notify,
        ensure_sessions=args.ensure_sessions,
    )
    engine = RealtimeEngine(cfg)

    if args.command == "once":
        cycle = asyncio.run(engine.tick())
        print(json.dumps(cycle, ensure_ascii=False, indent=2) if args.json else
              f"rt-adv once: new={cycle['new_count']} errors={len(cycle['errors'])} "
              f"sources={cycle['sources']} {cycle['duration_ms']}ms")
        return 0

    res = asyncio.run(engine.run(iterations=args.iterations))
    print(json.dumps(res, ensure_ascii=False) if args.json else
          f"rt-adv run: cycles={res['cycles']} total_new={res['total_new']}")
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
