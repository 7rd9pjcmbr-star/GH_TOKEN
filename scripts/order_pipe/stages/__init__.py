"""Stage runners — capability map over reverse_query hops.

Phase B: each capability lives in stages/<name>.py. Seed is extracted;
later stages still delegate hopN until the next slices.
"""

from __future__ import annotations

from typing import Callable

from ..constants import StageId
from .accept import run_accept
from .close import run_close
from .context import StageContext
from .deep import run_deep
from .enrich import run_enrich
from .pancake_id import run_pancake_id
from .seed import run_seed
from .tracking import run_tracking
from .waiting import run_waiting

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
        from ..constants import SAFE_STAGES

        return list(SAFE_STAGES)
    if isinstance(raw, list):
        parts = raw
    else:
        parts = [p.strip() for p in str(raw).replace(" ", "").split(",") if p.strip()]
    unknown = [p for p in parts if p not in STAGE_RUNNERS]
    if unknown:
        raise ValueError(f"Unknown stages: {unknown}; known={list(STAGE_RUNNERS)}")
    return parts


__all__ = [
    "STAGE_RUNNERS",
    "StageContext",
    "parse_stages",
    "run_accept",
    "run_close",
    "run_deep",
    "run_enrich",
    "run_pancake_id",
    "run_seed",
    "run_tracking",
    "run_waiting",
]
