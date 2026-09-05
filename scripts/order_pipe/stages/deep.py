"""deep — gaps, status, ward, geo, SPX-like, timeline plan (hop2–5)."""

from __future__ import annotations

from .context import StageContext
from .engine import rq


def run_deep(ctx: StageContext) -> list[dict]:
    engine = rq()
    wid = ctx.wid
    out: list[dict] = []
    out.append(engine.reverse_flow_gaps(ctx.store.conn, wid))
    for st in ("delivered", "shipped", "submitted", "canceled"):
        out.append(engine.reverse_by_status_warehouse(ctx.store.conn, wid, st, limit=12))
    out.extend(engine.reverse_chain_asumee_hop2(ctx.store.conn, wid))
    out.extend(engine.reverse_chain_asumee_hop3(ctx.store.conn, wid))
    out.extend(engine.reverse_chain_asumee_hop4(ctx.store.conn, wid))
    out.extend(engine.reverse_chain_asumee_hop5(ctx.store.conn, wid))
    return out
