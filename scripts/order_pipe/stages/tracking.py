"""tracking — aship URL sync · 3PL · probe (hop8)."""

from __future__ import annotations

from .context import StageContext
from .engine import rq


def run_tracking(ctx: StageContext) -> list[dict]:
    engine = rq()
    return engine.reverse_chain_asumee_hop8(
        ctx.store.conn,
        ctx.wid,
        apply=ctx.apply,
        probe=ctx.probe,
        probe_limit=min(ctx.limit, 8),
    )
