#!/usr/bin/env python3
"""Frida + Accessibility (owned) → GHN_API_TOKEN → gọi đơn.

Áp dụng capture Frida kết hợp Accessibility trên thiết bị/app GHN sở hữu:
  1) nhận export (JSON / text / printA5 / frida-a11y-offline-aes)
  2) trích Token UUID / printA5
  3) nhúng GHN_API_TOKEN (ghn_cookie_ingest / printA5)
  4) gọi đơn (ghn_access_token_orders)

Owned-only · no dump-login · không bypass SSL / không exploit app lạ.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
CAPTURE_PENDING = SECRETS / "frida_a11y_ghn.pending.json"

UUID_RE = re.compile(
    r"(?i)\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)
PRINT_A5_RE = re.compile(
    r"https?://[^\s\"'<>]*ghn\.vn[^\s\"'<>]*printA5[^\s\"'<>]*token=([0-9a-f-]{36})",
    re.I,
)
DUMP_MARKERS = (
    "acc_all",
    "stealer",
    "internal_search",
    "valid_accounts",
    "results_cookies",
    "ghn_tokens_",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mask(tok: str | None) -> str | None:
    if not tok:
        return None
    t = tok.strip()
    if len(t) <= 12:
        return "***"
    return f"{t[:8]}…{t[-4:]}(len={len(t)})"


def _reject_dump(blob: str) -> str | None:
    low = (blob or "").lower()
    for m in DUMP_MARKERS:
        if m in low:
            return f"rejected_dump_marker:{m}"
    # bulk user:pass:token lines
    lines = [ln for ln in (blob or "").splitlines() if ":" in ln and UUID_RE.search(ln)]
    if len(lines) > 3:
        return "rejected_bulk_credential_lines"
    return None


def _walk_strings(obj: Any, *, depth: int = 0) -> list[str]:
    if depth > 8:
        return []
    out: list[str] = []
    if isinstance(obj, str):
        if obj.strip():
            out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.strip():
                out.append(k)
            out.extend(_walk_strings(v, depth=depth + 1))
    elif isinstance(obj, (list, tuple)):
        for v in obj[:200]:
            out.extend(_walk_strings(v, depth=depth + 1))
    return out


def extract_candidates(payload: Any, *, source_hint: str = "") -> dict[str, Any]:
    """Trích printA5 / Token UUID từ capture Frida+a11y / AES plaintext / raw."""
    candidates: list[dict[str, str]] = []
    printA5_urls: list[str] = []
    shop_id: str | None = None
    texts: list[str] = []

    if isinstance(payload, str):
        texts.append(payload)
    elif isinstance(payload, dict):
        shop_id = (
            str(payload.get("shop_id") or payload.get("GHN_SHOP_ID") or "").strip() or None
        )
        for key in ("printA5", "print_a5", "url", "printA5_url"):
            v = payload.get(key)
            if isinstance(v, str) and "printA5" in v:
                printA5_urls.append(v.strip())
                texts.append(v)
        for key in ("token", "Token", "ghn_api_token", "GHN_API_TOKEN", "access_token"):
            v = payload.get(key)
            if isinstance(v, str) and UUID_RE.fullmatch(v.strip()):
                candidates.append({"token": v.strip(), "source": f"field:{key}"})
        headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
        for hk in ("Token", "token", "Authorization", "authorization"):
            hv = headers.get(hk)
            if isinstance(hv, str):
                m = UUID_RE.search(hv)
                if m:
                    candidates.append({"token": m.group(1), "source": f"headers.{hk}"})
        a11y = payload.get("a11y") if isinstance(payload.get("a11y"), dict) else {}
        clip = a11y.get("clipboard")
        if isinstance(clip, str) and clip.strip():
            texts.append(clip)
        nodes = a11y.get("nodes_text") or a11y.get("nodes") or []
        if isinstance(nodes, list):
            for n in nodes[:100]:
                if isinstance(n, str):
                    texts.append(n)
                elif isinstance(n, dict):
                    texts.extend(_walk_strings(n)[:20])
        # nested frida / response / meta
        texts.extend(_walk_strings(payload.get("frida")))
        texts.extend(_walk_strings(payload.get("raw")))
        texts.extend(_walk_strings(payload.get("text")))
        # full walk last (capped)
        texts.extend(_walk_strings(payload)[:400])
    else:
        texts.append(str(payload))

    blob = "\n".join(texts)
    reject = _reject_dump(blob)
    if reject:
        return {
            "ok": False,
            "error": reject,
            "candidates": [],
            "printA5_urls": [],
            "shop_id": shop_id,
            "source_hint": source_hint,
        }

    for m in PRINT_A5_RE.finditer(blob):
        url = m.group(0)
        tok = m.group(1)
        printA5_urls.append(url)
        candidates.append({"token": tok, "source": "printA5_url"})
    for m in UUID_RE.finditer(blob):
        tok = m.group(1)
        # skip obvious non-token contexts later via probe
        candidates.append({"token": tok, "source": source_hint or "text_uuid"})

    # dedupe preserve order
    seen: set[str] = set()
    uniq: list[dict[str, str]] = []
    for c in candidates:
        t = c["token"].lower()
        if t in seen:
            continue
        seen.add(t)
        uniq.append(c)

    # prefer printA5 / field / headers
    def rank(c: dict[str, str]) -> int:
        s = c.get("source") or ""
        if s.startswith("printA5"):
            return 0
        if s.startswith("field:") or s.startswith("headers"):
            return 1
        if "a11y" in s:
            return 2
        return 3

    uniq.sort(key=rank)
    printA5_urls = list(dict.fromkeys(printA5_urls))
    return {
        "ok": bool(uniq),
        "candidates": uniq[:12],
        "printA5_urls": printA5_urls[:5],
        "shop_id": shop_id,
        "source_hint": source_hint,
        "candidates_n": len(uniq),
    }


def load_capture(path: Path | str | None = None, *, raw: str | None = None) -> dict[str, Any]:
    """Nạp capture Frida+a11y (file JSON/text hoặc raw).

    Auto (không path/raw): ghn_session.raw → pending (nếu có token) → AES bundle.
    """
    out: dict[str, Any] = {
        "ok": False,
        "kind": None,
        "path": str(path) if path else None,
        "payload": None,
        "error": None,
    }
    if raw and raw.strip():
        text = raw.strip()
        try:
            out["payload"] = json.loads(text)
            out["kind"] = "json_raw"
        except json.JSONDecodeError:
            out["payload"] = text
            out["kind"] = "text_raw"
        out["ok"] = True
        return out

    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    else:
        session_raw = SECRETS / "ghn_session.raw"
        if session_raw.is_file():
            candidates.append(session_raw)
        if CAPTURE_PENDING.is_file():
            candidates.append(CAPTURE_PENDING)
        try:
            from crypto_decode_assist import find_frida_aes_bundles

            candidates.extend(find_frida_aes_bundles()[:2])
        except Exception:  # noqa: BLE001
            pass

    if not candidates:
        out["error"] = "Thiếu capture file / --raw / secrets/frida_a11y_ghn.pending.json"
        return out

    last_err = "Không thấy file capture"
    for p in candidates:
        out["path"] = str(p)
        if not p.is_file():
            last_err = f"Không thấy file: {p}"
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        name = p.name.lower()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            if PRINT_A5_RE.search(text) or UUID_RE.search(text):
                out["payload"] = text
                out["kind"] = "text_file"
                out["ok"] = True
                return out
            last_err = f"File không phải JSON/token: {p.name}"
            continue

        if "offline-aes" in name or (
            isinstance(data, dict)
            and isinstance(data.get("aes"), dict)
            and data["aes"].get("ciphertext_b64")
        ):
            kind = "frida_a11y_offline_aes"
        elif isinstance(data, dict) and (
            data.get("source") in {"frida+a11y", "frida_a11y", "a11y+frida"}
            or data.get("a11y")
            or data.get("printA5")
        ):
            kind = "frida_a11y_capture"
        else:
            kind = "json_file"

        peek = extract_candidates(data if kind != "frida_a11y_offline_aes" else text, source_hint=kind)
        # Ưu tiên file có Token/printA5; AES để sau cùng
        if kind != "frida_a11y_offline_aes" and not peek.get("ok"):
            last_err = f"{p.name}: không có Token/printA5 GHN"
            continue

        out["payload"] = data
        out["kind"] = kind
        out["ok"] = True
        return out

    out["ok"] = False
    out["error"] = last_err
    return out


def decrypt_aes_if_needed(loaded: dict[str, Any]) -> dict[str, Any]:
    """Nếu bundle AES → giải bằng key owned → payload plaintext."""
    if loaded.get("kind") != "frida_a11y_offline_aes":
        return {"ok": True, "skipped": True, "payload": loaded.get("payload")}
    from crypto_decode_assist import decrypt_frida_a11y_bundle

    dec = decrypt_frida_a11y_bundle(loaded["path"])
    if not dec.get("ok"):
        return {
            "ok": False,
            "skipped": False,
            "decrypt": {
                "ok": False,
                "verdict": dec.get("verdict"),
                "need": dec.get("need"),
                "error": dec.get("error"),
            },
            "payload": loaded.get("payload"),
        }
    # prefer parsed plaintext file
    plain_path = REPORTS / "frida_a11y_aes_plaintext.json"
    payload: Any = None
    if plain_path.is_file():
        try:
            payload = json.loads(plain_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = plain_path.read_text(encoding="utf-8", errors="ignore")
    return {
        "ok": True,
        "skipped": False,
        "decrypt": {
            "ok": True,
            "verdict": dec.get("verdict"),
            "outputs": dec.get("outputs"),
            "integrity": dec.get("integrity"),
        },
        "payload": payload if payload is not None else loaded.get("payload"),
    }


def stage_pending(payload: Any, *, kind: str | None = None) -> Path | None:
    """Chỉ stage khi có Token/printA5 — không ghi đè bằng AES Pancake."""
    if kind == "frida_a11y_offline_aes":
        return None
    peek = extract_candidates(payload, source_hint=kind or "stage")
    if not peek.get("ok"):
        return None
    SECRETS.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        CAPTURE_PENDING.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    else:
        CAPTURE_PENDING.write_text(str(payload) + "\n", encoding="utf-8")
    try:
        import os

        os.chmod(CAPTURE_PENDING, 0o600)
    except OSError:
        pass
    return CAPTURE_PENDING


def apply_capture(
    *,
    path: str | Path | None = None,
    raw: str | None = None,
    days: int = 3,
    limit: int = 50,
    fetch_orders: bool = True,
    force: bool = True,
    shop_id: str | None = None,
) -> dict[str, Any]:
    """Frida+a11y capture → access token → (optional) gọi đơn GHN."""
    report: dict[str, Any] = {
        "ok": False,
        "module": "frida_a11y_ghn_bridge",
        "checked_at": utc_now(),
        "policy": {
            "owned_only": True,
            "no_dump_login": True,
            "frida_plus_a11y": True,
            "no_ssl_bypass": True,
        },
        "verdict": "",
        "next": [],
    }

    loaded = load_capture(path, raw=raw)
    report["load"] = {
        "ok": loaded.get("ok"),
        "kind": loaded.get("kind"),
        "path": loaded.get("path"),
        "error": loaded.get("error"),
    }
    if not loaded.get("ok"):
        report["verdict"] = f"❌ {loaded.get('error')}"
        report["next"] = [
            "Export capture owned: cp config/frida_a11y_capture.example.json secrets/frida_a11y_ghn.pending.json",
            "hoặc: --raw 'https://online-gateway.ghn.vn/a5/public-api/printA5?token=UUID'",
            "python3 scripts/frida_a11y_ghn_bridge.py apply --orders",
        ]
        return report

    # stage for ensure/retry
    try:
        staged = stage_pending(loaded.get("payload"), kind=loaded.get("kind"))
        report["staged"] = str(staged) if staged else None
    except Exception as e:  # noqa: BLE001
        report["staged_error"] = str(e)[:120]

    dec = decrypt_aes_if_needed(loaded)
    report["aes"] = dec.get("decrypt") or {"skipped": dec.get("skipped")}
    payload = dec.get("payload") if dec.get("ok") else loaded.get("payload")
    if not dec.get("ok") and loaded.get("kind") == "frida_a11y_offline_aes":
        report["verdict"] = (
            "❌ Frida a11y AES bundle — thiếu MAPPER_ICON_AES_KEY_B64 để giải, "
            "hoặc dán printA5/Token owned trực tiếp"
        )
        report["next"] = [
            "Điền MAPPER_ICON_AES_KEY_B64 vào secrets/backend_pipes.env",
            "python3 scripts/crypto_decode_assist.py --frida-aes FILE",
            "hoặc apply --raw '<printA5 owned>'",
        ]
        return report

    extracted = extract_candidates(payload, source_hint=str(loaded.get("kind") or "capture"))
    report["extract"] = {
        "ok": extracted.get("ok"),
        "candidates_n": extracted.get("candidates_n"),
        "candidates_masked": [
            {"token": _mask(c["token"]), "source": c["source"]}
            for c in (extracted.get("candidates") or [])[:8]
        ],
        "printA5_n": len(extracted.get("printA5_urls") or []),
        "printA5_preview": (extracted.get("printA5_urls") or [None])[0],
        "error": extracted.get("error"),
    }
    if extracted.get("error"):
        report["verdict"] = f"❌ {extracted.get('error')} — từ chối dump"
        return report
    if not extracted.get("ok"):
        report["verdict"] = (
            "❌ Capture Frida+a11y không có Token UUID / printA5 "
            "(thường là envelope đơn Pancake mask — không phải GHN session)"
        )
        report["next"] = [
            "Trên app GHN owned: export printA5 hoặc header Token qua Frida+a11y",
            "Điền config/frida_a11y_capture.example.json → secrets/frida_a11y_ghn.pending.json",
            "python3 scripts/frida_a11y_ghn_bridge.py apply --file … --orders",
        ]
        return report

    shop = shop_id or extracted.get("shop_id")
    print_urls = extracted.get("printA5_urls") or []
    chosen = (extracted.get("candidates") or [])[0]
    token = chosen["token"]
    report["token"] = {
        "masked": _mask(token),
        "source": chosen.get("source"),
        "from_printA5": bool(print_urls),
    }

    # Prefer printA5 path when URL present
    ingest_report: dict[str, Any]
    orders_report: dict[str, Any] | None = None
    if print_urls:
        from ghn_access_token_orders import from_printA5

        orders_report = from_printA5(
            print_urls[0],
            days=days,
            limit=limit,
            force=force,
            resolve_shop=True,
        )
        ingest_report = orders_report.get("ingest") or {"ok": orders_report.get("ok")}
        report["ingest"] = ingest_report
        report["orders"] = orders_report.get("orders")
        report["shop_id"] = orders_report.get("shop_id") or shop
        report["ok"] = bool(orders_report.get("ok"))
        report["verdict"] = (
            f"Frida+a11y → printA5 → token · {orders_report.get('verdict')}"
        )
        report["next"] = orders_report.get("next") or []
        report["bridge"] = "frida+a11y→printA5→GHN_API_TOKEN→orders"
    else:
        from ghn_cookie_ingest import ingest
        from ghn_access_token_orders import fetch_orders, resolve_shop_id

        ingest_report = ingest(f"token={token}", shop_id=shop, force=force)
        report["ingest"] = {
            "ok": ingest_report.get("ok"),
            "verdict": ingest_report.get("verdict"),
            "probe": ingest_report.get("probe"),
        }
        alive = bool((ingest_report.get("probe") or {}).get("success"))
        shop_info = {"shop_id": shop, "ok": bool(shop)}
        if alive:
            shop_info = resolve_shop_id(token, shop_id=shop, persist=True)
        report["shop"] = shop_info
        report["shop_id"] = shop_info.get("shop_id") or shop

        if fetch_orders:
            orders = fetch_orders(
                token=token,
                shop_id=report["shop_id"],
                days=days,
                limit=limit,
            )
            report["orders"] = {
                "status": orders.get("status"),
                "fetched": orders.get("fetched"),
                "detail": orders.get("detail"),
                "preview": orders.get("orders_preview"),
                "attempts": orders.get("attempts"),
            }
            status = orders.get("status")
            if status == "auth_fail" or not alive:
                report["verdict"] = (
                    f"❌ Frida+a11y → token auth_fail · {_mask(token)} · "
                    f"{orders.get('detail') or (ingest_report.get('probe') or {}).get('message')}"
                )
                report["next"] = [
                    "Capture Token/printA5 owned còn hạn từ app GHN (Frida+a11y)",
                    "python3 scripts/frida_a11y_ghn_bridge.py apply --raw '<printA5>' --orders",
                ]
            else:
                report["ok"] = True
                report["verdict"] = (
                    f"✅ Frida+a11y → access token → gọi đơn · "
                    f"fetched={orders.get('fetched')} · {_mask(token)}"
                )
                report["next"] = [
                    "python3 scripts/ghn_access_token_orders.py run --days 3 --limit 50",
                    "python3 scripts/scan_buucuc_orders.py --backends GHN --days 3",
                ]
        else:
            report["ok"] = bool(ingest_report.get("ok") and alive)
            report["verdict"] = ingest_report.get("verdict") or "ingest done"
            report["bridge"] = "frida+a11y→GHN_API_TOKEN"
        report["bridge"] = report.get("bridge") or "frida+a11y→token→orders"

    write_outputs(report)
    return report


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in report.items() if k != "order_rows"}
    jp = REPORTS / "frida_a11y_ghn_bridge.json"
    tp = REPORTS / "frida_a11y_ghn_bridge.txt"
    jp.write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp)}


def format_text(report: dict[str, Any]) -> str:
    lines = [
        "📦 FRIDA + ACCESSIBILITY → GHN ACCESS TOKEN → ĐƠN",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
    ]
    load = report.get("load") or {}
    if load:
        lines.append(f"load: kind={load.get('kind')} path={load.get('path')}")
    ex = report.get("extract") or {}
    if ex:
        lines.append(
            f"extract: candidates={ex.get('candidates_n')} printA5={ex.get('printA5_n')} "
            f"preview={ex.get('printA5_preview') or '—'}"
        )
        for c in ex.get("candidates_masked") or []:
            lines.append(f"  · {c.get('token')} · {c.get('source')}")
    tok = report.get("token") or {}
    if tok:
        lines.append(f"token: {tok.get('masked')} · source={tok.get('source')}")
    orders = report.get("orders") or {}
    if orders:
        lines.append(
            f"orders: status={orders.get('status')} fetched={orders.get('fetched')} · "
            f"{orders.get('detail') or ''}"
        )
    for n in report.get("next") or []:
        lines.append(f"Next: {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Áp dụng Frida + Accessibility → GHN_API_TOKEN → gọi đơn (owned)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ex = sub.add_parser("extract", help="Chỉ trích Token/printA5 từ capture")
    p_ex.add_argument("--file", "-f", default="", help="Capture JSON/text")
    p_ex.add_argument("--raw", default="", help="Chuỗi printA5 / Token trực tiếp")
    p_ex.add_argument("--json", action="store_true")

    p_ap = sub.add_parser("apply", help="Capture → nhúng token → (optional) gọi đơn")
    p_ap.add_argument("--file", "-f", default="", help="Capture JSON/text / AES bundle")
    p_ap.add_argument("--raw", default="", help="printA5 URL hoặc token=UUID")
    p_ap.add_argument("--orders", action="store_true", help="Gọi đơn sau khi nhúng token")
    p_ap.add_argument("--no-orders", action="store_true", help="Chỉ nhúng token")
    p_ap.add_argument("--days", type=int, default=3)
    p_ap.add_argument("--limit", type=int, default=50)
    p_ap.add_argument("--shop-id", default="")
    p_ap.add_argument("--no-force", action="store_true")
    p_ap.add_argument("--json", action="store_true")

    p_st = sub.add_parser("status", help="Báo cáo bridge gần nhất")
    p_st.add_argument("--json", action="store_true")

    p_now = sub.add_parser("now", help="Dùng Frida ngay: pending/AES → token → gọi đơn")
    p_now.add_argument("--file", "-f", default="", help="Capture (mặc định pending/AES mới nhất)")
    p_now.add_argument("--raw", default="", help="printA5 / Token trực tiếp")
    p_now.add_argument("--days", type=int, default=3)
    p_now.add_argument("--limit", type=int, default=50)
    p_now.add_argument("--shop-id", default="")
    p_now.add_argument("--notify", action="store_true", help="Gửi verdict lên Telegram")
    p_now.add_argument("--json", action="store_true")

    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        jp = REPORTS / "frida_a11y_ghn_bridge.json"
        if not jp.is_file():
            print("Chưa có báo cáo — chạy: python3 scripts/frida_a11y_ghn_bridge.py now")
            return 1
        data = json.loads(jp.read_text(encoding="utf-8"))
        print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else data.get("verdict"))
        return 0 if data.get("ok") else 1

    if args.cmd == "extract":
        loaded = load_capture(args.file or None, raw=(args.raw or None))
        if not loaded.get("ok"):
            print(loaded.get("error"), file=sys.stderr)
            return 2
        dec = decrypt_aes_if_needed(loaded)
        payload = dec.get("payload") if dec.get("ok") else loaded.get("payload")
        extracted = extract_candidates(payload, source_hint=str(loaded.get("kind") or ""))
        report = {
            "ok": extracted.get("ok"),
            "load": {"kind": loaded.get("kind"), "path": loaded.get("path")},
            "aes": dec.get("decrypt") or {"skipped": dec.get("skipped")},
            "extract": extracted,
            "checked_at": utc_now(),
            "verdict": (
                f"✅ candidates={extracted.get('candidates_n')}"
                if extracted.get("ok")
                else f"❌ {extracted.get('error') or 'không có token'}"
            ),
        }
        if args.json:
            # mask tokens in extract for stdout
            safe = json.loads(json.dumps(report, default=str))
            cands = (safe.get("extract") or {}).get("candidates") or []
            for c in cands:
                if "token" in c:
                    c["token"] = _mask(c["token"])
            print(json.dumps(safe, ensure_ascii=False, indent=2))
        else:
            print(report["verdict"])
            for c in (extracted.get("candidates") or [])[:8]:
                print(f"  · {_mask(c['token'])} · {c.get('source')}")
        return 0 if report.get("ok") else 1

    # apply | now
    fetch = True
    if args.cmd == "apply":
        if args.no_orders:
            fetch = False
        elif args.orders:
            fetch = True
    report = apply_capture(
        path=(args.file or None),
        raw=(getattr(args, "raw", None) or None),
        days=int(getattr(args, "days", 3)),
        limit=int(getattr(args, "limit", 50)),
        fetch_orders=fetch,
        force=not bool(getattr(args, "no_force", False)),
        shop_id=(getattr(args, "shop_id", None) or None),
    )
    report["mode"] = args.cmd
    if args.cmd == "now":
        report["bridge"] = report.get("bridge") or "frida+a11y→now→orders"
        if getattr(args, "notify", False):
            try:
                from owned_credentials import load_env

                env = load_env(extra_files=(SECRETS / "telegram.env",))
                tg = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
                chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()
                if tg and chat:
                    import urllib.request

                    body = ("🧬 Frida NOW\n\n" + (report.get("verdict") or ""))[:3500]
                    req = urllib.request.Request(
                        f"https://api.telegram.org/bot{tg}/sendMessage",
                        data=json.dumps({"chat_id": chat, "text": body}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        report["notified"] = resp.status == 200
            except Exception as e:  # noqa: BLE001
                report["notify_error"] = str(e)[:120]

    if args.json:
        print(json.dumps({k: v for k, v in report.items() if k != "order_rows"}, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
