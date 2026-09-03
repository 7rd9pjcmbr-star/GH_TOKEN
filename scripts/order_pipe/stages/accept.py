"""accept — soft/hard accept · SPX 26* · commune (hop10/11)."""

from __future__ import annotations

from .context import StageContext
from .engine import rq


def run_accept(ctx: StageContext) -> list[dict]:
    engine = rq()
    wid = ctx.wid
    out: list[dict] = []
    out.extend(engine.reverse_chain_asumee_hop10(ctx.store.conn, wid, apply=ctx.apply))
    out.extend(
        engine.reverse_chain_asumee_hop11(
            ctx.store.conn,
            wid,
            live=ctx.live,
            apply=ctx.apply,
            limit=ctx.limit,
        )
    )
    return out
