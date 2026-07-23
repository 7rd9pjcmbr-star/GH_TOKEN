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
                {"text": "🔗 Đấu nối OMS", "callback_data": "q:oms"},
            ],
            [
                {"text": "✨ Icon realtime", "callback_data": "q:icon_rt"},
                {"text": "🗄 Backend/kho", "callback_data": "q:kho_be"},
            ],
            [
                {"text": "⏱ Mapper RT đơn", "callback_data": "q:rt_orders"},
                {"text": "📈 Mở rộng RT", "callback_data": "q:rt_expand"},
            ],
            [
                {"text": "🔐 So sánh mã hoá", "callback_data": "q:crypto_cmp"},
            ],
            [
                {"text": "🔓 Hỗ trợ giải mã", "callback_data": "q:decode"},
                {"text": "🗺 Giải mã×icon", "callback_data": "q:decode_map"},
            ],
            [
                {"text": "🏬 Kho·NS·Shop", "callback_data": "q:kho_shop"},
                {"text": "🗄 Backend BC·DB", "callback_data": "q:bc_db"},
            ],
            [
                {"text": "🔎 Rà soát DB BC", "callback_data": "q:bc_audit"},
                {"text": "🧬 Pipe kho·BC·FP", "callback_data": "q:pipe_fp"},
            ],
            [
                {"text": "🌊 Ngược·dòng chảy", "callback_data": "q:rev_q"},
                {"text": "📦 Đang giao·bảng", "callback_data": "q:dg_tbl"},
            ],
            [
                {"text": "🧭 Tracking aship", "callback_data": "q:aship"},
                {"text": "📥 Inbox·hôm nay", "callback_data": "q:inbox_today"},
            ],
            [
                {"text": "🔍 Quét·phân tích", "callback_data": "q:inbox_scan"},
                {"text": "🧪 Nginx·gọi đơn", "callback_data": "q:ngx_order"},
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
        from realtime_icon_feedback_mapper import attach_feedback_prefix
        from realtime_order_sync import format_cycle, load_env, run_cycle

        cycle = run_cycle(load_env(), limit=20, notify=False, notify_new_only=False)
        return attach_feedback_prefix(format_cycle(cycle))[:3800]
    except Exception as e:  # noqa: BLE001
        return f"Realtime lỗi: {e}\nChạy: python3 scripts/realtime_order_sync.py --once --notify"


def fmt_oms(_a: dict | None = None) -> str:
    try:
        from oms_interconnect import format_report, interconnect, load_env
        from realtime_icon_feedback_mapper import attach_feedback_prefix

        report = interconnect(load_env(), ingest=True)
        return attach_feedback_prefix(format_report(report))[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "oms_interconnect.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return f"Đấu nối OMS lỗi: {e}\nChạy: python3 scripts/oms_interconnect.py --once"


def fmt_icon_rt(_a: dict | None = None) -> str:
    try:
        from realtime_icon_feedback_mapper import build_from_live, format_text, write_outputs

        report = build_from_live()
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "realtime_icon_feedback.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return f"Icon realtime lỗi: {e}\nChạy: python3 scripts/realtime_icon_feedback_mapper.py"


def fmt_kho_be(_a: dict | None = None) -> str:
    try:
        from warehouse_backend_mapper import build_report, format_text, write_outputs

        report = build_report()
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "warehouse_backend_mapper.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return f"Mapper backend/kho lỗi: {e}\nChạy: python3 scripts/warehouse_backend_mapper.py"


def fmt_rt_orders(_a: dict | None = None) -> str:
    try:
        from realtime_order_backend_mapper import build_report, format_text, write_outputs

        report = build_report()
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "realtime_order_backend_mapper.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return (
            f"Mapper RT đơn lỗi: {e}\n"
            "Chạy: python3 scripts/realtime_order_backend_mapper.py"
        )


def fmt_rt_expand(_a: dict | None = None) -> str:
    try:
        from realtime_order_expand import build_report, format_text, write_outputs

        report = build_report()
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "realtime_order_expand.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return (
            f"Mở rộng RT lỗi: {e}\n"
            "Chạy: python3 scripts/realtime_order_expand.py"
        )


def fmt_crypto_cmp(_a: dict | None = None) -> str:
    try:
        from crypto_encryption_issue_compare import build_report, format_text, write_outputs

        report = build_report()
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "crypto_encryption_compare.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return (
            f"So sánh mã hoá lỗi: {e}\n"
            "Chạy: python3 scripts/crypto_encryption_issue_compare.py"
        )


def fmt_decode(_a: dict | None = None) -> str:
    try:
        from crypto_decode_assist import build_report, format_text, write_outputs

        report = build_report()
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "crypto_decode_assist.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return (
            f"Hỗ trợ giải mã lỗi: {e}\n"
            "Chạy: python3 scripts/crypto_decode_assist.py"
        )


def fmt_decode_map(_a: dict | None = None) -> str:
    try:
        from decode_icon_logistics_mapper import build_report, format_text, write_outputs

        report = build_report()
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "decode_icon_logistics_mapper.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return (
            f"Mapper giải mã×icon lỗi: {e}\n"
            "Chạy: python3 scripts/decode_icon_logistics_mapper.py"
        )


def fmt_kho_shop(_a: dict | None = None) -> str:
    try:
        from kho_buucuc_staff_shop_mapper import build_report, format_text, write_outputs

        report = build_report()
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "kho_buucuc_staff_shop.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return (
            f"Kho·NS·Shop lỗi: {e}\n"
            "Chạy: python3 scripts/kho_buucuc_staff_shop_mapper.py"
        )


def fmt_bc_db(_a: dict | None = None) -> str:
    try:
        from buucuc_backend_db_query import build_report, format_text, write_outputs

        report = build_report()
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "buucuc_backend_db_query.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return (
            f"Backend BC·DB lỗi: {e}\n"
            "Chạy: python3 scripts/buucuc_backend_db_query.py"
        )


def fmt_bc_audit(_a: dict | None = None) -> str:
    try:
        from buucuc_db_panorama_audit import build_report, format_text, write_outputs

        report = build_report(refresh_db=True)
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "buucuc_db_panorama_audit.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return (
            f"Rà soát DB BC lỗi: {e}\n"
            "Chạy: python3 scripts/buucuc_db_panorama_audit.py --refresh-db"
        )


def fmt_pipe_fp(_a: dict | None = None) -> str:
    try:
        from order_pipe_kho_buucuc_db import build_report, format_text, write_outputs

        report = build_report()
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "order_pipe_kho_buucuc.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return (
            f"Pipe kho·BC·FP lỗi: {e}\n"
            "Chạy: python3 scripts/order_pipe_kho_buucuc_db.py"
        )


def fmt_rev_q(_a: dict | None = None) -> str:
    try:
        from order_pipe_reverse_query import build_report, format_text, write_outputs

        report = build_report()
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "order_pipe_reverse_query.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return (
            f"Truy vấn ngược lỗi: {e}\n"
            "Chạy: python3 scripts/order_pipe_reverse_query.py"
        )


def fmt_dg_tbl(_a: dict | None = None) -> str:
    try:
        from dang_giao_chi_tiet_table import build_report, format_text, write_outputs

        report = build_report()  # as_of = hôm nay UTC
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "dang_giao_chi_tiet.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return (
            f"Bảng đang giao lỗi: {e}\n"
            "Chạy: python3 scripts/dang_giao_chi_tiet_table.py --as-of $(date -u +%F)"
        )


def fmt_aship(_a: dict | None = None) -> str:
    try:
        from tracking_aship import build_report, format_text, write_outputs

        report = build_report(probe=False)
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "tracking_aship.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return (
            f"Tracking aship lỗi: {e}\n"
            "Chạy: python3 scripts/tracking_aship.py"
        )


def fmt_inbox_today(_a: dict | None = None) -> str:
    try:
        from telegram_inbox_today_mapper import build_report, format_text, write_outputs

        report = build_report(pull=True, wait=2)
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "telegram_inbox_today_mapper.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return (
            f"Inbox hôm nay lỗi: {e}\n"
            "Chạy: python3 scripts/telegram_inbox_today_mapper.py"
        )


def fmt_inbox_scan(_a: dict | None = None) -> str:
    try:
        from telegram_inbox_scan_analyze import build_report, format_text, write_outputs

        report = build_report(pull=True, wait=2)
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "telegram_inbox_scan_analyze.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        return (
            f"Quét inbox lỗi: {e}\n"
            "Chạy: python3 scripts/telegram_inbox_scan_analyze.py"
        )


def fmt_ngx_order(_a: dict | None = None) -> str:
    try:
        from nginx_order_embed import format_text, run_when_needed, write_outputs

        report = run_when_needed(keep_alive=False)
        write_outputs(report)
        return format_text(report)[:3800]
    except Exception as e:  # noqa: BLE001
        path = ROOT / "reports" / "telegram-classify" / "nginx_order_embed.txt"
        alt = ROOT / "reports" / "telegram-classify" / "nginx_order_embed_test.txt"
        if path.is_file():
            return path.read_text(encoding="utf-8")[:3800]
        if alt.is_file():
            return alt.read_text(encoding="utf-8")[:3800]
        return (
            f"Nginx gọi đơn lỗi: {e}\n"
            "Chạy: python3 scripts/nginx_order_embed.py once"
        )


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
    "q:oms": fmt_oms,
    "q:icon_rt": fmt_icon_rt,
    "q:kho_be": fmt_kho_be,
    "q:rt_orders": fmt_rt_orders,
    "q:rt_expand": fmt_rt_expand,
    "q:crypto_cmp": fmt_crypto_cmp,
    "q:decode": fmt_decode,
    "q:decode_map": fmt_decode_map,
    "q:kho_shop": fmt_kho_shop,
    "q:bc_db": fmt_bc_db,
    "q:bc_audit": fmt_bc_audit,
    "q:pipe_fp": fmt_pipe_fp,
    "q:rev_q": fmt_rev_q,
    "q:dg_tbl": fmt_dg_tbl,
    "q:aship": fmt_aship,
    "q:inbox_today": fmt_inbox_today,
    "q:inbox_scan": fmt_inbox_scan,
    "q:ngx_order": fmt_ngx_order,
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
        "q:oms",
        "q:icon_rt",
        "q:kho_be",
        "q:rt_orders",
        "q:rt_expand",
        "q:crypto_cmp",
        "q:decode",
        "q:decode_map",
        "q:kho_shop",
        "q:bc_db",
        "q:bc_audit",
        "q:pipe_fp",
        "q:rev_q",
        "q:dg_tbl",
        "q:aship",
        "q:inbox_today",
        "q:inbox_scan",
        "q:ngx_order",
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
                "q:oms",
                "q:icon_rt",
                "q:kho_be",
                "q:rt_orders",
                "q:rt_expand",
                "q:crypto_cmp",
                "q:decode",
                "q:decode_map",
                "q:kho_shop",
                "q:bc_db",
                "q:bc_audit",
                "q:pipe_fp",
                "q:rev_q",
                "q:dg_tbl",
                "q:aship",
                "q:inbox_today",
                "q:inbox_scan",
                "q:ngx_order",
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
                # Capture order docs into quarantine so panel listen không nuốt file
                if msg.get("document"):
                    try:
                        from telegram_inbox_today_mapper import (
                            download_file,
                            is_order_document,
                            re_safe,
                            today_utc,
                        )

                        doc = msg["document"]
                        name = doc.get("file_name") or f"{doc.get('file_id')}.bin"
                        if is_order_document(name, doc.get("mime_type")):
                            day = today_utc().replace("-", "")
                            safe = re_safe(name)
                            dest_name = safe if safe.startswith(day) else f"{day}_{safe}"
                            dest = INBOX / dest_name
                            download_file(token, doc["file_id"], dest)
                            send(
                                token,
                                str(msg["chat"]["id"]),
                                f"📥 Đã lưu `{dest.name}` → mapper hôm nay sẵn sàng.\n"
                                "Bấm 📥 Inbox·hôm nay để map.",
                                panel_keyboard(),
                            )
                    except Exception as e:  # noqa: BLE001
                        send(
                            token,
                            str(msg.get("chat", {}).get("id") or chat),
                            f"❌ Lưu file inbox lỗi: `{e}`",
                            panel_keyboard(),
                        )
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
