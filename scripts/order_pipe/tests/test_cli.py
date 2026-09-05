"""CLI --start / --list-stages smoke (no live pipe DB)."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from order_pipe.cli import main
from order_pipe import ReverseFlow
from order_pipe.tests.fakes import WID, make_store


class CliTests(unittest.TestCase):
    def test_list_stages(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--list-stages"])
        self.assertEqual(rc, 0)
        text = buf.getvalue()
        for sid in (
            "seed",
            "deep",
            "enrich",
            "tracking",
            "pancake_id",
            "accept",
            "waiting",
            "close",
        ):
            self.assertIn(sid, text)

    def test_start_runs_seed(self):
        store = make_store()
        instance = ReverseFlow(store, warehouse_id=WID)
        buf = io.StringIO()
        with patch("order_pipe.ReverseFlow", return_value=instance):
            with patch("order_pipe.report.write_module_outputs", return_value={}):
                with redirect_stdout(buf):
                    rc = main(["--start"])
        self.assertEqual(rc, 0)
        text = buf.getvalue()
        self.assertIn("seed", text)
        self.assertIn("path_census", text)
        self.assertNotIn("stage=deep", text)
        self.assertNotIn("stage=close", text)


if __name__ == "__main__":
    unittest.main()
