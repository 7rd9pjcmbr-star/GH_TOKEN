"""Order Pipe — public facade."""

from __future__ import annotations

from .constants import (
    ASUMEE_KHO,
    ASUMEE_SHOP_ID,
    ASUMEE_WID,
    DEFAULT_STAGES,
    HOP_TO_STAGE,
    SAFE_STAGES,
    PathId,
    StageId,
)
from .flow import FlowService
from .lookup import ReverseLookup
from .pipeline import PipelineResult, ReversePipeline
from .store import PipeStore


class ReverseFlow:
    """Facade toàn diện: store + lookup + flow + pipeline."""

    def __init__(self, store: PipeStore | None = None, *, warehouse_id: str = ASUMEE_WID):
        self.store = store or PipeStore.ensure()
        self.warehouse_id = warehouse_id
        self.lookup = ReverseLookup(self.store)
        self.flow = FlowService(self.store)
        self.pipeline = ReversePipeline(self.store, warehouse_id=warehouse_id)

    def stats(self) -> dict:
        return self.store.asumee_stats(self.warehouse_id)

    def run(self, **kwargs):
        return self.pipeline.run(**kwargs)


__all__ = [
    "ASUMEE_KHO",
    "ASUMEE_SHOP_ID",
    "ASUMEE_WID",
    "DEFAULT_STAGES",
    "HOP_TO_STAGE",
    "SAFE_STAGES",
    "PathId",
    "PipeStore",
    "PipelineResult",
    "ReverseFlow",
    "ReverseLookup",
    "ReversePipeline",
    "FlowService",
    "StageId",
]
