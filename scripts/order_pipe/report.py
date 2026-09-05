"""Report build / write for module runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .constants import ASUMEE_WID
from .pipeline import PipelineResult
from .store import PIPE_DB, PipeStore

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports" / "telegram-classify"
OUT_JSON = REPORTS / "order_pipe_module.json"
OUT_TXT = REPORTS / "order_pipe_module.txt"


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_module_report(
    store: PipeStore,
    pipeline: PipelineResult,
    *,
    lookups: list[dict] | None = None,
) -> dict[str, Any]:
    import order_pipe_reverse_query as rq  # noqa: WPS433

    results = list(lookups or []) + list(pipeline.results)
    scrubbed = rq.scrub_phones_in_obj(
        {
            "ok": pipeline.ok and not pipeline.errors,
            "module": "order_pipe",
            "query": "Order Pipe module — capability pipeline",
            "checked_at": utc_now(),
            "warehouse_id": ASUMEE_WID,
            "db": {
                "pipe_db": str(store.path),
                "orders": store.count_orders(),
                "asumee": store.asumee_stats(),
            },
            "pipeline": {
                "stages": pipeline.stages,
                "meta": pipeline.meta,
                "errors": pipeline.errors,
            },
            "summary": {
                "queries": len(results),
                "hits": sum(1 for r in results if r.get("hit")),
            },
            "results": results,
            "safety": {
                "secrets_only": True,
                "no_dump_login": True,
                "phone_masked_in_report": True,
                "no_invent_timestamps": True,
            },
            "next_actions": [
                "python3 -m order_pipe --run",
                "python3 -m order_pipe --stages enrich,waiting --live --apply --limit 40",
                "python3 -m order_pipe --stages close --live --apply",
                "python3 scripts/order_pipe_reverse_query.py --continue-flow --hop13-live --hop13-apply",
            ],
        }
    )
    return scrubbed


def format_module_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("📦 ORDER PIPE MODULE · CAPABILITY PIPELINE")
    L(f"Lúc: {report.get('checked_at')}")
    L(f"DB: {(report.get('db') or {}).get('pipe_db')} · orders={(report.get('db') or {}).get('orders')}")
    asem = (report.get("db") or {}).get("asumee") or {}
    if asem:
        L(
            f"ASUMEE: n={asem.get('orders')} trk_real={asem.get('trk_real')} "
            f"url={asem.get('with_url')} pick={asem.get('with_pick')} "
            f"del={asem.get('with_del')} 3pl={asem.get('with_3pl')} "
            f"wait={int(asem.get('wait_submitted') or 0)+int(asem.get('wait_new') or 0)}"
        )
    pipe = report.get("pipeline") or {}
    L(f"Stages: {', '.join(pipe.get('stages') or [])}")
    meta = pipe.get("meta") or {}
    L(
        f"live={meta.get('live')} apply={meta.get('apply')} "
        f"queries={meta.get('queries')} hits={meta.get('hits')}"
    )
    if pipe.get("errors"):
        L(f"Errors: {pipe.get('errors')}")
    L("")
    L("=== Results (path headlines) ===")
    for r in report.get("results") or []:
        mark = "✅" if r.get("hit") else "○"
        stage = r.get("module_stage") or ""
        L(f"{mark} [{r.get('query_type')}] stage={stage} q={r.get('query')}")
        if r.get("path"):
            L(f"  path: {r['path']}")
    L("")
    L("Next:")
    for a in report.get("next_actions") or []:
        L(f"· {a}")
    return "\n".join(lines) + "\n"


def write_module_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    OUT_TXT.write_text(format_module_text(report), encoding="utf-8")
    return {"json": OUT_JSON, "txt": OUT_TXT}
