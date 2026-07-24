"""ReversePipeline — chạy theo capability stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .constants import ASUMEE_WID, SAFE_STAGES
from .stages import STAGE_RUNNERS, StageContext, parse_stages
from .store import PipeStore


@dataclass
class PipelineResult:
    ok: bool
    stages: list[str]
    results: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def hits(self) -> int:
        return sum(1 for r in self.results if r.get("hit"))


class ReversePipeline:
    def __init__(self, store: PipeStore, *, warehouse_id: str = ASUMEE_WID):
        self.store = store
        self.warehouse_id = warehouse_id

    def run(
        self,
        stages: str | list[str] | None = None,
        *,
        live: bool = False,
        apply: bool = False,
        limit: int = 40,
        probe: bool = False,
        stop_on_error: bool = False,
    ) -> PipelineResult:
        stage_ids = parse_stages(stages if stages is not None else list(SAFE_STAGES))
        ctx = StageContext(
            store=self.store,
            wid=self.warehouse_id,
            live=live,
            apply=apply,
            limit=limit,
            probe=probe,
        )
        collected: list[dict] = []
        errors: list[dict] = []
        for sid in stage_ids:
            runner = STAGE_RUNNERS[sid]
            try:
                chunk = runner(ctx) or []
                for r in chunk:
                    r.setdefault("module_stage", sid)
                collected.extend(chunk)
            except Exception as e:  # noqa: BLE001
                err = {"stage": sid, "error": str(e)}
                errors.append(err)
                collected.append(
                    {
                        "query_type": "stage_error",
                        "query": sid,
                        "hit": False,
                        "path": f"stage:{sid} ERROR {e}",
                        "module_stage": sid,
                        "error": str(e),
                    }
                )
                if stop_on_error:
                    break
        return PipelineResult(
            ok=not errors,
            stages=stage_ids,
            results=collected,
            errors=errors,
            meta={
                "warehouse_id": self.warehouse_id,
                "live": live,
                "apply": apply,
                "limit": limit,
                "probe": probe,
                "queries": len(collected),
                "hits": sum(1 for r in collected if r.get("hit")),
            },
        )
