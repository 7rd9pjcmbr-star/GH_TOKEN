"""Stage runners — capability map over reverse_query hops."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .constants import ASUMEE_WID, StageId
from .store import PipeStore


@dataclass
class StageContext:
    store: PipeStore
    wid: str = ASUMEE_WID
    live: bool = False
    apply: bool = False
    limit: int = 40
    probe: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


def _rq():
    import order_pipe_reverse_query as rq  # noqa: WPS433

    return rq


def run_seed(ctx: StageContext) -> list[dict]:
    """Warehouse / kho / tỉnh / van_tay / buucuc — hop1 base (no deep)."""
    rq = _rq()
    return rq.reverse_chain_asumee(
        ctx.store.conn,
        deep=False,
        hop2=False,
        hop6_live=False,
        hop7_live=False,
    )


def run_deep(ctx: StageContext) -> list[dict]:
    """Deep drills + hop2…5 offline (gaps, geo, SPX-like, timeline plan)."""
    rq = _rq()
    wid = ctx.wid
    out: list[dict] = []
    out.append(rq.reverse_flow_gaps(ctx.store.conn, wid))
    for st in ("delivered", "shipped", "submitted", "canceled"):
        out.append(rq.reverse_by_status_warehouse(ctx.store.conn, wid, st, limit=12))
    out.extend(rq.reverse_chain_asumee_hop2(ctx.store.conn, wid))
    out.extend(rq.reverse_chain_asumee_hop3(ctx.store.conn, wid))
    out.extend(rq.reverse_chain_asumee_hop4(ctx.store.conn, wid))
    out.extend(rq.reverse_chain_asumee_hop5(ctx.store.conn, wid))
    return out


def run_enrich(ctx: StageContext) -> list[dict]:
    """Live Pancake timeline + carrier remap — hop6/7."""
    rq = _rq()
    wid = ctx.wid
    out: list[dict] = []
    out.extend(
        rq.reverse_chain_asumee_hop6(
            ctx.store.conn,
            wid,
            live=ctx.live,
            apply=ctx.apply,
            limit=min(ctx.limit, 8) if ctx.live else 8,
        )
    )
    out.extend(
        rq.reverse_chain_asumee_hop7(
            ctx.store.conn,
            wid,
            live=ctx.live,
            apply=ctx.apply,
            limit=ctx.limit,
        )
    )
    return out


def run_tracking(ctx: StageContext) -> list[dict]:
    """Aship URL sync · 3PL · probe — hop8."""
    rq = _rq()
    return rq.reverse_chain_asumee_hop8(
        ctx.store.conn,
        ctx.wid,
        apply=ctx.apply,
        probe=ctx.probe,
        probe_limit=min(ctx.limit, 8),
    )


def run_pancake_id(ctx: StageContext) -> list[dict]:
    """Pancake-id cohort live — hop9."""
    rq = _rq()
    return rq.reverse_chain_asumee_hop9(
        ctx.store.conn,
        ctx.wid,
        live=ctx.live,
        apply=ctx.apply,
        limit=ctx.limit,
    )


def run_accept(ctx: StageContext) -> list[dict]:
    """Soft/hard accept · SPX 26* · commune — hop10/11 (live hard refetch opt)."""
    rq = _rq()
    wid = ctx.wid
    out: list[dict] = []
    out.extend(rq.reverse_chain_asumee_hop10(ctx.store.conn, wid, apply=ctx.apply))
    out.extend(
        rq.reverse_chain_asumee_hop11(
            ctx.store.conn,
            wid,
            live=ctx.live,
            apply=ctx.apply,
            limit=ctx.limit,
        )
    )
    return out


def run_waiting(ctx: StageContext) -> list[dict]:
    """Returning/submitted waiting live — hop12."""
    rq = _rq()
    return rq.reverse_chain_asumee_hop12(
        ctx.store.conn,
        ctx.wid,
        live=ctx.live,
        apply=ctx.apply,
        limit=ctx.limit,
        probe=ctx.probe,
    )


def run_close(ctx: StageContext) -> list[dict]:
    """Flow closure · PATH-WAIT · confirm — hop13."""
    rq = _rq()
    return rq.reverse_chain_asumee_hop13(
        ctx.store.conn,
        ctx.wid,
        live=ctx.live,
        apply=ctx.apply,
        limit=max(ctx.limit, 60) if ctx.live else ctx.limit,
    )


STAGE_RUNNERS: dict[str, Callable[[StageContext], list[dict]]] = {
    StageId.SEED.value: run_seed,
    StageId.DEEP.value: run_deep,
    StageId.ENRICH.value: run_enrich,
    StageId.TRACKING.value: run_tracking,
    StageId.PANCAKE_ID.value: run_pancake_id,
    StageId.ACCEPT.value: run_accept,
    StageId.WAITING.value: run_waiting,
    StageId.CLOSE.value: run_close,
}


def parse_stages(raw: str | list[str] | None) -> list[str]:
    if raw is None:
        from .constants import SAFE_STAGES

        return list(SAFE_STAGES)
    if isinstance(raw, list):
        parts = raw
    else:
        parts = [p.strip() for p in str(raw).replace(" ", "").split(",") if p.strip()]
    unknown = [p for p in parts if p not in STAGE_RUNNERS]
    if unknown:
        raise ValueError(f"Unknown stages: {unknown}; known={list(STAGE_RUNNERS)}")
    return parts
