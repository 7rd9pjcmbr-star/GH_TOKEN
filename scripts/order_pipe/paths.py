"""Path taxonomy helpers + per-order PathId classification."""

from __future__ import annotations

import sqlite3
from typing import Any

from .constants import PathId

PATH_LABELS: dict[str, str] = {
    PathId.CLEAR.value: "Đủ dữ liệu / đã map — monitor",
    PathId.WAIT.value: "Chờ ship / extend_code — không ép VĐ",
    PathId.MISSING.value: "Hard gap — không bịa timestamp",
    PathId.ACCEPT.value: "Accept có chủ đích (soft/commune/canceled)",
    PathId.MASK.value: "PII redaction **** — không AES-unmask",
}

# Mutually exclusive operational PathId. MASK is an overlay (PII), not a rival.
PATH_CASE_SQL = """
CASE
  WHEN lower(ifnull(status,'')) IN ('submitted','new')
       AND tracking_code IS NOT NULL AND tracking_code != ''
       AND tracking_code = so_noi_bo THEN 'PATH-WAIT'
  WHEN lower(ifnull(status,'')) = 'delivered' AND ifnull(delivered_at,'') = '' THEN 'PATH-MISSING'
  WHEN lower(ifnull(status,'')) = 'shipped' AND ifnull(picked_at,'') = '' THEN 'PATH-MISSING'
  WHEN lower(ifnull(status,'')) = 'delivered'
       AND ifnull(delivered_at,'') != '' AND ifnull(picked_at,'') = '' THEN 'PATH-ACCEPT'
  WHEN ifnull(district,'') = '' AND ifnull(ward,'') != '' THEN 'PATH-ACCEPT'
  WHEN lower(ifnull(status,'')) = 'canceled'
       AND tracking_code IS NOT NULL AND tracking_code != ''
       AND tracking_code = so_noi_bo THEN 'PATH-ACCEPT'
  ELSE 'PATH-CLEAR'
END
"""


def label(path_id: str | PathId | None) -> str:
    if path_id is None:
        return ""
    key = path_id.value if isinstance(path_id, PathId) else str(path_id)
    return PATH_LABELS.get(key, key)


def normalize_path(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip().upper()
    for p in PathId:
        if s == p.value or s == p.name:
            return p.value
    if s.startswith("PATH-"):
        return s
    return raw


def is_mask_redaction(row: dict[str, Any] | None) -> bool:
    """**** / phone_class=MASKED is redaction — never AES-unmask."""
    if not row:
        return False
    cls = str(row.get("phone_class") or "").strip().upper()
    if cls == "MASKED":
        return True
    phone = str(row.get("receiver_phone") or "")
    return "*" in phone


def classify_order(row: dict[str, Any] | None) -> PathId:
    """Operational PathId for one order row (MASK is overlay via is_mask_redaction)."""
    if not row:
        return PathId.MISSING
    status = str(row.get("status") or "").strip().lower()
    trk = str(row.get("tracking_code") or "").strip()
    so = str(row.get("so_noi_bo") or "").strip()
    pancake_id = bool(trk) and trk == so
    picked = str(row.get("picked_at") or "").strip()
    delivered = str(row.get("delivered_at") or "").strip()
    district = str(row.get("district") or "").strip()
    ward = str(row.get("ward") or "").strip()

    if status in ("submitted", "new") and pancake_id:
        return PathId.WAIT
    if status == "delivered" and not delivered:
        return PathId.MISSING
    if status == "shipped" and not picked:
        return PathId.MISSING
    if status == "delivered" and delivered and not picked:
        return PathId.ACCEPT
    if ward and not district:
        return PathId.ACCEPT
    if status == "canceled" and pancake_id:
        return PathId.ACCEPT
    return PathId.CLEAR


def path_census(conn: sqlite3.Connection, wid: str) -> dict[str, Any]:
    """Aggregate PathId counts for a warehouse — seed/close scoreboard."""
    by_path = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT {PATH_CASE_SQL} AS path_id, COUNT(*) AS orders
            FROM orders WHERE warehouse_id = ?
            GROUP BY 1 ORDER BY orders DESC
            """,
            (wid,),
        )
    ]
    counts = {p.value: 0 for p in PathId}
    for row in by_path:
        pid = normalize_path(row.get("path_id")) or PathId.CLEAR.value
        counts[pid] = int(row.get("orders") or 0)
    # MASK overlay (not mutually exclusive with operational PathId)
    mask_n = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM orders
            WHERE warehouse_id = ?
              AND (
                phone_class = 'MASKED'
                OR instr(ifnull(receiver_phone,''), '*') > 0
              )
            """,
            (wid,),
        ).fetchone()[0]
    )
    counts[PathId.MASK.value] = mask_n
    n = int(
        conn.execute(
            "SELECT COUNT(*) FROM orders WHERE warehouse_id = ?", (wid,)
        ).fetchone()[0]
    )
    wait = counts[PathId.WAIT.value]
    missing = counts[PathId.MISSING.value]
    accept = counts[PathId.ACCEPT.value]
    return {
        "query_type": "path_census",
        "query": wid,
        "hit": n > 0,
        "count": n,
        "by_path": counts,
        "by_path_rows": by_path,
        "mask_overlay": mask_n,
        "path": (
            f"path_census n={n} wait={wait} missing={missing} "
            f"accept={accept} mask*={mask_n}"
        ),
        "unmask_map": {
            "path_id": PathId.CLEAR.value if n else PathId.MISSING.value,
            "mask_overlay": PathId.MASK.value,
            "policy": "**** is redaction — do not AES-unmask",
        },
        "next": [
            "PATH-WAIT submitted/new → enrich after ship (no invented extend_code)",
            "PATH-MISSING hard gap → accept, do not invent timestamps",
            "PATH-ACCEPT soft/commune/canceled → keep",
        ],
    }
