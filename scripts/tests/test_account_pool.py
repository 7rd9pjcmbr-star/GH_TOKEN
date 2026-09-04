"""Tests for scripts/account_pool.py using FAKE owned accounts (no real secrets).

Accounts are injected via OWNED_ACCOUNTS_JSON and passed explicitly as `env`,
so nothing touches real secrets/*.env files or the network.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

FAKE_ENV = {
    "OWNED_ACCOUNTS_JSON": json.dumps(
        [
            {"platform": "GHN", "user": "u1", "token": "tok-GHN-AAA1", "shop_id": "s1", "label": "a"},
            {"platform": "GHN", "user": "u2", "token": "tok-GHN-BBB2", "shop_id": "s2", "label": "b"},
            {"platform": "Pancake", "token": "tok-PC-CCC3", "shop_id": "1530618", "label": "main"},
        ]
    )
}


class AccountPoolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ACCOUNT_POOL_PATH"] = str(Path(self._tmp.name) / "account_pool.json")
        # keep tests hermetic: neutralise ambient account sources that would leak in
        self._saved_env = {
            k: os.environ.pop(k, None) for k in ("ACCOUNT_POOL_ACCOUNTS_FILE", "OWNED_ACCOUNTS_JSON")
        }
        import account_pool as ap

        self.ap = ap
        ap.reset(None)  # clean state

    def tearDown(self):
        os.environ.pop("ACCOUNT_POOL_PATH", None)
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_load_accounts(self):
        accs = self.ap.load_accounts(FAKE_ENV)
        plats = sorted({a.platform for a in accs})
        self.assertEqual(plats, ["GHN", "Pancake"])
        self.assertEqual(len([a for a in accs if a.platform == "GHN"]), 2)

    def test_status_is_mask_only(self):
        rep = self.ap.status_report(FAKE_ENV)
        blob = json.dumps(rep)
        self.assertNotIn("tok-GHN-AAA1", blob)  # raw token never leaks
        self.assertNotIn("tok-PC-CCC3", blob)
        self.assertEqual(rep["totals"]["total"], 3)
        self.assertEqual(rep["totals"]["ready"], 3)

    def test_acquire_rotates(self):
        first = self.ap.acquire("GHN", strategy="lru", env=FAKE_ENV)
        second = self.ap.acquire("GHN", strategy="lru", env=FAKE_ENV)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first["key"], second["key"])  # LRU rotates between the 2 GHN accounts

    def test_acquire_none_for_unknown_platform(self):
        self.assertIsNone(self.ap.acquire("VNPost", strategy="lru", env=FAKE_ENV))

    def test_mark_bad_cooldown_excludes(self):
        got = self.ap.acquire("Pancake", strategy="lru", env=FAKE_ENV)
        self.assertIsNotNone(got)
        self.ap.mark_bad(got["key"], reason="429 rate limit", cooldown_s=600)
        # only 1 Pancake account → now none eligible
        self.assertIsNone(self.ap.acquire("Pancake", strategy="lru", env=FAKE_ENV))
        rep = self.ap.status_report(FAKE_ENV)
        pc = rep["platforms"]["Pancake"][0]
        self.assertGreater(pc["cooldown_remaining_s"], 0)
        self.assertFalse(pc["eligible"])

    def test_least_used_strategy(self):
        # use account "a" several times, then least_used should prefer "b"
        for _ in range(3):
            self.ap.acquire("GHN", strategy="first", env=FAKE_ENV)  # 'first' keeps hitting GHN:a
        pick = self.ap.acquire("GHN", strategy="least_used", env=FAKE_ENV)
        self.assertEqual(pick["key"], "GHN:b")

    def test_async_acquire(self):
        import asyncio

        got = asyncio.run(self.ap.acquire_async("GHN", strategy="lru", env=FAKE_ENV))
        self.assertIsNotNone(got)
        self.assertTrue(got["key"].startswith("GHN:"))

    def test_load_from_accounts_file(self):
        # write a fake accounts file and point the loader at it
        f = Path(self._tmp.name) / "accts.json"
        f.write_text(
            json.dumps({"accounts": [{"platform": "TPOS", "token": "tok-TPOS-XYZ", "shop_id": "t1", "label": "shop"}]}),
            encoding="utf-8",
        )
        os.environ["ACCOUNT_POOL_ACCOUNTS_FILE"] = str(f)
        try:
            accs = self.ap.load_accounts({})  # no env accounts -> only file
            keys = {self.ap.account_key(a) for a in accs}
            self.assertIn("TPOS:shop", keys)
        finally:
            os.environ.pop("ACCOUNT_POOL_ACCOUNTS_FILE", None)

    def test_example_file_is_valid(self):
        # the committed template must be valid and cover the platforms
        root = Path(__file__).resolve().parents[2]
        data = json.loads((root / "account_pool.accounts.example.json").read_text(encoding="utf-8"))
        plats = {a["platform"] for a in data["accounts"]}
        for expected in ("Pancake", "GHN", "ViettelPost", "TPOS", "SPX", "VNPost"):
            self.assertIn(expected, plats)

    def test_reset(self):
        self.ap.acquire("GHN", env=FAKE_ENV)
        self.ap.reset(None)
        state = self.ap.load_state()
        self.assertEqual(state["accounts"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
