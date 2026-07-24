#!/usr/bin/env python3
"""So sánh vấn đề mã hoá bằng thư viện mật mã học (pyca/cryptography + CRYPTO_ATLAS).

Đối chiếu các kiểu «bảo vệ» SĐT/PII trên ống OMS:
  - MASK **** (không phải mã hoá)
  - Encoding Base64 (không bí mật)
  - Hash SHA-256 (một chiều, không tra cứu được)
  - AEAD AES-GCM / ChaCha20-Poly1305 (đúng encryption — cryptography)

Không exploit. Chỉ demo giáo dục + khuyến nghị thư viện từ atlas.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import secrets
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports" / "telegram-classify"
ATLAS_JS = ROOT / "data" / "crypto-atlas.js"
ORDERS_CSV = ROOT / "quarantine" / "telegram" / "orders_detailed_Dang_giao_20260512_120712.csv"

# Demo key material — ephemeral, never for production secrets storage
_DEMO_AES_KEY = AESGCM.generate_key(bit_length=256)
_DEMO_CHA_KEY = ChaCha20Poly1305.generate_key()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_atlas_snippet() -> dict:
    """Parse lightly CRYPTO_ATLAS concepts/libraries needed for recommendations."""
    text = ATLAS_JS.read_text(encoding="utf-8") if ATLAS_JS.is_file() else ""
    concepts = {}
    for m in re.finditer(
        r'id:\s*"([^"]+)"[\s\S]*?name:\s*"([^"]+)"[\s\S]*?summary:\s*"([^"]*)"',
        text,
    ):
        cid, name, summary = m.group(1), m.group(2), m.group(3)
        if cid in concepts:
            continue
        concepts[cid] = {"id": cid, "name": name, "summary": summary}

    libs = {}
    # libraries block: id/name/tier/summary
    for m in re.finditer(
        r'\{\s*id:\s*"([^"]+)"\s*,\s*name:\s*"([^"]+)"[\s\S]*?tier:\s*"([^"]+)"[\s\S]*?summary:\s*"([^"]*)"',
        text,
    ):
        lid, name, tier, summary = m.group(1), m.group(2), m.group(3), m.group(4)
        libs[lid] = {"id": lid, "name": name, "tier": tier, "summary": summary}

    return {
        "concepts": concepts,
        "libraries": libs,
        "source": str(ATLAS_JS.relative_to(ROOT)) if ATLAS_JS.is_file() else None,
    }


def phone_class(ph: str | None) -> str:
    ph = (ph or "").strip()
    if not ph:
        return "MISSING"
    if "*" in ph or set(ph) <= {"*"}:
        return "MASKED"
    digits = re.sub(r"\D", "", ph)
    return "OK" if len(digits) >= 9 else "INVALID"


def scan_order_pii() -> dict:
    if not ORDERS_CSV.is_file():
        return {"rows": 0, "phone": {}, "by_source": {}}
    with ORDERS_CSV.open(newline="", encoding="utf-8", errors="replace") as fh:
        rows = list(csv.DictReader(fh))
    phone = Counter(phone_class(r.get("customer_phone")) for r in rows)
    by_source: dict[str, Counter] = {}
    for r in rows:
        src = r.get("source") or "(empty)"
        by_source.setdefault(src, Counter())[phone_class(r.get("customer_phone"))] += 1
    return {
        "file": ORDERS_CSV.name,
        "rows": len(rows),
        "phone": dict(phone),
        "by_source": {k: dict(v) for k, v in sorted(by_source.items(), key=lambda x: -sum(x[1].values()))},
    }


def approach_mask(plaintext: str) -> dict:
    if len(plaintext) <= 4:
        out = "*" * len(plaintext)
    else:
        out = plaintext[:2] + "*" * (len(plaintext) - 4) + plaintext[-2:]
    return {
        "id": "mask-redaction",
        "label": "Che PII (**** / partial mask)",
        "is_encryption": False,
        "reversible_without_key": False,
        "confidentiality": False,
        "integrity": False,
        "output": out,
        "issue": "Mất thông tin vĩnh viễn với ops; không phải encryption — chỉ redaction. Khớp direct_api ****.",
        "atlas_concepts": ["encryption-vs-encoding", "cia-triad"],
        "atlas_libs": [],
    }


def approach_encoding(plaintext: str) -> dict:
    out = base64.b64encode(plaintext.encode()).decode()
    back = base64.b64decode(out).decode()
    return {
        "id": "base64-encoding",
        "label": "Base64 encoding",
        "is_encryption": False,
        "reversible_without_key": True,
        "confidentiality": False,
        "integrity": False,
        "output": out,
        "roundtrip_ok": back == plaintext,
        "issue": "Ai cũng decode được — encoding ≠ encryption (CRYPTO_ATLAS).",
        "atlas_concepts": ["encryption-vs-encoding", "base64"],
        "atlas_libs": [],
    }


def approach_hash(plaintext: str) -> dict:
    digest = hashlib.sha256(plaintext.encode()).hexdigest()
    return {
        "id": "sha256-hash",
        "label": "SHA-256 hash (pyca/hashlib)",
        "is_encryption": False,
        "reversible_without_key": False,
        "confidentiality": "one-way",
        "integrity": True,
        "output": digest,
        "issue": "Không giải được để gọi khách khi giao — không phù hợp customer_phone ops.",
        "atlas_concepts": ["sha-256", "encryption-vs-encoding"],
        "atlas_libs": ["openssl", "webcrypto"],
    }


def approach_aes_gcm(plaintext: str) -> dict:
    aes = AESGCM(_DEMO_AES_KEY)
    nonce = secrets.token_bytes(12)
    aad = b"oms:customer_phone"
    ct = aes.encrypt(nonce, plaintext.encode(), aad)
    pt = aes.decrypt(nonce, ct, aad).decode()
    return {
        "id": "aes-gcm",
        "label": "AES-256-GCM AEAD (cryptography.hazmat)",
        "library": "pyca/cryptography",
        "is_encryption": True,
        "reversible_without_key": False,
        "confidentiality": True,
        "integrity": True,
        "output": {
            "nonce_b64": base64.b64encode(nonce).decode(),
            "ciphertext_b64": base64.b64encode(ct).decode(),
            "aad": aad.decode(),
        },
        "roundtrip_ok": pt == plaintext,
        "issue": None,
        "strength": "Chuẩn AEAD — bí mật + toàn vẹn; cần quản lý khoá (KMS/HSM).",
        "atlas_concepts": ["aes-gcm", "aead", "cia-triad"],
        "atlas_libs": ["cryptography-py", "tink", "webcrypto", "openssl"],
        "misuse_warnings": [
            "Không reuse nonce với cùng key",
            "Không đưa key vào dump/Telegram",
            "AAD nên gắn shop_id/order_key",
        ],
    }


def approach_chacha(plaintext: str) -> dict:
    ch = ChaCha20Poly1305(_DEMO_CHA_KEY)
    nonce = secrets.token_bytes(12)
    aad = b"oms:customer_phone"
    ct = ch.encrypt(nonce, plaintext.encode(), aad)
    pt = ch.decrypt(nonce, ct, aad).decode()
    return {
        "id": "chacha20-poly1305",
        "label": "ChaCha20-Poly1305 AEAD (cryptography.hazmat)",
        "library": "pyca/cryptography",
        "is_encryption": True,
        "reversible_without_key": False,
        "confidentiality": True,
        "integrity": True,
        "output": {
            "nonce_b64": base64.b64encode(nonce).decode(),
            "ciphertext_b64": base64.b64encode(ct).decode(),
        },
        "roundtrip_ok": pt == plaintext,
        "issue": None,
        "strength": "AEAD nhanh khi thiếu AES-NI; cùng family libsodium secretbox.",
        "atlas_concepts": ["chacha20-poly1305", "aead", "libsodium"],
        "atlas_libs": ["cryptography-py", "libsodium", "tink"],
        "misuse_warnings": ["Không reuse nonce", "Ưu tiên API high-level libsodium/Tink khi có thể"],
    }


def compare_matrix(approaches: list[dict]) -> list[dict]:
    rows = []
    for a in approaches:
        rows.append(
            {
                "id": a["id"],
                "label": a["label"],
                "is_encryption": a.get("is_encryption"),
                "confidentiality": a.get("confidentiality"),
                "integrity": a.get("integrity"),
                "reversible_without_key": a.get("reversible_without_key"),
                "suitable_for_ops_phone_callback": bool(
                    a.get("is_encryption") and a.get("roundtrip_ok")
                )
                if a.get("is_encryption")
                else False,
                "suitable_for_public_export": a["id"] == "mask-redaction",
                "atlas_concepts": a.get("atlas_concepts") or [],
                "atlas_libs": a.get("atlas_libs") or [],
                "verdict": a.get("issue") or a.get("strength") or "",
            }
        )
    return rows


def recommend_for_oms(atlas: dict) -> list[dict]:
    picks = [
        {
            "need": "Encrypt PII at rest (customer_phone) — decrypt nội bộ CS",
            "prefer_libs": ["cryptography-py", "tink", "libsodium"],
            "concepts": ["aes-gcm", "aead", "hybrid-encryption"],
            "avoid": ["mask-only trên bản nội bộ", "base64", "sha256 làm «mã hoá»"],
        },
        {
            "need": "Export ra ngoài / Telegram / đối tác",
            "prefer_libs": [],
            "concepts": ["encryption-vs-encoding", "cia-triad"],
            "prefer_approach": "mask-redaction hoặc không gửi PII",
            "avoid": ["đưa ciphertext kèm key", "dump Acc_all"],
        },
        {
            "need": "Transit Pancake/GHN API",
            "prefer_libs": ["openssl", "boringssl"],
            "concepts": ["tls", "hybrid-encryption"],
            "note": "HTTPS/TLS 1.3 — không tự bọc thêm weak cipher",
        },
        {
            "need": "Mật khẩu / token vault",
            "prefer_libs": ["libsodium", "cryptography-py"],
            "concepts": ["argon2"],
            "note": "Argon2id; secrets/backend_pipes.env chmod 600",
        },
    ]
    # enrich names from atlas when present
    for p in picks:
        p["concept_detail"] = [
            atlas["concepts"].get(c, {"id": c}) for c in p.get("concepts") or []
        ]
        p["lib_detail"] = [atlas["libraries"].get(l, {"id": l}) for l in p.get("prefer_libs") or []]
    return picks


def build_report(sample_phone: str = "0979263463") -> dict:
    atlas = load_atlas_snippet()
    pii = scan_order_pii()

    # Use a non-secret demo phone (sample pattern from dataset docs) — not a real customer dump
    demo = sample_phone
    approaches = [
        approach_mask(demo),
        approach_encoding(demo),
        approach_hash(demo),
        approach_aes_gcm(demo),
        approach_chacha(demo),
    ]
    # Strip raw demo from outputs in public-ish fields? keep for local educational report only
    matrix = compare_matrix(approaches)

    # Score: encryption problems observed in OMS
    problems = [
        {
            "id": "P-MASK-AS-CRYPTO",
            "title": "direct_api snapshot dùng **** — nhầm redaction với encryption",
            "evidence": pii.get("by_source", {}).get("direct_api_orders_snapshot"),
            "severity": "mask-redaction",
            "correct": "aes-gcm hoặc chacha20-poly1305 khi cần giữ PII nội bộ",
            "severity_concepts": ["encryption-vs-encoding"],
        },
        {
            "id": "P-MISSING-PHONE",
            "title": "Pancake/Telegram thiếu SĐT — không phải lỗi cipher, là data quality",
            "evidence": {
                "pancake": pii.get("by_source", {}).get("pancake_shop_1530618_orders"),
                "telegram": pii.get("by_source", {}).get("multi_platform_telegram_upload"),
            },
            "severity": None,
            "correct": "map billing/shipping phone → customer_phone; không «mã hoá» được dữ liệu trống",
            "severity_concepts": ["cia-triad"],
        },
        {
            "id": "P-ENCODING-CONFUSION",
            "title": "Nguy cơ dùng Base64/Hex như «mã hoá» khoá/API",
            "evidence": "Atlas cảnh báo encoding ≠ encryption",
            "severity": "base64-encoding",
            "correct": "AEAD + KMS; TLS trên wire",
            "severity_concepts": ["encryption-vs-encoding", "base64", "tls"],
        },
    ]

    try:
        from realtime_icon_feedback_mapper import chant, feedback_line

        icons = ["lock", "key", "hash", "cube", "monitor"]
        fb = feedback_line(icons, "so sánh mask/encoding/hash/AEAD bằng cryptography")
    except Exception:  # noqa: BLE001
        icons, fb = ["lock", "key", "hash"], "Mapper icon: lock → key → hash"

    report = {
        "ok": True,
        "query": "Sử dụng thư viện mật mã học để so sánh vấn đề mã hoá",
        "checked_at": utc_now(),
        "libraries_used": [
            {
                "id": "cryptography-py",
                "name": "pyca/cryptography",
                "version": __import__("cryptography").__version__,
                "primitives": ["AESGCM", "ChaCha20Poly1305", "SHA-256 via hashlib"],
                "atlas_tier": (atlas.get("libraries") or {}).get("cryptography-py", {}).get("tier")
                or "recommended",
            },
            {
                "id": "stdlib-hashlib-base64",
                "name": "Python stdlib hashlib/base64",
                "role": "đối chứng encoding/hash (không phải encryption)",
            },
        ],
        "atlas_ref": {
            "source": atlas.get("source"),
            "concepts_loaded": len(atlas.get("concepts") or {}),
            "libraries_loaded": len(atlas.get("libraries") or {}),
        },
        "oms_pii_scan": pii,
        "demo_plaintext_kind": "synthetic_phone_for_roundtrip_demo",
        "approaches": approaches,
        "comparison_matrix": matrix,
        "problems": problems,
        "recommendations": recommend_for_oms(atlas),
        "icon_feedback": fb,
        "icon_chant": " → ".join(icons) if isinstance(icons, list) else icons,
        "verdict": (
            f"So sánh bằng pyca/cryptography: MASK/Base64/SHA-256 không phải encryption; "
            f"AES-GCM & ChaCha20-Poly1305 AEAD roundtrip OK. "
            f"OMS Đang giao phone={pii.get('phone')} — masked/missing là vấn đề redaction/data, "
            f"không phải cipher. {fb}"
        ),
        "safety": {
            "no_production_keys": True,
            "ephemeral_demo_keys": True,
            "no_dump_login": True,
            "educational_only": True,
        },
        "next_actions": [
            "Nội bộ OMS: mã hoá PII bằng AES-GCM (cryptography/Tink) + KMS; không dùng **** trên bản CS",
            "Export ngoài: mask hoặc bỏ SĐT",
            "Wire TLS only tới Pancake/GHN — không tự invent protocol",
            "Tham chiếu /atlas/ · concepts encryption-vs-encoding, aead, aes-gcm",
        ],
    }
    return report


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔐 SO SÁNH VẤN ĐỀ MÃ HOÁ — THƯ VIỆN MẬT MÃ HỌC")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    L(f"✨ {report.get('icon_feedback')}")
    L(f"Chant: {report.get('icon_chant')}")
    L("")
    L("=== Thư viện dùng ===")
    for lib in report["libraries_used"]:
        L(f"· {lib.get('name')} {lib.get('version') or ''} — {lib.get('primitives') or lib.get('role')}")
    L(f"· Atlas: {report['atlas_ref']}")
    L("")
    L("=== OMS PII (Đang giao) ===")
    pii = report["oms_pii_scan"]
    L(f"· file={pii.get('file')} rows={pii.get('rows')} phone={pii.get('phone')}")
    for src, st in (pii.get("by_source") or {}).items():
        L(f"  - {src}: {st}")
    L("")
    L("=== Ma trận so sánh ===")
    for row in report["comparison_matrix"]:
        L(
            f"▶ {row['label']}: encryption={row['is_encryption']} "
            f"conf={row['confidentiality']} integ={row['integrity']} "
            f"ops_callback={row['suitable_for_ops_phone_callback']} export={row['suitable_for_public_export']}"
        )
        L(f"  {row['verdict']}")
        L(f"  concepts={row['atlas_concepts']} libs={row['atlas_libs']}")
    L("")
    L("=== Demo outputs (AEAD / đối chứng) ===")
    for a in report["approaches"]:
        out = a.get("output")
        if isinstance(out, dict):
            L(f"· {a['id']}: ciphertext_b64={str(out.get('ciphertext_b64'))[:48]}… roundtrip={a.get('roundtrip_ok')}")
        else:
            L(f"· {a['id']}: {str(out)[:80]}")
    L("")
    L("=== Vấn đề quan sát ===")
    for p in report["problems"]:
        L(f"▶ [{p['id']}] {p['title']}")
        L(f"  evidence={p.get('evidence')}")
        L(f"  wrong={p.get('wrong')} → correct={p.get('correct')}")
    L("")
    L("=== Khuyến nghị (atlas) ===")
    for r in report["recommendations"]:
        L(f"· {r['need']}")
        L(f"  libs={[x.get('id') for x in r.get('lib_detail') or []]} concepts={r.get('concepts')}")
        if r.get("avoid"):
            L(f"  avoid={r['avoid']}")
    L("")
    L("Next:")
    for a in report["next_actions"]:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = format_text(report)
    # Redact ephemeral key material already not in report; keep ciphertext only
    paths = {
        "json": REPORTS / "crypto_encryption_compare.json",
        "txt": REPORTS / "crypto_encryption_compare.txt",
    }
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="So sánh vấn đề mã hoá bằng cryptography + atlas")
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--demo-phone",
        default=os.environ.get("CRYPTO_DEMO_PHONE", "0979263463"),
        help="SĐT synthetic cho demo roundtrip (không dùng SĐT khách thật)",
    )
    args = ap.parse_args()
    report = build_report(sample_phone=args.demo_phone)
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
