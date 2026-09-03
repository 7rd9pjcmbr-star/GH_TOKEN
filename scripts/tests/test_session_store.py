"""Tests for scripts/session_store.py using FAKE tokens/cookies (no real secrets).

Runs fully offline: keepalive uses refresh=False, probe=False so no network/login.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _fake_jwt(exp_epoch: int) -> str:
    """Build a syntactically valid 3-part JWT whose payload carries `exp`."""
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp_epoch, "uid": "owned"}).encode()).decode().rstrip("=")
    return f"aaa.{payload}.bbb"


def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["SESSION_STORE_PATH"] = str(Path(self._tmp.name) / "session_store.json")
        # import after env override so store_path() picks it up
        import session_store as ss

        self.ss = ss
        # clean any leaked token env from other tests
        for k in ("GHN_API_TOKEN", "PANCAKE_POS_ACCESS_TOKEN"):
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        os.environ.pop("SESSION_STORE_PATH", None)
        self._tmp.cleanup()

    def test_save_load_roundtrip(self):
        self.ss.set_session("GHN", tokens={"GHN_API_TOKEN": "ghn-secret-1234"})
        store = self.ss.load_store()
        self.assertIn("GHN", store["platforms"])
        self.assertEqual(store["platforms"]["GHN"]["tokens"]["GHN_API_TOKEN"], "ghn-secret-1234")

    def test_status_is_mask_only(self):
        self.ss.set_session("GHN", tokens={"GHN_API_TOKEN": "ghn-secret-ABCD"})
        rep = self.ss.status_report()
        blob = json.dumps(rep)
        self.assertNotIn("ghn-secret-ABCD", blob)  # raw value never leaks
        tok = rep["platforms"]["GHN"]["tokens"][0]
        # mask_secret => "ghn-…ABCD(len=15)": shows only first/last 4, never the middle
        self.assertTrue(tok["masked"].startswith("ghn-"))
        self.assertIn("ABCD", tok["masked"])
        self.assertIn("…", tok["masked"])

    def test_token_expiry_statuses(self):
        now = _now()
        self.ss.set_session(
            "Pancake",
            tokens={
                "PANCAKE_POS_ACCESS_TOKEN": _fake_jwt(now + 7200),  # ok (>1h)
            },
        )
        rep = self.ss.status_report()
        self.assertEqual(rep["platforms"]["Pancake"]["tokens"][0]["status"], "ok")

        # expiring (< threshold) and expired
        self.ss.set_session("Pancake", tokens={"PANCAKE_POS_ACCESS_TOKEN": _fake_jwt(now + 60)})
        self.assertEqual(self.ss.status_report()["platforms"]["Pancake"]["tokens"][0]["status"], "expiring")

        self.ss.set_session("Pancake", tokens={"PANCAKE_POS_ACCESS_TOKEN": _fake_jwt(now - 60)})
        rep = self.ss.status_report()
        self.assertEqual(rep["platforms"]["Pancake"]["tokens"][0]["status"], "expired")
        self.assertEqual(rep["overall"], "expired")

    def test_cookie_header_filters_expired(self):
        now = _now()
        self.ss.set_session(
            "Pancake",
            cookies=[
                {"name": "sid", "value": "good", "domain": "pos.pancake.vn", "expires": now + 3600},
                {"name": "old", "value": "stale", "domain": "pos.pancake.vn", "expires": now - 3600},
                {"name": "sess", "value": "nolimit", "domain": "pos.pancake.vn", "expires": -1},
            ],
        )
        hdr = self.ss.cookie_header("Pancake", domain="pancake.vn")
        self.assertIn("sid=good", hdr)
        self.assertIn("sess=nolimit", hdr)
        self.assertNotIn("old=stale", hdr)  # expired filtered out

    def test_apply_to_env(self):
        self.ss.set_session("GHN", tokens={"GHN_API_TOKEN": "ghn-xyz-9999"})
        os.environ.pop("GHN_API_TOKEN", None)
        rep = self.ss.apply_to_env()
        self.assertIn("GHN_API_TOKEN", rep["keys"])
        self.assertEqual(os.environ.get("GHN_API_TOKEN"), "ghn-xyz-9999")

    def test_import_storage_state(self):
        now = _now()
        state = {
            "cookies": [
                {"name": "c_user", "value": "111", "domain": ".pancake.vn", "path": "/", "expires": now + 999, "httpOnly": True, "secure": True},
                {"name": "xs", "value": "222", "domain": ".pancake.vn"},
            ],
            "origins": [],
        }
        p = Path(self._tmp.name) / "pancake_storage_state.json"
        p.write_text(json.dumps(state), encoding="utf-8")
        self.ss.import_storage_state("Pancake", p)
        store = self.ss.load_store()
        names = {c["name"] for c in store["platforms"]["Pancake"]["cookies"]}
        self.assertEqual(names, {"c_user", "xs"})

    def test_keepalive_offline_updates_last_ok(self):
        self.ss.set_session("GHN", tokens={"GHN_API_TOKEN": "ghn-abc-0001"})
        rep = self.ss.keepalive(refresh=False, probe=False)  # no network/login
        self.assertTrue(rep["ok"])
        store = self.ss.load_store()
        self.assertIsNotNone(store["platforms"]["GHN"]["meta"]["last_ok_at"])

    def test_async_keepalive_offline(self):
        import asyncio

        self.ss.set_session("GHN", tokens={"GHN_API_TOKEN": "ghn-async-0001"})
        rep = asyncio.run(self.ss.keepalive_async(refresh=False, probe_urls=None))  # no network/login
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["module"], "session_store.keepalive_async")
        store = self.ss.load_store()
        self.assertIsNotNone(store["platforms"]["GHN"]["meta"]["last_ok_at"])

    def test_async_daemon_finite_iterations(self):
        import asyncio

        self.ss.set_session("GHN", tokens={"GHN_API_TOKEN": "ghn-async-0002"})
        self.ss._STOP["flag"] = False
        res = asyncio.run(self.ss.run_daemon_async(interval=0, iterations=3, probe_urls=None, refresh=False))
        self.assertTrue(res["ok"])
        self.assertEqual(res["iterations"], 3)

    def test_daemon_finite_iterations(self):
        self.ss.set_session("GHN", tokens={"GHN_API_TOKEN": "ghn-abc-0002"})
        # monkeypatch keepalive to avoid network and speed up
        calls = {"n": 0}
        orig = self.ss.keepalive

        def fake_ka(*a, **k):
            calls["n"] += 1
            return orig(refresh=False, probe=False)

        self.ss.keepalive = fake_ka  # type: ignore
        try:
            res = self.ss.run_daemon(interval=0, iterations=3, probe=False)
        finally:
            self.ss.keepalive = orig  # type: ignore
        self.assertEqual(res["iterations"], 3)
        self.assertEqual(calls["n"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
