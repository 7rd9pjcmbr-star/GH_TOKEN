"""Tests for scripts/realtime_advanced.py — adaptive policy + async concurrency/dedup.

Offline: uses injected fake async sources (no network, no secrets).
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import realtime_advanced as rt  # noqa: E402


class AdaptiveIntervalTests(unittest.TestCase):
    def test_new_snaps_to_min(self):
        self.assertEqual(rt.next_interval(had_new=True, had_error=False, current=120, min_i=5, base=30), 5)

    def test_error_backs_off(self):
        v = rt.next_interval(had_new=False, had_error=True, current=30, base=30, max_i=300, error_streak=1)
        self.assertGreater(v, 30)
        self.assertLessEqual(v, 300)

    def test_idle_returns_to_base_then_grows(self):
        self.assertEqual(rt.next_interval(had_new=False, had_error=False, current=5, base=30), 30)
        grown = rt.next_interval(had_new=False, had_error=False, current=30, base=30, max_i=300)
        self.assertGreater(grown, 30)
        self.assertLessEqual(grown, 300)

    def test_cap_at_max(self):
        self.assertEqual(rt.next_interval(had_new=False, had_error=False, current=1000, base=30, max_i=300), 300)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["REALTIME_ADV_STATE_PATH"] = str(Path(self._tmp.name) / "rt.json")

    def tearDown(self):
        os.environ.pop("REALTIME_ADV_STATE_PATH", None)
        self._tmp.cleanup()

    def test_dedup_across_ticks(self):
        async def src():
            return {"orders": [("Pancake", {"id": "A"}), ("GHN", {"id": "B"})]}

        engine = rt.RealtimeEngine(sources=[src])
        first = asyncio.run(engine.tick())
        self.assertEqual(first["new_count"], 2)
        second = asyncio.run(engine.tick())
        self.assertEqual(second["new_count"], 0)  # same orders -> deduped by fingerprint

    def test_sources_run_concurrently(self):
        async def slow_a():
            await asyncio.sleep(0.15)
            return {"orders": []}

        async def slow_b():
            await asyncio.sleep(0.15)
            return {"orders": []}

        engine = rt.RealtimeEngine(sources=[slow_a, slow_b])
        cycle = asyncio.run(engine.tick())
        # concurrent gather => ~150ms, not ~300ms sequential
        self.assertLess(cycle["duration_ms"], 260)

    def test_error_captured_and_streak(self):
        async def boom():
            raise RuntimeError("source down")

        engine = rt.RealtimeEngine(sources=[boom])
        cycle = asyncio.run(engine.tick())
        self.assertEqual(len(cycle["errors"]), 1)
        self.assertEqual(engine.stats.error_streak, 1)

    def test_run_finite_iterations(self):
        async def src():
            return {"orders": []}

        engine = rt.RealtimeEngine(rt.EngineConfig(min_interval=0, base_interval=0), sources=[src])
        res = asyncio.run(engine.run(iterations=3, start_interval=0))
        self.assertTrue(res["ok"])
        self.assertEqual(res["cycles"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
