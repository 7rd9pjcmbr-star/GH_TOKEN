#!/usr/bin/env python3
"""Minimal Pancake POS client (secrets-only). Port gọn từ Logtitan connection paths."""

from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_BASE_URLS = (
    "https://pos.pancake.vn/api/v1",
    "https://pos.pages.fm/api/v1",
)


def resolve_credentials(api_key: str = "", access_token: str = "") -> dict[str, str]:
    centralized = (
        api_key
        or os.getenv("PANCAKE_POS_API_KEY", "")
        or os.getenv("PANCAKE_API_KEY", "")
        or os.getenv("CENTRAL_API_KEY", "")
        or os.getenv("PANCAKE_API_TOKEN", "")
    ).strip()
    token = (
        access_token
        or os.getenv("PANCAKE_POS_ACCESS_TOKEN", "")
        or os.getenv("PANCAKE_POS_TOKEN", "")
        or os.getenv("PANCAKE_TOKEN", "")
    ).strip()
    if centralized and not token:
        if len(centralized) == 32:
            return {"api_key": centralized, "access_token": ""}
        return {"api_key": "", "access_token": centralized}
    return {"api_key": centralized, "access_token": token}


def auth_ready(creds: dict[str, str]) -> bool:
    return bool(creds.get("api_key") or creds.get("access_token"))


def _get(
    base_url: str,
    path: str,
    creds: dict[str, str],
    params: dict[str, Any] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    query = dict(params or {})
    headers = {"Accept": "application/json"}
    if creds.get("api_key"):
        query["api_key"] = creds["api_key"]
    elif creds.get("access_token"):
        headers["Authorization"] = f"Bearer {creds['access_token']}"
    else:
        raise ValueError("Missing Pancake credential")
    resp = requests.get(url, params=query, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected Pancake JSON")
    return data


def request_with_fallback(
    path: str,
    creds: dict[str, str],
    params: dict[str, Any] | None = None,
    base_urls: tuple[str, ...] | None = None,
    timeout: int = 20,
) -> tuple[dict[str, Any], str]:
    errors: list[str] = []
    for base in base_urls or DEFAULT_BASE_URLS:
        try:
            return _get(base, path, creds, params=params, timeout=timeout), base
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{base}: {exc}")
    raise RuntimeError(" | ".join(errors))


def fetch_shops(creds: dict[str, str], base_urls=None, timeout: int = 20):
    payload, base = request_with_fallback("/shops", creds, base_urls=base_urls, timeout=timeout)
    return payload.get("shops") or [], base


def fetch_shop_orders(
    creds: dict[str, str],
    shop_id: str | int,
    base_url: str,
    params: dict[str, Any] | None = None,
    timeout: int = 20,
) -> list[dict]:
    payload = _get(base_url, f"/shops/{shop_id}/orders", creds, params=params, timeout=timeout)
    data = payload.get("data")
    if isinstance(data, list):
        return data
    if isinstance(payload.get("orders"), list):
        return payload["orders"]
    return []
