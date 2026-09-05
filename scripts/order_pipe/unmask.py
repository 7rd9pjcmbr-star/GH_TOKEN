"""Unmask assist facade — owned key only; **** = redaction."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .constants import ASUMEE_WID


def _load_secret_envs() -> None:
    root = Path(__file__).resolve().parents[2]
    for name in ("backend_pipes.env", "mapper_icon_aes.env"):
        p = root / "secrets" / name
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def resolve_key_file() -> str | None:
    _load_secret_envs()
    key = os.environ.get("MAPPER_ICON_AES_KEY_B64") or os.environ.get("MAPPER_AES_KEY_B64")
    if not key:
        return None
    p = Path("/tmp/order_pipe_mapper_aes.key")
    p.write_text(key.strip() + "\n", encoding="utf-8")
    os.chmod(p, 0o600)
    return str(p)


def run_asunmee_live(*, sample_limit: int = 20) -> dict[str, Any]:
    _load_secret_envs()
    from crypto_decode_assist import assist_asunmee_structure

    return assist_asunmee_structure(live=True, sample_limit=sample_limit)


def run_unmask_assist(*, key_file: str | None = None) -> dict[str, Any]:
    _load_secret_envs()
    from crypto_decode_assist import assist_unmask

    kf = key_file or resolve_key_file()
    return assist_unmask(key_file=kf, include_asunmee=True, include_atlas=True)


def run_inner_warehouse(*, warehouse_id: str = ASUMEE_WID, key_file: str | None = None) -> dict[str, Any]:
    _load_secret_envs()
    import inner_unmask_deep_mapper as iud  # noqa: WPS433

    kf = key_file or resolve_key_file()
    return iud.build_report(warehouse_id=warehouse_id, key_file=kf)
