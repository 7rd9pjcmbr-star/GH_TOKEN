"""Icon / fingerprint feedback adapter."""

from __future__ import annotations

from typing import Any


def chant(icons: list[str] | None = None) -> str:
    from realtime_icon_feedback_mapper import chant as _chant

    return _chant(icons or [])


def feedback_line(**kwargs: Any) -> str:
    from realtime_icon_feedback_mapper import feedback_line as _fb

    return _fb(**kwargs)


def receive_fingerprint(payload: dict) -> dict:
    from realtime_icon_feedback_mapper import receive_fingerprint as _recv

    return _recv(payload)
