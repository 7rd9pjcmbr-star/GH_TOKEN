#!/usr/bin/env python3
"""Quét file mới hộp thoại Telegram → phân tích cấu trúc (local-only).

1) getUpdates → tải mọi document vào quarantine/telegram
2) Phân loại: order_export | dump_stealer | dump_token | account_list | report | unknown
3) Dump/stealer → chuyển _skipped_dumps, chỉ phân tích cấu trúc (không lộ password, không login)
4) Order → tóm tắt schema + đếm dòng / status
5) Báo cáo JSON/TXT + nút panel

Secrets-only. No dump login. No Acc_all mass-login.
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
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

INBOX = ROOT / "quarantine" / "telegram"
DUMPS = INBOX / "_skipped_dumps"
REPORTS = ROOT / "reports" / "telegram-classify"
DB_PATH = REPORTS / "telegram_inbox_scan.db"
OFFSET_FILE = ROOT / "secrets" / "telegram_inbox_scan.offset"
STATE_FILE = ROOT / "secrets" / "telegram_inbox_scan.state.json"
MAIN_OFFSET = ROOT / "secrets" / "telegram.offset"
INBOX_MAP_OFFSET = ROOT / "secrets" / "telegram_inbox.offset"

DUMP_HINTS = (
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
ORDER_HINTS = (
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
)
ACCOUNT_HINTS = ("account", "acc_", "login", "user")
REPORT_HINTS = ("report", "assassin", "final_report")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for path in (ROOT / "secrets" / "telegram.env", ROOT / "secrets" / "backend_pipes.env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
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
    with urllib.request.urlopen(url, timeout=180) as resp:
        dest.write_bytes(resp.read())
    return dest


def re_safe(name: str) -> str:
    out = "".join(c if c.isalnum() or c in "._-+" else "_" for c in name)
    return out[:180] or "file.bin"


def sha16(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def read_max_offset() -> int:
    vals = []
    for p in (OFFSET_FILE, INBOX_MAP_OFFSET, MAIN_OFFSET):
        if not p.is_file():
            continue
        try:
            vals.append(int(p.read_text().strip() or "0"))
        except ValueError:
            pass
    return max(vals) if vals else 0


def write_offsets(n: int) -> None:
    for p in (OFFSET_FILE, INBOX_MAP_OFFSET, MAIN_OFFSET):
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            cur = int(p.read_text().strip() or "0") if p.is_file() else 0
        except ValueError:
            cur = 0
        if n > cur:
            p.write_text(str(n), encoding="utf-8")


def load_state() -> dict:
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"seen": {}, "scans": []}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = utc_now()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def classify_kind(name: str) -> str:
    n = (name or "").lower()
    if any(h in n for h in DUMP_HINTS):
        if "stealer" in n or "internal_search" in n or "leak" in n or "password" in n:
            return "dump_stealer"
        if "token" in n or "ghn_tokens" in n:
            return "dump_token"
        if "acc_all" in n or "valid_accounts" in n:
            return "dump_account"
        return "dump_other"
    if any(h in n for h in ORDER_HINTS):
        return "order_export"
    if any(h in n for h in REPORT_HINTS):
        return "report"
    if any(h in n for h in ACCOUNT_HINTS):
        return "account_list"
    ext = Path(n).suffix
    if ext in {".csv", ".json", ".xlsx"} and "order" in n:
        return "order_export"
    if ext in {".txt", ".lst", ".log"}:
        return "text_blob"
    return "unknown"


def is_dump_kind(kind: str) -> bool:
    return kind.startswith("dump_")


def xlsx_structure(path: Path, max_sheets: int = 8) -> dict:
    """Đọc cấu trúc xlsx qua zip+xml — không cần openpyxl; không lấy giá trị nhạy cảm."""
    sheets: list[dict] = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            wb = ET.fromstring(zf.read("xl/workbook.xml"))
            ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            sheet_meta = []
            for sh in wb.findall("m:sheets/m:sheet", ns):
                sheet_meta.append(
                    {
                        "name": sh.attrib.get("name") or "",
                        "sheetId": sh.attrib.get("sheetId"),
                        "rId": sh.attrib.get(
                            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                        ),
                    }
                )
            # map rId → path
            rels = {}
            if "xl/_rels/workbook.xml.rels" in names:
                root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
                for rel in root:
                    rels[rel.attrib.get("Id")] = rel.attrib.get("Target")
            for meta in sheet_meta[:max_sheets]:
                target = rels.get(meta["rId"]) or f"worksheets/sheet{meta['sheetId']}.xml"
                if not target.startswith("worksheets"):
                    target = "worksheets/" + target.split("/")[-1]
                path_in = "xl/" + target.lstrip("/")
                info = {"name": meta["name"], "rows_sampled": 0, "headers": [], "approx_rows": 0}
                if path_in not in names:
                    sheets.append(info)
                    continue
                xml = zf.read(path_in)
                # count <row
                info["approx_rows"] = xml.count(b"<row")
                # first row cells — shared strings lookup limited
                shared: list[str] = []
                if "xl/sharedStrings.xml" in names:
                    try:
                        ss = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                        for si in ss.findall(
                            "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"
                        ):
                            texts = [
                                t.text or ""
                                for t in si.iter(
                                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
                                )
                            ]
                            shared.append("".join(texts))
                    except ET.ParseError:
                        pass
                try:
                    root = ET.fromstring(xml)
                    first = root.find(
                        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheetData/"
                        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"
                    )
                    if first is not None:
                        headers = []
                        ns_main = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
                        for c in first.findall(f"{ns_main}c"):
                            t = c.attrib.get("t")
                            val = ""
                            if t == "inlineStr":
                                is_node = c.find(f"{ns_main}is")
                                if is_node is not None:
                                    texts = [
                                        (node.text or "")
                                        for node in is_node.iter(f"{ns_main}t")
                                    ]
                                    val = "".join(texts)
                            else:
                                v = c.find(f"{ns_main}v")
                                if v is not None and v.text is not None:
                                    if t == "s":
                                        try:
                                            val = shared[int(v.text)]
                                        except (ValueError, IndexError):
                                            val = v.text
                                    else:
                                        val = str(v.text)
                            headers.append(val[:60])
                        info["headers"] = headers[:20]
                        info["rows_sampled"] = 1
                except ET.ParseError:
                    pass
                sheets.append(info)
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        return {"ok": False, "error": str(e)[:120], "sheets": []}
    return {"ok": True, "sheets": sheets, "sheet_count": len(sheets)}


def csv_structure(path: Path, limit: int = 3) -> dict:
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            sample = fh.read(256_000)
        dialect = csv.Sniffer().sniff(sample[:4096], delimiters=",;\t|")
        reader = csv.reader(sample.splitlines(), dialect)
        rows = []
        for i, row in enumerate(reader):
            rows.append([str(c)[:40] for c in row[:15]])
            if i >= limit:
                break
        headers = rows[0] if rows else []
        # rough line count
        with path.open("rb") as fh:
            nlines = sum(1 for _ in fh)
        return {
            "ok": True,
            "headers": headers,
            "sample_rows": max(0, len(rows) - 1),
            "approx_rows": max(0, nlines - 1),
            "delimiter": getattr(dialect, "delimiter", ","),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}


def json_structure(path: Path) -> dict:
    try:
        # stream first object lightly
        raw = path.read_bytes()[:2_000_000]
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        # try line-delimited / large: count keys from head
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:500_000]
            data = json.loads(text)
        except Exception as e2:  # noqa: BLE001
            return {"ok": False, "error": str(e2)[:120]}
    if isinstance(data, list):
        keys: Counter = Counter()
        for item in data[:50]:
            if isinstance(item, dict):
                keys.update(item.keys())
        return {
            "ok": True,
            "type": "array",
            "approx_items": len(data),
            "top_keys": [k for k, _ in keys.most_common(20)],
        }
    if isinstance(data, dict):
        return {
            "ok": True,
            "type": "object",
            "top_keys": list(data.keys())[:30],
            "nested_list_lens": {
                k: len(v) for k, v in data.items() if isinstance(v, list)
            },
        }
    return {"ok": True, "type": type(data).__name__}


def text_structure(path: Path, *, dump_safe: bool = False) -> dict:
    """Cấu trúc text. dump_safe=True → không lưu mẫu dòng (tránh lộ password)."""
    try:
        data = path.read_bytes()
        text = data.decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}
    lines = text.splitlines()
    nonempty = [ln for ln in lines if ln.strip()]
    colon = sum(1 for ln in nonempty if ":" in ln)
    comma = sum(1 for ln in nonempty if "," in ln)
    out = {
        "ok": True,
        "lines": len(lines),
        "nonempty": len(nonempty),
        "lines_with_colon": colon,
        "lines_with_comma": comma,
        "bytes": len(data),
        "looks_like_cred_list": colon >= max(3, len(nonempty) // 2) if nonempty else False,
    }
    if not dump_safe and nonempty:
        # chỉ giữ dạng che: độ dài + có/không @ /
        samples = []
        for ln in nonempty[:5]:
            samples.append(
                {
                    "len": len(ln),
                    "has_at": "@" in ln,
                    "has_colon": ":" in ln,
                    "prefix": ln[:12].replace("\t", " ") + ("…" if len(ln) > 12 else ""),
                }
            )
        out["samples_masked"] = samples
    return out


SENSITIVE_HEADERS = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "otp",
    "cookie",
}
# Không redact: URL, User, shop, tracking, order… — cần cho lấy đơn


def redact_headers(headers: list[str]) -> list[str]:
    out = []
    for h in headers:
        hl = (h or "").strip().lower()
        # chỉ che tên cột mật khẩu/secret — giữ User/URL/order keys
        if hl in SENSITIVE_HEADERS or hl.endswith("_password") or hl.endswith("password"):
            out.append(f"[SECRET_COL:{h[:24]}]")
        else:
            out.append(h)
    return out


def analyze_file(path: Path, *, kind: str | None = None) -> dict:
    kind = kind or classify_kind(path.name)
    dump = is_dump_kind(kind)
    ext = path.suffix.lower()
    st = path.stat()
    base = {
        "file": path.name,
        "path": str(path),
        "kind": kind,
        "dump": dump,
        "ext": ext,
        "size": st.st_size,
        "mtime": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "sha1_16": sha16(path) if st.st_size < 80_000_000 else None,
        "safety": {
            "no_dump_login": True,
            "passwords_redacted": dump,
            "action": "quarantine_skip" if dump else "analyze",
        },
        "analyzed_at": utc_now(),
    }
    struct: dict
    if ext in {".xlsx", ".xlsm"}:
        struct = xlsx_structure(path)
        # redact sensitive header names
        for sh in struct.get("sheets") or []:
            if dump:
                sh["headers"] = redact_headers(sh.get("headers") or [])
            # detect dump by headers
            headers_l = [h.lower() for h in (sh.get("headers") or [])]
            if any("password" in h for h in headers_l) or any(
                x in (sh.get("name") or "").lower() for x in ("leak", "darknet", "unique", "dup")
            ):
                if not dump:
                    kind = "dump_stealer"
                    dump = True
                    base["kind"] = kind
                    base["dump"] = True
                    base["safety"]["action"] = "quarantine_skip"
                    base["safety"]["passwords_redacted"] = True
                    sh["headers"] = redact_headers(sh.get("headers") or [])
    elif ext == ".csv":
        struct = csv_structure(path)
        if dump:
            struct["headers"] = redact_headers(struct.get("headers") or [])
    elif ext == ".json":
        struct = json_structure(path)
    elif ext in {".txt", ".log", ".tsv", ".lst"}:
        struct = text_structure(path, dump_safe=dump)
    else:
        struct = {"ok": True, "note": f"no deep parser for {ext}"}

    # verdict
    if dump:
        verdict = (
            f"⚠ DUMP/{kind}: giữ tín hiệu lấy đơn (URL/host/user/shop/platform) — "
            "che password · không auto-login"
        )
    elif kind == "order_export":
        verdict = "📦 ORDER export — phù hợp mapper đơn / OMS ingest"
    elif kind == "report":
        verdict = "📋 REPORT — xem schema; giữ giá trị liên quan đơn nếu có"
    else:
        verdict = f"· {kind} — phân tích cấu trúc + tín hiệu lấy đơn"

    base["structure"] = struct
    base["verdict"] = verdict

    # Luôn trích tín hiệu lấy đơn — kể cả dump (không bỏ qua giá trị quan trọng)
    try:
        from order_signal_extract import extract_order_signals

        base["order_signals"] = extract_order_signals(path)
        base["safety"]["action"] = "analyze_order_signals" if dump else base["safety"]["action"]
        base["safety"]["kept_order_values"] = True
    except Exception as e:  # noqa: BLE001
        base["order_signals"] = {"ok": False, "error": str(e)[:160]}

    return base


def pull_documents(token: str, *, chat_id: str | None = None, wait: int = 0) -> dict:
    offset = read_max_offset()
    state = load_state()
    downloaded: list[dict] = []
    skipped: list[dict] = []
    try:
        data = api(
            token,
            "getUpdates",
            {
                "offset": offset,
                "timeout": wait,
                "allowed_updates": ["message", "channel_post"],
            },
            timeout=wait + 20,
        )
    except urllib.error.URLError as e:
        return {"ok": False, "error": str(e), "downloaded": [], "offset": offset}

    if not data.get("ok"):
        return {"ok": False, "error": str(data)[:200], "downloaded": [], "offset": offset}

    day = today_utc().replace("-", "")
    INBOX.mkdir(parents=True, exist_ok=True)
    DUMPS.mkdir(parents=True, exist_ok=True)

    for upd in data.get("result") or []:
        offset = max(offset, int(upd["update_id"]) + 1)
        msg = upd.get("message") or upd.get("channel_post") or {}
        cid = str((msg.get("chat") or {}).get("id") or "")
        if chat_id and cid and cid != str(chat_id):
            continue
        doc = msg.get("document")
        if not doc:
            continue
        name = doc.get("file_name") or f"{doc.get('file_id')}.bin"
        kind = classify_kind(name)
        safe = re_safe(name)
        dest_name = safe if safe.startswith(day) else f"{day}_{safe}"
        dest_dir = DUMPS if is_dump_kind(kind) else INBOX
        dest = dest_dir / dest_name
        try:
            download_file(token, doc["file_id"], dest)
            meta = {
                "file": dest.name,
                "path": str(dest),
                "orig_name": name,
                "kind": kind,
                "size": dest.stat().st_size,
                "message_id": msg.get("message_id"),
                "chat_id": cid,
                "at": utc_now(),
                "dump": is_dump_kind(kind),
            }
            downloaded.append(meta)
            state.setdefault("seen", {})[dest.name] = {
                "sha1_16": sha16(dest),
                "kind": kind,
                "at": utc_now(),
            }
        except Exception as e:  # noqa: BLE001
            skipped.append({"file": name, "reason": str(e)[:120]})

    write_offsets(offset)
    save_state(state)
    return {
        "ok": True,
        "offset": offset,
        "downloaded": downloaded,
        "skipped": skipped,
        "inbox": str(INBOX),
        "dumps": str(DUMPS),
    }


def list_candidates(*, only_new: bool = True, since_day: str | None = None) -> list[Path]:
    state = load_state()
    seen = state.get("seen") or {}
    since_day = since_day or today_utc()
    day_compact = since_day.replace("-", "")
    out: list[Path] = []
    for folder in (INBOX, DUMPS):
        if not folder.is_dir():
            continue
        for p in folder.iterdir():
            if not p.is_file():
                continue
            if p.name.startswith("."):
                continue
            mtime_day = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).strftime(
                "%Y-%m-%d"
            )
            is_today = mtime_day == since_day or day_compact in p.name
            if only_new and not is_today and p.name not in seen:
                # also include files pulled this session marked in seen today
                meta = seen.get(p.name) or {}
                at = str(meta.get("at") or "")
                if not at.startswith(since_day):
                    continue
            elif only_new and not is_today:
                meta = seen.get(p.name) or {}
                at = str(meta.get("at") or "")
                if not (at.startswith(since_day) or day_compact in p.name):
                    continue
            out.append(p)
    # sort newest first
    out.sort(key=lambda x: -x.stat().st_mtime)
    return out


def enrich_order_summary(analysis: dict) -> dict:
    if analysis.get("kind") != "order_export":
        return analysis
    try:
        from oms_interconnect import ingest_local_orders

        # filter records from this file name
        fname = analysis["file"]
        # strip day prefix if present
        bare = re.sub(r"^\d{8}_", "", fname)
        recs = [
            r
            for r in ingest_local_orders(limit_per_file=3000)
            if (r.get("file") or "") in {fname, bare}
            or bare in str(r.get("file") or "")
            or fname in str(r.get("file") or "")
        ]
        analysis["order_summary"] = {
            "records": len(recs),
            "by_status": dict(Counter(str(r.get("status") or "") for r in recs)),
            "by_source": dict(Counter(str(r.get("source") or "") for r in recs)),
            "with_tracking": sum(1 for r in recs if r.get("tracking_code")),
        }
    except Exception as e:  # noqa: BLE001
        analysis["order_summary"] = {"error": str(e)[:120]}
    return analysis


def materialize(analyses: list[dict], *, as_of: str) -> dict:
    REPORTS.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(
        """
        CREATE TABLE inbox_scan (
          file TEXT PRIMARY KEY,
          kind TEXT,
          dump INTEGER,
          ext TEXT,
          size INTEGER,
          mtime TEXT,
          sha1_16 TEXT,
          verdict TEXT,
          structure_json TEXT,
          order_signals_json TEXT,
          analyzed_at TEXT,
          as_of TEXT
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    for a in analyses:
        conn.execute(
            "INSERT OR REPLACE INTO inbox_scan VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                a.get("file"),
                a.get("kind"),
                1 if a.get("dump") else 0,
                a.get("ext"),
                a.get("size"),
                a.get("mtime"),
                a.get("sha1_16"),
                a.get("verdict"),
                json.dumps(a.get("structure") or {}, ensure_ascii=False),
                json.dumps(a.get("order_signals") or {}, ensure_ascii=False),
                a.get("analyzed_at"),
                as_of,
            ),
        )
    conn.execute("INSERT INTO meta(key,value) VALUES ('as_of',?)", (as_of,))
    conn.execute("INSERT INTO meta(key,value) VALUES ('files',?)", (str(len(analyses)),))
    conn.execute("INSERT INTO meta(key,value) VALUES ('scanned_at',?)", (utc_now(),))
    conn.commit()
    conn.close()
    return {"path": str(DB_PATH), "files": len(analyses), "as_of": as_of}


def build_report(*, pull: bool = True, wait: int = 0, as_of: str | None = None) -> dict:
    as_of = as_of or today_utc()
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_CHAT_ID") or ""
    pull_result: dict = {"ok": False, "skipped_pull": True, "downloaded": []}
    if pull and token:
        pull_result = pull_documents(token, chat_id=chat or None, wait=wait)
    elif pull and not token:
        pull_result = {"ok": False, "error": "missing TELEGRAM_BOT_TOKEN", "downloaded": []}

    # candidates = today's files + just downloaded
    paths = list_candidates(only_new=True, since_day=as_of)
    # ensure downloaded paths included
    for d in pull_result.get("downloaded") or []:
        p = Path(d["path"])
        if p.is_file() and p not in paths:
            paths.append(p)

    analyses = []
    for p in paths:
        kind = None
        for d in pull_result.get("downloaded") or []:
            if Path(d["path"]) == p:
                kind = d.get("kind")
                break
        a = analyze_file(p, kind=kind)
        # if dump landed in INBOX, move to DUMPS
        if a.get("dump") and p.parent == INBOX:
            DUMPS.mkdir(parents=True, exist_ok=True)
            dest = DUMPS / p.name
            if not dest.exists():
                p.rename(dest)
            a["path"] = str(dest)
            a["moved_to_dumps"] = True
        a = enrich_order_summary(a)
        analyses.append(a)

    by_kind = Counter(a.get("kind") for a in analyses)
    # gộp tín hiệu lấy đơn toàn inbox
    platform_total: Counter = Counter()
    host_total: Counter = Counter()
    user_total: Counter = Counter()
    for a in analyses:
        sig = ((a.get("order_signals") or {}).get("signals") or {})
        platform_total.update(sig.get("platforms") or {})
        for h, n in sig.get("hosts") or []:
            host_total[h] += n
        for u, n in sig.get("users_top") or []:
            user_total[u] += n

    db = materialize(analyses, as_of=as_of)

    return {
        "ok": True,
        "query": "Quét file mới hộp thoại Telegram → phân tích (giữ tín hiệu lấy đơn)",
        "checked_at": utc_now(),
        "as_of": as_of,
        "pull": pull_result,
        "db": db,
        "stats": {
            "files": len(analyses),
            "downloaded": len(pull_result.get("downloaded") or []),
            "dumps": sum(1 for a in analyses if a.get("dump")),
            "orders": sum(1 for a in analyses if a.get("kind") == "order_export"),
            "by_kind": dict(by_kind),
            "order_platforms": dict(platform_total),
            "order_hosts_top": host_total.most_common(20),
            "order_users_kept": len(user_total),
        },
        "order_signal_rollup": {
            "platforms": dict(platform_total),
            "hosts_top": host_total.most_common(25),
            "users_top": user_total.most_common(40),
            "note": "User/URL/host/platform được giữ để lấy đơn; password che; không auto-login dump",
        },
        "analyses": analyses,
        "summary": {
            "as_of": as_of,
            "files": len(analyses),
            "dumps": sum(1 for a in analyses if a.get("dump")),
            "orders": sum(1 for a in analyses if a.get("kind") == "order_export"),
            "platforms": dict(platform_total),
        },
        "verdict": (
            f"Quét {len(analyses)} file · dump={sum(1 for a in analyses if a.get('dump'))} "
            f"· order={sum(1 for a in analyses if a.get('kind') == 'order_export')} · "
            f"platforms={dict(platform_total)} · users_kept={len(user_total)} · "
            f"downloaded={len(pull_result.get('downloaded') or [])}"
        ),
        "next_actions": [
            "Dùng hosts/platforms/users_top để cấu hình pipe lấy đơn (secrets sở hữu)",
            "Password/secret vẫn che — không dump-login",
            "Gửi orders_*.csv/json/xlsx vào chat nếu có export đơn thật",
            "python3 scripts/order_signal_extract.py quarantine/telegram/_skipped_dumps/*.xlsx",
            f"SQL: SELECT file,kind,order_signals_json FROM inbox_scan — {DB_PATH}",
        ],
        "safety": {
            "secrets_only": True,
            "no_dump_login": True,
            "passwords_redacted": True,
            "kept_order_related_values": True,
            "skips_acc_all_mass_login": True,
        },
    }


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔍 TELEGRAM INBOX SCAN → PHÂN TÍCH")
    L(f"Lúc: {report['checked_at']}")
    L(f"📅 as_of: {report.get('as_of')}")
    L(report["verdict"])
    L("")
    pull = report.get("pull") or {}
    L(f"Pull ok={pull.get('ok')} downloaded={len(pull.get('downloaded') or [])} offset={pull.get('offset')}")
    if pull.get("error"):
        L(f"  pull_error: {pull.get('error')}")
    for d in (pull.get("downloaded") or [])[:15]:
        mark = "⚠DUMP" if d.get("dump") else "↓"
        L(f"  {mark} {d.get('file')} kind={d.get('kind')} size={d.get('size')}")
    st = report.get("stats") or {}
    L("")
    L(f"DB: {report['db'].get('path')} · files={st.get('files')} by_kind={st.get('by_kind')}")
    L(f"dumps={st.get('dumps')} orders={st.get('orders')}")
    L(f"platforms={st.get('order_platforms')} users_kept={st.get('order_users_kept')}")
    L(f"hosts_top={st.get('order_hosts_top')}")
    roll = report.get("order_signal_rollup") or {}
    if roll.get("users_top"):
        L(f"users_top (giữ cho lấy đơn): {roll.get('users_top')[:15]}")
    L("")
    L("=== Phân tích từng file ===")
    for a in report.get("analyses") or []:
        L(f"· [{a.get('kind')}] {a.get('file')} ({a.get('size')} B)")
        L(f"  {a.get('verdict')}")
        struct = a.get("structure") or {}
        if struct.get("sheets"):
            for sh in struct["sheets"][:4]:
                hdrs = ", ".join(sh.get("headers") or [])[:120]
                L(f"  sheet `{sh.get('name')}` rows≈{sh.get('approx_rows')} headers=[{hdrs}]")
        elif struct.get("headers"):
            L(f"  csv headers={struct.get('headers')[:12]} rows≈{struct.get('approx_rows')}")
        elif struct.get("top_keys"):
            L(f"  json type={struct.get('type')} keys={struct.get('top_keys')[:12]}")
        elif struct.get("lines") is not None:
            L(
                f"  text lines={struct.get('lines')} colon={struct.get('lines_with_colon')} "
                f"cred_like={struct.get('looks_like_cred_list')}"
            )
        osig = a.get("order_signals") or {}
        sig = osig.get("signals") or {}
        if sig:
            L(f"  📡 platforms={sig.get('platforms')} orderish={sig.get('orderish_row_hits')}")
            L(f"  hosts={sig.get('hosts')[:8]}")
            L(f"  users_kept={sig.get('users_top')[:8]}")
            if sig.get("urls_sample"):
                L(f"  urls={sig.get('urls_sample')[:5]}")
            if sig.get("filtered_order_hits"):
                L(f"  filtered_order={sig.get('filtered_order_hits')[:3]}")
            for h in (osig.get("backend_hints") or [])[:4]:
                L(f"  pipe {h.get('platform')}: {h.get('pipe_hint')}")
        if a.get("order_summary"):
            L(f"  order_summary={a.get('order_summary')}")
        if a.get("moved_to_dumps"):
            L("  → lưu _skipped_dumps/ (vẫn trích tín hiệu lấy đơn)")
    L("")
    L("Safety: secrets-only · giữ URL/user/host/shop/tracking · che password · no dump-login")
    L("Next:")
    for n in report.get("next_actions") or []:
        L(f"· {n}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    # strip huge raw from json if needed — analyses already redacted
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    text = format_text(report)
    paths = {
        "json": REPORTS / "telegram_inbox_scan_analyze.json",
        "txt": REPORTS / "telegram_inbox_scan_analyze.txt",
        "db": DB_PATH,
    }
    paths["json"].write_text(payload, encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Quét + phân tích file mới Telegram inbox")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--as-of", dest="as_of", default=None)
    ap.add_argument("--no-pull", action="store_true")
    ap.add_argument("--wait", type=int, default=2)
    ap.add_argument("--all-today", action="store_true", help="Phân tích mọi file mtime hôm nay + dumps")
    args = ap.parse_args()
    report = build_report(pull=not args.no_pull, wait=args.wait, as_of=args.as_of)
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
