#!/usr/bin/env python3
"""Đấu nối ngay ống dẫn đơn từ sàn TMDT (owned-only).

Bước:
  1) OMS interconnect (probe + ingest local)
  2) Quét Pancake ALL shops (primary+secondary+api_key) → pipe
  3) Quét SPX local (thanhcoong.xlsx) → pipe
  4) Rebuild kho_buucuc_pipe.db
  5) Re-audit TMDT pipes → Telegram

Sàn thiếu token (Lazada/TikTok/Tiki/Sendo/Shopee API): ghi blocked, không bịa cred.
Không dump-login.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
PY = sys.executable


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for path in (
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
    try:
        from owned_credentials import env_overlay_from_owned

        env = env_overlay_from_owned(env)
    except Exception:  # noqa: BLE001
        pass
    return env


def run_step(name: str, argv: list[str], *, timeout: int = 600) -> dict[str, Any]:
    t0 = utc_now()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **{k: v for k, v in load_env().items() if v}},
        )
        out = (proc.stdout or "")[-4000:]
        err = (proc.stderr or "")[-1500:]
        return {
            "step": name,
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "started": t0,
            "cmd": " ".join(argv),
            "stdout_tail": out[-2000:],
            "stderr_tail": err[-800:] if err else "",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "step": name,
            "ok": False,
            "error": str(e)[:300],
            "trace": traceback.format_exc()[-500:],
            "started": t0,
            "cmd": " ".join(argv),
        }


def blocked_marketplaces(env: dict[str, str]) -> list[dict[str, Any]]:
    need = [
        ("shopee_api", "Shopee seller API", ["SHOPEE_ACCESS_TOKEN", "SHOPEE_TOKEN"]),
        ("spx_api", "SPX partner API", ["SPX_TOKEN"]),
        ("lazada", "Lazada VN", ["LAZADA_TOKEN", "LAZADA_APP_KEY"]),
        ("tiktokshop", "TikTok Shop VN", ["TIKTOK_SHOP_TOKEN"]),
        ("tiki", "Tiki", ["TIKI_TOKEN"]),
        ("sendo", "Sendo", ["SENDO_TOKEN"]),
    ]
    out = []
    for mid, name, keys in need:
        present = {k: bool((env.get(k) or "").strip()) for k in keys}
        if not any(present.values()):
            out.append(
                {
                    "id": mid,
                    "name": name,
                    "status": "blocked_missing_owned_token",
                    "need": keys,
                    "env_status": present,
                }
            )
    return out


def build_report(*, days: int = 7, limit: int = 10000) -> dict[str, Any]:
    env = load_env()
    steps: list[dict[str, Any]] = []

    # 1) OMS interconnect
    steps.append(
        run_step(
            "oms_interconnect",
            [PY, "scripts/oms_interconnect.py", "--once", "--notify"],
            timeout=180,
        )
    )

    # 2) Pancake ALL shops → pipe
    steps.append(
        run_step(
            "scan_pancake_all_shops",
            [
                PY,
                "scripts/scan_buucuc_orders.py",
                "--backend",
                "Pancake",
                "--days",
                str(days),
                "--limit",
                str(limit),
                "--notify",
            ],
            timeout=900,
        )
    )

    # 3) SPX local (thanhcoong) → pipe
    steps.append(
        run_step(
            "scan_spx_local",
            [
                PY,
                "scripts/scan_buucuc_orders.py",
                "--backend",
                "SPX",
                "--days",
                str(days),
                "--limit",
                str(limit),
                "--notify",
            ],
            timeout=180,
        )
    )

    # 4) GHN nếu có token (có thể auth_fail — vẫn thử)
    if (env.get("GHN_API_TOKEN") or "").strip():
        steps.append(
            run_step(
                "scan_ghn",
                [
                    PY,
                    "scripts/scan_buucuc_orders.py",
                    "--backend",
                    "GHN",
                    "--days",
                    str(days),
                    "--limit",
                    str(min(limit, 2000)),
                ],
                timeout=180,
            )
        )

    # 5) Rebuild pipe DB
    steps.append(
        run_step(
            "rebuild_pipe_db",
            [PY, "scripts/order_pipe_kho_buucuc_db.py", "--no-cycle", "--limit", str(limit)],
            timeout=300,
        )
    )

    # 6) Re-audit TMDT
    steps.append(
        run_step(
            "tmdt_audit",
            [PY, "scripts/tmdt_order_pipe_audit_mapper.py", "--notify"],
            timeout=120,
        )
    )

    audit = {}
    audit_path = REPORTS / "tmdt_order_pipe_audit.json"
    if audit_path.is_file():
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            audit = {}

    oms = {}
    oms_path = REPORTS / "oms_interconnect.json"
    if oms_path.is_file():
        try:
            oms = json.loads(oms_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            oms = {}

    blocked = blocked_marketplaces(env)
    ok_steps = sum(1 for s in steps if s.get("ok"))
    channels = oms.get("channels") or []
    connected = [c.get("backend") for c in channels if c.get("status") == "connected"]

    report: dict[str, Any] = {
        "ok": ok_steps == len(steps) or ok_steps >= 3,
        "module": "tmdt_pipe_connect",
        "checked_at": utc_now(),
        "policy": "owned-only · no dump-login · no fake tokens",
        "atlas": "Đấu nối TMDT: OMS → scan Pancake/SPX → pipe DB → audit",
        "steps": [
            {
                "step": s.get("step"),
                "ok": s.get("ok"),
                "returncode": s.get("returncode"),
                "error": s.get("error"),
                "cmd": s.get("cmd"),
            }
            for s in steps
        ],
        "oms_channels_connected": connected,
        "oms_verdict": oms.get("verdict"),
        "tmdt_audit_verdict": audit.get("verdict"),
        "tmdt_stats": audit.get("stats"),
        "tmdt_live": audit.get("live"),
        "tmdt_missing_marketplace": audit.get("missing_marketplace"),
        "blocked_need_token": blocked,
        "verdict": (
            f"🔌 Đấu nối TMDT: steps_ok={ok_steps}/{len(steps)} · "
            f"OMS connected={len(connected)} · "
            f"blocked_token={len(blocked)} · "
            f"{(audit.get('verdict') or '')[:120]}"
        ),
        "next": [
            "Gắn SHOPEE_ACCESS_TOKEN / SPX_TOKEN owned để mở API Shopee/SPX",
            "Gắn LAZADA_TOKEN / TIKTOK_SHOP_TOKEN / TIKI_TOKEN / SENDO_TOKEN nếu có",
            "GHN auth_fail → refresh GHN_API_TOKEN owned",
            "python3 scripts/tmdt_pipe_connect.py --notify",
            "python3 scripts/tmdt_order_pipe_audit_mapper.py --notify",
        ],
        "step_details": steps,
    }
    return report


def format_text(report: dict[str, Any]) -> str:
    L: list[str] = []
    A = L.append
    A("🔌 ĐẤU NỐI NGAY · ỐNG ĐƠN SÀN TMDT")
    A(f"Lúc: {report.get('checked_at')}")
    A(f"Verdict: {report.get('verdict')}")
    A(f"Atlas: {report.get('atlas')}")
    A("")
    A("=== Steps ===")
    for s in report.get("steps") or []:
        mark = "✅" if s.get("ok") else "❌"
        A(f"  {mark} {s.get('step')} rc={s.get('returncode')}")
        if s.get("error"):
            A(f"      err: {s.get('error')}")
    A("")
    A(f"OMS channels: {', '.join(report.get('oms_channels_connected') or []) or '(none)'}")
    if report.get("oms_verdict"):
        A(f"OMS: {report.get('oms_verdict')}")
    if report.get("tmdt_audit_verdict"):
        A(f"Audit: {report.get('tmdt_audit_verdict')}")
    st = report.get("tmdt_stats") or {}
    if st:
        A(
            f"TMDT stats: marketplace_live="
            f"{st.get('marketplace_with_orders')}/{st.get('marketplace_total')} · "
            f"SPX26*={st.get('spx26_tracking')} · total={st.get('orders_total')}"
        )
    A("")
    A("=== Live sau đấu nối ===")
    for x in report.get("tmdt_live") or []:
        A(f"  ✅ {x.get('name')} · đơn={x.get('orders')}")
    A("")
    A("=== Blocked — cần token owned ===")
    for b in report.get("blocked_need_token") or []:
        A(f"  🔒 {b.get('name')}: cần {', '.join(b.get('need') or [])}")
    miss = report.get("tmdt_missing_marketplace") or []
    if miss:
        A("")
        A("=== Marketplace vẫn chưa có đơn trong pipe ===")
        for m in miss:
            A(f"  ❌ {m.get('name')}")
    A("")
    for n in report.get("next") or []:
        A(f"Next: {n}")
    return "\n".join(L)


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    # strip heavy step_details from json on disk? keep but ok
    slim = {k: v for k, v in report.items() if k != "step_details"}
    slim["step_details_n"] = len(report.get("step_details") or [])
    jp = REPORTS / "tmdt_pipe_connect.json"
    tp = REPORTS / "tmdt_pipe_connect.txt"
    jp.write_text(json.dumps(slim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    (SECRETS / "tmdt_pipe_connect.state.json").write_text(
        json.dumps(
            {
                "updated_at": report.get("checked_at"),
                "verdict": report.get("verdict"),
                "steps": report.get("steps"),
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
        if not chunk.strip():
            continue
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
    ap = argparse.ArgumentParser(description="Đấu nối ngay ống đơn sàn TMDT")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--limit", type=int, default=10000)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args(argv)

    report = build_report(days=args.days, limit=args.limit)
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
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
