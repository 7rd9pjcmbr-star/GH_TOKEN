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
        pt = AESGCM(key).decrypt(nonce, ct, aad.encode() if aad else None)
        return {
            "ok": True,
            "kind": "aes-gcm",
            "plain_text": pt.decode("utf-8", errors="replace"),
            "explain": explain("aes-gcm"),
            "library": "pyca/cryptography AESGCM",
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "kind": "aes-gcm", "error": str(e), "explain": explain("aes-gcm")}


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

    try:
        from realtime_icon_feedback_mapper import chant, feedback_line

        icons = ["text", "lock", "key", "hash", "monitor"]
        fb = feedback_line(icons, "module hỗ trợ giải mã encode/* + AEAD")
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
            {"id": "pyca/cryptography", "role": "AESGCM / ChaCha20Poly1305.decrypt"},
        ],
        "batch_decode": decoded,
        "aead_demo": aead_demo,
        "orders_phone_assist": orders,
        "icon_feedback": fb,
        "icon_chant": " → ".join(icons) if isinstance(icons, list) else str(icons),
        "verdict": (
            f"Module giải mã: encode/* xử lý Base64/Morse/Braille/Hex/URL; "
            f"AEAD demo roundtrip={aead_demo['decrypt_result'].get('roundtrip_ok')}. "
            f"Đang giao: {orders.get('by_class')} — MASKED không giải được bằng decode. {fb}"
        ),
        "safety": {
            "no_password_cracking": True,
            "no_dump_login": True,
            "aead_requires_owned_key": True,
            "mask_not_decryptable": True,
        },
        "next_actions": [
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
    ap = argparse.ArgumentParser(description="Module hỗ trợ giải mã (encode + AEAD)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--text", action="append", help="Chuỗi cần giải (lặp lại được)")
    ap.add_argument("--aes-gcm", nargs=3, metavar=("KEY_B64", "NONCE_B64", "CT_B64"))
    ap.add_argument("--aad", default="")
    args = ap.parse_args()

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
