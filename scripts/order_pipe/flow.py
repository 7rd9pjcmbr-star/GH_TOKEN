"""Flow panorama / completeness / closure facade."""

from __future__ import annotations

from typing import Any

from .constants import ASUMEE_WID
from .store import PipeStore


class FlowService:
    def __init__(self, store: PipeStore):
        self.store = store
        self._rq = None

    @property
    def rq(self):
        if self._rq is None:
            import order_pipe_reverse_query as rq  # noqa: WPS433

            self._rq = rq
        return self._rq

    def panorama(self, order: dict | None) -> dict | None:
        return self.rq.build_flow_panorama(order)

    def completeness(self, wid: str = ASUMEE_WID) -> dict:
        return self.rq.reverse_flow_completeness(self.store.conn, wid)

    def closure(self, wid: str = ASUMEE_WID) -> dict:
        return self.rq.reverse_flow_closure(self.store.conn, wid)

    def open_paths(self, wid: str = ASUMEE_WID) -> dict:
        return self.rq.reverse_open_path_scorecard(self.store.conn, wid)

    def hard_soft_gaps(self, wid: str = ASUMEE_WID) -> dict:
        return self.rq.reverse_hard_soft_gaps(self.store.conn, wid)

    def matrix(self) -> dict[str, Any]:
        return self.rq.build_flow_matrix(self.store.conn)
