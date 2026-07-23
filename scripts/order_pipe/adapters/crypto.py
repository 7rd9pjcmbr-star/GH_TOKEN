"""Crypto / unmask adapter — owned secrets only; no claim on **** redaction."""

from __future__ import annotations

from typing import Any


def load_secrets() -> dict[str, str]:
    try:
        from crypto_decode_assist import load_env_secrets

        return load_env_secrets()
    except Exception:  # noqa: BLE001
        return {}


def assist_unmask(**kwargs: Any) -> dict:
    """Delegate to crypto_decode_assist — never invent cleartext from ****."""
    from crypto_decode_assist import assist_unmask as _assist

    return _assist(**kwargs)


def note_mask_policy() -> dict[str, str]:
    return {
        "path_id": "PATH-MASK-REDACTION",
        "policy": "**** is redaction, not ciphertext — do not AES-unmask",
    }
