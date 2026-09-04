#!/usr/bin/env python3
"""Lấy file mới từ hộp thoại Telegram → mapper đơn hàng hôm nay.

1) getUpdates → tải document (csv/json/xlsx) vào quarantine/telegram
2) Ingest + lọc đơn theo ngày hôm nay (UTC hoặc --as-of)
3) Ghi bảng orders_today + báo cáo mapper

Secrets-only (TELEGRAM_BOT_TOKEN). Không dump login. Bỏ qua Acc_all/token dumps.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

INBOX = ROOT / "quarantine" / "telegram"
REPORTS = ROOT / "reports" / "telegram-classify"
OUT = REPORTS / "realtime"
DB_PATH = REPORTS / "orders_today.db"
OFFSET_FILE = ROOT / "secrets" / "telegram_inbox.offset"
STATE_FILE = ROOT / "secrets" / "telegram_inbox.state.json"

ORDER_EXTS = {".csv", ".json", ".xlsx", ".xls", ".tsv", ".env", ".ini", ".txt"}
ORDER_NAME_HINTS = (
    "orders_",
    "order_",
    "don_hang",
    "danh_sach",
    "dang_giao",
    "da_gui",
    "thanhcoong",
    "spx",
    "pancake",
    "shipment",
    "tracking",
    "v9_credentials",
    "api_settings",
    "pancake_storage",
    "config.ini",
    "jt_api",
    "jt_tracking",
    "jt_parsed",
)
SKIP_NAME_HINTS = (
    "acc_all",
    "ghn_tokens",
    "valid_accounts",
    "assassin",
    "password",
    "passwords",
    "otp",
    "dump",
    "stealer",
    "internal_search",
    "leaks.",
    "leak_",
    "darknet",
    "results_cookies",
    "vnpost_ok",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for path in (
        ROOT / "secrets" / "telegram.env",
        ROOT / "secrets" / "backend_pipes.env",
        ROOT / "secrets" / "owned_accounts.env",
    ):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
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


def api(token: str, method: str, payload: dict | None = None, timeout: int = 35):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download_file(token: str, file_id: str, dest: Path) -> Path:
    meta = api(token, "getFile", {"file_id": file_id})
    if not meta.get("ok"):
        raise RuntimeError(str(meta)[:200])
    file_path = meta["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as resp:
        dest.write_bytes(resp.read())
    return dest


def re_safe(name: str) -> str:
    out = "".join(c if c.isalnum() or c in "._-+" else "_" for c in name)
    return out[:180] or "file.bin"


def is_order_document(name: str, mime: str | None = None) -> bool:
    n = (name or "").lower()
    if any(s in n for s in SKIP_NAME_HINTS):
        return False
    ext = Path(n).suffix
    if ext not in ORDER_EXTS:
        return False
    if any(h in n for h in ORDER_NAME_HINTS):
        return True
    # generic spreadsheet/json still accept if not skip-listed
    if ext in {".csv", ".json", ".xlsx", ".env", ".ini"}:
        return True
    if ext == ".txt" and any(h in n for h in ("jt_tracking", "jt_", "tracking_ref", "billcode")):
        return True
    return False


def is_dump_document(name: str) -> bool:
    n = (name or "").lower()
    return any(s in n for s in SKIP_NAME_HINTS)


def read_offset() -> int:
    if OFFSET_FILE.is_file():
        try:
            return int(OFFSET_FILE.read_text().strip() or "0")
        except ValueError:
            return 0
    # bootstrap from main telegram.offset if exists (avoid replaying ancient)
    main = ROOT / "secrets" / "telegram.offset"
    if main.is_file():
        try:
            return int(main.read_text().strip() or "0")
        except ValueError:
            return 0
    return 0


def write_offset(n: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(n), encoding="utf-8")


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"downloaded": {}, "mapped_at": None}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def pull_telegram_inbox(token: str, *, chat_id: str | None = None, wait: int = 0) -> dict:
    """Kéo document mới từ hộp thoại bot → INBOX (order) hoặc _skipped_dumps (dump)."""
    from order_signal_extract import extract_order_signals
    from telegram_poll_lock import TelegramPollLock

    offset = read_offset()
    state = load_state()
    downloaded: list[dict] = []
    skipped: list[dict] = []
    order_signals: list[dict] = []
    dumps_dir = INBOX / "_skipped_dumps"
    try:
        with TelegramPollLock(timeout=max(30.0, wait + 20.0)):
            data = api(
                token,
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": wait,
                    "allowed_updates": ["message", "channel_post"],
                },
                timeout=wait + 15,
            )
    except TimeoutError as e:
        return {"ok": False, "error": str(e), "downloaded": [], "offset": offset}
    except urllib.error.URLError as e:
        return {"ok": False, "error": str(e), "downloaded": [], "offset": offset}

    if not data.get("ok"):
        return {"ok": False, "error": str(data)[:200], "downloaded": [], "offset": offset}

    INBOX.mkdir(parents=True, exist_ok=True)
    dumps_dir.mkdir(parents=True, exist_ok=True)

    for upd in data.get("result") or []:
        offset = max(offset, int(upd["update_id"]) + 1)
        msg = upd.get("message") or upd.get("channel_post") or {}
        mid = msg.get("message_id")
        cid = str((msg.get("chat") or {}).get("id") or "")
        if chat_id and cid and cid != str(chat_id):
            continue
        doc = msg.get("document")
        text = (msg.get("text") or "").strip()
        if not doc and text:
            text_l = text.lower()
            try:
                from jt_tracking_ingest import ingest_chat_text

                ing = ingest_chat_text(text)
                if ing.get("added"):
                    downloaded.append(
                        {
                            "file": "(jt_tracking_chat)",
                            "orig_name": "jt_tracking_refs",
                            "refs": ing.get("added"),
                            "dump": False,
                        }
                    )
                    try:
                        from jt_public_trace import run_batch

                        run_batch()
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass
            if text.startswith("{") and ("pancake" in text_l or "token" in text_l):
                dest = INBOX / f"{today_utc().replace('-', '')}_api_settings_paste.json"
                dest.write_text(text, encoding="utf-8")
                downloaded.append({"file": dest.name, "orig_name": "api_settings_paste.json", "dump": False})
            elif (
                "pos.pancake.vn" in text_l
                or "pos_jwt" in text_l
                or (text.startswith("eyJ") and len(text) > 100)
            ):
                try:
                    from pancake_cookie_ingest import ingest_and_scan

                    ingest_and_scan(text, days=7, limit=10000, scan=True, notify=False)
                    downloaded.append({"file": "(pancake_jwt_paste)", "orig_name": "pos_jwt", "dump": False})
                except Exception as e:  # noqa: BLE001
                    skipped.append({"file": "pos_jwt_paste", "reason": str(e)[:120]})
            continue
        if not doc:
            continue
        name = doc.get("file_name") or f"{doc.get('file_id')}.bin"
        mime = doc.get("mime_type")
        dump = is_dump_document(name)
        order_like = is_order_document(name, mime)
        if not dump and not order_like:
            skipped.append({"file": name, "reason": "not_order_like", "mime": mime})
            continue
        safe = re_safe(name)
        day = today_utc().replace("-", "")
        dest_name = safe if safe.startswith(day) else f"{day}_{safe}"
        dest = (dumps_dir if dump else INBOX) / dest_name
        try:
            download_file(token, doc["file_id"], dest)
            meta = {
                "file": dest.name,
                "path": str(dest),
                "orig_name": name,
                "size": dest.stat().st_size,
                "message_id": mid,
                "chat_id": cid,
                "at": utc_now(),
                "dump": dump,
            }
            downloaded.append(meta)
            state.setdefault("downloaded", {})[dest.name] = meta
            # Luôn trích tín hiệu lấy đơn — kể cả dump (giữ URL/user/host; che password)
            try:
                order_signals.append(extract_order_signals(dest))
            except Exception as e:  # noqa: BLE001
                order_signals.append({"file": dest.name, "ok": False, "error": str(e)[:120]})
        except Exception as e:  # noqa: BLE001
            skipped.append({"file": name, "reason": str(e)[:120]})

    write_offset(offset)
    # Đồng bộ offset chính nếu inbox đã đi xa hơn — tránh panel/poll khác nuốt lại
    main_off = ROOT / "secrets" / "telegram.offset"
    try:
        cur = int(main_off.read_text().strip() or "0") if main_off.is_file() else 0
    except ValueError:
        cur = 0
    if offset > cur:
        main_off.parent.mkdir(parents=True, exist_ok=True)
        main_off.write_text(str(offset), encoding="utf-8")
    save_state(state)
    if downloaded:
        try:
            from export_orders_detailed import bootstrap_secrets_from_inbox

            bootstrap_secrets_from_inbox()
            order_files = [m for m in downloaded if not m.get("dump")]
            if order_files or any("api_settings" in (m.get("orig_name") or "").lower() for m in downloaded):
                import subprocess

                subprocess.run(
                    ["bash", str(ROOT / "scripts" / "orders_result_pipeline.sh")],
                    cwd=str(ROOT),
                    check=False,
                    capture_output=True,
                )
            if any("v9_credentials" in (m.get("orig_name") or "").lower() for m in downloaded):
                import subprocess

                subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "v9_credential_bootstrap.py")],
                    cwd=str(ROOT),
                    check=False,
                    capture_output=True,
                )
            if any(
                re.search(r"jt[_-]?parsed|j&t", (m.get("orig_name") or "").lower())
                for m in downloaded
            ):
                import subprocess

                subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "flex_local_ingest.py")],
                    cwd=str(ROOT),
                    check=False,
                    capture_output=True,
                )
            if any(
                re.search(r"jt_api|jt_tracking", (m.get("orig_name") or "").lower())
                for m in downloaded
            ):
                import subprocess

                subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "jt_bootstrap.py"), "--pull", "--wait", "3"],
                    cwd=str(ROOT),
                    check=False,
                    capture_output=True,
                )
        except Exception:  # noqa: BLE001
            pass
    return {
        "ok": True,
        "offset": offset,
        "downloaded": downloaded,
        "skipped": skipped,
        "order_signals": order_signals,
        "inbox": str(INBOX),
    }


def day_of(raw: str | None) -> str | None:
    if not raw:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(raw).strip())
    return m.group(1) if m else None


def file_day(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")


def list_inbox_files(*, as_of: str) -> list[dict]:
    out = []
    if not INBOX.is_dir():
        return out
    for p in sorted(INBOX.iterdir(), key=lambda x: -x.stat().st_mtime):
        if not p.is_file():
            continue
        if not is_order_document(p.name):
            continue
        st = p.stat()
        out.append(
            {
                "file": p.name,
                "path": str(p),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
                "mtime_day": file_day(p),
                "is_today": file_day(p) == as_of or as_of.replace("-", "") in p.name,
            }
        )
    return out


def map_orders_today(*, as_of: str, ingest_limit: int = 8000) -> tuple[list[dict], dict]:
    from buucuc_backend_db_query import classify_buucuc, kho_key, resolve_backend
    from oms_interconnect import ingest_local_orders
    from order_pipe_kho_buucuc_db import so_noi_bo, van_tay
    from owned_credentials import apply_owned_mapping, load_env as load_owned_env, mapping_summary
    from tracking_aship import attach_tracking_urls

    owned_env = load_owned_env()
    owned_info = mapping_summary(owned_env)
    inbox = list_inbox_files(as_of=as_of)
    today_files = {f["file"] for f in inbox if f["is_today"]}
    local = ingest_local_orders(limit_per_file=max(100, ingest_limit))
    rows: list[dict] = []

    for rec in local:
        status = str(rec.get("status") or "")
        created = day_of(rec.get("order_created_at") or rec.get("created_at"))
        synced = day_of(rec.get("synced_at"))
        updated = day_of(rec.get("updated_at"))
        fname = rec.get("file") or ""
        from_today_file = fname in today_files or as_of.replace("-", "") in fname
        date_hit = as_of in {created, synced, updated}
        # đơn hôm nay = ngày tạo/sync/update = as_of HOẶC nằm trong file inbox hôm nay
        if not (date_hit or from_today_file):
            continue

        buu = classify_buucuc(rec)
        backend = resolve_backend(rec, buu)
        kho = (
            (rec.get("kho") or "").strip()
            or kho_key(rec)
            or (f"shop:{rec.get('shop_id')}" if rec.get("shop_id") else "(chua_gan_kho)")
        )
        so = so_noi_bo(rec) or str(rec.get("order_key") or "")
        vt = van_tay(backend=backend, kho=kho, buucuc=buu, so=so or "(empty)", status=status)
        row = {
            "van_tay": vt,
            "so_noi_bo": so or None,
            "oms_id": rec.get("oms_id"),
            "order_key": rec.get("order_key"),
            "backend": backend,
            "buucuc": buu,
            "kho": kho,
            "shop_id": str(rec.get("shop_id") or "") or None,
            "shop_name": rec.get("shop_name"),
            "staff_creator": str(rec.get("creator") or "") or None,
            "carrier": rec.get("carrier"),
            "tracking_code": rec.get("tracking_code"),
            "status": status or None,
            "phone_class": rec.get("phone_class"),
            "customer_name": rec.get("customer_name") or rec.get("receiver_name"),
            "province": rec.get("province"),
            "district": rec.get("district"),
            "ward": rec.get("ward"),
            "full_address": rec.get("full_address") or rec.get("address_detail"),
            "source": rec.get("source"),
            "channel": rec.get("channel"),
            "file": fname,
            "order_created_at": rec.get("order_created_at") or rec.get("created_at"),
            "synced_at": rec.get("synced_at"),
            "updated_at": rec.get("updated_at"),
            "as_of": as_of,
            "match_reason": (
                "file_today"
                if from_today_file
                else "created"
                if created == as_of
                else "synced"
                if synced == as_of
                else "updated"
                if updated == as_of
                else "other"
            ),
            "mapped_at": utc_now(),
        }
        row = apply_owned_mapping(row, owned_env)
        rows.append(attach_tracking_urls(row))

    # dedupe
    dedup: dict[str, dict] = {}
    for r in rows:
        key = r.get("van_tay") or r.get("order_key") or hashlib.sha1(str(r).encode()).hexdigest()[:16]
        prev = dedup.get(key)
        if prev is None:
            dedup[key] = r
            continue
        merged = dict(prev)
        for k, v in r.items():
            if not merged.get(k) and v:
                merged[k] = v
        dedup[key] = merged

    stats = {
        "as_of": as_of,
        "inbox_files": len(inbox),
        "inbox_today_files": len(today_files),
        "today_files": sorted(today_files),
        "orders": len(dedup),
        "by_match": Counter(r.get("match_reason") for r in dedup.values()),
        "by_backend": Counter(r.get("backend") for r in dedup.values()),
        "by_buucuc": Counter(r.get("buucuc") for r in dedup.values()),
        "by_kho": Counter(r.get("kho") for r in dedup.values()),
        "by_status": Counter(r.get("status") for r in dedup.values()),
        "with_tracking_url": sum(1 for r in dedup.values() if r.get("tracking_url")),
        "owned_ready_platforms": owned_info.get("ready_platforms") or [],
        "owned_mapped_rows": sum(1 for r in dedup.values() if r.get("owned_ready")),
    }
    return list(dedup.values()), {"inbox": inbox, "owned": owned_info, **stats}


def materialize(rows: list[dict], *, as_of: str) -> dict:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        if DB_PATH.exists():
            DB_PATH.unlink()
    except OSError:
        pass
    from sqlite_perf import connect

    conn = connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE orders_today (
          van_tay TEXT PRIMARY KEY,
          so_noi_bo TEXT,
          oms_id TEXT,
          order_key TEXT,
          backend TEXT,
          buucuc TEXT,
          kho TEXT,
          shop_id TEXT,
          shop_name TEXT,
          staff_creator TEXT,
          carrier TEXT,
          tracking_code TEXT,
          status TEXT,
          phone_class TEXT,
          customer_name TEXT,
          province TEXT,
          district TEXT,
          ward TEXT,
          full_address TEXT,
          source TEXT,
          channel TEXT,
          file TEXT,
          order_created_at TEXT,
          synced_at TEXT,
          updated_at TEXT,
          as_of TEXT,
          match_reason TEXT,
          tracking_ref TEXT,
          tracking_provider TEXT,
          tracking_url TEXT,
          mapped_at TEXT
        );
        CREATE INDEX idx_ot_asof ON orders_today(as_of);
        CREATE INDEX idx_ot_kho ON orders_today(kho);
        CREATE INDEX idx_ot_buu ON orders_today(buucuc);
        CREATE INDEX idx_ot_file ON orders_today(file);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    cols = [
        "van_tay",
        "so_noi_bo",
        "oms_id",
        "order_key",
        "backend",
        "buucuc",
        "kho",
        "shop_id",
        "shop_name",
        "staff_creator",
        "carrier",
        "tracking_code",
        "status",
        "phone_class",
        "customer_name",
        "province",
        "district",
        "ward",
        "full_address",
        "source",
        "channel",
        "file",
        "order_created_at",
        "synced_at",
        "updated_at",
        "as_of",
        "match_reason",
        "tracking_ref",
        "tracking_provider",
        "tracking_url",
        "mapped_at",
    ]
    conn.executemany(
        f"INSERT INTO orders_today ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
        [tuple(r.get(c) for c in cols) for r in rows],
    )
    conn.execute("INSERT INTO meta(key,value) VALUES ('as_of', ?)", (as_of,))
    conn.execute("INSERT INTO meta(key,value) VALUES ('orders', ?)", (str(len(rows)),))
    conn.execute("INSERT INTO meta(key,value) VALUES ('mapped_at', ?)", (utc_now(),))
    conn.commit()
    info = {"path": str(DB_PATH), "orders": len(rows), "as_of": as_of, "table": "orders_today"}
    conn.close()
    return info


def build_report(*, as_of: str | None = None, pull: bool = True, wait: int = 0) -> dict:
    from realtime_icon_feedback_mapper import chant, feedback_line

    as_of = as_of or today_utc()
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_CHAT_ID") or ""
    pull_result: dict = {"ok": False, "skipped_pull": True, "downloaded": []}
    if pull and token:
        pull_result = pull_telegram_inbox(token, chat_id=chat or None, wait=wait)
    elif pull and not token:
        pull_result = {"ok": False, "error": "missing TELEGRAM_BOT_TOKEN", "downloaded": []}

    rows, stats = map_orders_today(as_of=as_of)
    db = materialize(rows, as_of=as_of)

    # also refresh đang giao stamp for today if có đơn đang giao
    dg_note = None
    try:
        from dang_giao_chi_tiet_table import build_report as dg_build, write_outputs as dg_write

        dg = dg_build(as_of=as_of)
        dg_write(dg)
        dg_note = {"orders": dg.get("summary", {}).get("orders"), "ngay_dang_giao": as_of}
    except Exception as e:  # noqa: BLE001
        dg_note = {"error": str(e)[:120]}

    icons = ["spark", "code", "cube", "compass", "hash"]
    # Trích tín hiệu lấy đơn từ dump hôm nay (không bỏ qua URL/user/host)
    signal_blocks = list(pull_result.get("order_signals") or [])
    dumps_dir = INBOX / "_skipped_dumps"
    if dumps_dir.is_dir():
        day = as_of.replace("-", "")
        try:
            from order_signal_extract import extract_order_signals

            for p in sorted(dumps_dir.glob(f"{day}_*")):
                if any((b.get("file") == p.name) for b in signal_blocks):
                    continue
                try:
                    signal_blocks.append(extract_order_signals(p))
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

    top_fb = feedback_line(
        icons,
        f"inbox→mapper hôm nay {as_of} · downloaded={len(pull_result.get('downloaded') or [])} · "
        f"orders_today={len(rows)} · files_today={stats.get('inbox_today_files')} · "
        f"order_signal_files={len(signal_blocks)}",
    )

    return {
        "ok": True,
        "query": "Lấy file mới hộp thoại Telegram → mapper đơn hàng hôm nay",
        "checked_at": utc_now(),
        "as_of": as_of,
        "pull": {k: v for k, v in pull_result.items() if k != "order_signals"},
        "order_signals": signal_blocks,
        "db": db,
        "stats": {
            **{k: (dict(v) if isinstance(v, Counter) else v) for k, v in stats.items() if k != "inbox"},
            "by_match": dict(stats["by_match"]),
            "by_backend": dict(stats["by_backend"]),
            "by_buucuc": dict(stats["by_buucuc"]),
            "by_kho": dict(stats["by_kho"]),
            "by_status": dict(stats["by_status"]),
        },
        "inbox_files": stats.get("inbox") or [],
        "dang_giao_refresh": dg_note,
        "samples": [
            {
                "van_tay": r.get("van_tay"),
                "so_noi_bo": r.get("so_noi_bo"),
                "kho": r.get("kho"),
                "buucuc": r.get("buucuc"),
                "status": r.get("status"),
                "file": r.get("file"),
                "match_reason": r.get("match_reason"),
                "tracking_url": r.get("tracking_url"),
                "order_created_at": r.get("order_created_at"),
            }
            for r in rows[:30]
        ],
        "summary": {
            "as_of": as_of,
            "downloaded": len(pull_result.get("downloaded") or []),
            "orders_today": len(rows),
            "icon_chant": chant(icons),
            "feedback": top_fb,
        },
        "verdict": top_fb,
        "next_actions": [
            f"SQL: SELECT * FROM orders_today WHERE as_of='{as_of}' LIMIT 20 — {DB_PATH}",
            "Re-pull: python3 scripts/telegram_inbox_today_mapper.py --pull",
            "Chỉ map (không kéo): python3 scripts/telegram_inbox_today_mapper.py --no-pull",
            "Gửi file orders_*.csv/json/xlsx vào chat bot Telegram rồi chạy lại --pull",
        ],
        "safety": {
            "secrets_only": True,
            "skips_acc_all_dumps": True,
            "no_dump_login": True,
        },
    }


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("📥 TELEGRAM INBOX → MAPPER ĐƠN HÔM NAY")
    L(f"Lúc: {report['checked_at']}")
    L(f"📅 as_of: {report.get('as_of')}")
    L(report["verdict"])
    L("")
    s = report["summary"]
    L(f"✨ {s.get('feedback')}")
    L(f"Chant: {s.get('icon_chant')}")
    pull = report.get("pull") or {}
    L(f"Pull ok={pull.get('ok')} downloaded={len(pull.get('downloaded') or [])} offset={pull.get('offset')}")
    if pull.get("error"):
        L(f"  pull_error: {pull.get('error')}")
    for d in (pull.get("downloaded") or [])[:12]:
        L(f"  ↓ {d.get('file')} ({d.get('size')} B) from msg={d.get('message_id')}")
    for sk in (pull.get("skipped") or [])[:8]:
        L(f"  skip {sk.get('file')}: {sk.get('reason')}")
    dump_skips = [s for s in (pull.get("skipped") or []) if "dump" in str(s.get("reason") or "") or "stealer" in str(s.get("reason") or "")]
    if dump_skips:
        L(f"⚠ Đã bỏ qua {len(dump_skips)} file dump/stealer (không map, không login).")
    st = report.get("stats") or {}
    L("")
    L(f"DB: {report['db'].get('path')} · orders_today={report['db'].get('orders')}")
    L(f"inbox_files={st.get('inbox_files')} today_files={st.get('inbox_today_files')} {st.get('today_files')}")
    L(f"match={st.get('by_match')} backend={st.get('by_backend')}")
    L(f"buucuc={st.get('by_buucuc')}")
    L(f"kho={st.get('by_kho')}")
    L(f"status={st.get('by_status')}")
    L(f"with_tracking_url={st.get('with_tracking_url')} · dang_giao_refresh={report.get('dang_giao_refresh')}")
    L(f"owned_ready={st.get('owned_ready_platforms')} owned_mapped_rows={st.get('owned_mapped_rows')}")
    owned = (report.get("stats") or {}).get("owned") or report.get("owned")
    if not owned:
        owned = st.get("owned")
    if isinstance(owned, dict) and owned.get("verdict"):
        L(f"owned: {owned.get('verdict')}")
    L("")
    L("=== Tín hiệu lấy đơn (giữ URL/user/host — che password) ===")
    for b in (report.get("order_signals") or [])[:8]:
        sig = b.get("signals") or {}
        L(f"· {b.get('file')}: {b.get('verdict')}")
        L(f"  platforms={sig.get('platforms')} hosts={sig.get('hosts')[:5]}")
        L(f"  users_kept={sig.get('users_top')[:5]}")
    L("")
    L("=== File inbox (mới nhất) ===")
    for f in (report.get("inbox_files") or [])[:12]:
        mark = "★" if f.get("is_today") else "·"
        L(f"{mark} {f.get('file')} mtime={f.get('mtime')} size={f.get('size')}")
    L("")
    L("=== Mẫu đơn hôm nay ===")
    for r in report.get("samples") or []:
        L(
            f"· [{r.get('van_tay')}] {r.get('match_reason')} so={r.get('so_noi_bo')} "
            f"{r.get('kho')}/{r.get('buucuc')} · {r.get('status')} · file={r.get('file')}"
        )
        if r.get("tracking_url"):
            L(f"  aship: {r.get('tracking_url')}")
    L("")
    L("Next:")
    for a in report["next_actions"]:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    text = format_text(report)
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=list)
    # CSV export
    csv_path = REPORTS / "orders_today.csv"
    if DB_PATH.is_file():
        from sqlite_perf import connect

        conn = connect(DB_PATH, row_factory=sqlite3.Row)
        rows = conn.execute("SELECT * FROM orders_today").fetchall()
        if rows:
            cols = list(rows[0].keys())
            with csv_path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=cols)
                w.writeheader()
                for r in rows:
                    w.writerow({k: r[k] for k in cols})
        conn.close()
    paths = {
        "json": REPORTS / "telegram_inbox_today_mapper.json",
        "txt": REPORTS / "telegram_inbox_today_mapper.txt",
        "csv": csv_path,
        "rt_json": OUT / "telegram_inbox_today_mapper.json",
        "rt_txt": OUT / "telegram_inbox_today_mapper.txt",
        "db": DB_PATH,
    }
    paths["json"].write_text(payload, encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    paths["rt_json"].write_text(payload, encoding="utf-8")
    paths["rt_txt"].write_text(text, encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Telegram inbox → mapper đơn hôm nay")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--as-of", dest="as_of", default=None, help="YYYY-MM-DD (mặc định hôm nay UTC)")
    ap.add_argument("--pull", action="store_true", default=True, help="Kéo file mới từ Telegram")
    ap.add_argument("--no-pull", action="store_true", help="Chỉ map file inbox sẵn có")
    ap.add_argument("--wait", type=int, default=0, help="getUpdates long-poll seconds")
    args = ap.parse_args()
    report = build_report(
        as_of=args.as_of,
        pull=not args.no_pull,
        wait=args.wait,
    )
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=list))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
