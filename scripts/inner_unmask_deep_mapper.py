#!/usr/bin/env python3
"""Mapper truy vấn sâu toàn diện lớp bên trong — ánh xạ hỗ trợ giải mã unmask.

Lớp (ngoài → trong):
  L0 bundle Frida (AES-GCM) → L1 plaintext JSON envelope
  → L2 call/backend/encoding/response → L3 preview_masked / details_masked
  → L4 field node {encoding, display, b64, sha256_16, masked}
  → L5 decode assist (mask / b64→mask / clear)

Không phá ****; AEAD chỉ với key owned (MAPPER_AES_KEY_B64).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports" / "telegram-classify"
UPLOADS = Path("/home/ubuntu/.cursor/projects/workspace/uploads")
ASUNMEE_CFG = ROOT / "config" / "asunmee_shop_decode.json"

sys.path.insert(0, str(ROOT / "scripts"))

from crypto_decode_assist import (  # noqa: E402
    decrypt_frida_a11y_bundle,
    detect_and_decode,
    find_frida_aes_bundles,
    is_pii_mask,
    resolve_aes_key_b64,
)

LAYER_DEFS = [
    {"id": "L0", "name": "Frida AES bundle", "role": "ciphertext AES-256-GCM"},
    {"id": "L1", "name": "Plaintext envelope", "role": "JSON sau decrypt"},
    {"id": "L2", "name": "Envelope sections", "role": "call / backend / encoding / response"},
    {"id": "L3", "name": "Order collections", "role": "preview_masked / details_masked / related"},
    {"id": "L4", "name": "Field nodes", "role": "{encoding, display, b64, sha256_16, masked}"},
    {"id": "L5", "name": "Unmask assist", "role": "path MASK / ENCODING / AEAD / FETCH"},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_path(path: str) -> str:
    return re.sub(r"\[\d+\]", "[]", path or "")


def is_envelope_node(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    keys = set(obj.keys())
    return bool({"display", "b64"} <= keys) or bool({"display", "sha256_16", "masked"} <= keys)


def classify_envelope_node(path: str, node: dict) -> dict[str, Any]:
    display = node.get("display")
    b64 = node.get("b64")
    enc = node.get("encoding")
    masked_flag = bool(node.get("masked"))
    row: dict[str, Any] = {
        "path": path,
        "path_norm": _norm_path(path),
        "layer": "L4",
        "encoding_declared": enc,
        "masked_flag": masked_flag,
        "display": display if isinstance(display, str) else None,
        "sha256_16": node.get("sha256_16") or None,
        "has_b64": bool(isinstance(b64, str) and b64.strip()),
    }

    display_kind = None
    if isinstance(display, str) and display.strip():
        if is_pii_mask(display):
            display_kind = "mask"
        elif display.strip() == "":
            display_kind = "empty"
        else:
            display_kind = "clear"
    elif display == "" or display is None:
        display_kind = "empty"
    row["display_kind"] = display_kind

    b64_plain = None
    b64_result = None
    if isinstance(b64, str) and b64.strip():
        dec = detect_and_decode(b64)
        b64_plain = dec.get("plain_text")
        if dec.get("ok") and isinstance(b64_plain, str):
            if is_pii_mask(b64_plain):
                b64_result = "decodes_to_mask"
            elif b64_plain == "":
                b64_result = "decodes_to_empty"
            else:
                b64_result = "decodes_to_clear"
        else:
            b64_result = "b64_fail_or_unknown"
        row["b64_assist"] = {
            "ok": dec.get("ok"),
            "kind": dec.get("kind") or dec.get("detected"),
            "plain_text": b64_plain,
        }
    row["b64_result"] = b64_result

    # Path mapping for unmask assist
    if display_kind == "mask" or b64_result == "decodes_to_mask" or (masked_flag and enc == "mask"):
        path_id = "PATH-MASK-REDACTION"
        action = "fetch_unmasked_from_source_api"
        crypto_unmask = False
    elif b64_result == "decodes_to_clear" or display_kind == "clear":
        path_id = "PATH-ENCODING" if b64_result == "decodes_to_clear" else "PATH-CLEAR"
        action = "use_clear_value" if display_kind == "clear" else "ma_mo_encode_decode"
        crypto_unmask = False
    elif display_kind == "empty" and not row["has_b64"]:
        path_id = "PATH-MISSING"
        action = "backfill_from_oms"
        crypto_unmask = False
    else:
        path_id = "PATH-UNKNOWN"
        action = "classify_then_retry"
        crypto_unmask = False

    row["path_id"] = path_id
    row["mapper_action"] = action
    row["crypto_unmask"] = crypto_unmask
    row["assist_note"] = {
        "PATH-MASK-REDACTION": "Redaction — không decrypt; refetch API/export full PII owned",
        "PATH-ENCODING": "b64 chỉ đổi dạng; nếu plain vẫn **** thì vẫn MASK",
        "PATH-CLEAR": "Giá trị clear — dùng trực tiếp cho ops",
        "PATH-MISSING": "Trống — không có ciphertext/mask để giải",
        "PATH-UNKNOWN": "Không nhận dạng — kiểm tra schema",
    }.get(path_id)
    return row


def deep_walk_envelope(obj: Any, path: str = "", depth: int = 0, max_depth: int = 10) -> list[dict]:
    """Truy vấn sâu mọi node envelope + string đáng chú ý."""
    hits: list[dict] = []
    if depth > max_depth:
        return hits
    if is_envelope_node(obj):
        hits.append(classify_envelope_node(path or "$", obj))
        # vẫn đi xuống nếu có nested envelope (hiếm)
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in {"display", "b64", "sha256_16", "encoding", "masked"}:
                    continue
                if isinstance(v, (dict, list)):
                    hits.extend(deep_walk_envelope(v, f"{path}.{k}" if path else k, depth + 1, max_depth))
        return hits
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            hits.extend(deep_walk_envelope(v, p, depth + 1, max_depth))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(deep_walk_envelope(v, f"{path}[{i}]", depth + 1, max_depth))
    elif isinstance(obj, str) and obj.strip() and ("*" in obj or len(obj) >= 8):
        # bare string: chỉ giữ mask thật hoặc encoding rõ (tránh order id / tên SP)
        if "*" in obj and is_pii_mask(obj):
            hits.append(
                {
                    "path": path,
                    "path_norm": _norm_path(path),
                    "layer": "L5-string",
                    "display": obj[:120],
                    "display_kind": "mask",
                    "b64_result": None,
                    "path_id": "PATH-MASK-REDACTION",
                    "mapper_action": "fetch_unmasked_from_source_api",
                    "crypto_unmask": False,
                    "assist_note": "Bare mask string trong envelope",
                }
            )
            return hits
        # Base64 có padding hoặc alphabet rõ — không phải thuần chữ/số unicode dài
        b64cand = re.sub(r"\s+", "", obj)
        if (
            re.fullmatch(r"[A-Za-z0-9+/]+=*", b64cand)
            and len(b64cand) >= 12
            and ("=" in b64cand or re.search(r"[A-Za-z]", b64cand) and re.search(r"[0-9+/]", b64cand))
            and not re.fullmatch(r"\d+", obj)
        ):
            assist = detect_and_decode(obj)
            if assist.get("ok") and (assist.get("kind") or assist.get("detected")) == "base64":
                plain = assist.get("plain_text") or ""
                hits.append(
                    {
                        "path": path,
                        "path_norm": _norm_path(path),
                        "layer": "L5-string",
                        "display": obj[:120],
                        "display_kind": "encoded",
                        "b64_result": "decodes_to_mask"
                        if is_pii_mask(plain)
                        else ("decodes_to_clear" if plain else "other"),
                        "path_id": "PATH-MASK-REDACTION"
                        if is_pii_mask(plain)
                        else "PATH-ENCODING",
                        "mapper_action": "fetch_unmasked_from_source_api"
                        if is_pii_mask(plain)
                        else "ma_mo_encode_decode",
                        "crypto_unmask": False,
                        "assist": {
                            "ok": True,
                            "kind": "base64",
                            "plain_text": plain[:120],
                        },
                        "assist_note": "Bare base64 trong cây — kiểm tra plain có phải mask",
                    }
                )
    return hits


def layer_inventory(inner: dict) -> dict[str, Any]:
    """Thống kê cấu trúc từng lớp L1–L3."""
    resp = inner.get("response") if isinstance(inner.get("response"), dict) else {}
    return {
        "L1_keys": list(inner.keys()) if isinstance(inner, dict) else [],
        "L2": {
            "call": inner.get("call"),
            "backend": inner.get("backend"),
            "encoding": inner.get("encoding"),
            "error": inner.get("error"),
            "hint": inner.get("hint"),
            "ok": inner.get("ok"),
            "called_at": inner.get("called_at"),
        },
        "L3": {
            "http_status": resp.get("http_status"),
            "success": resp.get("success"),
            "total_entries": resp.get("total_entries"),
            "count": resp.get("count"),
            "preview_masked_n": len(resp.get("preview_masked") or []),
            "details_masked_n": len(resp.get("details_masked") or []),
            "related_n": len(resp.get("related") or []),
            "preview_sample_ids": [
                o.get("id")
                for o in (resp.get("preview_masked") or [])[:8]
                if isinstance(o, dict)
            ],
            "detail_sample_ids": [
                o.get("id")
                for o in (resp.get("details_masked") or [])[:8]
                if isinstance(o, dict)
            ],
        },
    }


def map_path_matrix(field_hits: list[dict]) -> list[dict]:
    """Gom theo path_norm × path_id."""
    buckets: dict[tuple[str, str], dict] = {}
    for h in field_hits:
        key = (h.get("path_norm") or h.get("path") or "?", h.get("path_id") or "?")
        b = buckets.setdefault(
            key,
            {
                "path_norm": key[0],
                "path_id": key[1],
                "count": 0,
                "crypto_unmask": h.get("crypto_unmask"),
                "mapper_action": h.get("mapper_action"),
                "assist_note": h.get("assist_note"),
                "samples": [],
            },
        )
        b["count"] += 1
        if len(b["samples"]) < 3:
            b["samples"].append(
                {
                    "path": h.get("path"),
                    "display": h.get("display"),
                    "b64_result": h.get("b64_result"),
                    "encoding_declared": h.get("encoding_declared"),
                }
            )
    return sorted(buckets.values(), key=lambda x: (-x["count"], x["path_norm"]))


def asunmee_layer_bridge() -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if ASUNMEE_CFG.is_file():
        cfg = json.loads(ASUNMEE_CFG.read_text(encoding="utf-8"))
    mask = cfg.get("pii_mask") or {}
    primary_wh = cfg.get("primary_warehouse_id")
    wh_list = cfg.get("warehouses") or []
    primary = next((w for w in wh_list if str(w.get("id")) == str(primary_wh)), None)
    return {
        "shop": cfg.get("shop"),
        "mask_fields": mask.get("fields") or [],
        "detail_unmasks": mask.get("detail_unmasks"),
        "policy": mask.get("policy"),
        "maps_to_path": "PATH-MASK-REDACTION",
        "decode_assist": cfg.get("decode_assist"),
        "primary_warehouse": primary,
        "alignment": (
            "Frida L4 mask fields ↔ ASUNMEE bill_*/shipping_*/customer.* — cùng redaction, "
            "không unmask bằng AES hay fromBase64. "
            f"Kho chính ASUMEE id={primary_wh}: SĐT kho CLEAR / PII đơn MASK."
        ),
    }


def lookup_warehouse(warehouse_id: str) -> dict[str, Any]:
    """Tra cứu kho theo UUID + thống kê đơn cache + ánh xạ unmask."""
    wid = (warehouse_id or "").strip()
    cfg: dict[str, Any] = {}
    if ASUNMEE_CFG.is_file():
        cfg = json.loads(ASUNMEE_CFG.read_text(encoding="utf-8"))
    wh_meta = next(
        (w for w in (cfg.get("warehouses") or []) if str(w.get("id")) == wid),
        {"id": wid},
    )

    cache_paths = [
        ROOT / "docker" / "nginx-order" / "orders_buucuc_scan_cache.json",
        REPORTS / "scan_buucuc_orders.json",
        REPORTS / "asunmee_orders_last3d.json",
    ]
    matched: list[dict] = []
    source = None
    for cand in cache_paths:
        if not cand.is_file():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        rows = data if isinstance(data, list) else data.get("orders") or data.get("data") or []
        if not isinstance(rows, list):
            continue
        hit = [o for o in rows if isinstance(o, dict) and str(o.get("warehouse_id")) == wid]
        if hit:
            matched = hit
            source = str(cand)
            break

    st: Counter = Counter()
    ph: Counter = Counter()
    for o in matched:
        st[str(o.get("status_name") or o.get("status") or "?")] += 1
        phone = o.get("customer_phone") or o.get("bill_phone_number")
        if not phone:
            ph["MISSING"] += 1
        elif "*" in str(phone):
            ph["MASKED"] += 1
        else:
            ph["OK"] += 1

    unmask = (wh_meta.get("unmask_map") if isinstance(wh_meta, dict) else None) or {
        "warehouse_phone": "PATH-CLEAR",
        "customer_pii": "PATH-MASK-REDACTION",
    }
    return {
        "ok": True,
        "checked_at": utc_now(),
        "warehouse_id": wid,
        "warehouse": wh_meta,
        "shop": cfg.get("shop"),
        "orders_source": source,
        "orders_n": len(matched),
        "status": dict(st),
        "phone_class": dict(ph),
        "unmask_map": unmask,
        "verdict": (
            f"Kho {wh_meta.get('name') or wid}: id={wid} · đơn={len(matched)} · "
            f"phone={dict(ph)} · unmask warehouse_phone={unmask.get('warehouse_phone')} "
            f"customer_pii={unmask.get('customer_pii')}"
        ),
        "next_actions": [
            "PII đơn MASK → fetch_unmasked / --asunmee --live",
            "SĐT kho CLEAR — dùng trực tiếp cho liên hệ kho",
            "python3 scripts/inner_unmask_deep_mapper.py --warehouse " + wid,
        ],
    }


def load_or_decrypt_inner(
    *,
    plaintext_path: str | None = None,
    bundle_path: str | None = None,
    key_b64: str | None = None,
    key_file: str | None = None,
) -> tuple[dict | None, dict]:
    """Trả (inner_dict, l0_meta)."""
    meta: dict[str, Any] = {"layer": "L0"}

    if plaintext_path:
        p = Path(plaintext_path)
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            meta.update({"source": "plaintext_file", "path": str(p), "decrypt_ok": None})
            return data if isinstance(data, dict) else None, meta

    bundles: list[Path] = []
    if bundle_path:
        bundles = [Path(bundle_path)]
    else:
        bundles = find_frida_aes_bundles()
        # prefer uploads
        bundles = sorted(
            bundles,
            key=lambda x: (0 if "uploads" in str(x) else 1, -x.stat().st_mtime if x.is_file() else 0),
        )

    if not bundles:
        # fall back to existing plaintext report
        fallback = REPORTS / "frida_a11y_aes_plaintext.json"
        if fallback.is_file():
            data = json.loads(fallback.read_text(encoding="utf-8"))
            meta.update({"source": "reports_plaintext", "path": str(fallback)})
            return data if isinstance(data, dict) else None, meta
        meta["error"] = "Không có Frida bundle / plaintext"
        return None, meta

    b = bundles[0]
    key_info = resolve_aes_key_b64(key_b64, key_file)
    meta.update(
        {
            "source": "frida_decrypt",
            "path": str(b),
            "key_ok": key_info.get("ok"),
            "key_source": key_info.get("source"),
        }
    )
    dec = decrypt_frida_a11y_bundle(b, key_b64=key_b64, key_file=key_file)
    meta["decrypt_ok"] = dec.get("ok")
    meta["verdict"] = dec.get("verdict")
    meta["integrity"] = dec.get("integrity")
    meta["inner_unmask_assist_summary"] = {
        k: (dec.get("inner_unmask_assist") or {}).get(k)
        for k in (
            "masked_n",
            "b64_decodes_to_mask",
            "b64_decodes_to_clear",
            "verdict",
            "action",
        )
    }
    if not dec.get("ok"):
        meta["error"] = dec.get("error") or dec.get("need")
        # try plaintext leftover
        fb = REPORTS / "frida_a11y_aes_plaintext.json"
        if fb.is_file():
            data = json.loads(fb.read_text(encoding="utf-8"))
            meta["fallback_plaintext"] = str(fb)
            return data if isinstance(data, dict) else None, meta
        return None, meta

    plain_path = REPORTS / "frida_a11y_aes_plaintext.json"
    if plain_path.is_file():
        data = json.loads(plain_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None, meta
    return None, meta


def build_mermaid(inv: dict, by_path_id: Counter, matrix_n: int) -> str:
    enc = ((inv.get("L2") or {}).get("encoding") or {}) if inv else {}
    l3 = inv.get("L3") or {}
    return "\n".join(
        [
            "```mermaid",
            "flowchart TB",
            "  L0[L0 Frida AES-GCM]",
            "  L1[L1 Plaintext envelope]",
            "  L2[L2 call/backend/encoding/response]",
            f"  L3[L3 preview={l3.get('preview_masked_n')} detail={l3.get('details_masked_n')}]",
            "  L4[L4 field nodes display/b64/sha]",
            "  L5[L5 unmask assist paths]",
            "  L0 --> L1 --> L2 --> L3 --> L4 --> L5",
            f"  L2 -. mode={enc.get('mode')} .-> L4",
            f"  L5 --> M[PATH-MASK n={by_path_id.get('PATH-MASK-REDACTION', 0)}]",
            f"  L5 --> E[PATH-ENCODING/CLEAR n={by_path_id.get('PATH-ENCODING', 0) + by_path_id.get('PATH-CLEAR', 0)}]",
            f"  L5 --> X[matrix cells={matrix_n}]",
            "```",
        ]
    )


def build_report(
    *,
    plaintext_path: str | None = None,
    bundle_path: str | None = None,
    key_b64: str | None = None,
    key_file: str | None = None,
    warehouse_id: str | None = None,
) -> dict[str, Any]:
    if warehouse_id and not plaintext_path and not bundle_path:
        # Warehouse-focused lookup (+ optional deep if plaintext exists)
        wh = lookup_warehouse(warehouse_id)
        # Still attach deep summary from existing plaintext if available
        deep = None
        try:
            plain = REPORTS / "frida_a11y_aes_plaintext.json"
            if plain.is_file():
                deep = build_report(plaintext_path=str(plain))
        except Exception:  # noqa: BLE001
            deep = None
        try:
            from realtime_icon_feedback_mapper import feedback_line

            icons = ["cube", "lock", "key", "monitor"]
            fb = feedback_line(icons, f"kho {warehouse_id[:8]}… × unmask map")
        except Exception:  # noqa: BLE001
            icons, fb = ["cube", "lock"], "kho → unmask"
        return {
            "ok": True,
            "query": f"Tra cứu warehouse_id {warehouse_id}",
            "checked_at": utc_now(),
            "mode": "warehouse_lookup",
            "warehouse_lookup": wh,
            "deep_inner_summary": {
                "ok": (deep or {}).get("ok"),
                "stats": (deep or {}).get("stats"),
                "verdict": (deep or {}).get("verdict"),
            }
            if deep
            else None,
            "icon_feedback": fb,
            "icon_chant": " → ".join(icons),
            "verdict": f"{wh.get('verdict')} | {fb}",
            "next_actions": wh.get("next_actions") or [],
            "policy": {
                "mask_not_decryptable": True,
                "warehouse_phone_clear": True,
                "aes_unwrap_ne_pii_unmask": True,
            },
        }

    inner, l0 = load_or_decrypt_inner(
        plaintext_path=plaintext_path,
        bundle_path=bundle_path,
        key_b64=key_b64,
        key_file=key_file,
    )

    try:
        from realtime_icon_feedback_mapper import feedback_line

        icons = ["cube", "lock", "key", "text", "hash", "monitor"]
        fb = feedback_line(icons, "mapper truy vấn sâu lớp trong × unmask assist")
    except Exception:  # noqa: BLE001
        icons, fb = ["cube", "lock", "key", "text"], "deep inner → lock → key → text"

    if not isinstance(inner, dict):
        return {
            "ok": False,
            "query": "Mapper truy vấn sâu toàn diện lớp bên trong — hỗ trợ giải mã unmask",
            "checked_at": utc_now(),
            "L0": l0,
            "error": l0.get("error") or "Không load được inner envelope",
            "verdict": f"❌ Deep inner mapper thất bại: {l0.get('error')}",
            "icon_feedback": fb,
        }

    inv = layer_inventory(inner)
    # Deep walk full tree
    field_hits = deep_walk_envelope(inner)
    # Also explicit walk details_masked.detail.* envelopes
    resp = inner.get("response") or {}
    for d in resp.get("details_masked") or []:
        if not isinstance(d, dict):
            continue
        detail = d.get("detail")
        if isinstance(detail, dict):
            field_hits.extend(
                deep_walk_envelope(detail, f"response.details_masked[id={d.get('id')}].detail")
            )

    # de-dupe by path
    seen: set[str] = set()
    uniq: list[dict] = []
    for h in field_hits:
        p = h.get("path") or ""
        if p in seen:
            continue
        seen.add(p)
        uniq.append(h)
    field_hits = uniq

    by_path_id: Counter = Counter(h.get("path_id") for h in field_hits)
    by_encoding = Counter(h.get("encoding_declared") or "(none)" for h in field_hits)
    by_b64_result = Counter(h.get("b64_result") or "(none)" for h in field_hits)
    matrix = map_path_matrix(field_hits)

    # Layer assist map
    layer_map = [
        {
            **LAYER_DEFS[0],
            "status": "ok" if l0.get("decrypt_ok") else ("skip" if l0.get("source") == "plaintext_file" else "need_key"),
            "detail": l0,
            "unmask": "AES unwrap only — không mở PII",
        },
        {
            **LAYER_DEFS[1],
            "status": "ok",
            "keys": inv.get("L1_keys"),
            "unmask": "Envelope JSON sẵn sàng phân tích",
        },
        {
            **LAYER_DEFS[2],
            "status": "ok",
            "encoding_mode": ((inv.get("L2") or {}).get("encoding") or {}).get("mode"),
            "backend": (inv.get("L2") or {}).get("backend"),
            "unmask": "mode=mask ⇒ mọi PII đã redaction trước khi bọc AES",
        },
        {
            **LAYER_DEFS[3],
            "status": "ok",
            "stats": inv.get("L3"),
            "unmask": "preview/details chứa field nodes L4",
        },
        {
            **LAYER_DEFS[4],
            "status": "ok",
            "field_nodes": len([h for h in field_hits if h.get("layer") == "L4"]),
            "by_encoding": dict(by_encoding),
            "unmask": "display/b64/sha — b64 thường = encode(mask)",
        },
        {
            **LAYER_DEFS[5],
            "status": "ok",
            "by_path_id": dict(by_path_id),
            "unmask": "Ánh xạ action hỗ trợ giải mã unmask theo path_id",
        },
    ]

    asunmee = asunmee_layer_bridge()
    mermaid = build_mermaid(inv, by_path_id, len(matrix))

    mask_n = int(by_path_id.get("PATH-MASK-REDACTION") or 0)
    b64_mask = int(by_b64_result.get("decodes_to_mask") or 0)
    clear_n = int(by_path_id.get("PATH-CLEAR") or 0) + int(by_b64_result.get("decodes_to_clear") or 0)

    report = {
        "ok": True,
        "query": "Mapper truy vấn sâu toàn diện lớp bên trong để phân tích và ánh xạ hỗ trợ giải mã unmask",
        "checked_at": utc_now(),
        "layers": LAYER_DEFS,
        "L0": l0,
        "inventory": inv,
        "layer_map": layer_map,
        "field_hits": field_hits[:200],
        "field_hits_total": len(field_hits),
        "matrix": matrix,
        "stats": {
            "by_path_id": dict(by_path_id),
            "by_encoding_declared": dict(by_encoding),
            "by_b64_result": dict(by_b64_result),
            "mask_n": mask_n,
            "b64_to_mask": b64_mask,
            "clear_n": clear_n,
        },
        "asunmee_bridge": asunmee,
        "warehouse_lookup": (
            lookup_warehouse(warehouse_id)
            if warehouse_id
            else (
                lookup_warehouse(str((asunmee.get("primary_warehouse") or {}).get("id")))
                if (asunmee.get("primary_warehouse") or {}).get("id")
                else None
            )
        ),
        "mermaid": mermaid,
        "icon_feedback": fb,
        "icon_chant": " → ".join(icons),
        "policy": {
            "mask_not_decryptable": True,
            "aead_requires_owned_key": True,
            "aes_unwrap_ne_pii_unmask": True,
            "no_bruteforce": True,
        },
        "verdict": (
            f"Deep inner mapper: L0={'OK' if l0.get('decrypt_ok') or l0.get('source') in {'plaintext_file', 'reports_plaintext'} else 'FAIL'} "
            f"→ L4 nodes={len(field_hits)} · MASK={mask_n} · b64→mask={b64_mask} · clear={clear_n}. "
            f"Ánh xạ unmask: hầu hết PATH-MASK-REDACTION (fetch_unmasked). "
            f"AES≠unmask PII. {fb}"
        ),
        "next_actions": [
            "L0: đảm bảo MAPPER_AES_KEY_B64 trong secrets (đã có thì OK)",
            "L4/L5 MASK: không fromBase64 kỳ vọng SĐT đầy đủ — b64→mask",
            "Unmask thật: export CS / JWT full-PII owned / AEAD nội bộ trước khi mask",
            "ASUNMEE: python3 scripts/crypto_decode_assist.py --asunmee --live",
            "Unmask assist: python3 scripts/crypto_decode_assist.py --unmask --frida-aes FILE",
            "Chạy lại: python3 scripts/inner_unmask_deep_mapper.py",
        ],
        "modules": [
            {"id": "inner_unmask_deep_mapper", "file": "scripts/inner_unmask_deep_mapper.py"},
            {"id": "crypto_decode_assist", "role": "AES decrypt + detect_and_decode"},
            {"id": "unmask_redaction_crypto_mapper", "role": "atlas path catalog"},
            {"id": "asunmee_shop_decode", "file": "config/asunmee_shop_decode.json"},
        ],
    }
    return report


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🧊 MAPPER TRUY VẤN SÂU — LỚP TRONG × UNMASK")
    L(f"Lúc: {report.get('checked_at')}")
    L(report.get("verdict") or report.get("error") or "")
    L("")
    L(f"✨ {report.get('icon_feedback')}")
    L(f"Policy: {report.get('policy')}")
    L("")
    L("=== L0 (AES bundle) ===")
    l0 = report.get("L0") or {}
    L(f"· source={l0.get('source')} decrypt_ok={l0.get('decrypt_ok')} key={l0.get('key_source')}")
    L(f"· path={l0.get('path')}")
    if l0.get("verdict"):
        L(f"· {l0.get('verdict')}")
    L("")
    L("=== Layer map L0→L5 ===")
    for layer in report.get("layer_map") or []:
        L(
            f"· {layer.get('id')} {layer.get('name')}: status={layer.get('status')} "
            f"— {layer.get('unmask')}"
        )
    inv = report.get("inventory") or {}
    L("")
    L("=== Inventory L2/L3 ===")
    L(f"· encoding={((inv.get('L2') or {}).get('encoding'))}")
    L(f"· backend={((inv.get('L2') or {}).get('backend'))}")
    L(f"· L3={inv.get('L3')}")
    st = report.get("stats") or {}
    L("")
    L("=== Stats L4/L5 ===")
    L(f"· field_hits={report.get('field_hits_total')} by_path_id={st.get('by_path_id')}")
    L(f"· by_encoding={st.get('by_encoding_declared')}")
    L(f"· by_b64_result={st.get('by_b64_result')}")
    L("")
    L("=== Matrix path_norm × path_id ===")
    for m in (report.get("matrix") or [])[:25]:
        L(
            f"· {m.get('path_norm')}: {m.get('path_id')} ×{m.get('count')} "
            f"→ {m.get('mapper_action')}"
        )
        for s in m.get("samples") or []:
            L(f"    sample display={s.get('display')!r} b64_result={s.get('b64_result')}")
    L("")
    L("=== Sample field hits ===")
    for h in (report.get("field_hits") or [])[:12]:
        L(
            f"· [{h.get('layer')}] {h.get('path')}: path_id={h.get('path_id')} "
            f"display={h.get('display')!r} b64={h.get('b64_result')}"
        )
    asu = report.get("asunmee_bridge") or {}
    L("")
    L("=== ASUNMEE bridge ===")
    L(f"· shop={asu.get('shop')} → {asu.get('maps_to_path')}")
    L(f"· {asu.get('alignment')}")
    whl = report.get("warehouse_lookup") or report.get("mode") and report.get("warehouse_lookup")
    if report.get("mode") == "warehouse_lookup":
        whl = report.get("warehouse_lookup")
    if whl:
        L("")
        L("=== Warehouse lookup ===")
        L(f"· {whl.get('verdict')}")
        L(f"· warehouse={whl.get('warehouse')}")
        L(f"· orders_n={whl.get('orders_n')} status={whl.get('status')} phone={whl.get('phone_class')}")
        L(f"· unmask_map={whl.get('unmask_map')}")
    if report.get("mermaid"):
        L("")
        L(report["mermaid"])
    L("")
    L("Next:")
    for a in report.get("next_actions") or []:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": REPORTS / "inner_unmask_deep_mapper.json",
        "txt": REPORTS / "inner_unmask_deep_mapper.txt",
    }
    paths["json"].write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    paths["txt"].write_text(format_text(report) + "\n", encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Deep inner-layer mapper × unmask assist")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--plaintext", default="", help="Frida plaintext JSON (đã giải)")
    ap.add_argument("--bundle", default="", help="Frida AES bundle để decrypt")
    ap.add_argument("--warehouse", default="", help="Tra cứu warehouse_id (UUID kho ASUMEE)")
    ap.add_argument("--key-b64", default="")
    ap.add_argument("--key-file", default="")
    args = ap.parse_args()

    report = build_report(
        plaintext_path=args.plaintext or None,
        bundle_path=args.bundle or None,
        key_b64=args.key_b64 or None,
        key_file=args.key_file or None,
        warehouse_id=args.warehouse or None,
    )
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
