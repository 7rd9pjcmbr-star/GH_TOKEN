#!/usr/bin/env python3
"""Shim: python3 scripts/order_pipe_module.py → order_pipe CLI."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from order_pipe.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
