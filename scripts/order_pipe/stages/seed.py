"""seed — warehouse / kho / tỉnh / van_tay / buucuc + PathId census.

Phase B: composition lives here instead of reverse_chain_asumee(deep=False).
Lookups still reuse reverse-query SQL (gradual extract).
"""

from __future__ import annotations

from ..constants import ASUMEE_KHO, ASUMEE_WID
from ..paths import path_census
from .context import StageContext
from .engine import rq


def _kho_for(conn, wid: str) -> str | None:
    if wid == ASUMEE_WID:
        return ASUMEE_KHO
    row = conn.execute(
        """
        SELECT kho FROM orders
        WHERE warehouse_id = ? AND kho IS NOT NULL AND kho != ''
        LIMIT 1
        """,
        (wid,),
    ).fetchone()
    return row[0] if row else None


def run_seed(ctx: StageContext) -> list[dict]:
    """Warehouse / kho / tỉnh / van_tay / buucuc — hop1 base (no deep)."""
    engine = rq()
    conn = ctx.store.conn
    wid = ctx.wid
    results: list[dict] = [engine.reverse_by_warehouse_id(conn, wid, limit=20)]
    kho = _kho_for(conn, wid)
    if kho:
        results.append(engine.reverse_by_kho(conn, kho, limit=15))

    top_prov = [
        r[0]
        for r in conn.execute(
            """
            SELECT province FROM orders
            WHERE warehouse_id = ? AND province IS NOT NULL AND province != ''
            GROUP BY province ORDER BY COUNT(*) DESC LIMIT 3
            """,
            (wid,),
        )
    ]
    for p in top_prov:
        results.append(engine.reverse_by_province(conn, p, limit=10))

    vt = conn.execute(
        """
        SELECT van_tay FROM orders
        WHERE warehouse_id = ? AND van_tay IS NOT NULL
        ORDER BY piped_at DESC LIMIT 3
        """,
        (wid,),
    ).fetchall()
    for (v,) in vt:
        results.append(engine.reverse_by_van_tay(conn, v))

    buu = conn.execute(
        """
        SELECT buucuc FROM orders WHERE warehouse_id = ?
        GROUP BY buucuc ORDER BY COUNT(*) DESC LIMIT 2
        """,
        (wid,),
    ).fetchall()
    for (b,) in buu:
        if b:
            results.append(engine.reverse_by_buucuc(conn, b, limit=10))

    results.append(path_census(conn, wid))
    return results
