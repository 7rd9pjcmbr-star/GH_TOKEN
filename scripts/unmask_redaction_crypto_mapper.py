#!/usr/bin/env python3
"""Tra cứu mapper: unmask ↔ redaction ↔ thư viện mật mã học (CRYPTO_ATLAS + pyca).

Ánh xạ đường đi đúng:
  MASK ****  → không decrypt được (redaction) → fetch_unmasked / AEAD owned
  Base64/Hex → encode decode (MaMoCrypto.encode) — ≠ unmask PII
  AES-GCM / ChaCha20-Poly1305 → decrypt khi có key owned (cryptography)

Không: phá ****, brute-force, dump-login.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports" / "telegram-classify"
ATLAS_JS = ROOT / "data" / "crypto-atlas.js"
ASUNMEE_CFG = ROOT / "config" / "asunmee_shop_decode.json"

# Queries người dùng thường dùng khi «tra cứu unmask redaction»
LOOKUP_QUERIES = (
    "unmask",
    "redaction",
    "mask ****",
    "encryption vs encoding",
    "aead",
    "aes-gcm",
    "pii",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_atlas() -> dict[str, Any]:
    text = ATLAS_JS.read_text(encoding="utf-8") if ATLAS_JS.is_file() else ""
    concepts: dict[str, dict] = {}
    for m in re.finditer(
        r'id:\s*"([^"]+)"\s*,\s*category:\s*"([^"]+)"\s*,\s*name:\s*"([^"]+)"\s*,'
        r'[\s\S]*?summary:\s*"([^"]*)"',
        text,
    ):
        cid, cat, name, summary = m.group(1), m.group(2), m.group(3), m.group(4)
        concepts.setdefault(cid, {"id": cid, "category": cat, "name": name, "summary": summary})

    # Fallback looser concept parse (some entries order differ)
    for m in re.finditer(
        r'id:\s*"([^"]+)"[\s\S]*?name:\s*"([^"]+)"[\s\S]*?summary:\s*"([^"]*)"',
        text,
    ):
        cid, name, summary = m.group(1), m.group(2), m.group(3)
        concepts.setdefault(cid, {"id": cid, "name": name, "summary": summary})

    libs: dict[str, dict] = {}
    for m in re.finditer(
        r'\{\s*id:\s*"([^"]+)"\s*,\s*name:\s*"([^"]+)"[\s\S]*?tier:\s*"([^"]+)"'
        r'[\s\S]*?summary:\s*"([^"]*)"',
        text,
    ):
        lid, name, tier, summary = m.group(1), m.group(2), m.group(3), m.group(4)
        libs.setdefault(lid, {"id": lid, "name": name, "tier": tier, "summary": summary})

    guide: list[dict] = []
    for m in re.finditer(r'need:\s*"([^"]+)"\s*,\s*pick:\s*"([^"]+)"', text):
        guide.append({"need": m.group(1), "pick": m.group(2)})

    cheat_do = re.findall(r'cheatSheet:\s*\{[\s\S]*?do:\s*\[([^\]]*)\]', text)
    dos: list[str] = []
    if cheat_do:
        dos = re.findall(r'"([^"]+)"', cheat_do[0])

    return {
        "concepts": concepts,
        "libraries": libs,
        "guide": guide,
        "cheat_do": dos,
        "source": str(ATLAS_JS.relative_to(ROOT)) if ATLAS_JS.is_file() else None,
    }


def atlas_lookup(atlas: dict, query: str) -> dict[str, Any]:
    """Tra cứu kiểu MaMoCrypto.search — rank concept/lib theo token."""
    q = (query or "").lower().strip()
    tokens = [t for t in re.split(r"[^\w+-]+", q) if t]
    synonyms = {
        "unmask": ["mask", "redaction", "encryption-vs-encoding", "aead"],
        "redaction": ["mask", "encryption-vs-encoding", "cia-triad"],
        "mask": ["encryption-vs-encoding", "base64", "redaction"],
        "pii": ["aead", "aes-gcm", "encryption-vs-encoding"],
        "decrypt": ["aead", "aes-gcm", "chacha20-poly1305"],
        "****": ["encryption-vs-encoding", "mask"],
    }
    expand = set(tokens)
    for t in tokens:
        for s in synonyms.get(t, []):
            expand.add(s)

    lib_ids = set((atlas.get("libraries") or {}).keys())
    # Concepts only — bỏ id trùng thư viện (parser JS lỏng)
    scored_c: list[tuple[int, dict]] = []
    for c in (atlas.get("concepts") or {}).values():
        cid = c.get("id") or ""
        if cid in lib_ids:
            continue
        blob = f"{cid} {c.get('name','')} {c.get('summary','')}".lower()
        score = sum(3 if tok == cid else 1 for tok in expand if tok in blob)
        if score:
            scored_c.append((score, c))
    scored_c.sort(key=lambda x: (-x[0], x[1].get("id") or ""))

    prefer_aead = {"pyca-cryptography", "tink", "libsodium", "webcrypto", "openssl"}
    want_aead = bool(expand & {"aead", "aes-gcm", "chacha20-poly1305", "unmask", "pii", "decrypt"})

    scored_l: list[tuple[int, dict]] = []
    for lib in (atlas.get("libraries") or {}).values():
        lid = lib.get("id") or ""
        blob = f"{lid} {lib.get('name','')} {lib.get('summary','')}".lower()
        score = sum(2 for tok in expand if tok in blob)
        if want_aead and lid in prefer_aead:
            score += 5
        if want_aead and any(k in blob for k in ("aead", "aes", "gcm", "chacha", "sodium", "tink", "fernet")):
            score += 2
        if score:
            scored_l.append((score, lib))
    scored_l.sort(key=lambda x: (-x[0], x[1].get("id") or ""))

    guide_hits = [
        g
        for g in atlas.get("guide") or []
        if any(t in f"{g.get('need','')} {g.get('pick','')}".lower() for t in expand)
    ]

    return {
        "query": query,
        "tokens": sorted(expand),
        "concepts": [c for _, c in scored_c[:8]],
        "libraries": [l for _, l in scored_l[:8]],
        "guide": guide_hits[:5],
    }


def mapper_paths() -> list[dict[str, Any]]:
    """Đồ thị ánh xạ unmask/redaction → action → lib."""
    return [
        {
            "id": "PATH-MASK-REDACTION",
            "input": "**** / H** N*** / +84335****64",
            "kind": "redaction",
            "is_ciphertext": False,
            "crypto_unmask": False,
            "atlas_concepts": ["encryption-vs-encoding", "cia-triad"],
            "atlas_libs": [],
            "mapper_action": "fetch_unmasked_from_source_api",
            "tools": [
                "scripts/fix_order_phones.py",
                "scripts/crypto_decode_assist.py --asunmee",
                "config/asunmee_shop_decode.json",
            ],
            "verdict": "Redaction mất bit — thư viện mật mã không khôi phục được.",
        },
        {
            "id": "PATH-ENCODING",
            "input": "Base64 / Hex / URL / Morse / Braille",
            "kind": "encoding",
            "is_ciphertext": False,
            "crypto_unmask": False,
            "atlas_concepts": ["base64", "encryption-vs-encoding"],
            "atlas_libs": [],
            "mapper_action": "ma_mo_encode_decode",
            "tools": [
                "js/crypto/encode.js",
                "scripts/crypto_decode_assist.py --text …",
            ],
            "verdict": "Đổi biểu diễn — ai cũng giải; không phải unmask PII.",
        },
        {
            "id": "PATH-AEAD-AT-REST",
            "input": "AES-GCM / ChaCha20-Poly1305 ciphertext + key owned",
            "kind": "aead",
            "is_ciphertext": True,
            "crypto_unmask": True,
            "atlas_concepts": ["aead", "aes-gcm", "chacha20-poly1305"],
            "atlas_libs": ["pyca-cryptography", "tink", "libsodium", "webcrypto"],
            "mapper_action": "decrypt_aead_owned_key",
            "tools": [
                "scripts/crypto_decode_assist.py --aes-gcm KEY NONCE CT",
                "cryptography.hazmat.primitives.ciphers.aead.AESGCM",
            ],
            "verdict": "Đúng unmask crypto: decrypt AEAD khi có key+nonce (+AAD).",
        },
        {
            "id": "PATH-FRIDA-AES",
            "input": "mapper-icon-aes-v1 Frida bundle",
            "kind": "aead-frida",
            "is_ciphertext": True,
            "crypto_unmask": True,
            "atlas_concepts": ["aead", "aes-gcm"],
            "atlas_libs": ["pyca-cryptography"],
            "mapper_action": "decrypt_frida_a11y_aes",
            "tools": [
                "scripts/crypto_decode_assist.py --frida-aes FILE",
                "MAPPER_ICON_AES_KEY_B64",
            ],
            "verdict": "AES unwrap bundle ≠ unmask Pancake **** trong cùng payload.",
        },
        {
            "id": "PATH-HASH",
            "input": "SHA-256 digest",
            "kind": "hash",
            "is_ciphertext": False,
            "crypto_unmask": False,
            "atlas_concepts": ["sha-256", "encryption-vs-encoding"],
            "atlas_libs": ["openssl", "webcrypto"],
            "mapper_action": "reject_hash_as_phone_store",
            "tools": ["hashlib / pyca hashes"],
            "verdict": "Một chiều — không unmask để gọi khách.",
        },
    ]


def enrich_paths(paths: list[dict], atlas: dict) -> list[dict]:
    out = []
    for p in paths:
        row = dict(p)
        row["concept_detail"] = [
            atlas["concepts"].get(c, {"id": c}) for c in p.get("atlas_concepts") or []
        ]
        row["lib_detail"] = [
            atlas["libraries"].get(l, {"id": l}) for l in p.get("atlas_libs") or []
        ]
        out.append(row)
    return out


def asunmee_bridge() -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if ASUNMEE_CFG.is_file():
        cfg = json.loads(ASUNMEE_CFG.read_text(encoding="utf-8"))
    mask = cfg.get("pii_mask") or {}
    return {
        "shop": cfg.get("shop"),
        "detail_unmasks": mask.get("detail_unmasks"),
        "mask_fields_n": len(mask.get("fields") or []),
        "policy": mask.get("policy"),
        "path_id": "PATH-MASK-REDACTION",
        "cli": "python3 scripts/crypto_decode_assist.py --asunmee --live",
    }


def aead_capability_probe() -> dict[str, Any]:
    """Chứng minh lib densely: pyca có decrypt — không áp dụng cho ****."""
    try:
        import base64
        import secrets

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = AESGCM.generate_key(bit_length=256)
        nonce = secrets.token_bytes(12)
        aad = b"oms:unmask_mapper"
        pt = b"0979000000"  # synthetic
        ct = AESGCM(key).encrypt(nonce, pt, aad)
        back = AESGCM(key).decrypt(nonce, ct, aad)
        return {
            "ok": True,
            "library": "pyca/cryptography",
            "version": __import__("cryptography").__version__,
            "primitive": "AESGCM",
            "roundtrip_ok": back == pt,
            "sample_ct_b64": base64.b64encode(ct).decode()[:48] + "…",
            "note": "AEAD decrypt OK — chỉ khi ciphertext+key; không áp dụng mask redaction",
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def classify_sample(text: str) -> dict[str, Any]:
    """Gắn mẫu đầu vào vào path mapper (+ decode assist nếu có)."""
    t = (text or "").strip()
    try:
        from crypto_decode_assist import detect_and_decode, is_pii_mask

        assist = detect_and_decode(t)
        if is_pii_mask(t) or assist.get("kind") == "mask":
            return {"sample": t, "path_id": "PATH-MASK-REDACTION", "assist": assist}
        if assist.get("ok") and assist.get("kind") in {"base64", "hex", "url", "morse", "braille"}:
            return {"sample": t, "path_id": "PATH-ENCODING", "assist": assist}
        return {"sample": t, "path_id": None, "assist": assist}
    except Exception as e:  # noqa: BLE001
        if "*" in t:
            return {"sample": t, "path_id": "PATH-MASK-REDACTION", "error": str(e)}
        return {"sample": t, "path_id": None, "error": str(e)}


def build_report(samples: list[str] | None = None) -> dict[str, Any]:
    atlas = load_atlas()
    lookups = [atlas_lookup(atlas, q) for q in LOOKUP_QUERIES]
    paths = enrich_paths(mapper_paths(), atlas)
    samples = samples or [
        "+84335****64",
        "H** N***",
        "MDk3OTI2MzQ2Mw==",
        "09******63",
    ]
    classified = [classify_sample(s) for s in samples]

    try:
        from realtime_icon_feedback_mapper import feedback_line

        icons = ["lock", "key", "text", "hash", "monitor"]
        fb = feedback_line(icons, "tra cứu unmask redaction × CRYPTO_ATLAS × pyca")
    except Exception:  # noqa: BLE001
        icons, fb = ["lock", "key", "text"], "Mapper: lock → key → text"

    aead = aead_capability_probe()
    crypto_unmask_paths = [p for p in paths if p.get("crypto_unmask")]
    no_crypto = [p for p in paths if not p.get("crypto_unmask")]

    report = {
        "ok": True,
        "query": "Tra cứu unmask redaction mapper thư viện mật mã học",
        "checked_at": utc_now(),
        "atlas": {
            "source": atlas.get("source"),
            "concepts_n": len(atlas.get("concepts") or {}),
            "libraries_n": len(atlas.get("libraries") or {}),
        },
        "lookups": lookups,
        "mapper_paths": paths,
        "summary": {
            "paths_total": len(paths),
            "crypto_unmask_ok": [p["id"] for p in crypto_unmask_paths],
            "no_crypto_unmask": [p["id"] for p in no_crypto],
            "primary_for_asunmee_mask": "PATH-MASK-REDACTION",
            "primary_for_owned_pii_store": "PATH-AEAD-AT-REST",
        },
        "asunmee": asunmee_bridge(),
        "aead_probe": aead,
        "samples": classified,
        "icon_feedback": fb,
        "icon_chant": " → ".join(icons),
        "modules": [
            {"id": "CRYPTO_ATLAS", "file": "data/crypto-atlas.js"},
            {"id": "MaMoCrypto", "file": "js/crypto/api.js", "role": "lookup/search/recommend"},
            {"id": "pyca/cryptography", "role": "AESGCM / ChaCha20Poly1305"},
            {"id": "crypto_decode_assist", "file": "scripts/crypto_decode_assist.py"},
            {"id": "crypto_encryption_issue_compare", "file": "scripts/crypto_encryption_issue_compare.py"},
            {"id": "asunmee_shop_decode", "file": "config/asunmee_shop_decode.json"},
            {"id": "fix_order_phones", "file": "scripts/fix_order_phones.py"},
        ],
        "verdict": (
            "Tra cứu atlas: MASK/redaction ≠ ciphertext — không unmask bằng crypto. "
            f"Đường crypto hợp lệ: {', '.join(p['id'] for p in crypto_unmask_paths)} "
            f"(pyca AEAD probe ok={aead.get('ok')}). "
            f"ASUNMEE mask → PATH-MASK-REDACTION / fetch_unmasked. {fb}"
        ),
        "next_actions": [
            "ASUNMEE ****: python3 scripts/crypto_decode_assist.py --asunmee --live",
            "AEAD owned: python3 scripts/crypto_decode_assist.py --aes-gcm KEY NONCE CT",
            "So sánh đầy đủ: python3 scripts/crypto_encryption_issue_compare.py",
            "UI atlas: MaMoCrypto.lookup('encryption-vs-encoding') · recommend('aead')",
            "Ops mask: fix_order_phones → fetch_unmasked_from_source_api",
        ],
        "safety": {
            "no_bruteforce": True,
            "no_unmask_redaction": True,
            "aead_requires_owned_key": True,
            "no_dump_login": True,
        },
    }
    return report


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔎 UNMASK × REDACTION × CRYPTO MAPPER")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    L(f"✨ {report.get('icon_feedback')}")
    L(f"Atlas: {report.get('atlas')}")
    L("")
    L("=== Tra cứu CRYPTO_ATLAS ===")
    for lu in report.get("lookups") or []:
        L(f"▶ query={lu.get('query')!r} tokens={lu.get('tokens')}")
        for c in (lu.get("concepts") or [])[:4]:
            L(f"  concept: {c.get('id')} — {c.get('name')}")
        for lib in (lu.get("libraries") or [])[:4]:
            L(f"  lib: {lib.get('id')} [{lib.get('tier')}] — {lib.get('name')}")
    L("")
    L("=== Mapper paths ===")
    for p in report.get("mapper_paths") or []:
        L(
            f"▶ {p.get('id')}: kind={p.get('kind')} crypto_unmask={p.get('crypto_unmask')} "
            f"action={p.get('mapper_action')}"
        )
        L(f"  in: {p.get('input')}")
        L(f"  → {p.get('verdict')}")
        L(f"  concepts={[c.get('id') for c in (p.get('concept_detail') or [])]}")
        L(f"  libs={[x.get('id') for x in (p.get('lib_detail') or [])]}")
    L("")
    L("=== Samples ===")
    for s in report.get("samples") or []:
        ar = s.get("assist") or {}
        L(
            f"· {s.get('sample')!r} → path={s.get('path_id')} "
            f"assist_kind={ar.get('kind') or ar.get('detected')}"
        )
    L("")
    asu = report.get("asunmee") or {}
    L("=== ASUNMEE bridge ===")
    L(f"· shop={asu.get('shop')} detail_unmasks={asu.get('detail_unmasks')}")
    L(f"· path={asu.get('path_id')} cli={asu.get('cli')}")
    L("")
    probe = report.get("aead_probe") or {}
    L("=== pyca AEAD probe ===")
    L(
        f"· ok={probe.get('ok')} {probe.get('library')} {probe.get('version')} "
        f"roundtrip={probe.get('roundtrip_ok')} — {probe.get('note') or probe.get('error')}"
    )
    L("")
    L("Next:")
    for a in report.get("next_actions") or []:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": REPORTS / "unmask_redaction_crypto_mapper.json",
        "txt": REPORTS / "unmask_redaction_crypto_mapper.txt",
    }
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
    paths["txt"].write_text(format_text(report) + "\n", encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Tra cứu unmask/redaction mapper × CRYPTO_ATLAS")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--sample", action="append", help="Mẫu chuỗi để classify vào path")
    ap.add_argument("--lookup", action="append", help="Thêm query tra cứu atlas")
    args = ap.parse_args()

    report = build_report(samples=args.sample)
    if args.lookup:
        atlas = load_atlas()
        report["lookups"] = list(report.get("lookups") or []) + [
            atlas_lookup(atlas, q) for q in args.lookup
        ]
        # refresh verdict lightly
        report["extra_lookups"] = args.lookup

    paths = write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
        print(f"\nWrote: {paths['json']}")
        print(f"Wrote: {paths['txt']}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
