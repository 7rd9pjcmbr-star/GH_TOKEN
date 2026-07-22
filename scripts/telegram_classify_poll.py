#!/usr/bin/env python3
"""
Poll Telegram bot inbox → tải document → phân loại local-only → gửi báo cáo.
Đọc token từ secrets/telegram.env hoặc biến môi trường.
Không gọi breach/OSINT.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from classify_accounts import classify_file, format_telegram_report, write_outputs  # noqa: E402

INBOX = ROOT / "quarantine" / "telegram"
REPORTS = ROOT / "reports" / "telegram-classify"
OFFSET_FILE = ROOT / "secrets" / "telegram.offset"


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
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download_file(token: str, file_id: str, dest: Path) -> Path:
    meta = api(token, "getFile", {"file_id": file_id})
    if not meta.get("ok"):
        raise RuntimeError(meta)
    file_path = meta["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as resp:
        dest.write_bytes(resp.read())
    return dest


def send(token: str, chat_id: str, text: str):
    # Telegram limit ~4096
    chunk = text[:4000]
    return api(token, "sendMessage", {"chat_id": chat_id, "text": chunk})


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


def handle_document(token: str, chat_id: str, doc: dict) -> None:
    name = doc.get("file_name") or f"{doc.get('file_id')}.bin"
    safe = re_safe(name)
    dest = INBOX / safe
    download_file(token, doc["file_id"], dest)
    # Only text-ish
    mime = (doc.get("mime_type") or "").lower()
    if mime and not any(x in mime for x in ("text", "csv", "json", "plain")):
        # still try if extension looks text
        if dest.suffix.lower() not in {".txt", ".csv", ".json", ".log", ".tsv", ".lst"}:
            send(token, chat_id, f"Bỏ qua (không phải text): {name} ({mime})")
            return
    result = classify_file(dest)
    paths = write_outputs(result, REPORTS, dest.stem)
    report = format_telegram_report(result["summary"])
    report += f"\n\nCSV: {paths['csv']}"
    send(token, chat_id, report)


def re_safe(name: str) -> str:
    out = "".join(c if c.isalnum() or c in "._-+" else "_" for c in name)
    return out[:180] or "file.bin"


def handle_text_blob(token: str, chat_id: str, text: str, msg_id: int) -> None:
    # Multi-line identifier:password pasted as message
    lines = [ln for ln in text.splitlines() if ":" in ln or "," in ln]
    if len(lines) < 3:
        return
    dest = INBOX / f"paste_{msg_id}.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = classify_file(dest)
    paths = write_outputs(result, REPORTS, dest.stem)
    send(token, chat_id, format_telegram_report(result["summary"]) + f"\n\nCSV: {paths['csv']}")


def process_updates(token: str, default_chat: str | None, once: bool, wait: int) -> int:
    offset = read_offset()
    processed = 0
    while True:
        try:
            data = api(
                token,
                "getUpdates",
                {"offset": offset, "timeout": wait, "allowed_updates": ["message"]},
                timeout=wait + 10,
            )
        except urllib.error.URLError as e:
            print("poll error", e, flush=True)
            if once:
                return processed
            time.sleep(2)
            continue
        if not data.get("ok"):
            print(data, flush=True)
            return processed
        for upd in data.get("result") or []:
            offset = max(offset, int(upd["update_id"]) + 1)
            write_offset(offset)
            msg = upd.get("message") or {}
            chat_id = str((msg.get("chat") or {}).get("id") or default_chat or "")
            if not chat_id:
                continue
            if msg.get("document"):
                try:
                    handle_document(token, chat_id, msg["document"])
                    processed += 1
                except Exception as e:
                    send(token, chat_id, f"Lỗi phân loại: {e}")
            elif msg.get("text"):
                try:
                    handle_text_blob(token, chat_id, msg["text"], msg.get("message_id") or 0)
                except Exception as e:
                    print("text handle", e, flush=True)
        if once:
            return processed
        if not data.get("result"):
            time.sleep(1)


def main() -> int:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN") or ""
    chat = env.get("TELEGRAM_CHAT_ID") or ""
    if not token:
        print("Missing TELEGRAM_BOT_TOKEN in secrets/telegram.env", file=sys.stderr)
        return 2
    once = "--once" in sys.argv
    wait = 25 if not once else 0
    n = process_updates(token, chat, once=once, wait=wait)
    print(json.dumps({"processed": n}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
