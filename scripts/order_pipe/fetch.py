"""Fetch orders via owned Pancake / pipe builders."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def fetch_realtime(*, limit: int = 80) -> dict[str, Any]:
    """Chạy realtime_order_sync --once."""
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "realtime_order_sync.py"),
        "--once",
        "--limit",
        str(limit),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    return {
        "ok": r.returncode == 0,
        "exit": r.returncode,
        "cmd": cmd,
        "stdout_tail": "\n".join(r.stdout.splitlines()[-30:]),
        "stderr_tail": "\n".join(r.stderr.splitlines()[-20:]),
    }


def repipe(*, limit: int = 8000) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "order_pipe_kho_buucuc_db.py"),
        "--limit",
        str(limit),
        "--no-cycle",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    return {
        "ok": r.returncode == 0,
        "exit": r.returncode,
        "cmd": cmd,
        "stdout_tail": "\n".join(r.stdout.splitlines()[-20:]),
    }


def scan_buucuc(*, days: int = 3, limit: int = 5000) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "scan_buucuc_orders.py"),
        "--days",
        str(days),
        "--limit",
        str(limit),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return {
        "ok": r.returncode == 0,
        "exit": r.returncode,
        "cmd": cmd,
        "stdout_tail": "\n".join(r.stdout.splitlines()[-25:]),
    }
