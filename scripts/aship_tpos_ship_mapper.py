#!/usr/bin/env python3
"""Mapper Aship ↔ TPOS ship (owned tokenShip / ConfigId).

Ống:
  secrets/aship_tpos_ship.env
    → aship.tpos.vn/odata (ShippingProviderConfigs)
    → aship-v2.tpos.app/api/v1 (probe; may 500)
    → tracking.aship.app SSR (ViettelPost|BEST) — không cần API key
    → map ConfigId carrier → buucuc tip

Không dump-login Acc_all · không commit secrets.
"""

from __future__ import annotations

import argparse
import html as htmlmod
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
PIPE_DB = REPORTS / "kho_buucuc_pipe.db"
SHIP_ENV = SECRETS / "aship_tpos_ship.env"
STATE = SECRETS / "aship_tpos_ship.state.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for path in (
        SHIP_ENV,
        SECRETS / "order_session.env",
        SECRETS / "backend_pipes.env",
        SECRETS / "telegram.env",
        ROOT / ".env",
    ):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def mask(s: str | None, keep: int = 4) -> str | None:
    if not s:
        return None
    s = str(s)
    if len(s) <= keep * 2:
        return "***"
    return s[:keep] + "…" + s[-2:]


def http(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: Any = None,
    timeout: int = 30,
) -> tuple[int, Any]:
    data = None
    if body is not None:
        data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return int(resp.status), json.loads(raw.decode() or "null")
            except json.JSONDecodeError:
                return int(resp.status), raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp else b""
        try:
            return int(e.code), json.loads(raw.decode() or "null")
        except Exception:
            return int(e.code), raw.decode("utf-8", "replace")[:400]
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)[:200]}


def decode_b64_caesar(enc: str, shift: int = -11) -> str:
    import base64

    raw = base64.b64decode(enc).decode()
    out = []
    for ch in raw:
        if "a" <= ch <= "z":
            out.append(chr((ord(ch) - ord("a") + shift) % 26 + ord("a")))
        elif "A" <= ch <= "Z":
            out.append(chr((ord(ch) - ord("A") + shift) % 26 + ord("A")))
        else:
            out.append(ch)
    return "".join(out)


def scrape_tracking(provider_code: str, provider: str) -> dict[str, Any]:
    url = (
        "https://tracking.aship.app/order?"
        + urllib.parse.urlencode(
            {"provider_code": provider_code, "provider": provider}
        )
    )
    st, body = http(url, headers={"Accept": "text/html", "User-Agent": "Mozilla/5.0"})
    if st != 200 or not isinstance(body, str):
        return {"ok": False, "http": st, "url": url}
    texts = [
        htmlmod.unescape(t.strip())
        for t in re.findall(r">([^<]{2,100})<", body)
        if t.strip() and not re.search(r"[{}]|function|var |https?:", t)
    ]
    status = None
    for i, t in enumerate(texts):
        if t.strip().lower().startswith("trạng thái") and i + 1 < len(texts):
            # sometimes next is empty and history has status
            pass
    # history entries often appear after "Lịch sử vận đơn"
    hist: list[str] = []
    if "Lịch sử vận đơn" in texts:
        i = texts.index("Lịch sử vận đơn")
        hist = texts[i + 1 : i + 12]
    order_code = None
    carrier = None
    for i, t in enumerate(texts):
        if t == "Mã đơn hàng:" and i + 1 < len(texts):
            order_code = texts[i + 1]
        if t == "Đối tác giao hàng:" and i + 1 < len(texts):
            carrier = texts[i + 1]
        if t == "Mã vận đơn:" and i + 1 < len(texts):
            provider_code = texts[i + 1]
        if t == "Trạng thái:" and i + 1 < len(texts):
            status = texts[i + 1]
    return {
        "ok": True,
        "http": st,
        "url": url,
        "order_code": order_code,
        "carrier": carrier,
        "provider_code": provider_code,
        "status": status,
        "history_head": hist[:8],
    }


def build_report(*, track_code: str | None = None, track_provider: str = "ViettelPost") -> dict[str, Any]:
    env = load_env()
    token = (env.get("ASHIP_TOKEN_SHIP") or env.get("TPOS_ACCESS_TOKEN") or "").strip()
    base = (env.get("ASHIP_BASE_URL") or env.get("TPOS_BASE_URL") or "https://aship.tpos.vn").rstrip("/")
    v2 = (env.get("ASHIP_V2_API") or "https://aship-v2.tpos.app/api/v1").rstrip("/")
    cfg = (env.get("ASHIP_CONFIG_ID") or env.get("TPOS_SHOP_ID") or "").strip()
    user = (env.get("ASHIP_USER") or env.get("TPOS_USER") or "").strip()
    vtp_cfg = (env.get("ASHIP_CARRIER_VTP_CONFIG_ID") or "").strip()
    best_cfg = (env.get("ASHIP_CARRIER_BEST_KONTUM_CONFIG_ID") or "").strip()

    b64 = "c2VlYWQ6Ly9sZHN0YS5lYXpkLmd5"
    decoded = decode_b64_caesar(b64)

    # OData catalog
    st_od, od = http(f"{base}/odata", headers={"Accept": "application/json"})
    entities = []
    if isinstance(od, dict):
        entities = [x.get("name") for x in (od.get("value") or []) if isinstance(x, dict)]

    st_cfg, cfg_body = http(
        f"{base}/odata/ShippingProviderConfigs?$top=100",
        headers={
            "Accept": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    providers: list[dict[str, Any]] = []
    if isinstance(cfg_body, dict):
        for r in cfg_body.get("value") or []:
            if not isinstance(r, dict):
                continue
            providers.append(
                {
                    "id": r.get("Id"),
                    "provider": r.get("Provider"),
                    "description": r.get("Description"),
                    "keys": [
                        c.get("Key")
                        for c in (r.get("Configs") or [])
                        if isinstance(c, dict)
                    ],
                }
            )

    # v2 probes
    v2_probes = []
    for path in ("/app-user/init", "/shop", "/shops", "/sign-in/password"):
        if path == "/sign-in/password":
            continue
        st, body = http(
            f"{v2}{path}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}" if token else "",
                "Content-Type": "application/json",
            },
        )
        v2_probes.append(
            {
                "path": path,
                "http": st,
                "body_preview": (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))[
                    :120
                ],
            }
        )

    # phamthuhoa tenant check
    st_ph, ph_body = http(
        "https://phamthuhoa.tpos.vn/odata",
        headers={"Accept": "application/json"},
    )

    # tracking SSR
    code = track_code or "TPO1408375976"
    track = scrape_tracking(code, track_provider)

    # pipe: TPO* / Viettel / BEST counts
    pipe_stats: dict[str, Any] = {"orders": 0, "tpo_like": 0, "vtp": 0, "best": 0}
    if PIPE_DB.is_file():
        conn = sqlite3.connect(str(PIPE_DB))
        pipe_stats["orders"] = int(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
        pipe_stats["tpo_like"] = int(
            conn.execute(
                "SELECT COUNT(*) FROM orders WHERE tracking_code LIKE 'TPO%'"
            ).fetchone()[0]
        )
        pipe_stats["vtp"] = int(
            conn.execute(
                "SELECT COUNT(*) FROM orders WHERE upper(coalesce(buucuc,'')) LIKE '%VIETTEL%' "
                "OR upper(coalesce(carrier,'')) LIKE '%VIETTEL%' OR buucuc='VTP'"
            ).fetchone()[0]
        )
        pipe_stats["best"] = int(
            conn.execute(
                "SELECT COUNT(*) FROM orders WHERE upper(coalesce(buucuc,'')) LIKE '%BEST%' "
                "OR upper(coalesce(carrier,'')) LIKE '%BEST%'"
            ).fetchone()[0]
        )
        conn.close()

    known = {
        cfg: "ASHIP_CONFIG_ID (user)",
        vtp_cfg: "Viettel Post",
        best_cfg: "BEST KON TUM",
    }
    known = {k: v for k, v in known.items() if k}
    in_catalog = {
        kid: any(str(p.get("id")) == kid for p in providers) for kid in known
    }

    report: dict[str, Any] = {
        "ok": True,
        "module": "aship_tpos_ship_mapper",
        "checked_at": utc_now(),
        "policy": "owned secrets only · gitignored · no Acc_all login · mask tokens",
        "atlas": "tokenShip → aship.tpos.vn OData / aship-v2 / tracking.aship SSR → buucuc",
        "secrets_present": {
            "token_ship": bool(token),
            "user": bool(user),
            "config_id": bool(cfg),
            "env_file": SHIP_ENV.is_file(),
        },
        "identity": {
            "user": user or None,
            "config_id": cfg or None,
            "token_ship_masked": mask(token),
            "base_url": base,
            "v2_api": v2,
            "b64": b64,
            "b64_decoded": decoded,
        },
        "carrier_config_ids": {
            "user": cfg or None,
            "viettel_post": vtp_cfg or None,
            "best_kontum": best_cfg or None,
            "in_aship_public_catalog": in_catalog,
        },
        "odata": {
            "http": st_od,
            "entities": entities,
            "shipping_provider_configs_http": st_cfg,
            "shipping_provider_configs": providers,
            "providers_count": Counter(p.get("provider") for p in providers),
        },
        "aship_v2_probes": v2_probes,
        "phamthuhoa_tenant": {
            "http": st_ph,
            "body": ph_body if isinstance(ph_body, dict) else str(ph_body)[:160],
            "status": (
                "expired_payment_required"
                if st_ph == 402
                else ("ok" if st_ph == 200 else f"http_{st_ph}")
            ),
        },
        "tracking_ssr": track,
        "pipe": pipe_stats,
        "verdict": (
            f"Aship ship: OData configs={len(providers)} · "
            f"user ConfigId in catalog={in_catalog.get(cfg)} · "
            f"phamthuhoa={('EXPIRED' if st_ph==402 else st_ph)} · "
            f"SSR track={'OK' if track.get('ok') else 'FAIL'} · "
            f"pipe TPO*={pipe_stats['tpo_like']}"
        ),
        "next": [
            "ConfigId VTP/BEST/user là id phía shop — không trùng catalog public aship.tpos.vn",
            "Enrich đơn pipe bằng tracking.aship.app SSR (provider=ViettelPost|BEST)",
            "phamthuhoa.tpos.vn hết hạn >3 tháng — cần gia hạn tenant hoặc shop TPOS khác còn sống",
            "aship-v2 cần phoneNumber + tenant đúng (ConfigId ≠ tenantId)",
            "python3 scripts/aship_tpos_ship_mapper.py --notify",
        ],
    }
    return report


def format_text(report: dict[str, Any]) -> str:
    L: list[str] = []
    A = L.append
    idn = report.get("identity") or {}
    A("🚢 MAPPER ASHIP ↔ TPOS SHIP")
    A(f"Lúc: {report.get('checked_at')}")
    A(f"Verdict: {report.get('verdict')}")
    A(f"Atlas: {report.get('atlas')}")
    A("")
    A("=== Owned identity (masked) ===")
    A(f"  user={idn.get('user')} · configId={idn.get('config_id')}")
    A(f"  tokenShip={idn.get('token_ship_masked')} · base={idn.get('base_url')}")
    A(f"  b64→ {idn.get('b64_decoded')}")
    A("")
    A("=== Carrier ConfigId ===")
    cc = report.get("carrier_config_ids") or {}
    A(f"  user: {cc.get('user')} · in_catalog={ (cc.get('in_aship_public_catalog') or {}).get(cc.get('user')) }")
    A(f"  ViettelPost: {cc.get('viettel_post')} · in_catalog={ (cc.get('in_aship_public_catalog') or {}).get(cc.get('viettel_post')) }")
    A(f"  BEST KON TUM: {cc.get('best_kontum')} · in_catalog={ (cc.get('in_aship_public_catalog') or {}).get(cc.get('best_kontum')) }")
    A("")
    od = report.get("odata") or {}
    A(f"=== aship.tpos.vn OData (http={od.get('http')}) ===")
    A(f"  entities: {', '.join(od.get('entities') or [])}")
    A(f"  ShippingProviderConfigs http={od.get('shipping_provider_configs_http')} n={len(od.get('shipping_provider_configs') or [])}")
    for p in od.get("shipping_provider_configs") or []:
        A(f"    · {p.get('provider')} id={p.get('id')}")
    A("")
    A("=== aship-v2 probes ===")
    for p in report.get("aship_v2_probes") or []:
        A(f"  · {p.get('path')}: http={p.get('http')} {str(p.get('body_preview') or '')[:80]}")
    ph = report.get("phamthuhoa_tenant") or {}
    A("")
    A(f"=== phamthuhoa.tpos.vn ===")
    A(f"  status={ph.get('status')} http={ph.get('http')} body={ph.get('body')}")
    tr = report.get("tracking_ssr") or {}
    A("")
    A("=== tracking.aship.app SSR ===")
    A(f"  ok={tr.get('ok')} order={tr.get('order_code')} carrier={tr.get('carrier')} code={tr.get('provider_code')}")
    A(f"  status={tr.get('status')} hist={tr.get('history_head')}")
    A(f"  url={tr.get('url')}")
    pipe = report.get("pipe") or {}
    A("")
    A(f"=== Pipe === orders={pipe.get('orders')} TPO*={pipe.get('tpo_like')} VTP={pipe.get('vtp')} BEST={pipe.get('best')}")
    A("")
    for n in report.get("next") or []:
        A(f"Next: {n}")
    return "\n".join(L)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "aship_tpos_ship_mapper.json"
    tp = REPORTS / "aship_tpos_ship_mapper.txt"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    STATE.write_text(
        json.dumps(
            {
                "updated_at": report.get("checked_at"),
                "verdict": report.get("verdict"),
                "carrier_config_ids": report.get("carrier_config_ids"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"json": str(jp), "txt": str(tp)}


def notify_telegram(text: str) -> list[int]:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_CHAT_ID") or ""
    if not token or not chat:
        return []
    statuses: list[int] = []
    for i in range(0, min(len(text), 10500), 3500):
        chunk = text[i : i + 3500]
        body = json.dumps({"chat_id": chat, "text": chunk}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            statuses.append(resp.status)
    return statuses


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mapper Aship ↔ TPOS ship")
    ap.add_argument("--track", help="provider_code để scrape SSR")
    ap.add_argument("--provider", default="ViettelPost")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)
    report = build_report(track_code=args.track, track_provider=args.provider)
    paths = write_outputs(report)
    text = format_text(report)
    if args.notify:
        try:
            report["telegram"] = notify_telegram(text)
            write_outputs(report)
        except Exception as e:  # noqa: BLE001
            report["telegram_error"] = str(e)[:160]
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else text)
    print(f"\nWrote: {paths['txt']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
