"""Lookup facade — truy vấn ngược theo khóa."""

from __future__ import annotations

from typing import Any

from .constants import ASUMEE_KHO, ASUMEE_WID
from .store import PipeStore


class ReverseLookup:
    def __init__(self, store: PipeStore):
        self.store = store
        self._rq = None

    @property
    def rq(self):
        if self._rq is None:
            import order_pipe_reverse_query as rq  # noqa: WPS433

            self._rq = rq
        return self._rq

    def by_van_tay(self, van_tay: str) -> dict:
        return self.rq.reverse_by_van_tay(self.store.conn, van_tay)

    def by_so(self, so: str, limit: int = 20) -> dict:
        return self.rq.reverse_by_so_noi_bo(self.store.conn, so, limit=limit)

    def by_tracking(self, tracking: str) -> dict:
        return self.rq.reverse_by_tracking(self.store.conn, tracking)

    def by_kho(self, kho: str = ASUMEE_KHO, limit: int = 15) -> dict:
        return self.rq.reverse_by_kho(self.store.conn, kho, limit=limit)

    def by_warehouse(self, wid: str = ASUMEE_WID, limit: int = 20) -> dict:
        return self.rq.reverse_by_warehouse_id(self.store.conn, wid, limit=limit)

    def by_buucuc(self, buucuc: str, limit: int = 10) -> dict:
        return self.rq.reverse_by_buucuc(self.store.conn, buucuc, limit=limit)

    def by_province(self, province: str, limit: int = 30) -> dict:
        return self.rq.reverse_by_province(self.store.conn, province, limit=limit)

    def by_address(self, fragment: str, limit: int = 20) -> dict:
        return self.rq.reverse_by_address(self.store.conn, fragment, limit=limit)

    def auto(self, q: str) -> list[dict[str, Any]]:
        """Auto-detect query — reuse build_report single-q path lightly."""
        report = self.rq.build_report(q=q)
        return list(report.get("results") or [])
