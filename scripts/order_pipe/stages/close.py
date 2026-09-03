"""close — flow closure · PATH-WAIT · confirm (hop13)."""

from __future__ import annotations

from ..paths import path_census
from .context import StageContext
from .engine import rq


def run_close(ctx: StageContext) -> list[dict]:
    engine = rq()
    out = engine.reverse_chain_asumee_hop13(
        ctx.store.conn,
        ctx.wid,
        live=ctx.live,
        apply=ctx.apply,
        limit=max(ctx.limit, 60) if ctx.live else ctx.limit,
    )
    # Phase B: stamp PathId census next to hop13 closure.
    out.append(path_census(ctx.store.conn, ctx.wid))
    return out
