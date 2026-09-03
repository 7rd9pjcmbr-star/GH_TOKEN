"""Shared stage context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..constants import ASUMEE_WID
from ..store import PipeStore


@dataclass
class StageContext:
    store: PipeStore
    wid: str = ASUMEE_WID
    live: bool = False
    apply: bool = False
    limit: int = 40
    probe: bool = False
    extras: dict[str, Any] = field(default_factory=dict)
