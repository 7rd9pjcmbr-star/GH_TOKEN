"""waiting — returning/submitted waiting live (hop12)."""

from __future__ import annotations

from .context import StageContext
from .engine import rq


def run_waiting(ctx: StageContext) -> list[dict]:
    engine = rq()
    return engine.reverse_chain_asumee_hop12(
        ctx.store.conn,
        ctx.wid,
        live=ctx.live,
        apply=ctx.apply,
        limit=ctx.limit,
        probe=ctx.probe,
    )
