#!/usr/bin/env python3
"""Module hỗ trợ giải mã — port giáo dục từ MaMoCrypto.encode + cryptography AEAD.

Hỗ trợ:
  - fromBase64 / fromHex / fromMorse / fromBraille / URL-decode (biểu diễn)
  - AES-GCM / ChaCha20-Poly1305 decrypt khi có key+nonce+ciphertext (demo/owned)
  - Quét mẫu Đang giao: phân loại MASKED/MISSING/OK — không «phá» ****

Không: crack password dump, không brute-force, không login third-party.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import json
import re
import secrets
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports" / "telegram-classify"
ORDERS_CSV = ROOT / "quarantine" / "telegram" / "orders_detailed_Dang_giao_20260512_120712.csv"
ASUNMEE_CONFIG = ROOT / "config" / "asunmee_shop_decode.json"
ASUNMEE_STRUCTURE_RAW = REPORTS / "asunmee_structure_raw.json"

# Morse / Braille mirrors js/crypto/encode.js
MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
}
MORSE_REV = {v: k for k, v in MORSE.items()}
BRAILLE = {
    "a": "⠁", "b": "⠃", "c": "⠉", "d": "⠙", "e": "⠑", "f": "⠋", "g": "⠛",
    "h": "⠓", "i": "⠊", "j": "⠚", "k": "⠅", "l": "⠇", "m": "⠍", "n": "⠝",
    "o": "⠕", "p": "⠏", "q": "⠟", "r": "⠗", "s": "⠎", "t": "⠞", "u": "⠥",
    "v": "⠧", "w": "⠺", "x": "⠭", "y": "⠽", "z": "⠵", " ": "⠀",
}
BRAILLE_REV = {v: k for k, v in BRAILLE.items()}

DISCLAIMER = (
    "encode/* chỉ đổi dạng biểu diễn (Morse/Braille/Base64/Hex) — không phải mật mã bảo mật. "
    "AEAD decrypt chỉ khi có key owned; không phá **** / password dump."
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def explain(kind: str) -> str:
    return {
        "morse": "Morse: tín hiệu chấm/gạch — hỗ trợ giao tiếp, không phải encryption.",
        "braille": "Braille grade 1 Unicode — trợ năng khiếm thị.",
        "base64": "Base64: biểu diễn nhị phân bằng text — ai cũng giải được.",
        "hex": "Hex: biểu diễn byte — không bảo mật.",
        "url": "URL-encoding: biểu diễn form/query — không bảo mật.",
        "aes-gcm": "AES-GCM AEAD: cần key + nonce (+ AAD) để giải mã đúng.",
        "chacha20-poly1305": "ChaCha20-Poly1305 AEAD: cần key + nonce (+ AAD).",
        "mask": "**** / partial mask là redaction — không giải mã được.",
        "missing": "Trường trống — không có ciphertext để giải.",
    }.get(kind, DISCLAIMER)


# ----- representation decode (MaMoCrypto.encode parity) -----


def from_base64(b64: str) -> dict:
    raw = re.sub(r"\s+", "", b64 or "")
    try:
        data = base64.b64decode(raw, validate=False)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        return {
            "ok": True,
            "kind": "base64",
            "plain_text": text,
            "plain_hex": data.hex(),
            "explain": explain("base64"),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "kind": "base64", "error": str(e), "explain": explain("base64")}


def from_hex(h: str) -> dict:
    raw = re.sub(r"[\s:]", "", h or "")
    if raw.lower().startswith("0x"):
        raw = raw[2:]
    try:
        data = binascii.unhexlify(raw)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        return {
            "ok": True,
            "kind": "hex",
            "plain_text": text,
            "plain_hex": data.hex(),
            "explain": explain("hex"),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "kind": "hex", "error": str(e), "explain": explain("hex")}


def from_morse(code: str) -> dict:
    words = []
    for word in re.split(r"\s*/\s*", (code or "").strip()):
        letters = [MORSE_REV.get(t, "�") for t in word.strip().split() if t]
        words.append("".join(letters))
    plain = " ".join(words)
    return {"ok": True, "kind": "morse", "plain_text": plain, "explain": explain("morse")}


def from_braille(code: str) -> dict:
    plain = "".join(BRAILLE_REV.get(ch, " " if ch == "⠀" else ch) for ch in (code or ""))
    return {"ok": True, "kind": "braille", "plain_text": plain.strip(), "explain": explain("braille")}


def from_url(text: str) -> dict:
    try:
        plain = urllib.parse.unquote_plus(text or "")
        return {"ok": True, "kind": "url", "plain_text": plain, "explain": explain("url")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "kind": "url", "error": str(e), "explain": explain("url")}


def is_pii_mask(text: str) -> bool:
    """Redaction mask (**** / +84…**** / VĐ***** / H** N***) — not ciphertext."""
    t = (text or "").strip()
    if "*" not in t:
        return False
    # digits / x with optional phone prefix +84 / 84 / 0 and separators
    if re.fullmatch(r"[+]?(?:84)?[\d\s\-.*xX]+", t) and re.search(r"\d", t):
        return True
    if re.fullmatch(r"[\d*xX]+", t):
        return True
    # tracking-style partial masks (ASCII letters + digits + *)
    if re.fullmatch(r"[A-Za-z0-9*]+", t) and re.search(r"[A-Za-z0-9]", t):
        return True
    # Vietnamese letter prefixes e.g. VĐ*******
    if re.fullmatch(r"[^\W\d_]*[\d*]+", t, flags=re.UNICODE) and "*" in t:
        return True
    # Name / address redaction: H** N*** · 61**** Xã********* · P***ngEm
    if re.search(r"\*{2,}", t) and re.search(r"[0-9A-Za-zÀ-ỹ]", t, flags=re.UNICODE):
        # avoid treating Morse-like or pure symbols
        if re.search(r"[A-Za-zÀ-ỹ0-9]", t, flags=re.UNICODE):
            return True
    return False


def detect_and_decode(text: str) -> dict:
    t = (text or "").strip()
    if not t:
        return {"ok": False, "kind": "missing", "explain": explain("missing")}
    if is_pii_mask(t):
        return {
            "ok": False,
            "kind": "mask",
            "input": t,
            "explain": explain("mask"),
            "assist": "Không giải được mask — cần bản AEAD nội bộ hoặc refetch API không PII-mask",
        }
    # braille
    if any(ch in BRAILLE_REV for ch in t):
        return {**from_braille(t), "detected": "braille"}
    # morse
    if re.fullmatch(r"[.\-\s/]+", t) and ("." in t or "-" in t):
        return {**from_morse(t), "detected": "morse"}
    # hex
    hx = re.sub(r"[\s:]", "", t)
    if re.fullmatch(r"(?i)(0x)?[0-9a-f]{8,}", hx) and len(hx.replace("0x", "").replace("0X", "")) % 2 == 0:
        r = from_hex(t)
        if r.get("ok"):
            return {**r, "detected": "hex"}
    # base64
    b64cand = re.sub(r"\s+", "", t)
    if re.fullmatch(r"[A-Za-z0-9+/]+=*", b64cand) and len(b64cand) >= 8:
        r = from_base64(t)
        if r.get("ok") and (r.get("plain_text") or r.get("plain_hex")):
            return {**r, "detected": "base64"}
    # url — require %xx (or form +) but not phone-like +84…
    if "%" in t or ("+" in t and not re.match(r"^\+\d", t)):
        r = from_url(t)
        if r.get("ok") and r.get("plain_text") != t:
            return {**r, "detected": "url"}
    return {
        "ok": False,
        "kind": "unknown",
        "input_preview": t[:80],
        "explain": DISCLAIMER,
        "assist": "Không nhận dạng encoding — nếu là AEAD ciphertext cần key+nonce",
    }


# ----- AEAD decrypt assist (owned key only) -----

SECRETS = ROOT / "secrets"
UPLOADS = Path("/home/ubuntu/.cursor/projects/workspace/uploads")
FRIDA_AAD_DEFAULT = "mapper-icon-aes-v1"
KEY_ENV_NAMES = (
    "MAPPER_ICON_AES_KEY_B64",
    "ICON_AES_KEY_B64",
    "AES_GCM_KEY_B64",
    "FRIDA_A11Y_AES_KEY_B64",
)


def load_env_secrets() -> dict[str, str]:
    import os

    env = dict(os.environ)
    for path in (SECRETS / "telegram.env", SECRETS / "backend_pipes.env", SECRETS / "mapper_icon_aes.env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def resolve_aes_key_b64(explicit: str | None = None, key_file: str | None = None) -> dict[str, Any]:
    """Tìm key owned: CLI → file → env/secrets (không brute-force)."""
    if explicit and explicit.strip():
        return {"ok": True, "key_b64": explicit.strip(), "source": "cli"}
    if key_file:
        p = Path(key_file)
        if p.is_file():
            raw = p.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0].strip()
            if raw:
                return {"ok": True, "key_b64": raw, "source": str(p)}
    env = load_env_secrets()
    for name in KEY_ENV_NAMES:
        v = (env.get(name) or "").strip()
        if v:
            return {"ok": True, "key_b64": v, "source": f"env:{name}"}
    key_path = SECRETS / "mapper_icon_aes.key"
    if key_path.is_file():
        raw = key_path.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0].strip()
        if raw:
            return {"ok": True, "key_b64": raw, "source": str(key_path)}
    return {
        "ok": False,
        "error": "Thiếu key AES owned",
        "need": [
            f"Đặt một trong: {', '.join(KEY_ENV_NAMES)} trong secrets/backend_pipes.env",
            "hoặc secrets/mapper_icon_aes.key (1 dòng key_b64)",
            "hoặc CLI --key-b64 / --key-file",
        ],
    }


def decrypt_aes_gcm(
    key_b64: str,
    nonce_b64: str,
    ciphertext_b64: str,
    aad: str = "",
) -> dict:
    try:
        key = base64.b64decode(key_b64)
        nonce = base64.b64decode(nonce_b64)
        ct = base64.b64decode(ciphertext_b64)
        if len(key) not in (16, 24, 32):
            return {
                "ok": False,
                "kind": "aes-gcm",
                "error": f"key length {len(key)} — cần 16/24/32 bytes",
                "explain": explain("aes-gcm"),
            }
        pt = AESGCM(key).decrypt(nonce, ct, aad.encode() if aad else None)
        return {
            "ok": True,
            "kind": "aes-gcm",
            "plain_text": pt.decode("utf-8", errors="replace"),
            "plain_bytes": len(pt),
            "explain": explain("aes-gcm"),
            "library": "pyca/cryptography AESGCM",
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "kind": "aes-gcm", "error": str(e), "explain": explain("aes-gcm")}


def _sha256_16(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()[:16]


def find_frida_aes_bundles() -> list[Path]:
    """Tìm bundle frida-a11y-offline-aes trong uploads + reports."""
    cands: list[Path] = []
    for root in (UPLOADS, REPORTS, ROOT / "quarantine" / "telegram"):
        if not root.is_dir():
            continue
        for p in root.glob("**/frida-a11y-offline-aes*.json"):
            cands.append(p)
        for p in root.glob("**/*offline-aes*.json"):
            if p not in cands:
                cands.append(p)
    cands.sort(key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True)
    return cands


def decrypt_frida_a11y_bundle(
    path: Path | str,
    *,
    key_b64: str | None = None,
    key_file: str | None = None,
) -> dict[str, Any]:
    """Giải bundle Frida a11y offline AES (mapper-icon-aes-v1).

    Bundle schema:
      call / encoding / aes{nonce_b64,ciphertext_b64,aad,plaintext_sha256_16} / meta
    Inner: masked mapper envelope — AES không unmask PII Pancake.
    """
    p = Path(path)
    report: dict[str, Any] = {
        "ok": False,
        "module": "frida_a11y_aes_decrypt",
        "checked_at": utc_now(),
        "path": str(p),
        "policy": {"owned_key_only": True, "no_bruteforce": True, "no_unmask_pii": True},
    }
    if not p.is_file():
        report["error"] = f"Không thấy file: {p}"
        report["verdict"] = "❌ Thiếu bundle Frida AES"
        return report

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        report["error"] = f"JSON lỗi: {e}"
        report["verdict"] = "❌ Bundle không phải JSON hợp lệ"
        return report

    aes = data.get("aes") if isinstance(data.get("aes"), dict) else {}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    call = data.get("call") if isinstance(data.get("call"), dict) else {}
    encoding = data.get("encoding") if isinstance(data.get("encoding"), dict) else {}
    report["call"] = call
    report["encoding"] = encoding
    report["meta"] = meta
    report["aes_meta"] = {
        "alg": aes.get("alg") or aes.get("encoding"),
        "v": aes.get("v"),
        "aad": aes.get("aad") or FRIDA_AAD_DEFAULT,
        "nonce_b64": aes.get("nonce_b64"),
        "ciphertext_len": len(aes.get("ciphertext_b64") or ""),
        "plaintext_sha256_16": aes.get("plaintext_sha256_16"),
        "encrypted_at": aes.get("encrypted_at"),
        "has_key_in_bundle": bool(aes.get("key_b64") or data.get("key_b64")),
    }

    key_info = resolve_aes_key_b64(
        explicit=key_b64 or aes.get("key_b64") or data.get("key_b64"),
        key_file=key_file,
    )
    report["key"] = {"ok": key_info.get("ok"), "source": key_info.get("source"), "need": key_info.get("need")}
    if not key_info.get("ok"):
        report["error"] = key_info.get("error")
        report["need"] = key_info.get("need")
        report["verdict"] = (
            f"❌ Có ciphertext AES ({report['aes_meta']['ciphertext_len']} chars) "
            f"shop={meta.get('path')} — thiếu key owned để giải"
        )
        return report

    aad = str(aes.get("aad") or FRIDA_AAD_DEFAULT)
    dec = decrypt_aes_gcm(
        key_info["key_b64"],
        str(aes.get("nonce_b64") or ""),
        str(aes.get("ciphertext_b64") or ""),
        aad,
    )
    report["decrypt"] = {k: v for k, v in dec.items() if k != "plain_text"}
    if not dec.get("ok"):
        report["error"] = dec.get("error")
        report["verdict"] = f"❌ AES-GCM decrypt thất bại: {dec.get('error')}"
        return report

    plain = dec.get("plain_text") or ""
    plain_bytes = plain.encode("utf-8")
    expect = str(aes.get("plaintext_sha256_16") or "")
    got = _sha256_16(plain_bytes)
    report["integrity"] = {
        "plaintext_sha256_16_expected": expect or None,
        "plaintext_sha256_16_got": got,
        "match": (not expect) or expect == got,
    }

    inner: Any = None
    try:
        inner = json.loads(plain)
    except json.JSONDecodeError:
        inner = None

    report["ok"] = True
    report["plain"] = {
        "bytes": len(plain_bytes),
        "is_json": inner is not None,
        "preview": plain[:400],
        # Không dump full PII vào report mặc định — ghi file riêng khi ok
    }
    report["inner_note"] = encoding.get("note") or (
        "Inner mask envelope — AES unwrap ≠ unmask Pancake PII"
    )

    out_plain = REPORTS / "frida_a11y_aes_plaintext.json"
    out_txt = REPORTS / "frida_a11y_aes_plaintext.txt"
    REPORTS.mkdir(parents=True, exist_ok=True)
    if inner is not None:
        out_plain.write_text(json.dumps(inner, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["plain"]["json_keys"] = list(inner.keys())[:30] if isinstance(inner, dict) else None
        if isinstance(inner, dict):
            report["plain"]["orders_n"] = len(inner.get("orders") or inner.get("data") or [])
    else:
        out_plain.write_text(plain, encoding="utf-8")
    out_txt.write_text(plain[:20000], encoding="utf-8")
    report["outputs"] = {"json": str(out_plain), "txt": str(out_txt)}

    shop = meta.get("path") or "?"
    report["verdict"] = (
        f"✅ Đã giải AES-GCM · {shop} · plain={len(plain_bytes)}B · "
        f"sha16={'OK' if report['integrity']['match'] else 'MISMATCH'} · "
        f"key_src={key_info.get('source')}"
    )
    return report


def assist_frida_aes_latest(*, key_b64: str | None = None, key_file: str | None = None) -> dict[str, Any]:
    bundles = find_frida_aes_bundles()
    if not bundles:
        return {
            "ok": False,
            "checked_at": utc_now(),
            "verdict": "❌ Không tìm thấy frida-a11y-offline-aes*.json trong uploads/reports",
            "bundles": [],
        }
    result = decrypt_frida_a11y_bundle(bundles[0], key_b64=key_b64, key_file=key_file)
    result["bundles_found"] = [str(b) for b in bundles[:8]]
    return result


def decrypt_chacha(
    key_b64: str,
    nonce_b64: str,
    ciphertext_b64: str,
    aad: str = "",
) -> dict:
    try:
        key = base64.b64decode(key_b64)
        nonce = base64.b64decode(nonce_b64)
        ct = base64.b64decode(ciphertext_b64)
        pt = ChaCha20Poly1305(key).decrypt(nonce, ct, aad.encode() if aad else None)
        return {
            "ok": True,
            "kind": "chacha20-poly1305",
            "plain_text": pt.decode("utf-8", errors="replace"),
            "explain": explain("chacha20-poly1305"),
            "library": "pyca/cryptography ChaCha20Poly1305",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "kind": "chacha20-poly1305",
            "error": str(e),
            "explain": explain("chacha20-poly1305"),
        }

def demo_roundtrip_assist(sample: str = "0979263463") -> dict:
    """Tạo ciphertext demo rồi giải bằng module — chứng minh đường AEAD."""
    key = AESGCM.generate_key(bit_length=256)
    nonce = secrets.token_bytes(12)
    aad = b"oms:customer_phone"
    ct = AESGCM(key).encrypt(nonce, sample.encode(), aad)
    bundle = {
        "key_b64": base64.b64encode(key).decode(),
        "nonce_b64": base64.b64encode(nonce).decode(),
        "ciphertext_b64": base64.b64encode(ct).decode(),
        "aad": aad.decode(),
    }
    # Do not return raw key in report — decrypt immediately and drop key from public report
    dec = decrypt_aes_gcm(bundle["key_b64"], bundle["nonce_b64"], bundle["ciphertext_b64"], bundle["aad"])
    return {
        "demo": True,
        "plaintext_kind": "synthetic_phone",
        "ciphertext_b64": bundle["ciphertext_b64"],
        "nonce_b64": bundle["nonce_b64"],
        "aad": bundle["aad"],
        "decrypt_result": {k: v for k, v in dec.items() if k != "plain_text"}
        | {"plain_text": dec.get("plain_text"), "roundtrip_ok": dec.get("plain_text") == sample},
        "note": "Key ephemeral — không lưu; minh hoạ module hỗ trợ giải mã AEAD",
    }


def load_asunmee_config() -> dict[str, Any]:
    if ASUNMEE_CONFIG.is_file():
        return json.loads(ASUNMEE_CONFIG.read_text(encoding="utf-8"))
    return {
        "shop": {"id": 714934229, "name": "ASUNMEE"},
        "pii_mask": {"fields": [], "detail_unmasks": False},
        "decode_assist": {},
    }


def _walk_string_fields(obj: Any, path: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            out.extend(_walk_string_fields(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:8]):
            out.extend(_walk_string_fields(v, f"{path}[{i}]"))
    elif isinstance(obj, str) and obj.strip():
        out.append((path, obj))
    return out


def _normalize_mask_path(path: str) -> str:
    """Collapse indexes so field map matches config paths."""
    return re.sub(r"\[\d+\]", "[]", path or "")


def assist_asunmee_structure(*, live: bool = False, sample_limit: int = 12) -> dict[str, Any]:
    """Phân tích cấu trúc shop ASUNMEE + hỗ trợ người dùng giải mã che (mask).

    Mask = redaction Pancake qua api_key — không phải ciphertext.
    """
    cfg = load_asunmee_config()
    shop = cfg.get("shop") or {}
    shop_id = shop.get("id") or 714934229
    mask_cfg = cfg.get("pii_mask") or {}
    configured_fields = set(mask_cfg.get("fields") or [])

    report: dict[str, Any] = {
        "ok": True,
        "module": "asunmee_structure_decode_assist",
        "checked_at": utc_now(),
        "shop": {
            "id": shop_id,
            "name": shop.get("name") or "ASUNMEE",
            "warehouse_alias": shop.get("warehouse_alias") or "ASUMEE",
            "auth": shop.get("auth"),
            "config_path": str(ASUNMEE_CONFIG.relative_to(ROOT)) if ASUNMEE_CONFIG.is_file() else None,
        },
        "policy": {
            "mask_not_decryptable": True,
            "detail_unmasks": bool(mask_cfg.get("detail_unmasks")),
            "customers_unmasks": bool(mask_cfg.get("customers_unmasks")),
            "owned_key_only": True,
            "no_bruteforce": True,
        },
        "endpoints": cfg.get("endpoints"),
        "warehouses": cfg.get("warehouses"),
        "clear_fields_useful": cfg.get("clear_fields_useful"),
        "user_flow": cfg.get("user_flow"),
        "field_map": [],
        "samples": [],
        "by_kind": {"mask": 0, "clear": 0, "encoded": 0, "missing": 0, "unknown": 0},
        "live": None,
    }

    orders: list[dict] = []
    source = "none"

    if live:
        live_info = _probe_asunmee_live(shop_id, sample_limit=sample_limit)
        report["live"] = {k: v for k, v in live_info.items() if k != "orders"}
        orders = live_info.get("orders") or []
        source = "live_api"
        if live_info.get("structure_raw_updated"):
            report["structure_raw"] = str(ASUNMEE_STRUCTURE_RAW)

    if not orders:
        for cand in (
            REPORTS / "asunmee_orders_last3d.json",
            REPORTS / "asunmee_orders_normalized.json",
            REPORTS / "scan_buucuc_orders.json",
        ):
            if not cand.is_file():
                continue
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            rows = data if isinstance(data, list) else data.get("orders") or data.get("data") or []
            if not isinstance(rows, list):
                continue
            # Prefer ASUNMEE shop_id when scan is multi-shop
            filtered = [
                r
                for r in rows
                if isinstance(r, dict)
                and str(r.get("shop_id") or r.get("shop") or "") in {"", str(shop_id), "714934229"}
            ]
            if not filtered and rows and isinstance(rows[0], dict):
                # asunmee-specific files
                if "asunmee" in cand.name:
                    filtered = [r for r in rows if isinstance(r, dict)]
            if filtered:
                orders = filtered[: max(sample_limit, 20)]
                source = cand.name
                break

    if ASUNMEE_STRUCTURE_RAW.is_file() and not report.get("live"):
        try:
            raw = json.loads(ASUNMEE_STRUCTURE_RAW.read_text(encoding="utf-8"))
            report["structure_raw_summary"] = {
                "list_mask_fields": list((raw.get("list_mask") or {}).keys()),
                "detail_mask_fields": list((raw.get("detail_mask") or {}).keys()),
                "list_phone": raw.get("list_phone"),
                "detail_phone": raw.get("detail_phone"),
                "endpoints_ok": [e.get("path") for e in (raw.get("endpoints") or []) if e.get("ok")],
            }
        except Exception:  # noqa: BLE001
            pass

    seen_paths: set[str] = set()
    field_stats: dict[str, dict[str, Any]] = {}

    for order in orders[:sample_limit]:
        if not isinstance(order, dict):
            continue
        oid = order.get("id") or order.get("order_id") or order.get("system_id")
        sample_masks: list[dict] = []
        for path, value in _walk_string_fields(order):
            npath = _normalize_mask_path(path)
            leaf = npath.split(".")[-1].replace("[]", "")
            pii_leaf = leaf in {
                "phone",
                "phone_number",
                "phone_numbers",
                "bill_phone_number",
                "bill_full_name",
                "full_name",
                "name",
                "address",
                "full_address",
                "marketplace_address",
                "email",
                "bill_email",
            }
            # Chỉ classify PII / mask / candidate encode — tránh đếm tracking_link URL là "encoded"
            if not (pii_leaf or "*" in value or npath in configured_fields):
                report["by_kind"]["clear"] = report["by_kind"].get("clear", 0) + 1
                continue
            # Plain VN phone / clear name without * → CLEAR (không coi hex digits là encode)
            digits = re.sub(r"\D", "", value)
            if "*" not in value and (
                re.fullmatch(r"\+?84\d{8,11}", digits)
                or re.fullmatch(r"0\d{9,10}", digits)
                or (pii_leaf and leaf in {"name", "full_name", "address", "full_address"} and len(value) < 120)
            ):
                report["by_kind"]["clear"] = report["by_kind"].get("clear", 0) + 1
                continue
            assist = detect_and_decode(value)
            kind = assist.get("kind") or assist.get("detected") or "unknown"
            if assist.get("ok") and kind in {"base64", "hex", "url", "morse", "braille", "aes-gcm"}:
                bucket = "encoded"
            elif kind == "mask" or ("*" in value and pii_leaf):
                bucket = "mask"
                if kind != "mask":
                    assist = {
                        "ok": False,
                        "kind": "mask",
                        "input": value[:80],
                        "explain": explain("mask"),
                        "assist": (cfg.get("decode_assist") or {}).get("mask", {}).get("assist")
                        or explain("mask"),
                    }
                    kind = "mask"
            elif kind == "missing":
                bucket = "missing"
            elif kind == "unknown" and "*" not in value:
                bucket = "clear"
                kind = "clear"
            else:
                bucket = "unknown"
            report["by_kind"][bucket] = report["by_kind"].get(bucket, 0) + 1

            if npath not in field_stats:
                field_stats[npath] = {
                    "path": npath,
                    "configured_mask_field": npath in configured_fields
                    or any(
                        npath == f or npath.endswith(f) or f.endswith(npath)
                        for f in configured_fields
                    ),
                    "kind": kind if bucket == "mask" else bucket,
                    "count": 0,
                    "sample": value[:80],
                    "assist": assist.get("assist") or assist.get("explain"),
                }
            field_stats[npath]["count"] += 1
            if bucket == "mask" and len(sample_masks) < 6:
                sample_masks.append(
                    {
                        "path": path,
                        "value": value[:80],
                        "assist": assist,
                    }
                )
            seen_paths.add(npath)

        report["samples"].append(
            {
                "order_id": oid,
                "status": order.get("status_name") or order.get("status"),
                "warehouse": (order.get("warehouse_info") or {}).get("name")
                if isinstance(order.get("warehouse_info"), dict)
                else order.get("warehouse_id"),
                "masks": sample_masks,
                "clear_preview": {
                    k: order.get(k)
                    for k in ("total_price", "shipping_fee", "tracking_link", "note", "status_name")
                    if order.get(k) not in (None, "")
                },
            }
        )

    # Ensure configured mask fields appear even if sample missed them
    for f in configured_fields:
        if f not in field_stats:
            field_stats[f] = {
                "path": f,
                "configured_mask_field": True,
                "kind": "mask",
                "count": 0,
                "sample": None,
                "assist": (cfg.get("decode_assist") or {}).get("mask", {}).get("assist")
                or explain("mask"),
            }

    report["field_map"] = sorted(
        field_stats.values(),
        key=lambda x: (0 if x.get("kind") == "mask" else 1, -(x.get("count") or 0), x.get("path") or ""),
    )
    report["orders_source"] = source
    report["orders_sampled"] = len(report["samples"])

    da = cfg.get("decode_assist") or {}
    report["decode_guide"] = {
        "mask": da.get("mask"),
        "frida_aes": da.get("frida_aes"),
        "aead_at_rest": da.get("aead_at_rest"),
        "cli": da.get("cli") or "python3 scripts/crypto_decode_assist.py --asunmee",
    }
    mask_n = int(report["by_kind"].get("mask") or 0)
    enc_n = int(report["by_kind"].get("encoded") or 0)
    report["verdict"] = (
        f"ASUNMEE shop={shop_id}: sampled={report['orders_sampled']} source={source}. "
        f"PII fields MASK={mask_n} (không giải ****), encoded={enc_n}. "
        f"Detail/customers api_key không unmask. "
        f"Hỗ trợ: phân loại mask + AEAD/Frida khi có key owned."
    )
    report["next_actions"] = [
        "SĐT/tên **** trên ASUNMEE: không dùng fromBase64 — đây là redaction",
        "Unmask hợp lệ: export CS owned / JWT full-PII owned (nếu có) / AEAD nội bộ có key",
        "Frida AES: MAPPER_ICON_AES_KEY_B64 + crypto_decode_assist.py --frida-aes FILE",
        "Vận hành: dùng clear_fields (warehouse, status, total_price, tracking_link) trong pipe kho/BC",
        f"Cấu hình: {ASUNMEE_CONFIG}",
    ]
    return report


def _probe_asunmee_live(shop_id: int | str, *, sample_limit: int = 8) -> dict[str, Any]:
    """Live probe owned api_key — cập nhật structure raw nhẹ."""
    import urllib.error
    import urllib.request

    env = load_env_secrets()
    key = (env.get("PANCAKE_POS_API_KEY") or env.get("PANCAKE_API_KEY") or "").strip()
    out: dict[str, Any] = {
        "ok": False,
        "auth": "api_key" if key else None,
        "orders": [],
        "endpoints": [],
        "structure_raw_updated": False,
    }
    if not key:
        out["error"] = "Thiếu PANCAKE_POS_API_KEY / PANCAKE_API_KEY owned"
        return out

    base = f"https://pos.pages.fm/api/v1/shops/{shop_id}"

    def get(path: str) -> tuple[int, Any]:
        url = f"{base}{path}"
        sep = "&" if "?" in url else "?"
        full = f"{url}{sep}api_key={key}"
        try:
            with urllib.request.urlopen(full, timeout=45) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
            except Exception:  # noqa: BLE001
                body = {"error": str(e)}
            return e.code, body
        except Exception as e:  # noqa: BLE001
            return 0, {"error": str(e)}

    for path in ("", "/orders?page_number=1&page_size=5", "/customers?page=1&page_size=2", "/warehouses"):
        code, body = get(path if path else "")
        out["endpoints"].append(
            {
                "path": f"/shops/{shop_id}{path.split('?')[0]}",
                "http": code,
                "ok": 200 <= int(code) < 300,
                "keys": list(body.keys())[:12] if isinstance(body, dict) else [],
            }
        )

    code, body = get(f"/orders?page_number=1&page_size={max(5, sample_limit)}")
    orders = body.get("data") if isinstance(body, dict) else None
    if not isinstance(orders, list):
        out["error"] = f"orders http={code}"
        return out

    # Enrich first few with detail (confirm no unmask)
    enriched: list[dict] = []
    detail_same_mask = True
    for o in orders[:sample_limit]:
        if not isinstance(o, dict) or not o.get("id"):
            continue
        dcode, dbody = get(f"/orders/{o['id']}")
        detail = dbody.get("data") if isinstance(dbody, dict) else None
        if isinstance(detail, dict) and detail.get("id"):
            list_phone = o.get("bill_phone_number")
            detail_phone = detail.get("bill_phone_number")
            if list_phone and detail_phone and "*" not in str(detail_phone) and "*" in str(list_phone):
                detail_same_mask = False
            enriched.append(detail)
        else:
            enriched.append(o)

    out["ok"] = True
    out["orders"] = enriched
    out["total_entries"] = body.get("total_entries") if isinstance(body, dict) else None
    out["detail_unmasks"] = not detail_same_mask
    out["user"] = env.get("PANCAKE_API_KEY_USER") or env.get("PANCAKE_USER") or "ASUNMEE"

    # Refresh compact structure raw mask counts from samples
    list_mask: dict[str, int] = {}
    for o in enriched:
        for path, value in _walk_string_fields(o):
            if is_pii_mask(value):
                npath = _normalize_mask_path(path)
                list_mask[npath] = list_mask.get(npath, 0) + 1
    try:
        REPORTS.mkdir(parents=True, exist_ok=True)
        payload = {
            "shop": {"id": int(shop_id) if str(shop_id).isdigit() else shop_id, "name": "ASUNMEE"},
            "checked_at": utc_now(),
            "list_mask": list_mask,
            "detail_mask": list_mask,
            "detail_unmasks": out["detail_unmasks"],
            "endpoints": out["endpoints"],
            "total_entries": out.get("total_entries"),
            "source": "crypto_decode_assist --asunmee --live",
        }
        ASUNMEE_STRUCTURE_RAW.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        out["structure_raw_updated"] = True
    except Exception as e:  # noqa: BLE001
        out["structure_raw_error"] = str(e)
    return out


def assist_order_phones(limit: int = 40) -> dict:
    if not ORDERS_CSV.is_file():
        return {"rows": 0, "samples": []}
    with ORDERS_CSV.open(newline="", encoding="utf-8", errors="replace") as fh:
        rows = list(csv.DictReader(fh))
    by_class: dict[str, int] = {"OK": 0, "MISSING": 0, "MASKED": 0, "INVALID": 0, "ENCODED_CANDIDATE": 0}
    assists = []
    for r in rows:
        ph = (r.get("customer_phone") or "").strip()
        if not ph:
            by_class["MISSING"] += 1
            continue
        if "*" in ph:
            by_class["MASKED"] += 1
            if len(assists) < limit and by_class["MASKED"] <= limit:
                assists.append(
                    {
                        "source": r.get("source"),
                        "order_key": r.get("order_key"),
                        "input": ph,
                        "assist": detect_and_decode(ph),
                    }
                )
            continue
        digits = re.sub(r"\D", "", ph)
        if len(digits) >= 9 and ph == digits or re.fullmatch(r"0?\d{9,11}", ph):
            by_class["OK"] += 1
            continue
        # maybe encoded
        hit = detect_and_decode(ph)
        if hit.get("ok") and hit.get("kind") in {"base64", "hex", "url", "morse", "braille"}:
            by_class["ENCODED_CANDIDATE"] += 1
            assists.append(
                {
                    "source": r.get("source"),
                    "order_key": r.get("order_key"),
                    "input": ph[:80],
                    "assist": hit,
                }
            )
        else:
            by_class["INVALID"] += 1
    return {
        "file": ORDERS_CSV.name,
        "rows": len(rows),
        "by_class": by_class,
        "assist_samples": assists[:limit],
        "module": "MaMoCrypto.encode parity + cryptography AEAD",
    }


def build_report(inputs: list[str] | None = None) -> dict:
    inputs = inputs or [
        "MDk3OTI2MzQ2Mw==",  # base64 of demo phone
        "09******63",
        "",
        ".... . .-.. .-.. ---",  # HELLO morse
        "⠓⠑⠇⠇⠕",  # hello braille
    ]
    decoded = []
    for item in inputs:
        decoded.append({"input": item, "result": detect_and_decode(item)})

    aead_demo = demo_roundtrip_assist()
    orders = assist_order_phones()
    frida = assist_frida_aes_latest()

    try:
        from realtime_icon_feedback_mapper import chant, feedback_line

        icons = ["text", "lock", "key", "hash", "monitor"]
        fb = feedback_line(icons, "module hỗ trợ giải mã encode/* + AEAD + Frida AES")
    except Exception:  # noqa: BLE001
        icons, fb = ["text", "lock", "key"], "Mapper gọi: text → lock → key"

    report = {
        "ok": True,
        "query": "Sử dụng module hỗ trợ giải mã",
        "checked_at": utc_now(),
        "disclaimer": DISCLAIMER,
        "modules": [
            {"id": "MaMoCrypto.encode", "file": "js/crypto/encode.js", "role": "fromBase64/fromMorse/fromBraille"},
            {"id": "crypto_decode_assist", "file": "scripts/crypto_decode_assist.py", "role": "Python parity + AEAD decrypt"},
            {"id": "frida_a11y_aes", "role": "AES-GCM decrypt bundle mapper-icon-aes-v1 (owned key)"},
            {"id": "pyca/cryptography", "role": "AESGCM / ChaCha20Poly1305.decrypt"},
        ],
        "batch_decode": decoded,
        "aead_demo": aead_demo,
        "frida_a11y_aes": {
            "ok": frida.get("ok"),
            "verdict": frida.get("verdict"),
            "path": frida.get("path"),
            "meta": frida.get("meta"),
            "key": frida.get("key"),
            "need": frida.get("need"),
            "integrity": frida.get("integrity"),
            "outputs": frida.get("outputs"),
            "bundles_found": frida.get("bundles_found"),
        },
        "orders_phone_assist": orders,
        "asunmee": None,
        "icon_feedback": fb,
        "icon_chant": " → ".join(icons) if isinstance(icons, list) else str(icons),
        "safety": {
            "no_password_cracking": True,
            "no_dump_login": True,
            "aead_requires_owned_key": True,
            "mask_not_decryptable": True,
            "no_bruteforce": True,
        },
        "next_actions": [
            "Frida AES: điền MAPPER_ICON_AES_KEY_B64 vào secrets rồi: "
            "python3 scripts/crypto_decode_assist.py --frida-aes FILE",
            "SĐT **** → refetch API / bản AEAD nội bộ có key — không dùng fromBase64",
            "ASUNMEE mask: python3 scripts/crypto_decode_assist.py --asunmee [--live]",
            "PII at rest: lưu AES-GCM; giải bằng crypto_decode_assist --aes-gcm khi CS cần",
            "UI: MaMoCrypto.encode.fromBase64 / fromMorse / fromBraille",
        ],
    }
    asu = assist_asunmee_structure(live=False, sample_limit=8)
    report["asunmee"] = {
        "verdict": asu.get("verdict"),
        "shop": asu.get("shop"),
        "by_kind": asu.get("by_kind"),
        "orders_source": asu.get("orders_source"),
        "orders_sampled": asu.get("orders_sampled"),
        "policy": asu.get("policy"),
        "decode_guide": asu.get("decode_guide"),
        "next_actions": asu.get("next_actions"),
    }
    report["modules"].append(
        {
            "id": "asunmee_shop_decode",
            "file": "config/asunmee_shop_decode.json",
            "role": "Cấu trúc shop ASUNMEE + map PII mask / hướng dẫn giải mã che",
        }
    )
    report["verdict"] = (
        f"Module giải mã: encode/* + AEAD demo roundtrip="
        f"{aead_demo['decrypt_result'].get('roundtrip_ok')}. "
        f"Frida AES: {frida.get('verdict')}. "
        f"Đang giao: {orders.get('by_class')} — MASKED không giải bằng decode. "
        f"ASUNMEE: {asu.get('verdict')}. {fb}"
    )
    return report


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔓 MODULE HỖ TRỢ GIẢI MÃ")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    L(f"⚠ {report['disclaimer']}")
    L(f"✨ {report.get('icon_feedback')}")
    L("")
    L("=== Modules ===")
    for m in report["modules"]:
        L(f"· {m.get('id')}: {m.get('role')} ({m.get('file') or ''})")
    L("")
    L("=== Batch decode ===")
    for item in report["batch_decode"]:
        r = item["result"]
        L(f"· input={item['input']!r}")
        L(
            f"  → ok={r.get('ok')} kind={r.get('kind') or r.get('detected')} "
            f"plain={r.get('plain_text')!r} assist={r.get('assist') or r.get('explain', '')[:80]}"
        )
    L("")
    L("=== AEAD demo decrypt ===")
    d = report["aead_demo"]["decrypt_result"]
    L(f"· kind={d.get('kind')} roundtrip={d.get('roundtrip_ok')} plain={d.get('plain_text')!r}")
    L(f"· lib={d.get('library')} — {d.get('explain')}")
    L("")
    fr = report.get("frida_a11y_aes") or {}
    L("=== Frida a11y AES (mapper-icon-aes-v1) ===")
    L(f"· {fr.get('verdict')}")
    if fr.get("path"):
        L(f"· bundle: {fr.get('path')}")
    if fr.get("meta"):
        L(f"· meta: {fr.get('meta')}")
    if fr.get("key"):
        L(f"· key: ok={fr['key'].get('ok')} source={fr['key'].get('source')}")
    if fr.get("need"):
        for n in fr["need"]:
            L(f"  need: {n}")
    if fr.get("outputs"):
        L(f"· outputs: {fr.get('outputs')}")
    L("")
    L("=== Đang giao phone assist ===")
    o = report["orders_phone_assist"]
    L(f"· rows={o.get('rows')} by_class={o.get('by_class')}")
    for s in (o.get("assist_samples") or [])[:8]:
        ar = s.get("assist") or {}
        L(
            f"  - {s.get('source')}: {s.get('input')!r} → {ar.get('kind')} "
            f"{ar.get('assist') or ar.get('explain', '')[:70]}"
        )
    asu = report.get("asunmee") or {}
    if asu:
        L("")
        L("=== ASUNMEE structure / giải mã che ===")
        L(f"· {asu.get('verdict')}")
        L(f"· by_kind={asu.get('by_kind')} source={asu.get('orders_source')}")
        guide = asu.get("decode_guide") or {}
        if guide.get("mask"):
            L(f"· mask: {guide['mask']}")
        for a in (asu.get("next_actions") or [])[:4]:
            L(f"  → {a}")
    L("")
    L("Next:")
    for a in report["next_actions"]:
        L(f"· {a}")
    return "\n".join(lines)


def format_asunmee_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🏪 ASUNMEE — CẤU TRÚC & HỖ TRỢ GIẢI MÃ CHE")
    L(f"Lúc: {report.get('checked_at')}")
    L(report.get("verdict") or "")
    L("")
    shop = report.get("shop") or {}
    L(f"Shop: {shop.get('name')} id={shop.get('id')} alias_kho={shop.get('warehouse_alias')}")
    L(f"Config: {shop.get('config_path')}")
    L(f"Policy: {report.get('policy')}")
    L(f"Source: {report.get('orders_source')} sampled={report.get('orders_sampled')}")
    L(f"by_kind: {report.get('by_kind')}")
    if report.get("live"):
        live = report["live"]
        L(
            f"Live: ok={live.get('ok')} total_entries={live.get('total_entries')} "
            f"detail_unmasks={live.get('detail_unmasks')}"
        )
        if live.get("error"):
            L(f"Live error: {live.get('error')}")
    L("")
    L("=== Field map (mask ưu tiên) ===")
    for f in (report.get("field_map") or [])[:30]:
        if f.get("kind") != "mask" and not f.get("configured_mask_field"):
            continue
        L(
            f"· {f.get('path')}: kind={f.get('kind')} count={f.get('count')} "
            f"sample={f.get('sample')!r}"
        )
    L("")
    L("=== Samples ===")
    for s in (report.get("samples") or [])[:5]:
        L(f"· order={s.get('order_id')} status={s.get('status')} wh={s.get('warehouse')}")
        for m in (s.get("masks") or [])[:4]:
            ar = m.get("assist") or {}
            L(f"  - {m.get('path')}={m.get('value')!r} → {ar.get('kind')}")
        if s.get("clear_preview"):
            L(f"  clear={s.get('clear_preview')}")
    L("")
    L("=== Decode guide ===")
    guide = report.get("decode_guide") or {}
    for k, v in guide.items():
        L(f"· {k}: {v}")
    L("")
    L("User flow:")
    for step in report.get("user_flow") or []:
        L(f"· {step}")
    L("")
    L("Next:")
    for a in report.get("next_actions") or []:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = format_text(report)
    paths = {
        "json": REPORTS / "crypto_decode_assist.json",
        "txt": REPORTS / "crypto_decode_assist.txt",
    }
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    return paths


def write_asunmee_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": REPORTS / "asunmee_decode_assist.json",
        "txt": REPORTS / "asunmee_decode_assist.txt",
    }
    paths["json"].write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    paths["txt"].write_text(format_asunmee_text(report) + "\n", encoding="utf-8")
    return paths


def _path_for_assist(assist: dict) -> dict[str, Any]:
    """Gắn kết quả detect_and_decode vào path unmask."""
    kind = assist.get("kind") or assist.get("detected") or "unknown"
    if kind == "mask":
        return {
            "path_id": "PATH-MASK-REDACTION",
            "crypto_unmask": False,
            "action": "fetch_unmasked_from_source_api",
            "howto": [
                "Không dùng fromBase64/fromHex trên ****",
                "ASUNMEE: python3 scripts/crypto_decode_assist.py --asunmee --live",
                "Hoặc lưu PII AEAD nội bộ rồi --aes-gcm KEY NONCE CT",
            ],
        }
    if kind == "missing":
        return {
            "path_id": "PATH-MISSING",
            "crypto_unmask": False,
            "action": "backfill_from_oms",
            "howto": ["Trường trống — không có gì để giải; map phone từ OMS/API"],
        }
    if assist.get("ok") and kind in {"base64", "hex", "url", "morse", "braille"}:
        return {
            "path_id": "PATH-ENCODING",
            "crypto_unmask": False,
            "action": "ma_mo_encode_decode",
            "howto": [
                f"Đã decode {kind} → plain={assist.get('plain_text')!r}",
                "Encoding ≠ unmask PII đã redaction",
            ],
            "plain_text": assist.get("plain_text"),
        }
    if kind in {"aes-gcm", "chacha20-poly1305"}:
        return {
            "path_id": "PATH-AEAD-AT-REST",
            "crypto_unmask": True,
            "action": "decrypt_aead_owned_key",
            "howto": ["Cần KEY_B64 + NONCE_B64 + CT_B64 (+ AAD)"],
        }
    return {
        "path_id": "PATH-UNKNOWN",
        "crypto_unmask": False,
        "action": "classify_then_retry",
        "howto": [
            "Không nhận dạng — nếu ciphertext AEAD: --aes-gcm …",
            "Nếu Frida bundle: --frida-aes FILE",
            "Nếu ****: path MASK (không decrypt)",
        ],
    }


def assist_unmask(
    texts: list[str] | None = None,
    *,
    aes_gcm: tuple[str, str, str] | None = None,
    aad: str = "",
    frida_file: str | None = None,
    key_b64: str | None = None,
    key_file: str | None = None,
    include_asunmee: bool = True,
    include_atlas: bool = True,
) -> dict[str, Any]:
    """Hỗ trợ giải mã unmask — phân loại + path + decrypt owned (không phá ****)."""
    texts = texts or [
        "+84335****64",
        "H** N***",
        "MDk3OTI2MzQ2Mw==",
        "09******63",
        "",
    ]

    items: list[dict] = []
    by_path: dict[str, int] = {}
    for t in texts:
        assist = detect_and_decode(t)
        path = _path_for_assist(assist)
        pid = path["path_id"]
        by_path[pid] = by_path.get(pid, 0) + 1
        items.append(
            {
                "input": t,
                "assist": assist,
                "path": path,
                "unmask_ok": bool(
                    path.get("crypto_unmask") and assist.get("ok")
                )
                or bool(assist.get("ok") and path["path_id"] == "PATH-ENCODING"),
            }
        )

    aead_result = None
    if aes_gcm:
        aead_result = decrypt_aes_gcm(aes_gcm[0], aes_gcm[1], aes_gcm[2], aad)
        aead_result["path_id"] = "PATH-AEAD-AT-REST"
        aead_result["unmask_ok"] = bool(aead_result.get("ok"))

    frida_result = None
    if frida_file:
        frida_result = decrypt_frida_a11y_bundle(
            frida_file, key_b64=key_b64, key_file=key_file
        )
    else:
        # Auto-try latest Frida bundle if key present (không fail nếu thiếu)
        key_info = resolve_aes_key_b64(key_b64, key_file)
        if key_info.get("ok"):
            latest = assist_frida_aes_latest()
            if latest.get("bundles_found"):
                frida_result = {
                    "ok": latest.get("ok"),
                    "verdict": latest.get("verdict"),
                    "path": latest.get("path"),
                    "need": latest.get("need"),
                    "key": latest.get("key"),
                    "note": "Auto probe latest Frida bundle khi có key owned",
                }

    atlas_block = None
    if include_atlas:
        try:
            from unmask_redaction_crypto_mapper import atlas_lookup, load_atlas, mapper_paths

            atlas = load_atlas()
            atlas_block = {
                "lookup": atlas_lookup(atlas, "unmask redaction"),
                "paths": [
                    {
                        "id": p["id"],
                        "crypto_unmask": p["crypto_unmask"],
                        "action": p["mapper_action"],
                        "verdict": p["verdict"],
                    }
                    for p in mapper_paths()
                ],
            }
        except Exception as e:  # noqa: BLE001
            atlas_block = {"error": str(e)}

    asunmee_block = None
    if include_asunmee:
        try:
            asu = assist_asunmee_structure(live=False, sample_limit=3)
            asunmee_block = {
                "verdict": asu.get("verdict"),
                "shop": asu.get("shop"),
                "by_kind": asu.get("by_kind"),
                "policy": asu.get("policy"),
                "path_id": "PATH-MASK-REDACTION",
                "cli": "python3 scripts/crypto_decode_assist.py --asunmee --live",
            }
        except Exception as e:  # noqa: BLE001
            asunmee_block = {"error": str(e)}

    try:
        from realtime_icon_feedback_mapper import feedback_line

        icons = ["lock", "key", "text", "hash", "monitor"]
        fb = feedback_line(icons, "hỗ trợ giải mã unmask — mask≠decrypt · AEAD có key")
    except Exception:  # noqa: BLE001
        icons, fb = ["lock", "key", "text"], "unmask assist: lock → key → text"

    mask_n = by_path.get("PATH-MASK-REDACTION", 0)
    enc_n = by_path.get("PATH-ENCODING", 0)
    aead_ok = bool(aead_result and aead_result.get("ok"))
    frida_ok = bool(frida_result and frida_result.get("ok"))

    report: dict[str, Any] = {
        "ok": True,
        "module": "unmask_decode_assist",
        "query": "Hỗ trợ giải mã unmask",
        "checked_at": utc_now(),
        "disclaimer": DISCLAIMER,
        "policy": {
            "mask_not_decryptable": True,
            "aead_requires_owned_key": True,
            "no_bruteforce": True,
            "no_dump_login": True,
        },
        "items": items,
        "by_path": by_path,
        "aead_decrypt": aead_result,
        "frida_a11y_aes": frida_result,
        "atlas": atlas_block,
        "asunmee": asunmee_block,
        "icon_feedback": fb,
        "icon_chant": " → ".join(icons),
        "verdict": (
            f"Unmask assist: {len(items)} mẫu · MASK={mask_n} ENCODING={enc_n} "
            f"by_path={by_path}. "
            f"AEAD decrypt={'OK' if aead_ok else '—'} · Frida={'OK' if frida_ok else '—'}. "
            f"**** không giải bằng crypto — dùng fetch_unmasked hoặc AEAD owned. {fb}"
        ),
        "next_actions": [
            "MASK **** → python3 scripts/crypto_decode_assist.py --asunmee --live",
            "Encoding → đã plain trong items[].path.plain_text / --text …",
            "AEAD → python3 scripts/crypto_decode_assist.py --unmask --aes-gcm KEY NONCE CT",
            "Frida → --unmask --frida-aes FILE (MAPPER_ICON_AES_KEY_B64)",
            "Atlas → python3 scripts/unmask_redaction_crypto_mapper.py",
        ],
        "cli": "python3 scripts/crypto_decode_assist.py --unmask",
    }
    return report


def format_unmask_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔓 HỖ TRỢ GIẢI MÃ UNMASK")
    L(f"Lúc: {report.get('checked_at')}")
    L(report.get("verdict") or "")
    L("")
    L(f"⚠ {report.get('disclaimer')}")
    L(f"✨ {report.get('icon_feedback')}")
    L(f"Policy: {report.get('policy')}")
    L(f"by_path: {report.get('by_path')}")
    L("")
    L("=== Phân loại từng mẫu ===")
    for it in report.get("items") or []:
        ar = it.get("assist") or {}
        path = it.get("path") or {}
        L(f"· input={it.get('input')!r}")
        L(
            f"  kind={ar.get('kind') or ar.get('detected')} path={path.get('path_id')} "
            f"action={path.get('action')} unmask_ok={it.get('unmask_ok')}"
        )
        if ar.get("plain_text") is not None:
            L(f"  plain={ar.get('plain_text')!r}")
        if ar.get("assist"):
            L(f"  assist={ar.get('assist')}")
        for h in (path.get("howto") or [])[:3]:
            L(f"  → {h}")
    aead = report.get("aead_decrypt")
    if aead:
        L("")
        L("=== AEAD decrypt (owned) ===")
        L(
            f"· ok={aead.get('ok')} kind={aead.get('kind')} "
            f"plain={aead.get('plain_text')!r} err={aead.get('error')}"
        )
    fr = report.get("frida_a11y_aes")
    if fr:
        L("")
        L("=== Frida AES ===")
        L(f"· {fr.get('verdict') or fr}")
        if fr.get("need"):
            for n in fr["need"]:
                L(f"  need: {n}")
    asu = report.get("asunmee")
    if asu:
        L("")
        L("=== ASUNMEE ===")
        L(f"· {asu.get('verdict') or asu}")
        L(f"· cli: {asu.get('cli')}")
    atlas = report.get("atlas") or {}
    if atlas.get("paths"):
        L("")
        L("=== Atlas mapper paths ===")
        for p in atlas["paths"]:
            L(
                f"· {p.get('id')}: crypto_unmask={p.get('crypto_unmask')} "
                f"→ {p.get('action')}"
            )
    L("")
    L("Next:")
    for a in report.get("next_actions") or []:
        L(f"· {a}")
    return "\n".join(lines)


def write_unmask_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": REPORTS / "unmask_decode_assist.json",
        "txt": REPORTS / "unmask_decode_assist.txt",
    }
    paths["json"].write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    paths["txt"].write_text(format_unmask_text(report) + "\n", encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Module hỗ trợ giải mã (encode + AEAD + Frida AES)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--text", action="append", help="Chuỗi cần giải (lặp lại được)")
    ap.add_argument("--aes-gcm", nargs=3, metavar=("KEY_B64", "NONCE_B64", "CT_B64"))
    ap.add_argument("--aad", default="")
    ap.add_argument("--frida-aes", metavar="FILE", help="Giải bundle frida-a11y-offline-aes*.json")
    ap.add_argument("--key-b64", default="", help="AES-256 key (base64) owned")
    ap.add_argument("--key-file", default="", help="File chứa key_b64 (1 dòng)")
    ap.add_argument("--asunmee", action="store_true", help="Phân tích cấu trúc ASUNMEE + hỗ trợ giải mã che")
    ap.add_argument("--unmask", action="store_true", help="Hỗ trợ giải mã unmask (phân loại + path + AEAD)")
    ap.add_argument("--live", action="store_true", help="Với --asunmee: probe API bằng api_key owned")
    ap.add_argument("--sample-limit", type=int, default=12, help="Số đơn mẫu ASUNMEE")
    args = ap.parse_args()

    if args.unmask:
        aes = tuple(args.aes_gcm) if args.aes_gcm else None
        res = assist_unmask(
            texts=args.text,
            aes_gcm=aes,  # type: ignore[arg-type]
            aad=args.aad,
            frida_file=args.frida_aes or None,
            key_b64=args.key_b64 or None,
            key_file=args.key_file or None,
        )
        paths = write_unmask_outputs(res)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_unmask_text(res))
            print(f"\nWrote: {paths['json']}")
            print(f"Wrote: {paths['txt']}")
        return 0 if res.get("ok") else 1

    if args.asunmee:
        res = assist_asunmee_structure(live=bool(args.live), sample_limit=max(3, args.sample_limit))
        paths = write_asunmee_outputs(res)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_asunmee_text(res))
            print(f"\nWrote: {paths['json']}")
            print(f"Wrote: {paths['txt']}")
        return 0 if res.get("ok") else 1

    if args.frida_aes:
        res = decrypt_frida_a11y_bundle(
            args.frida_aes,
            key_b64=args.key_b64 or None,
            key_file=args.key_file or None,
        )
        out = REPORTS / "frida_a11y_aes_decrypt.json"
        REPORTS.mkdir(parents=True, exist_ok=True)
        # strip huge preview duplicates
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        (REPORTS / "frida_a11y_aes_decrypt.txt").write_text(
            f"FRIDA A11Y AES DECRYPT\nLúc: {res.get('checked_at')}\nVerdict: {res.get('verdict')}\n"
            f"Path: {res.get('path')}\nKey: {res.get('key')}\nNeed: {res.get('need')}\n"
            f"Outputs: {res.get('outputs')}\nError: {res.get('error')}\n",
            encoding="utf-8",
        )
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        else:
            print(res.get("verdict"))
            if res.get("need"):
                for n in res["need"]:
                    print(" need:", n)
            if res.get("outputs"):
                print(" outputs:", res["outputs"])
        return 0 if res.get("ok") else 1

    if args.aes_gcm:
        res = decrypt_aes_gcm(args.aes_gcm[0], args.aes_gcm[1], args.aes_gcm[2], args.aad)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 1

    report = build_report(inputs=args.text)
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
