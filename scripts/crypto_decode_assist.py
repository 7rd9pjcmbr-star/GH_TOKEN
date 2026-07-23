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
    """Redaction mask (**** / +84…**** / VĐ*****) — not ciphertext."""
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
        "icon_feedback": fb,
        "icon_chant": " → ".join(icons) if isinstance(icons, list) else str(icons),
        "verdict": (
            f"Module giải mã: encode/* + AEAD demo roundtrip="
            f"{aead_demo['decrypt_result'].get('roundtrip_ok')}. "
            f"Frida AES: {frida.get('verdict')}. "
            f"Đang giao: {orders.get('by_class')} — MASKED không giải bằng decode. {fb}"
        ),
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
            "PII at rest: lưu AES-GCM; giải bằng crypto_decode_assist --aes-gcm khi CS cần",
            "UI: MaMoCrypto.encode.fromBase64 / fromMorse / fromBraille",
        ],
    }
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
    L("")
    L("Next:")
    for a in report["next_actions"]:
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Module hỗ trợ giải mã (encode + AEAD + Frida AES)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--text", action="append", help="Chuỗi cần giải (lặp lại được)")
    ap.add_argument("--aes-gcm", nargs=3, metavar=("KEY_B64", "NONCE_B64", "CT_B64"))
    ap.add_argument("--aad", default="")
    ap.add_argument("--frida-aes", metavar="FILE", help="Giải bundle frida-a11y-offline-aes*.json")
    ap.add_argument("--key-b64", default="", help="AES-256 key (base64) owned")
    ap.add_argument("--key-file", default="", help="File chứa key_b64 (1 dòng)")
    args = ap.parse_args()

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
