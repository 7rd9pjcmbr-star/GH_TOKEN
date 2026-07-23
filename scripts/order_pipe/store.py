"""Pipe SQLite store facade."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .constants import ASUMEE_WID

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "reports" / "telegram-classify"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"


class PipeStore:
    """Mở / đảm bảo kho_buucuc_pipe.db."""

    def __init__(self, conn: sqlite3.Connection, *, path: Path = PIPE_DB):
        self.conn = conn
        self.path = path

    @classmethod
    def open(cls, path: Path | None = None) -> PipeStore | None:
        db = path or PIPE_DB
        if not db.is_file():
            return None
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return cls(conn, path=db)

    @classmethod
    def ensure(cls, path: Path | None = None) -> PipeStore:
        """Mở DB; nếu thiếu thì build qua order_pipe_reverse_query.ensure_pipe_or_build."""
        store = cls.open(path)
        if store:
            return store
        import order_pipe_reverse_query as rq  # noqa: WPS433

        conn = rq.ensure_pipe_or_build()
        return cls(conn, path=path or PIPE_DB)

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> PipeStore:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def count_orders(self, warehouse_id: str | None = None) -> int:
        if warehouse_id:
            return int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM orders WHERE warehouse_id = ?",
                    (warehouse_id,),
                ).fetchone()[0]
            )
        return int(self.conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])

    def asumee_stats(self, wid: str = ASUMEE_WID) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT
              COUNT(*) AS orders,
              SUM(CASE WHEN tracking_code IS NOT NULL AND tracking_code != ''
                        AND tracking_code != so_noi_bo THEN 1 ELSE 0 END) AS trk_real,
              SUM(CASE WHEN ifnull(tracking_url,'') != '' THEN 1 ELSE 0 END) AS with_url,
              SUM(CASE WHEN ifnull(picked_at,'') != '' THEN 1 ELSE 0 END) AS with_pick,
              SUM(CASE WHEN ifnull(delivered_at,'') != '' THEN 1 ELSE 0 END) AS with_del,
              SUM(CASE WHEN buucuc IN ('J&T','SPX','GHN') THEN 1 ELSE 0 END) AS with_3pl,
              SUM(CASE WHEN status='submitted' AND tracking_code=so_noi_bo THEN 1 ELSE 0 END) AS wait_submitted,
              SUM(CASE WHEN status='new' AND tracking_code=so_noi_bo THEN 1 ELSE 0 END) AS wait_new
            FROM orders WHERE warehouse_id = ?
            """,
            (wid,),
        ).fetchone()
        return {k: row[k] for k in row.keys()}
