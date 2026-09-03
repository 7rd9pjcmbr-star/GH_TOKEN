"""python3 -m order_pipe.tests"""

from __future__ import annotations

import unittest


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromNames(
        [
            "order_pipe.tests.test_paths",
            "order_pipe.tests.test_seed",
            "order_pipe.tests.test_cli",
        ]
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
