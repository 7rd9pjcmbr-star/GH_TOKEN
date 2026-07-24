"""Tracking adapter — aship URL / probe."""

from __future__ import annotations

from typing import Any


def attach_urls(row: dict) -> dict:
    try:
        from tracking_aship import attach_tracking_urls

        return attach_tracking_urls(row)
    except Exception as e:  # noqa: BLE001
        out = dict(row)
        out["_tracking_error"] = str(e)
        return out


def build_url(
    code: str,
    *,
    provider: str | None = None,
    carrier: str | None = None,
    buucuc: str | None = None,
) -> str | None:
    from tracking_aship import build_tracking_url

    return build_tracking_url(
        code, provider=provider, carrier=carrier, buucuc=buucuc, tracking_code=code
    )


def probe(url: str, timeout: float = 8.0) -> dict[str, Any]:
    from tracking_aship import probe_url

    return probe_url(url, timeout=timeout)
