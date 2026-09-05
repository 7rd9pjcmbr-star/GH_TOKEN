"""Seed stage + parse_stages + pipeline --start slice."""

from __future__ import annotations

import unittest

from order_pipe import ReverseFlow, classify_order
from order_pipe.constants import PathId
from order_pipe.stages import STAGE_RUNNERS, parse_stages
from order_pipe.stages.seed import run_seed
from order_pipe.stages.context import StageContext
from order_pipe.tests.fakes import WID, make_store


class ParseStagesTests(unittest.TestCase):
    def test_default_safe(self):
        self.assertEqual(parse_stages(None), ["seed", "deep", "accept", "close"])

    def test_csv(self):
        self.assertEqual(parse_stages("seed, close"), ["seed", "close"])

    def test_list(self):
        self.assertEqual(parse_stages(["seed"]), ["seed"])

    def test_unknown(self):
        with self.assertRaises(ValueError):
            parse_stages("hop14")

    def test_registry_has_all_capabilities(self):
        self.assertEqual(
            set(STAGE_RUNNERS),
            {
                "seed",
                "deep",
                "enrich",
                "tracking",
                "pancake_id",
                "accept",
                "waiting",
                "close",
            },
        )


class SeedStageTests(unittest.TestCase):
    def test_seed_hits_warehouse_kho_and_census(self):
        store = make_store()
        ctx = StageContext(store=store, wid=WID)
        results = run_seed(ctx)
        types = [r.get("query_type") for r in results]
        self.assertIn("warehouse_id", types)
        self.assertIn("kho", types)
        self.assertIn("path_census", types)
        self.assertTrue(any(r.get("hit") for r in results))
        census = next(r for r in results if r.get("query_type") == "path_census")
        self.assertEqual(census["by_path"][PathId.WAIT.value], 2)
        self.assertEqual(census["count"], 7)

    def test_pipeline_start_seed_only(self):
        store = make_store()
        rf = ReverseFlow(store, warehouse_id=WID)
        result = rf.pipeline.run(stages=["seed"], live=False, apply=False)
        self.assertTrue(result.ok)
        self.assertEqual(result.stages, ["seed"])
        self.assertGreaterEqual(result.hits, 1)
        self.assertTrue(all(r.get("module_stage") == "seed" for r in result.results))
        types = {r.get("query_type") for r in result.results}
        self.assertIn("path_census", types)
        # no hop13 / deep queries
        self.assertNotIn("flow_closure", types)
        self.assertNotIn("flow_gaps", types)

    def test_classify_fixture_clear_row(self):
        store = make_store()
        row = dict(
            store.conn.execute(
                "SELECT * FROM orders WHERE van_tay = 'fp-clear'"
            ).fetchone()
        )
        self.assertEqual(classify_order(row), PathId.CLEAR)


if __name__ == "__main__":
    unittest.main()
