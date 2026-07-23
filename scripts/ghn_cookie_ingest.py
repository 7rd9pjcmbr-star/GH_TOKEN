#!/usr/bin/env python3
"""Nhúng session/token GHN từ cookie Netscape hoặc URL printA5 → secrets.

Nhận:
  - URL: https://online-gateway.ghn.vn/a5/public-api/printA5?token=<uuid>
  - Cookie domain ghn.vn / online-gateway.ghn.vn tên token|Token|auth*|access*
  - UUID / Token=… thuần

Từ chối: hjSession*, _ga*, analytics/marketing (không phải GHN API Token).

Owned-only. Probe province API trước khi ghi GHN_API_TOKEN.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT / "reports" / "telegram-classify"

UUID_RE = re.compile(
    r"(?i)\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)
PRINT_A5_RE = re.compile(
    r"(?i)https?://(?:online-gateway|dev-online-gateway)\.ghn\.vn/[^\s\"']*token=([0-9a-f-]{36})"
)
TOKEN_KV_RE = re.compile(r"(?i)\b(?:token|ghn_api_token|ghn_token)\s*[:=]\s*([0-9a-f-]{36})")

# Cookie names that are NEVER GHN API auth
REJECT_COOKIE_NAMES = {
    "hjsessionuser",
    "hjsession",
    "hjabsoluteessioninprogress",
    "_ga",
    "_gid",
    "_gat",
    "_fbp",
    "_ttp",
    "ttcsid",
    "__maxlead_uuid",
    "__maxlead_visited",
    "cur_sid",
    "device_id",
    "cur_req_ts",
    "last_req_ts",
    "sapisid",
    "account_chooser",
}

# Cookie names that MAY hold API/session token on GHN hosts
ACCEPT_COOKIE_NAMES = {
    "token",
    "ghn_token",
    "ghn_api_token",
    "access_token",
    "auth_token",
    "authorization",
    "apitoken",
    "api_token",
    "shop_token",
}

GHN_HOST_HINT = re.compile(r"(?i)(?:^|\.)ghn\.vn$|online-gateway\.ghn\.vn|dev-online-gateway\.ghn\.vn")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mask(tok: str) -> str:
    t = (tok or "").strip()
    if len(t) <= 12:
        return "***"
    return f"{t[:8]}…{t[-4:]}(len={len(t)})"


def extract_from_text(raw: str) -> dict[str, Any]:
    """Parse printA5 URL / Netscape cookie / Token=uuid → candidates."""
    text = (raw or "").strip()
    candidates: list[dict[str, str]] = []
    cookies_found: dict[str, str] = {}
    rejected: list[dict[str, str]] = []

    for m in PRINT_A5_RE.finditer(text):
        candidates.append({"token": m.group(1), "source": "printA5_url"})

    # bare URLs with ?token=
    for m in re.finditer(r"https?://[^\s\"']+", text):
        url = m.group(0)
        if "ghn.vn" not in url.lower():
            continue
        qs = parse_qs(urlparse(url).query)
        for key in ("token", "Token", "access_token"):
            vals = qs.get(key) or []
            if vals and UUID_RE.fullmatch(vals[0].strip()):
                candidates.append({"token": vals[0].strip(), "source": f"url_query:{key}"})

    for m in TOKEN_KV_RE.finditer(text):
        candidates.append({"token": m.group(1), "source": "kv"})

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\t+", line)
        if len(parts) >= 7:
            domain, name, value = parts[0].strip(), parts[5].strip(), parts[6].strip()
            lname = name.lower().split("_", 1)[0] if name.lower().startswith("hjsession") else name.lower()
            # hjSessionUser_2982605 → reject by prefix
            base = name.lower()
            if base.startswith("hjsession") or base in REJECT_COOKIE_NAMES or any(
                base.startswith(x) for x in ("_ga", "ttcsid", "_onmarketer")
            ):
                rejected.append({"domain": domain, "name": name, "reason": "analytics_or_non_api"})
                continue
            if not GHN_HOST_HINT.search(domain) and "ghn" not in domain.lower():
                # non-GHN netscape line — skip unless name looks like token
                if name.lower() not in ACCEPT_COOKIE_NAMES:
                    continue
            cookies_found[name] = value
            if name.lower() in ACCEPT_COOKIE_NAMES and UUID_RE.fullmatch(value.strip()):
                candidates.append({"token": value.strip(), "source": f"cookie:{name}"})
            continue
        if "=" in line and not line.startswith("http"):
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k.lower() in ACCEPT_COOKIE_NAMES and UUID_RE.fullmatch(v):
                candidates.append({"token": v, "source": f"kv:{k}"})

    # last resort: UUIDs near ghn context
    if not candidates and re.search(r"(?i)ghn|shiip|printa5", text):
        for m in UUID_RE.finditer(text):
            candidates.append({"token": m.group(1), "source": "uuid_near_ghn"})

    # dedupe preserve order
    seen: set[str] = set()
    uniq: list[dict[str, str]] = []
    for c in candidates:
        t = c["token"].lower()
        if t in seen:
            continue
        seen.add(t)
        uniq.append(c)

    return {
        "candidates": uniq,
        "cookies_found": sorted(cookies_found.keys()),
        "rejected_cookies": rejected[:20],
        "chosen": uniq[0] if uniq else None,
    }


def probe_token(token: str) -> dict[str, Any]:
    import requests

    headers = {"Token": token, "Content-Type": "application/json"}
    urls = [
        "https://online-gateway.ghn.vn/shiip/public-api/master-data/province",
        "https://dev-online-gateway.ghn.vn/shiip/public-api/master-data/province",
    ]
    last: dict[str, Any] = {"http": 0, "success": False, "message": "no_attempt", "url": None}
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=20)
            data = r.json() if r.text else {}
            msg = data.get("message") if isinstance(data, dict) else None
            ok = r.status_code == 200 and (
                (isinstance(data, dict) and data.get("code") in (200, 0, None) and data.get("data") is not None)
                or (isinstance(data, dict) and isinstance(data.get("data"), list))
            )
            # GHN success shape: code==200, data=list
            if isinstance(data, dict) and data.get("code") == 200:
                ok = True
            last = {
                "http": r.status_code,
                "success": bool(ok),
                "message": msg or (None if ok else (r.text or "")[:120]),
                "url": url,
                "code": data.get("code") if isinstance(data, dict) else None,
                "provinces_n": len(data.get("data") or [])
                if isinstance(data, dict) and isinstance(data.get("data"), list)
                else None,
            }
            if ok:
                return last
        except Exception as e:  # noqa: BLE001
            last = {"http": 0, "success": False, "message": str(e)[:160], "url": url}
    return last


def apply_token(token: str, *, shop_id: str | None = None) -> dict[str, Any]:
    from access_token_rotate import set_access_token, upsert_env_values

    set_report = set_access_token("GHN", token, shop_id=shop_id)
    extras = {"GHN_API_TOKEN": token}
    if shop_id:
        extras["GHN_SHOP_ID"] = str(shop_id)
    upsert_env_values(extras)
    # refresh consolidated session env
    try:
        from order_session_env import export_session_env

        export_session_env()
    except Exception as e:  # noqa: BLE001
        set_report["session_export_error"] = str(e)[:120]
    return {"set": set_report, "extras_keys": sorted(extras.keys()), "token_masked": _mask(token)}


PENDING_FILES = (
    ROOT / "secrets" / "ghn_session.raw",
    ROOT / "secrets" / "ghn_cookie.pending",
    ROOT / "secrets" / "ghn_ingest.pending",
)
GHN_STATE = ROOT / "secrets" / "ghn_session.state.json"


def _load_env_token() -> tuple[str, str | None]:
    from owned_credentials import load_env

    env = load_env(extra_files=(ROOT / "secrets" / "order_session.env",))
    token = (env.get("GHN_API_TOKEN") or env.get("GHN_TOKEN") or "").strip()
    shop = (env.get("GHN_SHOP_ID") or "").strip() or None
    return token, shop


def ensure_ghn_session(*, try_pending: bool = True) -> dict[str, Any]:
    """Duy trì GHN_API_TOKEN: probe → nếu chết thử nhúng lại từ secrets/*.pending owned.

    Không tự bịa token. printA5/cookie pending phải do chủ sở hữu đặt vào secrets/.
    """
    report: dict[str, Any] = {
        "ok": False,
        "module": "ghn_cookie_ingest.ensure",
        "checked_at": utc_now(),
        "alive": False,
        "reingested": False,
        "token_masked": None,
        "probe": None,
        "pending_tried": [],
        "need": None,
        "verdict": "",
    }

    token, shop_id = _load_env_token()
    if token:
        report["token_masked"] = _mask(token)
        probe = probe_token(token)
        report["probe"] = {k: v for k, v in probe.items()}
        if probe.get("success"):
            report["ok"] = True
            report["alive"] = True
            report["verdict"] = (
                f"✅ GHN token alive · {_mask(token)} · provinces={probe.get('provinces_n')}"
            )
            _save_ghn_state(report)
            write_outputs({**report, "via": "ghn_ensure_probe"})
            return report
        report["need"] = "Token GHN chết/401 — cần printA5 hoặc cookie token owned mới"

    if try_pending:
        for path in PENDING_FILES:
            if not path.is_file():
                continue
            raw = path.read_text(encoding="utf-8", errors="ignore")
            report["pending_tried"].append(path.name)
            if not raw.strip():
                continue
            ing = ingest(raw, shop_id=shop_id, force=False)
            report["pending_ingest"] = {
                "file": path.name,
                "ok": ing.get("ok"),
                "verdict": ing.get("verdict"),
                "chosen_masked": (ing.get("extracted") or {}).get("chosen_masked"),
            }
            if ing.get("ok"):
                # archive pending so we don't re-apply forever
                try:
                    archived = path.with_suffix(path.suffix + f".ok.{int(datetime.now(timezone.utc).timestamp())}")
                    path.rename(archived)
                except OSError:
                    pass
                report["ok"] = True
                report["alive"] = True
                report["reingested"] = True
                report["token_masked"] = (ing.get("apply") or {}).get("token_masked") or (
                    (ing.get("extracted") or {}).get("chosen_masked")
                )
                report["probe"] = ing.get("probe")
                report["verdict"] = f"✅ GHN re-ingest từ {path.name} · {report['token_masked']}"
                _save_ghn_state(report)
                write_outputs({**report, "via": "ghn_ensure_reingest"})
                return report

    if not token:
        report["need"] = (
            "Thiếu GHN_API_TOKEN — đặt printA5/cookie vào secrets/ghn_session.raw "
            "hoặc: python3 scripts/ghn_cookie_ingest.py --raw-file …"
        )
        report["verdict"] = "❌ GHN chưa có token — chưa duy trì được"
    else:
        report["verdict"] = (
            f"❌ GHN token chết (http={(report.get('probe') or {}).get('http')}) — "
            f"{report.get('need')}"
        )

    _save_ghn_state(report)
    write_outputs({**report, "via": "ghn_ensure"})
    return report


def _save_ghn_state(report: dict[str, Any]) -> None:
    GHN_STATE.parent.mkdir(parents=True, exist_ok=True)
    slim = {
        "updated_at": report.get("checked_at"),
        "ok": report.get("ok"),
        "alive": report.get("alive"),
        "reingested": report.get("reingested"),
        "token_masked": report.get("token_masked"),
        "probe": report.get("probe"),
        "verdict": report.get("verdict"),
        "need": report.get("need"),
    }
    GHN_STATE.write_text(json.dumps(slim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        import os

        os.chmod(GHN_STATE, 0o600)
    except OSError:
        pass


def ingest(
    raw: str,
    *,
    shop_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    extracted = extract_from_text(raw)
    chosen = extracted.get("chosen") or {}
    token = (chosen.get("token") or "").strip()

    report: dict[str, Any] = {
        "ok": False,
        "module": "ghn_cookie_ingest",
        "checked_at": utc_now(),
        "via": "ghn_cookie_ingest",
        "extracted": {
            "candidates_n": len(extracted.get("candidates") or []),
            "candidates_masked": [
                {"token": _mask(c["token"]), "source": c["source"]}
                for c in (extracted.get("candidates") or [])[:8]
            ],
            "cookies_found": extracted.get("cookies_found"),
            "rejected_cookies": extracted.get("rejected_cookies"),
            "chosen_source": chosen.get("source") if chosen else None,
            "chosen_masked": _mask(token) if token else None,
        },
        "probe": None,
        "apply": None,
        "policy": {"owned_only": True, "no_dump_login": True, "reject_analytics_cookies": True},
    }

    if extracted.get("rejected_cookies") and not token:
        report["error"] = "Chỉ thấy cookie analytics/Hotjar — không phải GHN API Token"
        report["need"] = (
            "Gửi printA5?token=<uuid> còn hạn hoặc cookie token trên ghn.vn / "
            "GHN_API_TOKEN owned (header Token)"
        )
        report["verdict"] = "❌ Cookie phiên web/analytics — không nhúng được GHN API"
        write_outputs(report)
        return report

    if not token:
        report["error"] = "Không tìm thấy token GHN (UUID)"
        report["need"] = (
            "Dán URL printA5?token=… hoặc Netscape cookie name=token trên *.ghn.vn "
            "hoặc GHN_API_TOKEN=<uuid>"
        )
        report["verdict"] = "❌ Thiếu token GHN — chưa nhúng"
        write_outputs(report)
        return report

    probe = probe_token(token)
    report["probe"] = probe

    if not probe.get("success") and not force:
        report["error"] = probe.get("message") or "probe_failed"
        report["need"] = "Token GHN owned còn hạn (probe province API = 200)"
        report["verdict"] = (
            f"❌ API từ chối token (http={probe.get('http')} · {probe.get('message')}) — "
            f"source={chosen.get('source')} · {_mask(token)} — không ghi đè"
        )
        write_outputs(report)
        return report

    apply = apply_token(token, shop_id=shop_id)
    report["apply"] = apply
    report["ok"] = True
    note = " (force)" if force and not probe.get("success") else ""
    report["verdict"] = (
        f"✅ Đã nhúng GHN_API_TOKEN{note} · source={chosen.get('source')} · "
        f"{_mask(token)} · provinces={probe.get('provinces_n')}"
    )
    write_outputs(report)
    return report


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    OUT.mkdir(parents=True, exist_ok=True)
    jp = OUT / "ghn_cookie_ingest.json"
    tp = OUT / "ghn_cookie_ingest.txt"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp)}


def format_text(report: dict[str, Any]) -> str:
    lines = [
        "📦 GHN COOKIE / SESSION INGEST",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
    ]
    ex = report.get("extracted") or {}
    if ex.get("chosen_masked"):
        lines.append(f"Chosen: {ex.get('chosen_masked')} · source={ex.get('chosen_source')}")
    if ex.get("rejected_cookies"):
        lines.append(f"Rejected analytics cookies: {len(ex['rejected_cookies'])}")
    probe = report.get("probe") or {}
    if probe:
        lines.append(
            f"Probe: http={probe.get('http')} success={probe.get('success')} "
            f"provinces={probe.get('provinces_n')} · {probe.get('message')}"
        )
    if report.get("need"):
        lines.append(f"Need: {report.get('need')}")
    if report.get("error"):
        lines.append(f"Error: {report.get('error')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Nhúng cookie/URL session GHN → GHN_API_TOKEN")
    ap.add_argument(
        "command",
        nargs="?",
        default="ingest",
        choices=["ingest", "ensure", "status"],
        help="ingest (mặc định) | ensure (duy trì) | status",
    )
    ap.add_argument("--raw-file", help="File cookie Netscape / URL printA5")
    ap.add_argument("--raw", help="Chuỗi cookie/URL trực tiếp")
    ap.add_argument("--shop-id", default="", help="GHN_SHOP_ID (optional)")
    ap.add_argument("--force", action="store_true", help="Ghi dù probe fail (không khuyến nghị)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.command == "status":
        if GHN_STATE.is_file():
            state = json.loads(GHN_STATE.read_text(encoding="utf-8"))
            print(json.dumps(state, ensure_ascii=False, indent=2) if args.json else state.get("verdict"))
            return 0 if state.get("ok") else 1
        print("Chưa có ghn_session.state — chạy: python3 scripts/ghn_cookie_ingest.py ensure")
        return 1

    if args.command == "ensure":
        report = ensure_ghn_session(try_pending=True)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_text(report))
        return 0 if report.get("ok") else 1

    raw = args.raw or ""
    if args.raw_file:
        raw = Path(args.raw_file).read_text(encoding="utf-8", errors="ignore")
    if not raw.strip():
        print("Cần --raw hoặc --raw-file (hoặc: ensure)", file=sys.stderr)
        return 2
    report = ingest(raw, shop_id=(args.shop_id or None), force=args.force)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
