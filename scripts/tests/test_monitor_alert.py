"""Tests for scripts/monitor_alert.py — dedup/cooldown logic + async web check.

Offline: evaluate() is pure; web check uses the local nginx + a refused port.
No Telegram is sent (dry-run), no secrets.
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

import monitor_alert as ma  # noqa: E402


class EvaluateTests(unittest.TestCase):
    def test_worsen_then_dedup_then_cooldown(self):
        state = {"components": {}}
        # 1) first WARN -> alert (worsened from ok)
        v = ma.evaluate([{"component": "x", "status": "warn", "detail": "d"}], state, cooldown_s=900, now=1000)
        self.assertEqual(len(v["alerts"]), 1)
        self.assertEqual(v["alerts"][0]["kind"], "worsened")
        # 2) same WARN shortly after -> NO alert (not worsened, cooldown not elapsed)
        v = ma.evaluate([{"component": "x", "status": "warn", "detail": "d"}], state, cooldown_s=900, now=1100)
        self.assertEqual(len(v["alerts"]), 0)
        # 3) same WARN after cooldown -> alert again (ongoing/due)
        v = ma.evaluate([{"component": "x", "status": "warn", "detail": "d"}], state, cooldown_s=900, now=2100)
        self.assertEqual(len(v["alerts"]), 1)
        self.assertEqual(v["alerts"][0]["kind"], "ongoing")

    def test_escalation_and_recovery(self):
        state = {"components": {}}
        ma.evaluate([{"component": "y", "status": "warn", "detail": "d"}], state, now=1000)
        # warn -> critical escalates -> alert
        v = ma.evaluate([{"component": "y", "status": "critical", "detail": "d"}], state, now=1010)
        self.assertEqual(v["alerts"][0]["kind"], "worsened")
        self.assertEqual(v["overall"], "critical")
        # critical -> ok recovers -> recovered alert
        v = ma.evaluate([{"component": "y", "status": "ok", "detail": "d"}], state, now=1020)
        self.assertEqual(len(v["alerts"]), 1)
        self.assertEqual(v["alerts"][0]["kind"], "recovered")
        self.assertEqual(v["overall"], "ok")

    def test_ok_never_alerts(self):
        state = {"components": {}}
        v = ma.evaluate([{"component": "z", "status": "ok", "detail": "d"}], state, now=1000)
        self.assertEqual(v["alerts"], [])
        self.assertEqual(v["overall"], "ok")


class WebCheckTests(unittest.TestCase):
    def test_check_web_ok_and_unreachable(self):
        res = asyncio.run(ma.check_web(["http://localhost/healthz", "http://localhost:1/"]))
        by = {r["component"]: r for r in res}
        # local nginx healthz should be ok (prod tier running); refused port -> critical
        self.assertEqual(by["web:http://localhost/healthz"]["status"], "ok")
        self.assertEqual(by["web:http://localhost:1/"]["status"], "critical")


class RunOnceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["MONITOR_STATE_PATH"] = str(Path(self._tmp.name) / "monitor.json")

    def tearDown(self):
        os.environ.pop("MONITOR_STATE_PATH", None)
        self._tmp.cleanup()

    def test_run_once_dry_run_no_send(self):
        rep = asyncio.run(ma.run_once_async(web_urls=["http://localhost:1/"], send=False))
        self.assertTrue(rep["ok"])
        self.assertFalse(rep["alert_sent"])          # dry-run: never sends
        self.assertEqual(rep["overall"], "critical")  # refused web url -> critical
        self.assertTrue(rep["alert_pending"])          # would have alerted


if __name__ == "__main__":
    unittest.main(verbosity=2)
