#!/usr/bin/env python3
"""Mapper icon — nhận phản hồi truy vấn thời gian thực.

Gắn quân đội icon (cùng vocabulary NETWORK_MAP.iconArmy) vào từng channel /
link OMS + chu kỳ realtime, rồi xuất feedback mapper gọi được.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
OUT = REPORTS / "realtime"

# Đồng bộ tên gọi với data/network-map.js iconArmy (+ alias ops realtime)
ICON_ARMY = {
    "spark": {"call": "Tia Lửa Hub", "role": "hub", "motto": "mở cổng OMS / realtime"},
    "layers": {"call": "Lớp Khiên", "role": "group", "motto": "nhóm nguồn / trạng thái"},
    "key": {"call": "Chìa Khái Niệm", "role": "secret", "motto": "credential / shop key"},
    "lock": {"call": "Ổ Khóa", "role": "auth", "motto": "auth_fail / thiếu quyền"},
    "network": {"call": "Mạch Mạng", "role": "pipe", "motto": "pipe backend / bưu cục"},
    "compass": {"call": "La Bàn Tracking", "role": "track", "motto": "tracking.aship / mã VĐ"},
    "monitor": {"call": "Màn Realtime", "role": "live", "motto": "probe sống / dashboard"},
    "cube": {"call": "Khối Kho", "role": "warehouse", "motto": "warehouse / kho xuất"},
    "wrench": {"call": "Cờ Lê Sự Cố", "role": "error", "motto": "lỗi / sửa ống"},
    "cpu": {"call": "Nhân Sync", "role": "engine", "motto": "realtime_order_sync"},
    "hash": {"call": "Dấu Băm Đơn", "role": "fingerprint", "motto": "fingerprint đơn mới"},
    "text": {"call": "Dòng Phản Hồi", "role": "feedback", "motto": "chuỗi feedback mapper"},
    "code": {"call": "Mã Nguồn Pipe", "role": "local", "motto": "inbox / file local"},
    "chip": {"call": "Chip Kênh", "role": "channel", "motto": "channel id"},
}

# status → icon
STATUS_ICON = {
    "connected": "monitor",
    "alive": "monitor",
    "ok": "monitor",
    "missing_cred": "key",
    "auth_fail": "lock",
    "error": "wrench",
    "stale": "wrench",
    "blocked": "lock",
}

# channel id → lead icon
CHANNEL_ICON = {
    "telegram": "spark",
    "pancake": "layers",
    "ghn": "network",
    "viettelpost": "network",
    "tracking": "compass",
    "tpos": "cpu",
    "direct_api": "code",
    "spx_local": "cube",
    "vnpost_local": "code",
    "oms_bus": "spark",
    "GHN": "network",
    "Pancake": "layers",
    "Telegram": "spark",
    "Telegram+direct_api": "code",
    "TPOS": "cpu",
    "Tracking": "compass",
    "ViettelPost": "network",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def describe(name: str) -> dict:
    meta = ICON_ARMY.get(name) or {"call": name, "role": "unit", "motto": ""}
    return {"name": name, **meta}


def chant(icons: list[str]) -> str:
    return " → ".join(describe(i)["call"] for i in icons)


def feedback_line(icons: list[str], detail: str = "") -> str:
    base = f"Mapper gọi: {chant(icons)}" if icons else "Mapper: chưa có icon"
    return f"{base} — {detail}" if detail else base


def map_channel(ch: dict) -> dict:
    cid = ch.get("id") or ch.get("backend") or "?"
    status = (ch.get("status") or "error").lower()
    lead = CHANNEL_ICON.get(cid) or CHANNEL_ICON.get(ch.get("backend") or "") or "chip"
    st_icon = STATUS_ICON.get(status, "wrench")
    icons = [lead, st_icon]
    if status == "missing_cred":
        icons = [lead, "key", "lock"]
    elif status in {"connected", "alive", "ok"}:
        icons = [lead, "monitor"]
    detail = f"{ch.get('backend') or cid}: {status} · {str(ch.get('detail') or '')[:80]}"
    return {
        "channel": cid,
        "backend": ch.get("backend") or cid,
        "status": status,
        "icons": icons,
        "icon_chant": chant(icons),
        "called": [describe(i) for i in icons],
        "feedback": feedback_line(icons, detail),
    }


def map_link(link: dict) -> dict:
    icons = ["spark", CHANNEL_ICON.get(link.get("to"), "network")]
    if link.get("live"):
        icons.append("monitor")
    else:
        icons.extend(["key", "wrench"])
    detail = f"{link.get('from')}→{link.get('to')} ({link.get('role')}) live={link.get('live')}"
    return {
        "from": link.get("from"),
        "to": link.get("to"),
        "role": link.get("role"),
        "live": bool(link.get("live")),
        "icons": icons,
        "icon_chant": chant(icons),
        "feedback": feedback_line(icons, detail),
    }


def map_realtime_backend(b: dict) -> dict:
    name = b.get("backend") or "?"
    status = (b.get("status") or "").lower()
    lead = CHANNEL_ICON.get(name, "cpu")
    st = STATUS_ICON.get(status, "wrench")
    icons = [lead, st, "hash"] if (b.get("new_orders") or b.get("new")) else [lead, st]
    new_n = len(b.get("new_orders") or []) if isinstance(b.get("new_orders"), list) else int(b.get("new") or 0)
    if new_n:
        icons = [lead, "spark", "hash"]
    detail = f"{name}: {status} new={new_n} · {str(b.get('detail') or '')[:80]}"
    return {
        "backend": name,
        "status": status,
        "new": new_n,
        "icons": icons,
        "icon_chant": chant(icons),
        "feedback": feedback_line(icons, detail),
    }


def build_from_live() -> dict:
    from oms_interconnect import interconnect, load_env
    from realtime_order_sync import run_cycle

    env = load_env()
    oms = interconnect(env, ingest=True)
    rt = run_cycle(env, limit=20, notify=False, notify_new_only=False)

    channel_maps = [map_channel(c) for c in oms.get("channels") or []]
    link_maps = [map_link(x) for x in oms.get("links") or []]
    rt_maps = [map_realtime_backend(b) for b in rt.get("backends") or []]

    # Global chant: hub → live channels → blockers
    global_icons = ["spark", "monitor", "cpu"]
    connected = [c for c in channel_maps if c["status"] in {"connected", "alive", "ok"}]
    blocked = [c for c in channel_maps if c["status"] in {"missing_cred", "auth_fail", "error", "stale"}]
    if connected:
        global_icons.append(CHANNEL_ICON.get(connected[0]["channel"], "monitor"))
    if blocked:
        global_icons.extend(["key", "lock"])
    # warehouse / tracking accents
    if any(c["channel"] in {"spx_local", "direct_api"} for c in connected):
        global_icons.append("cube")
    if any(c["channel"] == "tracking" for c in connected):
        global_icons.append("compass")

    # dedupe preserve order
    seen = set()
    uniq = []
    for i in global_icons:
        if i not in seen:
            seen.add(i)
            uniq.append(i)

    live_links = sum(1 for x in link_maps if x["live"])
    new_orders = int(rt.get("new_count") or 0)

    top_feedback = feedback_line(
        uniq,
        f"OMS {len(connected)}/{len(channel_maps)} connected · links live {live_links}/{len(link_maps)} · "
        f"realtime new={new_orders} · blocked={len(blocked)}",
    )

    paths = []
    for c in channel_maps:
        paths.append(
            {
                "kind": "channel",
                "path": f"OMS-bus → {c['backend']} → {c['status']}",
                "count": 1,
                "icon_chant": c["icon_chant"],
                "feedback": c["feedback"],
            }
        )
    for b in rt_maps:
        paths.append(
            {
                "kind": "realtime_sync",
                "path": f"realtime_order_sync → {b['backend']} → new={b['new']}",
                "count": b["new"] or 1,
                "icon_chant": b["icon_chant"],
                "feedback": b["feedback"],
            }
        )

    report = {
        "ok": True,
        "query": "Mapper icon nhận phản hồi truy vấn thời gian thực",
        "checked_at": utc_now(),
        "icon_army_size": len(ICON_ARMY),
        "global": {
            "icons": uniq,
            "icon_chant": chant(uniq),
            "feedback": top_feedback,
            "called": [describe(i) for i in uniq],
        },
        "channels": channel_maps,
        "links": link_maps,
        "realtime_backends": rt_maps,
        "icon_paths": paths,
        "oms_verdict": oms.get("verdict"),
        "realtime": {
            "checked_at": rt.get("checked_at"),
            "new_count": new_orders,
            "blocked": rt.get("blocked"),
        },
        "orders_summary": oms.get("orders_summary"),
        "verdict": top_feedback,
        "policy": "icon feedback only; secrets-only probes; no dump login",
    }
    return report


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("✨ MAPPER ICON · PHẢN HỒI TRUY VẤN REALTIME")
    L(f"Lúc: {report['checked_at']}")
    L(report["global"]["feedback"])
    L("")
    L(f"Chant: {report['global']['icon_chant']}")
    L("")
    L("=== Channels ===")
    for c in report["channels"]:
        mark = "✅" if c["status"] in {"connected", "alive", "ok"} else "⚠️"
        L(f"{mark} {c['icon_chant']}")
        L(f"   {c['feedback']}")
    L("")
    L("=== Realtime sync ===")
    for b in report["realtime_backends"]:
        L(f"· {b['icon_chant']}")
        L(f"  {b['feedback']}")
    L("")
    L("=== Links live (icon) ===")
    for x in report["links"]:
        if x["live"]:
            L(f"✅ {x['icon_chant']} — {x['from']}→{x['to']}")
    pending = [x for x in report["links"] if not x["live"]]
    L(f"… pending links: {len(pending)}")
    L("")
    L("=== Icon paths (feedback) ===")
    for p in report["icon_paths"][:16]:
        L(f"· [{p['kind']}] {p['path']}")
        L(f"  {p['feedback']}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    text = format_text(report)
    paths = {
        "json": REPORTS / "realtime_icon_feedback.json",
        "txt": REPORTS / "realtime_icon_feedback.txt",
        "rt_json": OUT / "realtime_icon_feedback.json",
        "rt_txt": OUT / "realtime_icon_feedback.txt",
    }
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    paths["rt_json"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["rt_txt"].write_text(text, encoding="utf-8")
    return paths


def attach_feedback_prefix(body: str, report: dict | None = None) -> str:
    """Ghép dòng feedback icon lên đầu phản hồi truy vấn realtime."""
    if report is None:
        try:
            report = build_from_live()
            write_outputs(report)
        except Exception as e:  # noqa: BLE001
            return f"✨ Mapper icon: (probe lỗi: {e})\n\n{body}"
    fb = (report.get("global") or {}).get("feedback") or report.get("verdict") or ""
    chant_s = (report.get("global") or {}).get("icon_chant") or ""
    return f"✨ {fb}\nChant: {chant_s}\n\n{body}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Mapper icon phản hồi realtime")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = build_from_live()
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
