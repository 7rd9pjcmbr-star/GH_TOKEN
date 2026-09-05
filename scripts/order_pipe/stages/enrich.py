"""enrich — live Pancake timeline + carrier remap (hop6/7)."""

from __future__ import annotations

from .context import StageContext
from .engine import rq


def run_enrich(ctx: StageContext) -> list[dict]:
    engine = rq()
    wid = ctx.wid
    out: list[dict] = []
    out.extend(
        engine.reverse_chain_asumee_hop6(
            ctx.store.conn,
            wid,
            live=ctx.live,
            apply=ctx.apply,
            limit=min(ctx.limit, 8) if ctx.live else 8,
        )
    )
    out.extend(
        engine.reverse_chain_asumee_hop7(
            ctx.store.conn,
            wid,
            live=ctx.live,
            apply=ctx.apply,
            limit=ctx.limit,
        )
    )
    return out
