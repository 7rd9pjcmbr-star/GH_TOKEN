#!/usr/bin/env python3
"""AssassinTool MCP server — expose report/assassin quarantine analysis to Cursor."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "AssassinTool MCP requires the mcp package. Install: pip install 'mcp[cli]'"
    ) from exc

from assassin_tool import analyze_assassin_file, build_report, format_text, list_assassin_files

mcp = FastMCP("AssassinTool")


@mcp.tool()
def scan_assassin_reports(all_files: bool = False) -> str:
    """Scan quarantine/telegram for report/assassin files and return a summary."""
    report = build_report(all_files=all_files)
    return format_text(report)


@mcp.tool()
def analyze_assassin_file_tool(path: str) -> str:
    """Analyze one quarantine file by path. Passwords are redacted; order signals kept."""
    p = Path(path)
    if not p.is_file():
        return json.dumps({"ok": False, "error": f"not a file: {path}"}, ensure_ascii=False)
    analysis = analyze_assassin_file(p)
    return json.dumps(analysis, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
def list_assassin_candidates(all_files: bool = False) -> str:
    """List candidate report/assassin files in quarantine (newest first)."""
    files = list_assassin_files(all_files=all_files)
    payload = [{"name": p.name, "path": str(p), "size": p.stat().st_size} for p in files]
    return json.dumps({"count": len(payload), "files": payload}, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
