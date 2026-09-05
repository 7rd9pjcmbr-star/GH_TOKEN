"""PathId classifier + census."""

from __future__ import annotations

import unittest

from order_pipe.constants import PathId
from order_pipe.paths import classify_order, is_mask_redaction, path_census
from order_pipe.tests.fakes import make_store


class ClassifyOrderTests(unittest.TestCase):
    def test_wait_submitted_pancake_id(self):
        self.assertEqual(
            classify_order(
                {"status": "submitted", "tracking_code": "SO1", "so_noi_bo": "SO1"}
            ),
            PathId.WAIT,
        )

    def test_wait_new(self):
        self.assertEqual(
            classify_order({"status": "new", "tracking_code": "X", "so_noi_bo": "X"}),
            PathId.WAIT,
        )

    def test_missing_delivered_no_ts(self):
        self.assertEqual(
            classify_order(
                {
                    "status": "delivered",
                    "tracking_code": "SPX1",
                    "so_noi_bo": "SO",
                    "delivered_at": "",
                }
            ),
            PathId.MISSING,
        )

    def test_missing_shipped_no_pick(self):
        self.assertEqual(
            classify_order(
                {
                    "status": "shipped",
                    "tracking_code": "SPX1",
                    "so_noi_bo": "SO",
                    "picked_at": "",
                }
            ),
            PathId.MISSING,
        )

    def test_accept_soft_gap(self):
        self.assertEqual(
            classify_order(
                {
                    "status": "delivered",
                    "tracking_code": "SPX1",
                    "so_noi_bo": "SO",
                    "delivered_at": "2026-08-02T00:00:00Z",
                    "picked_at": "",
                }
            ),
            PathId.ACCEPT,
        )

    def test_accept_commune(self):
        self.assertEqual(
            classify_order(
                {
                    "status": "shipped",
                    "tracking_code": "SPX1",
                    "so_noi_bo": "SO",
                    "picked_at": "2026-08-01T00:00:00Z",
                    "ward": "Phú Hội",
                    "district": "",
                }
            ),
            PathId.ACCEPT,
        )

    def test_accept_canceled_pancake_id(self):
        self.assertEqual(
            classify_order(
                {"status": "canceled", "tracking_code": "SO1", "so_noi_bo": "SO1"}
            ),
            PathId.ACCEPT,
        )

    def test_clear_delivered_complete(self):
        self.assertEqual(
            classify_order(
                {
                    "status": "delivered",
                    "tracking_code": "SPX1",
                    "so_noi_bo": "SO",
                    "picked_at": "a",
                    "delivered_at": "b",
                    "district": "TP Huế",
                    "ward": "Phú Hội",
                }
            ),
            PathId.CLEAR,
        )

    def test_empty_row_is_missing(self):
        self.assertEqual(classify_order(None), PathId.MISSING)

    def test_mask_overlay_does_not_steal_wait(self):
        row = {
            "status": "submitted",
            "tracking_code": "SO1",
            "so_noi_bo": "SO1",
            "phone_class": "MASKED",
            "receiver_phone": "0901****21",
        }
        self.assertEqual(classify_order(row), PathId.WAIT)
        self.assertTrue(is_mask_redaction(row))

    def test_clear_phone_is_not_mask(self):
        self.assertFalse(
            is_mask_redaction({"phone_class": "OK", "receiver_phone": "0901234567"})
        )


class PathCensusTests(unittest.TestCase):
    def test_census_counts(self):
        store = make_store()
        census = path_census(store.conn, store.conn.execute(
            "SELECT warehouse_id FROM orders LIMIT 1"
        ).fetchone()[0])
        by = census["by_path"]
        self.assertEqual(by[PathId.WAIT.value], 2)  # submitted + new
        self.assertEqual(by[PathId.MISSING.value], 1)
        self.assertEqual(by[PathId.ACCEPT.value], 3)  # soft + commune + canceled
        self.assertEqual(by[PathId.CLEAR.value], 1)
        self.assertEqual(census["count"], 7)
        self.assertTrue(census["hit"])
        self.assertIn("path_census", census["path"])
        self.assertGreaterEqual(census["mask_overlay"], 1)


if __name__ == "__main__":
    unittest.main()
