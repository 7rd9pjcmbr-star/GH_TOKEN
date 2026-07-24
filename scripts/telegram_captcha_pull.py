#!/usr/bin/env python3
"""Mở hộp thoại Telegram → kéo captcha (file/ảnh/text) → secrets.

Luồng:
  1) (optional) gửi prompt vào TELEGRAM_CHAT_ID
  2) getUpdates → tìm captcha.json / ảnh / mã text
  3) lưu quarantine/telegram/_captcha + secrets/captcha.pending.json
  4) parse JSON — phân biệt mã captcha thật vs lỗi API

Owned dialog only. Không dump-login.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SECRETS = ROOT / "secrets"
INBOX = ROOT / "quarantine" / "telegram"
CAPTCHA_DIR = INBOX / "_captcha"
REPORTS = ROOT / "reports" / "telegram-classify"
OFFSET_PATH = SECRETS / "telegram_captcha_pull.offset"
PENDING_JSON = SECRETS / "captcha.pending.json"
PENDING_TXT = SECRETS / "captcha.pending.txt"
PENDING_IMG = SECRETS / "captcha.pending.bin"

NAME_RE = re.compile(r"(?i)(captcha|capcha|verify[_-]?code|otp[_-]?img|mã[_-]?xác)")
CODE_RE = re.compile(
    r"(?i)\b(?:captcha|capcha|otp|mã\s*xác\s*thực|verify(?:\s*code)?)\s*[:=]\s*([A-Za-z0-9\-_]{3,12})\b"
)
BARE_CODE_RE = re.compile(r"^\s*([A-Za-z0-9]{4,8})\s*$")
ERROR_KEYS = ("không được hỗ trợ", "not supported", "error", "invalid", "unauthorized")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for p in (SECRETS / "telegram.env", SECRETS / "backend_pipes.env"):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            key = k.strip()
            val = v.strip().strip('"').strip("'")
            cur = env.get(key)
            if cur is None or not str(cur).strip():
                env[key] = val
    return env


def api(token: str, method: str, payload: dict | None = None, timeout: int = 45) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def download_file(token: str, file_id: str, dest: Path) -> Path:
    meta = api(token, "getFile", {"file_id": file_id})
    fpath = (meta.get("result") or {}).get("file_path")
    if not fpath:
        raise RuntimeError(f"getFile missing path: {meta}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f"https://api.telegram.org/file/bot{token}/{fpath}", timeout=120) as r:
        dest.write_bytes(r.read())
    return dest


def open_dialog(token: str, chat_id: str, *, text: str | None = None) -> dict[str, Any]:
    body = text or (
        "🔐 Mở hộp thoại — gửi CAPTCHA\n\n"
        "Reply bằng một trong các dạng:\n"
        "• file `captcha.json` / ảnh captcha\n"
        "• text: `captcha:ABCD` hoặc mã 4–8 ký tự\n\n"
        "Bot đang chờ trong hộp thoại này."
    )
    data = api(token, "sendMessage", {"chat_id": chat_id, "text": body})
    return {
        "ok": bool(data.get("ok")),
        "message_id": (data.get("result") or {}).get("message_id"),
        "chat_id": chat_id,
        "error": None if data.get("ok") else str(data)[:160],
    }


def parse_captcha_payload(raw: str | bytes | dict | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "kind": None,
        "code": None,
        "token": None,
        "image_b64": None,
        "status": None,
        "msg": None,
        "is_api_error": False,
        "verdict": "",
    }
    if raw is None:
        out["verdict"] = "❌ Captcha trống"
        return out
    data: Any
    if isinstance(raw, (bytes, bytearray)):
        try:
            data = json.loads(raw.decode("utf-8", errors="ignore"))
        except json.JSONDecodeError:
            out["kind"] = "binary"
            out["verdict"] = f"⚠ Binary captcha {len(raw)}B — chưa OCR"
            return out
    elif isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            m = CODE_RE.search(raw) or BARE_CODE_RE.match(raw.strip())
            if m:
                out["ok"] = True
                out["kind"] = "text_code"
                out["code"] = m.group(1)
                out["verdict"] = f"✅ Captcha text · {out['code']}"
                return out
            out["kind"] = "text"
            out["msg"] = raw[:200]
            out["verdict"] = "❌ Text không khớp mã captcha"
            return out
    else:
        data = raw

    if not isinstance(data, dict):
        out["verdict"] = "❌ Captcha JSON không phải object"
        return out

    out["status"] = data.get("status") or data.get("code_status")
    out["msg"] = data.get("msg") or data.get("message") or data.get("error")
    blob = json.dumps(data, ensure_ascii=False).lower()
    if str(out["status"]).lower() in {"error", "fail", "failed"} or any(x in blob for x in ERROR_KEYS):
        # still extract if code present
        out["is_api_error"] = True

    for k in ("captcha", "code", "otp", "value", "answer", "text", "captcha_code"):
        v = data.get(k)
        if isinstance(v, str) and 3 <= len(v.strip()) <= 32 and " " not in v.strip():
            out["code"] = v.strip()
            break
    for k in ("token", "captcha_token", "key", "id"):
        v = data.get(k)
        if isinstance(v, str) and len(v.strip()) >= 8:
            out["token"] = v.strip()
            break
    for k in ("image_b64", "image", "img", "captcha_image", "base64"):
        v = data.get(k)
        if isinstance(v, str) and len(v) > 40:
            out["image_b64"] = v[:80] + f"…(len={len(v)})"
            out["kind"] = "image_b64"
            break

    if out["code"] or out["token"] or out.get("kind") == "image_b64":
        out["ok"] = True
        out["kind"] = out["kind"] or "json"
        out["is_api_error"] = False
        out["verdict"] = (
            f"✅ Captcha JSON · code={out.get('code') or '—'} · "
            f"token={(out.get('token') or '')[:8] + '…' if out.get('token') else '—'}"
        )
        return out

    if out["is_api_error"]:
        out["kind"] = "api_error"
        out["verdict"] = (
            f"❌ captcha.json là lỗi API · status={out.get('status')} · {out.get('msg')}"
        )
        return out

    out["kind"] = "json_empty"
    out["verdict"] = "❌ JSON không có code/token/image captcha"
    return out


def _read_offset() -> int:
    if OFFSET_PATH.is_file():
        try:
            return int(OFFSET_PATH.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            return 0
    return 0


def pull(
    token: str,
    *,
    chat_id: str | None = None,
    lookback: int = 200,
    wait: int = 0,
) -> dict[str, Any]:
    """Kéo captcha từ hộp thoại Telegram."""
    CAPTCHA_DIR.mkdir(parents=True, exist_ok=True)
    SECRETS.mkdir(parents=True, exist_ok=True)
    off = _read_offset()
    start = max(0, off - max(0, lookback))
    payload: dict[str, Any] = {
        "timeout": int(wait),
        "limit": 100,
        "allowed_updates": ["message", "channel_post", "edited_message"],
    }
    if start > 0:
        payload["offset"] = start
    data = api(token, "getUpdates", payload)
    if not data.get("ok"):
        return {"ok": False, "error": str(data)[:200], "hits": [], "downloaded": []}

    hits: list[dict[str, Any]] = []
    downloaded: list[dict[str, Any]] = []
    parsed: dict[str, Any] | None = None
    max_off = off
    day = datetime.now(timezone.utc).strftime("%Y%m%d")

    for upd in data.get("result") or []:
        uid = int(upd.get("update_id") or 0)
        max_off = max(max_off, uid + 1)
        msg = upd.get("message") or upd.get("channel_post") or upd.get("edited_message") or {}
        if chat_id and str((msg.get("chat") or {}).get("id")) != str(chat_id):
            continue
        text = msg.get("text") or msg.get("caption") or ""
        doc = msg.get("document") or {}
        name = doc.get("file_name") or ""
        photos = msg.get("photo") or []
        captchaish = bool(NAME_RE.search(name) or NAME_RE.search(text) or CODE_RE.search(text))
        if not captchaish and not (photos and NAME_RE.search(text or "captcha")):
            # also accept bare short reply codes when caption/text is short
            if not (photos or (doc and NAME_RE.search(name)) or BARE_CODE_RE.match(text.strip())):
                continue
            if BARE_CODE_RE.match(text.strip()) and not NAME_RE.search(text):
                # only treat bare code if recent / explicit — still accept in captcha pull context
                captchaish = True
        if not captchaish and not (doc and NAME_RE.search(name)) and not photos:
            continue

        hit: dict[str, Any] = {
            "update_id": uid,
            "message_id": msg.get("message_id"),
            "document_name": name or None,
            "text_preview": text[:160],
            "has_photo": bool(photos),
        }
        hits.append(hit)

        if doc.get("file_id"):
            safe = re.sub(r"[^\w.\-+]", "_", name)[:180] or f"captcha_{msg.get('message_id')}.bin"
            dest = CAPTCHA_DIR / (safe if safe.startswith(day) else f"{day}_{safe}")
            try:
                download_file(token, doc["file_id"], dest)
                downloaded.append({"file": str(dest), "size": dest.stat().st_size, "orig": name})
                if name.lower().endswith(".json") or "captcha" in name.lower():
                    raw = dest.read_text(encoding="utf-8", errors="ignore")
                    parsed = parse_captcha_payload(raw)
                    hit["parsed"] = {
                        "ok": parsed.get("ok"),
                        "kind": parsed.get("kind"),
                        "verdict": parsed.get("verdict"),
                        "is_api_error": parsed.get("is_api_error"),
                        "code": parsed.get("code"),
                    }
                    PENDING_JSON.write_text(raw if raw.endswith("\n") else raw + "\n", encoding="utf-8")
                    try:
                        os.chmod(PENDING_JSON, 0o600)
                    except OSError:
                        pass
            except Exception as e:  # noqa: BLE001
                hit["download_error"] = str(e)[:120]

        if photos:
            best = max(photos, key=lambda p: int(p.get("file_size") or 0))
            dest = CAPTCHA_DIR / f"{day}_captcha_{msg.get('message_id')}.jpg"
            try:
                download_file(token, best["file_id"], dest)
                downloaded.append({"file": str(dest), "size": dest.stat().st_size, "orig": "photo"})
                PENDING_IMG.write_bytes(dest.read_bytes())
                try:
                    os.chmod(PENDING_IMG, 0o600)
                except OSError:
                    pass
                if parsed is None:
                    parsed = {
                        "ok": True,
                        "kind": "photo",
                        "code": None,
                        "verdict": f"✅ Đã tải ảnh captcha · {dest.name}",
                        "path": str(dest),
                    }
                    hit["parsed"] = {"ok": True, "kind": "photo", "verdict": parsed["verdict"]}
            except Exception as e:  # noqa: BLE001
                hit["download_error"] = str(e)[:120]

        m = CODE_RE.search(text) or BARE_CODE_RE.match(text.strip())
        if m and (NAME_RE.search(text) or BARE_CODE_RE.match(text.strip())):
            code = m.group(1)
            parsed = {
                "ok": True,
                "kind": "text_code",
                "code": code,
                "verdict": f"✅ Captcha text · {code}",
            }
            PENDING_TXT.write_text(code + "\n", encoding="utf-8")
            try:
                os.chmod(PENDING_TXT, 0o600)
            except OSError:
                pass
            hit["parsed"] = {"ok": True, "kind": "text_code", "code": code}

    OFFSET_PATH.write_text(str(max_off), encoding="utf-8")
    return {
        "ok": True,
        "updates_n": len(data.get("result") or []),
        "hits_n": len(hits),
        "hits": hits[:40],
        "downloaded": downloaded,
        "parsed": parsed,
        "offset": max_off,
        "pending": {
            "json": str(PENDING_JSON) if PENDING_JSON.is_file() else None,
            "txt": str(PENDING_TXT) if PENDING_TXT.is_file() else None,
            "img": str(PENDING_IMG) if PENDING_IMG.is_file() else None,
        },
    }


def build_report(
    *,
    open_chat: bool = True,
    wait: int = 0,
    lookback: int = 200,
    notify: bool = False,
) -> dict[str, Any]:
    env = load_env()
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    report: dict[str, Any] = {
        "ok": False,
        "module": "telegram_captcha_pull",
        "checked_at": utc_now(),
        "policy": {"owned_dialog_only": True, "no_dump_login": True},
        "verdict": "",
        "next": [],
    }
    if not token:
        report["verdict"] = "❌ Thiếu TELEGRAM_BOT_TOKEN"
        return report

    if open_chat and chat:
        report["dialog"] = open_dialog(token, chat)
    elif open_chat and not chat:
        report["dialog"] = {"ok": False, "error": "Thiếu TELEGRAM_CHAT_ID"}

    pull_res = pull(token, chat_id=chat or None, lookback=lookback, wait=wait)
    report["pull"] = {
        "ok": pull_res.get("ok"),
        "updates_n": pull_res.get("updates_n"),
        "hits_n": pull_res.get("hits_n"),
        "downloaded": pull_res.get("downloaded"),
        "hits": pull_res.get("hits"),
        "offset": pull_res.get("offset"),
        "pending": pull_res.get("pending"),
        "error": pull_res.get("error"),
    }
    parsed = pull_res.get("parsed")
    report["captcha"] = parsed

    # re-parse pending json if present
    if PENDING_JSON.is_file() and (not parsed or parsed.get("is_api_error") or not parsed.get("ok")):
        report["captcha"] = parse_captcha_payload(PENDING_JSON.read_text(encoding="utf-8", errors="ignore"))
        report["captcha"]["path"] = str(PENDING_JSON)

    cap = report.get("captcha") or {}
    if cap.get("ok"):
        report["ok"] = True
        report["verdict"] = f"Hộp thoại TG → {cap.get('verdict')}"
        report["next"] = [
            "Dùng mã: secrets/captcha.pending.txt hoặc captcha.pending.json",
            "python3 scripts/telegram_captcha_pull.py status",
        ]
    elif cap.get("is_api_error"):
        report["verdict"] = (
            f"Hộp thoại TG có captcha.json nhưng là lỗi API · {cap.get('msg')} — "
            "cần gọi lại API lấy captcha (đúng method) rồi gửi lại file/ảnh"
        )
        report["next"] = [
            "Gửi lại ảnh captcha hoặc captcha:CODE vào chat bot",
            "python3 scripts/telegram_captcha_pull.py pull --wait 60 --no-open",
        ]
    elif (pull_res.get("hits_n") or 0) == 0:
        report["verdict"] = (
            "📬 Đã mở hộp thoại — chưa thấy captcha mới. "
            "Gửi captcha.json / ảnh / captcha:CODE vào chat bot."
        )
        report["next"] = [
            "python3 scripts/telegram_captcha_pull.py pull --wait 90 --no-open",
        ]
    else:
        report["verdict"] = cap.get("verdict") or "⚠ Có file captcha nhưng chưa parse được mã"
        report["next"] = ["Kiểm tra quarantine/telegram/_captcha/"]

    if notify and chat:
        try:
            api(token, "sendMessage", {"chat_id": chat, "text": "🔐 Captcha pull\n\n" + report["verdict"][:3500]})
            report["notified"] = True
        except Exception as e:  # noqa: BLE001
            report["notify_error"] = str(e)[:120]

    write_outputs(report)
    return report


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    jp = REPORTS / "telegram_captcha_pull.json"
    tp = REPORTS / "telegram_captcha_pull.txt"
    jp.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    return {"json": str(jp), "txt": str(tp)}


def format_text(report: dict[str, Any]) -> str:
    lines = [
        "🔐 TELEGRAM · MỞ HỘP THOẠI LẤY CAPTCHA",
        f"Lúc: {report.get('checked_at')}",
        f"Verdict: {report.get('verdict')}",
    ]
    dlg = report.get("dialog") or {}
    if dlg:
        lines.append(f"dialog: ok={dlg.get('ok')} message_id={dlg.get('message_id')}")
    pull = report.get("pull") or {}
    lines.append(
        f"pull: updates={pull.get('updates_n')} hits={pull.get('hits_n')} "
        f"downloaded={len(pull.get('downloaded') or [])}"
    )
    for d in (pull.get("downloaded") or [])[:8]:
        lines.append(f"  · {d.get('orig') or d.get('file')} ({d.get('size')}B)")
    cap = report.get("captcha") or {}
    if cap:
        lines.append(
            f"captcha: ok={cap.get('ok')} kind={cap.get('kind')} "
            f"code={cap.get('code') or '—'} api_error={cap.get('is_api_error')}"
        )
        if cap.get("msg"):
            lines.append(f"  msg: {cap.get('msg')}")
    for n in report.get("next") or []:
        lines.append(f"Next: {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Mở hộp thoại Telegram lấy captcha")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_open = sub.add_parser("open", help="Chỉ mở hộp thoại (gửi prompt)")
    p_open.add_argument("--json", action="store_true")

    p_pull = sub.add_parser("pull", help="Kéo captcha từ updates")
    p_pull.add_argument("--wait", type=int, default=0, help="Long-poll giây")
    p_pull.add_argument("--lookback", type=int, default=200)
    p_pull.add_argument("--no-open", action="store_true")
    p_pull.add_argument("--notify", action="store_true")
    p_pull.add_argument("--json", action="store_true")

    p_run = sub.add_parser("run", help="Mở hộp thoại + kéo captcha")
    p_run.add_argument("--wait", type=int, default=0)
    p_run.add_argument("--lookback", type=int, default=200)
    p_run.add_argument("--no-open", action="store_true")
    p_run.add_argument("--notify", action="store_true")
    p_run.add_argument("--json", action="store_true")

    p_st = sub.add_parser("status", help="Báo cáo gần nhất / pending")
    p_st.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    env = load_env()
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()

    if args.cmd == "open":
        if not token or not chat:
            print("❌ Thiếu TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID", file=sys.stderr)
            return 2
        report = {"ok": False, "checked_at": utc_now(), "dialog": open_dialog(token, chat)}
        report["ok"] = bool(report["dialog"].get("ok"))
        report["verdict"] = (
            f"✅ Đã mở hộp thoại · mid={report['dialog'].get('message_id')}"
            if report["ok"]
            else f"❌ {report['dialog'].get('error')}"
        )
        write_outputs(report)
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else report["verdict"])
        return 0 if report["ok"] else 1

    if args.cmd == "status":
        jp = REPORTS / "telegram_captcha_pull.json"
        pending = {}
        if PENDING_JSON.is_file():
            pending["json"] = parse_captcha_payload(PENDING_JSON.read_text(encoding="utf-8", errors="ignore"))
        if PENDING_TXT.is_file():
            pending["txt"] = PENDING_TXT.read_text(encoding="utf-8").strip()
        if PENDING_IMG.is_file():
            pending["img_bytes"] = PENDING_IMG.stat().st_size
        if args.json:
            data = json.loads(jp.read_text(encoding="utf-8")) if jp.is_file() else {}
            data["pending_now"] = pending
            print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        else:
            if jp.is_file():
                data = json.loads(jp.read_text(encoding="utf-8"))
                print(data.get("verdict"))
            else:
                print("Chưa có báo cáo")
            if pending.get("json"):
                print("pending.json:", pending["json"].get("verdict"))
            if pending.get("txt"):
                print("pending.txt:", pending["txt"])
            if pending.get("img_bytes"):
                print("pending.img bytes:", pending["img_bytes"])
        ok = bool((pending.get("json") or {}).get("ok") or pending.get("txt"))
        return 0 if ok else 1

    report = build_report(
        open_chat=not args.no_open,
        wait=int(args.wait),
        lookback=int(args.lookback),
        notify=bool(args.notify),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
