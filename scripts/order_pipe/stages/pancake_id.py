"""pancake_id — pancake-id cohort live (hop9)."""

from __future__ import annotations

from .context import StageContext
from .engine import rq


def run_pancake_id(ctx: StageContext) -> list[dict]:
    engine = rq()
    return engine.reverse_chain_asumee_hop9(
        ctx.store.conn,
        ctx.wid,
        live=ctx.live,
        apply=ctx.apply,
        limit=ctx.limit,
    )
