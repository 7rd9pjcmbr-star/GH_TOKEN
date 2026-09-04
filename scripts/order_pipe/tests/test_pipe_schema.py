"""Regression: a freshly-built (empty) pipe DB must expose every column that
order_pipe.store.asumee_stats selects — notably `tracking_url`, which was
missing from ensure_pipe_schema and crashed `order_pipe --start` on an empty DB
with: sqlite3.OperationalError: no such column: tracking_url.
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class PipeSchemaRegressionTests(unittest.TestCase):
    def _fresh_conn(self) -> sqlite3.Connection:
        from order_pipe_kho_buucuc_db import ensure_pipe_schema

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        ensure_pipe_schema(conn)
        return conn

    def test_tracking_url_column_present(self):
        conn = self._fresh_conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)")}
        self.assertIn("tracking_url", cols)

    def test_asumee_stats_does_not_crash_on_empty_db(self):
        from order_pipe.store import PipeStore

        conn = self._fresh_conn()
        stats = PipeStore(conn).asumee_stats()  # must not raise OperationalError
        self.assertEqual(stats["orders"], 0)
        self.assertIn("with_url", stats)


if __name__ == "__main__":
    unittest.main(verbosity=2)
