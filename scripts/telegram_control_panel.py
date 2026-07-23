#!/usr/bin/env python3
"""
Bảng điều khiển Telegram — truy vấn nguyên nhân lỗi SĐT đơn hàng.
Lệnh / panel inline: tổng quan · theo nguồn · masked · thiếu · todo khắc phục.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from fix_order_phones import load_rows, normalize_vn_phone, remediation_for  # noqa: E402

INBOX = ROOT / "quarantine" / "telegram"
REPORTS = ROOT / "reports" / "telegram-classify" / "phone-fix"
OFFSET_FILE = ROOT / "secrets" / "telegram.panel.offset"


def load_env() -> dict:
    env = dict(os.environ)
    secret = ROOT / "secrets" / "telegram.env"
    if secret.is_file():
        for line in secret.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def api(token: str, method: str, payload: dict | None = None, timeout: int = 35):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send(token: str, chat_id: str, text: str, reply_markup: dict | None = None):
    payload = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return api(token, "sendMessage", payload)


def answer_callback(token: str, callback_id: str, text: str = ""):
    return api(
        token,
        "answerCallbackQuery",
        {"callback_query_id": callback_id, "text": text[:180], "show_alert": False},
    )


def target_csv() -> Path:
    p = INBOX / "orders_detailed_Dang_giao_20260512_120712.csv"
    if p.exists():
        return p
    cands = sorted(INBOX.glob("orders_detailed_*.csv"), key=lambda x: -x.stat().st_mtime)
    return cands[0] if cands else p


def analyze(path: Path) -> dict:
    rows = load_rows(path)
    stats = Counter()
    by_source = defaultdict(Counter)
    by_shop = defaultdict(Counter)
    by_platform = defaultdict(Counter)
    actions = Counter()
    masked_samples = Counter()
    path_missing = Counter()

    for r in rows:
        raw = r.get("customer_phone")
        _, status = normalize_vn_phone(raw)
        src = str(r.get("source") or "(empty)")
        shop = str(r.get("shop_id") or "(empty)")
        plat = str(r.get("platform") or "(empty)")
        st_norm = str(r.get("status_normalized") or r.get("status_raw") or "")
        rem = remediation_for(status, src, plat)
        stats[status] += 1
        by_source[src][status] += 1
        by_shop[shop][status] += 1
        by_platform[plat][status] += 1
        actions[rem["action"]] += 1
        if status == "masked":
            masked_samples[str(raw)] += 1
        if status in {"missing", "masked"}:
            path_missing[f"{src} → {plat} → shop:{shop} → {st_norm} → {status.upper()}"] += 1

    return {
        "file": path.name,
        "records": len(rows),
        "stats": dict(stats),
        "by_source": {k: dict(v) for k, v in by_source.items()},
        "by_shop": {k: dict(v) for k, v in by_shop.items()},
        "by_platform": {k: dict(v) for k, v in by_platform.items()},
        "actions": dict(actions),
        "masked_top": masked_samples.most_common(8),
        "hot_paths": path_missing.most_common(10),
    }


def panel_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Tổng quan", "callback_data": "q:overview"},
                {"text": "🗂 Theo nguồn", "callback_data": "q:source"},
            ],
            [
                {"text": "🕶 SĐT masked", "callback_data": "q:masked"},
                {"text": "📭 SĐT thiếu", "callback_data": "q:missing"},
            ],
            [
                {"text": "🛠 Todo khắc phục", "callback_data": "q:todo"},
                {"text": "🗺 Đường dẫn nóng", "callback_data": "q:paths"},
            ],
            [
                {"text": "🔌 Pipe backend", "callback_data": "q:pipes"},
                {"text": "⏱ Realtime đơn", "callback_data": "q:realtime"},
            ],
            [
                {"text": "🌐 URL mở rộng", "callback_data": "q:urls"},
                {"text": "🗺 Mapper EP", "callback_data": "q:endpoints"},
            ],
            [
                {"text": "🧭 Mapper toàn diện", "callback_data": "q:mapper_full"},
            ],
            [{"text": "🔁 Làm mới phân tích", "callback_data": "q:refresh"}],
        ]
    }


def fmt_overview(a: dict) -> str:
    st = a["stats"]
    n = a["records"] or 1
    lines = [
        "BẢNG ĐIỀU KHIỂN · Tổng quan SĐT",
        f"File: {a['file']}",
        f"Records: {a['records']}",
        "",
        f"OK: {st.get('ok',0)} ({100*st.get('ok',0)/n:.1f}%)",
        f"THIẾU: {st.get('missing',0)} ({100*st.get('missing',0)/n:.1f}%)",
        f"MASKED: {st.get('masked',0)} ({100*st.get('masked',0)/n:.1f}%)",
        f"INVALID: {st.get('invalid',0)} ({100*st.get('invalid',0)/n:.1f}%)",
        "",
        "Kết luận nhanh:",
        "· Thiếu = upstream không ghi customer_phone (Pancake / Telegram upload)",
        "· Masked = API snapshot che PII bằng **** — không phải SĐT ngắn",
    ]
    return "\n".join(lines)


def fmt_source(a: dict) -> str:
    lines = ["BẢNG ĐIỀU KHIỂN · Theo nguồn", ""]
    for src, st in sorted(a["by_source"].items(), key=lambda x: -(x[1].get("missing", 0) + x[1].get("masked", 0))):
        total = sum(st.values()) or 1
        miss = st.get("missing", 0)
        mask = st.get("masked", 0)
        ok = st.get("ok", 0)
        lines.append(f"· {src}")
        lines.append(f"  total={total} ok={ok} missing={miss} masked={mask} miss%={100*miss/total:.1f}")
    return "\n".join(lines)


def fmt_masked(a: dict) -> str:
    lines = [
        "BẢNG ĐIỀU KHIỂN · SĐT MASKED",
        f"Tổng masked: {a['stats'].get('masked',0)}",
        "Nguồn chính: direct_api_orders_snapshot (PII mask)",
        "",
        "Mẫu giá trị (đã che sẵn trong data):",
    ]
    for v, n in a["masked_top"][:8]:
        lines.append(f"· {v} ×{n}")
    lines += [
        "",
        "Nguyên nhân: export/snapshot bật PII redaction.",
        "Khắc phục: tắt mask khi export nội bộ hoặc gọi API gốc lấy phone đầy đủ.",
        "Không khôi phục được chỉ từ chuỗi ****.",
    ]
    return "\n".join(lines)


def fmt_missing(a: dict) -> str:
    lines = [
        "BẢNG ĐIỀU KHIỂN · SĐT THIẾU",
        f"Tổng missing: {a['stats'].get('missing',0)}",
        "",
        "Theo nguồn:",
    ]
    for src, st in sorted(a["by_source"].items(), key=lambda x: -x[1].get("missing", 0)):
        if not st.get("missing"):
            continue
        total = sum(st.values()) or 1
        lines.append(f"· {src}: {st['missing']}/{total} ({100*st['missing']/total:.1f}%)")
    lines += [
        "",
        "Theo shop:",
    ]
    for shop, st in sorted(a["by_shop"].items(), key=lambda x: -x[1].get("missing", 0))[:8]:
        if not st.get("missing"):
            continue
        lines.append(f"· shop:{shop}: missing={st.get('missing',0)} masked={st.get('masked',0)} ok={st.get('ok',0)}")
    lines += [
        "",
        "Nguyên nhân: customer_phone='' từ sync Pancake / template Telegram upload thiếu cột SĐT.",
        "Khắc phục P0: map billing/shipping.phone → customer_phone; reject upload thiếu SĐT.",
    ]
    return "\n".join(lines)


def fmt_todo(a: dict) -> str:
    lines = ["BẢNG ĐIỀU KHIỂN · Todo khắc phục", ""]
    order = [
        ("fix_pancake_sync_mapping", "P0"),
        ("require_phone_on_telegram_upload", "P0"),
        ("fetch_unmasked_from_source_api", "P1"),
        ("backfill_from_oms", "P1"),
        ("manual_validate", "P2"),
    ]
    for action, pri in order:
        n = a["actions"].get(action, 0)
        if not n:
            continue
        lines.append(f"· [{pri}] {action} ×{n}")
    lines += [
        "",
        "Chi tiết file:",
        "reports/telegram-classify/phone-fix/*.phone_fix.todo.json",
        "*.phone_fixed.csv (đã gắn phone_status + hành động)",
    ]
    return "\n".join(lines)


def fmt_paths(a: dict) -> str:
    lines = [
        "BẢNG ĐIỀU KHIỂN · Đường dẫn nóng",
        "Mapper: TRUNG TÂM → NGUỒN → SÀN → SHOP → THIẾU/MASK",
        "",
    ]
    for path, n in a["hot_paths"][:8]:
        lines.append(f"· [{n}] {path}")
    return "\n".join(lines)


def fmt_pipes(_a: dict | None = None) -> str:
    """Đấu nối ống dẫn backend + chống logout (secrets-only)."""
    try:
        from backend_pipe_keepalive import format_report, load_env, load_state, run_once

        env = load_env()
        run_once(env, notify=False)
        state = load_state()
        from backend_pipe_keepalive import PipeResult

        pipes = state.get("pipes") or {}
        results = [
            PipeResult(
                backend=k,
                channel=v.get("channel") or "?",
                status=v.get("status") or "?",
                http=v.get("http"),
                detail=v.get("detail") or "",
                session_risk=bool(v.get("session_risk")),
                checked_at=v.get("checked_at") or "",
            )
            for k, v in pipes.items()
        ]
        return format_report(results, state)
    except Exception as e:  # noqa: BLE001
        return f"Pipe backend lỗi: {e}\nChạy: python3 scripts/backend_pipe_keepalive.py --once --notify"


def fmt_realtime(_a: dict | None = None) -> str:
    try:
        from realtime_order_sync import format_cycle, load_env, run_cycle

        cycle = run_cycle(load_env(), limit=20, notify=False, notify_new_only=False)
        return format_cycle(cycle)
    except Exception as e:  # noqa: BLE001
        return f"Realtime lỗi: {e}\nChạy: python3 scripts/realtime_order_sync.py --once --notify"


def fmt_urls(_a: dict | None = None) -> str:
    path = ROOT / "reports" / "telegram-classify" / "url_paths_expanded.txt"
    alt = ROOT / "reports" / "telegram-classify" / "url_paths_expanded.json"
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        return text[:3800]
    if alt.is_file():
        data = json.loads(alt.read_text(encoding="utf-8"))
        lines = [
            "🌐 URL MỞ RỘNG",
            f"Tổng mention: {data.get('totals', {}).get('url_mentions')}",
            "",
        ]
        for b, n in (data.get("totals", {}).get("by_backend") or {}).items():
            lines.append(f"· {b}: {n}")
        lines.append("")
        for item in (data.get("observed_ranked_top") or [])[:20]:
            lines.append(f"· [{item['count']}] {item['backend']} · {item['url_path']}")
        return "\n".join(lines)
    return "Chưa có báo cáo URL. Chạy lại truy vấn mở rộng URL trên agent."


def fmt_endpoints(_a: dict | None = None) -> str:
    path = ROOT / "reports" / "telegram-classify" / "endpoint_mapper_deep.txt"
    alt = ROOT / "reports" / "telegram-classify" / "endpoint_mapper_deep.json"
    if path.is_file():
        return path.read_text(encoding="utf-8")[:3800]
    if alt.is_file():
        data = json.loads(alt.read_text(encoding="utf-8"))
        lines = [
            f"🗺 MAPPER ENDPOINT — {data.get('endpoint_count')} EP",
            "",
            "Theo role:",
        ]
        for k, v in (data.get("totals", {}).get("by_role") or {}).items():
            lines.append(f"· {k}: {v}")
        lines.append("")
        lines.append("P0/P1:")
        for ep in data.get("endpoints") or []:
            if ep.get("priority") in {"P0", "P1"}:
                lines.append(f"· [{ep['priority']}] {ep['backend']} · {ep['url_path']}")
                lines.append(f"  {ep.get('mapper_node')} → {ep.get('action')}")
        return "\n".join(lines)[:3800]
    return "Chưa có endpoint_mapper_deep. Chạy mapper endpoint trên agent."


def fmt_mapper_full(_a: dict | None = None) -> str:
    path = ROOT / "reports" / "telegram-classify" / "comprehensive_mapper.txt"
    alt = ROOT / "reports" / "telegram-classify" / "comprehensive_mapper.json"
    try:
        from comprehensive_order_mapper import build_report, format_text, write_outputs

        report = build_report()
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        if alt.is_file():
            data = json.loads(alt.read_text(encoding="utf-8"))
            return (
                f"🧭 MAPPER TOÀN DIỆN\n{data.get('verdict')}\n"
                f"pipes={len(data.get('master_pipes') or [])} "
                f"layers={len(data.get('layers') or [])}\n"
                f"(regen lỗi: {e})"
            )[:3800]
        return f"Mapper toàn diện lỗi: {e}\nChạy: python3 scripts/comprehensive_order_mapper.py"


HANDLERS = {
    "q:overview": fmt_overview,
    "q:source": fmt_source,
    "q:masked": fmt_masked,
    "q:missing": fmt_missing,
    "q:todo": fmt_todo,
    "q:paths": fmt_paths,
    "q:pipes": fmt_pipes,
    "q:realtime": fmt_realtime,
    "q:urls": fmt_urls,
    "q:endpoints": fmt_endpoints,
    "q:mapper_full": fmt_mapper_full,
}


def open_panel(token: str, chat_id: str, analysis: dict):
    text = (
        "BẢNG ĐIỀU KHIỂN HỘP THƯ · Truy vấn nguyên nhân SĐT\n"
        f"Đang trỏ file: {analysis['file']} ({analysis['records']} đơn)\n"
        "Chọn nút bên dưới để truy vấn rõ nguyên nhân."
    )
    return send(token, chat_id, text, panel_keyboard())


def read_offset() -> int:
    if OFFSET_FILE.is_file():
        try:
            return int(OFFSET_FILE.read_text().strip() or "0")
        except ValueError:
            return 0
    return 0


def write_offset(n: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(n), encoding="utf-8")


def main() -> int:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_CHAT_ID") or ""
    if not token or not chat:
        print("Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID", file=sys.stderr)
        return 2

    path = target_csv()
    analysis = analyze(path)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "control_panel.snapshot.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Always open panel + push full query pack so nguyên nhân rõ ngay
    open_panel(token, chat, analysis)
    for key in [
        "q:overview",
        "q:source",
        "q:masked",
        "q:missing",
        "q:todo",
        "q:paths",
        "q:pipes",
        "q:realtime",
        "q:urls",
        "q:endpoints",
        "q:mapper_full",
    ]:
        send(
            token,
            chat,
            HANDLERS[key](analysis),
            panel_keyboard()
            if key
            in {
                "q:paths",
                "q:pipes",
                "q:realtime",
                "q:urls",
                "q:endpoints",
                "q:mapper_full",
            }
            else None,
        )

    once = "--once" in sys.argv
    wait = 0 if once else 25
    if once and "--listen" not in sys.argv:
        print(json.dumps({"ok": True, "panel": "sent", "file": analysis["file"], "stats": analysis["stats"]}, ensure_ascii=False))
        return 0

    # listen callbacks
    offset = read_offset()
    deadline = time.time() + (5 if once else 120)
    while time.time() < deadline:
        data = api(
            token,
            "getUpdates",
            {
                "offset": offset,
                "timeout": wait,
                "allowed_updates": ["callback_query", "message"],
            },
            timeout=wait + 10,
        )
        if not data.get("ok"):
            break
        for upd in data.get("result") or []:
            offset = max(offset, int(upd["update_id"]) + 1)
            write_offset(offset)
            cb = upd.get("callback_query")
            if not cb:
                msg = upd.get("message") or {}
                text = (msg.get("text") or "").strip().lower()
                if text in {"/panel", "panel", "/menu", "bang dieu khien"}:
                    open_panel(token, str(msg["chat"]["id"]), analysis)
                continue
            data_key = cb.get("data") or ""
            chat_id = str(cb.get("message", {}).get("chat", {}).get("id") or chat)
            answer_callback(token, cb["id"], "Đang truy vấn…")
            if data_key == "q:refresh":
                analysis = analyze(target_csv())
                (REPORTS / "control_panel.snapshot.json").write_text(
                    json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                send(token, chat_id, fmt_overview(analysis), panel_keyboard())
                continue
            fn = HANDLERS.get(data_key)
            if fn:
                send(token, chat_id, fn(analysis), panel_keyboard())
        if once:
            break
        if wait == 0:
            time.sleep(1)
    print(json.dumps({"ok": True, "listening_done": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
