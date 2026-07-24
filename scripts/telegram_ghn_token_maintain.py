#!/usr/bin/env python3
"""Tìm file ghn_token* trong hộp thoại Telegram → duy trì token live (owned-only).

Luồng:
  1) getUpdates → tải document tên khớp ghn_token / ghn_tokens / GHN_API_TOKEN
  2) Phân loại: DUMP (user:pass:token nhiều dòng) → quarantine, KHÔNG login
  3) OWNED đơn (printA5 / token=UUID / 1 UUID) → ensure + maintain

Owned-only · no dump-login · mask only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

INBOX = ROOT / "quarantine" / "telegram"
DUMPS = INBOX / "_skipped_dumps"
REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
OFFSET_PATH = SECRETS / "telegram_ghn_token_maintain.offset"
STATE_PATH = SECRETS / "telegram_ghn_token_maintain.state.json"

NAME_RE = re.compile(r"(?i)(ghn[_\-]?tokens?|GHN_API_TOKEN|ghn\.session|printA5)")
UUID_RE = re.compile(
    r"(?i)\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)
UP_TOKEN_RE = re.compile(
    r"(?i)^[^:\s]{2,80}:[^:\s]{2,120}:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
PRINT_RE = re.compile(r"(?i)https?://[^\s\"'<>]*ghn\.vn[^\s\"'<>]*printA5[^\s\"'<>]*")
TOKEN_KV_RE = re.compile(
    r"(?i)\b(?:GHN_API_TOKEN|GHN_TOKEN|token)\s*[=:]\s*([0-9a-f-]{36})\b"
)
DUMP_HINT_RE = re.compile(
    r"(?i)(username:password:token|Acc_all|stealer|ghn_tokens|valid_accounts|dump)"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mask(v: str | None, keep: int = 4) -> str | None:
    if not v:
        return None
    t = v.strip()
    if len(t) <= keep * 2:
        return "***"
    return f"{t[:keep]}…{t[-keep:]}(len={len(t)})"


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    for p in (SECRETS / "telegram.env", SECRETS / "backend_pipes.env", SECRETS / "order_session.env"):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def api(token: str, method: str, payload: dict | None = None, timeout: int = 40) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


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


def _safe_name(name: str) -> str:
    out = "".join(c if c.isalnum() or c in "._-+" else "_" for c in name)
    return out[:180] or "ghn_token.bin"


def _read_offset() -> int:
    best = 0
    for op in (
        OFFSET_PATH,
        SECRETS / "telegram_ghn_scan.offset",
        SECRETS / "telegram.offset",
        SECRETS / "telegram_inbox.offset",
        SECRETS / "telegram_inbox_scan.offset",
    ):
        if not op.is_file():
            continue
        try:
            best = max(best, int(op.read_text(encoding="utf-8").strip() or "0"))
        except ValueError:
            pass
    return best


def pull_ghn_token_docs(token: str, *, lookback: int = 500) -> dict[str, Any]:
    """Kéo document tên khớp ghn_token* từ getUpdates."""
    bot_off = _read_offset()
    start = max(0, bot_off - max(0, lookback))
    try:
        data = api(
            token,
            "getUpdates",
            {
                "offset": start,
                "timeout": 0,
                "allowed_updates": ["message", "channel_post", "edited_message"],
            },
            timeout=45,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200], "downloaded": [], "seen": []}

    if not data.get("ok"):
        return {"ok": False, "error": str(data)[:200], "downloaded": [], "seen": []}

    INBOX.mkdir(parents=True, exist_ok=True)
    DUMPS.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict] = []
    seen: list[dict] = []
    max_off = start
    for upd in data.get("result") or []:
        max_off = max(max_off, int(upd["update_id"]) + 1)
        msg = upd.get("message") or upd.get("channel_post") or upd.get("edited_message") or {}
        text = msg.get("text") or msg.get("caption") or ""
        doc = msg.get("document") or {}
        name = doc.get("file_name") or ""
        blob = f"{text}\n{name}"
        name_hit = bool(NAME_RE.search(name) or NAME_RE.search(text))
        if not name_hit and not (doc and re.search(r"(?i)\bghn\b", name)):
            # still note text-only GHN token paste later via classify_text
            if not NAME_RE.search(blob) and "ghn" not in blob.lower():
                continue
        entry = {
            "update_id": upd.get("update_id"),
            "message_id": msg.get("message_id"),
            "chat_id": str((msg.get("chat") or {}).get("id") or ""),
            "document_name": name or None,
            "has_document": bool(doc),
            "text_preview": (text[:160] + ("…" if len(text) > 160 else "")) if text else "",
            "name_match": bool(NAME_RE.search(name)),
        }
        seen.append(entry)
        if not doc or not NAME_RE.search(name):
            continue
        # dump-named → _skipped_dumps; otherwise inbox for review
        dump_named = bool(re.search(r"(?i)ghn_tokens|acc_all|stealer|dump", name))
        dest_dir = DUMPS if dump_named else INBOX
        dest = dest_dir / _safe_name(name)
        if dest.exists() and dest.stat().st_size > 0:
            entry["already"] = str(dest.relative_to(INBOX))
            downloaded.append(
                {
                    "file": dest.name,
                    "path": str(dest.relative_to(INBOX)),
                    "orig_name": name,
                    "size": dest.stat().st_size,
                    "dump_named": dump_named,
                    "already": True,
                }
            )
            continue
        try:
            download_file(token, doc["file_id"], dest)
            downloaded.append(
                {
                    "file": dest.name,
                    "path": str(dest.relative_to(INBOX)),
                    "orig_name": name,
                    "size": dest.stat().st_size,
                    "dump_named": dump_named,
                    "already": False,
                }
            )
        except Exception as e:  # noqa: BLE001
            entry["download_error"] = str(e)[:120]

    SECRETS.mkdir(parents=True, exist_ok=True)
    OFFSET_PATH.write_text(str(max_off), encoding="utf-8")
    return {
        "ok": True,
        "updates_n": len(data.get("result") or []),
        "start_offset": start,
        "saved_offset": max_off,
        "seen": seen[:80],
        "downloaded": downloaded,
    }


def classify_file(path: Path) -> dict[str, Any]:
    """Phân loại file ghn_token*: dump vs owned-single."""
    rel = str(path.relative_to(INBOX)) if path.is_relative_to(INBOX) else str(path)
    out: dict[str, Any] = {
        "path": rel,
        "name": path.name,
        "size": path.stat().st_size if path.is_file() else 0,
        "kind": "unknown",
        "blocked_for_login": False,
        "owned_candidate": False,
        "up_token_lines": 0,
        "bare_uuids": 0,
        "printA5_n": 0,
        "token_kv_n": 0,
        "sample_masked": [],
        "reason": "",
    }
    if not path.is_file():
        out["reason"] = "missing"
        return out
    try:
        text = path.read_bytes()[:800_000].decode("utf-8", errors="ignore")
    except Exception as e:  # noqa: BLE001
        out["reason"] = str(e)[:120]
        return out

    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    up_n = sum(1 for ln in lines if UP_TOKEN_RE.match(ln))
    bare = [ln for ln in lines if UUID_RE.fullmatch(ln)]
    prints = PRINT_RE.findall(text)
    kvs = TOKEN_KV_RE.findall(text)
    out["up_token_lines"] = up_n
    out["bare_uuids"] = len(bare)
    out["printA5_n"] = len(prints)
    out["token_kv_n"] = len(kvs)

    name_dump = bool(re.search(r"(?i)ghn_tokens|acc_all|stealer|dump", path.name))
    body_dump = bool(DUMP_HINT_RE.search(text[:2000]) or up_n >= 3)
    owned_claim = False
    try:
        claim = ROOT / "secrets" / "OWNED_CLAIM_GHN"
        if claim.is_file() and claim.read_text(encoding="utf-8", errors="ignore").strip().lower() in {
            "1",
            "true",
            "yes",
            "i-own-this",
            "owned",
        }:
            owned_claim = True
        if (os.environ.get("OWNED_CLAIM_GHN") or "").strip().lower() in {"1", "true", "yes"}:
            owned_claim = True
    except Exception:  # noqa: BLE001
        pass
    if name_dump or body_dump or up_n >= 3:
        if owned_claim and up_n >= 1 and not re.search(r"(?i)(acc_all|stealer|results_cookies)", path.name):
            # Chủ xác nhận sở hữu — không block; hướng sang owned maintain
            out["kind"] = "OWNED_MULTI"
            out["owned_candidate"] = True
            out["blocked_for_login"] = False
            out["reason"] = (
                f"owned multi-token ×{up_n} (đã claim) — dùng "
                "ghn_tokens_owned_maintain.py --i-own-this"
            )
            for ln in lines[:3]:
                if UP_TOKEN_RE.match(ln):
                    parts = ln.split(":")
                    out["sample_masked"].append(f"{mask(parts[0], 2)}:***:{mask(parts[-1])}")
            out["raw_for_ingest"] = None  # multi → owned maintain, không ingest 1 dòng
            return out
        out["kind"] = "MULTI_TOKEN_LIST"
        out["blocked_for_login"] = True
        out["reason"] = (
            f"list user:pass:token ×{up_n} — heuristic gắn dump; "
            "nếu là của bạn: python3 scripts/ghn_tokens_owned_maintain.py --i-own-this"
        )
        for ln in lines[:3]:
            if UP_TOKEN_RE.match(ln):
                parts = ln.split(":")
                out["sample_masked"].append(f"{mask(parts[0], 2)}:***:{mask(parts[-1])}")
        return out

    # owned single shapes
    if len(prints) == 1 or (len(prints) >= 1 and len(prints) <= 3 and up_n == 0):
        out["kind"] = "OWNED_PRINTA5"
        out["owned_candidate"] = True
        out["reason"] = "printA5 URL owned candidate"
        out["raw_for_ingest"] = prints[0]
        out["sample_masked"] = [re.sub(r"(?i)(token=)([0-9a-f-]{36})", lambda m: m.group(1) + mask(m.group(2)), prints[0])]
        return out
    if len(kvs) == 1 and up_n == 0:
        out["kind"] = "OWNED_KV"
        out["owned_candidate"] = True
        out["reason"] = "GHN_API_TOKEN=UUID owned candidate"
        out["raw_for_ingest"] = f"token={kvs[0]}"
        out["sample_masked"] = [mask(kvs[0])]
        return out
    if len(bare) == 1 and up_n == 0 and len(lines) <= 5:
        out["kind"] = "OWNED_UUID"
        out["owned_candidate"] = True
        out["reason"] = "single UUID owned candidate"
        out["raw_for_ingest"] = f"token={bare[0]}"
        out["sample_masked"] = [mask(bare[0])]
        return out
    if up_n == 1 and len(lines) <= 3:
        # single user:pass:token — still treat as dump-login style unless explicitly owned file name
        out["kind"] = "DUMP_SINGLE"
        out["blocked_for_login"] = True
        out["reason"] = "1 dòng user:pass:token — không dump-login (cần printA5/token thuần owned)"
        return out

    out["kind"] = "REVIEW"
    out["reason"] = "không nhận diện owned đơn / dump rõ"
    return out


def find_ghn_token_files() -> list[Path]:
    if not INBOX.is_dir():
        return []
    hits: list[Path] = []
    for p in INBOX.rglob("*"):
        if not p.is_file() or p.name.startswith("."):
            continue
        if NAME_RE.search(p.name) or NAME_RE.search(str(p.relative_to(INBOX))):
            hits.append(p)
    return sorted(hits, key=lambda x: x.stat().st_mtime, reverse=True)


def quarantine_if_dump(path: Path, cls: dict[str, Any]) -> dict[str, Any] | None:
    if not cls.get("blocked_for_login"):
        return None
    if path.parent == DUMPS:
        return {"path": str(path.relative_to(INBOX)), "already": True}
    if path.parent != INBOX:
        return None
    DUMPS.mkdir(parents=True, exist_ok=True)
    dest = DUMPS / path.name
    if dest.exists():
        dest = DUMPS / f"{path.stem}_{int(datetime.now(timezone.utc).timestamp())}{path.suffix}"
    try:
        path.rename(dest)
        return {"from": path.name, "to": str(dest.relative_to(INBOX))}
    except OSError as e:
        return {"from": path.name, "error": str(e)[:120]}


def try_maintain_owned(cls: dict[str, Any]) -> dict[str, Any]:
    """Nhúng owned candidate → ensure GHN → maintain."""
    raw = cls.get("raw_for_ingest") or ""
    if not raw or cls.get("blocked_for_login"):
        return {"ok": False, "skipped": True, "reason": "not_owned_candidate"}

    from ghn_cookie_ingest import ingest
    from ghn_access_token_orders import get_token_and_fetch_orders
    from token_session_maintain import maintain_once

    # write pending for ensure loop
    pending = SECRETS / "ghn_session.raw"
    SECRETS.mkdir(parents=True, exist_ok=True)
    pending.write_text(raw.strip() + "\n", encoding="utf-8")
    try:
        os.chmod(pending, 0o600)
    except OSError:
        pass

    ing = ingest(raw, force=False)
    if not ing.get("ok"):
        return {
            "ok": False,
            "step": "ingest",
            "verdict": ing.get("verdict") or ing.get("error"),
            "probe": ing.get("probe"),
        }

    orders = get_token_and_fetch_orders(days=3, limit=20, try_pending=False, resolve_shop=True)
    maint = maintain_once(notify_on_risk=False)
    return {
        "ok": bool(ing.get("ok") and (orders.get("ok") or (orders.get("token") or {}).get("alive"))),
        "step": "ingest+orders+maintain",
        "ingest": {
            "ok": ing.get("ok"),
            "token_masked": (ing.get("apply") or {}).get("token_masked")
            or (ing.get("extracted") or {}).get("chosen_masked"),
            "verdict": ing.get("verdict"),
        },
        "orders": {
            "ok": orders.get("ok"),
            "fetched": (orders.get("orders") or {}).get("fetched"),
            "status": (orders.get("orders") or {}).get("status"),
            "verdict": orders.get("verdict"),
            "shop_id": orders.get("shop_id"),
        },
        "maintain": {
            "ok": maint.get("ok"),
            "ghn_ready": maint.get("ghn_ready"),
            "verdict": maint.get("verdict"),
        },
        "verdict": (
            f"✅ Duy trì GHN từ telegram owned · "
            f"fetched={(orders.get('orders') or {}).get('fetched')} · "
            f"shop={orders.get('shop_id') or '—'}"
        ),
    }


def run(*, lookback: int = 500, try_apply: bool = True) -> dict[str, Any]:
    env = load_env()
    bot = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    report: dict[str, Any] = {
        "ok": False,
        "module": "telegram_ghn_token_maintain",
        "checked_at": utc_now(),
        "pull": None,
        "files": [],
        "quarantined": [],
        "owned_candidates": [],
        "apply": None,
        "policy": {"owned_only": True, "no_dump_login": True},
        "verdict": "",
        "next": [],
    }

    if bot:
        report["pull"] = pull_ghn_token_docs(bot, lookback=lookback)
    else:
        report["pull"] = {"ok": False, "error": "missing TELEGRAM_BOT_TOKEN"}

    files = find_ghn_token_files()
    classified: list[dict] = []
    owned: list[dict] = []
    quarantined: list[dict] = []
    for p in files:
        cls = classify_file(p)
        classified.append({k: v for k, v in cls.items() if k != "raw_for_ingest"})
        moved = quarantine_if_dump(p, cls)
        if moved:
            quarantined.append(moved)
        if cls.get("owned_candidate") and not cls.get("blocked_for_login"):
            owned.append(cls)

    report["files"] = classified
    report["quarantined"] = quarantined
    report["owned_candidates"] = [
        {
            "path": o.get("path"),
            "kind": o.get("kind"),
            "reason": o.get("reason"),
            "sample_masked": o.get("sample_masked"),
        }
        for o in owned
    ]

    dump_n = sum(1 for f in classified if f.get("blocked_for_login"))
    if try_apply and owned:
        # chỉ lấy candidate mới nhất
        report["apply"] = try_maintain_owned(owned[0])
        report["ok"] = bool((report["apply"] or {}).get("ok"))
        report["verdict"] = (report["apply"] or {}).get("verdict") or report["verdict"]
    elif owned:
        report["ok"] = True
        report["verdict"] = (
            f"⚠ Có {len(owned)} owned candidate — chạy với --apply để ensure/maintain"
        )
    else:
        report["ok"] = False
        report["verdict"] = (
            f"❌ Telegram ghn_token: files={len(classified)} · dump_blocked={dump_n} · "
            f"owned_usable=0 — không duy trì được từ dump"
        )
        report["next"] = [
            "Nếu ghn_tokens* là của bạn: python3 scripts/ghn_tokens_owned_maintain.py --i-own-this",
            "hoặc gửi printA5 / token=<UUID> owned còn hạn → secrets/ghn_session.raw",
            "python3 scripts/ghn_access_token_orders.py run --days 3 --limit 50",
        ]

    if dump_n and not owned:
        report["next"] = report.get("next") or []
        report["next"].insert(
            0,
            f"Dump ghn_tokens* đã giữ trong quarantine/telegram/_skipped_dumps ({dump_n} file) — không login",
        )

    # also run maintain status snapshot (no apply dump)
    try:
        from token_session_maintain import maintain_once

        snap = maintain_once(notify_on_risk=False)
        report["maintain_snapshot"] = {
            "ghn_ready": snap.get("ghn_ready"),
            "verdict": snap.get("verdict"),
            "ghn": {
                "alive": (snap.get("ghn") or {}).get("alive"),
                "token_masked": (snap.get("ghn") or {}).get("token_masked"),
                "need": (snap.get("ghn") or {}).get("need"),
            },
        }
    except Exception as e:  # noqa: BLE001
        report["maintain_snapshot"] = {"error": str(e)[:120]}

    _write(report)
    return report


def format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("📦 TELEGRAM · TÌM ghn_token → DUY TRÌ LIVE")
    L(f"Lúc: {report.get('checked_at') or utc_now()}")
    L(report.get("verdict") or "")
    pull = report.get("pull") or {}
    L(
        f"Pull: ok={pull.get('ok')} updates={pull.get('updates_n')} "
        f"downloaded={len(pull.get('downloaded') or [])} err={pull.get('error')}"
    )
    for d in (pull.get("downloaded") or [])[:8]:
        L(
            f"  · {d.get('orig_name')} → {d.get('path')} "
            f"dump_named={d.get('dump_named')} size={d.get('size')}"
        )
    L("")
    L("=== File ghn_token* ===")
    files = report.get("files") or []
    if not files:
        L("(không có)")
    for f in files[:20]:
        flag = "BLOCK_DUMP" if f.get("blocked_for_login") else f.get("kind")
        L(
            f"· [{flag}] {f.get('path')} size={f.get('size')} "
            f"up_lines={f.get('up_token_lines')} bare={f.get('bare_uuids')} "
            f"printA5={f.get('printA5_n')}"
        )
        L(f"  {f.get('reason')}")
        for s in (f.get("sample_masked") or [])[:2]:
            L(f"  sample: {s}")
    if report.get("quarantined"):
        L("")
        L(f"Quarantined: {len(report['quarantined'])}")
        for q in report["quarantined"][:10]:
            L(f"  · {q}")
    if report.get("owned_candidates"):
        L("")
        L(f"Owned candidates: {len(report['owned_candidates'])}")
        for o in report["owned_candidates"]:
            L(f"  · {o.get('path')} · {o.get('kind')} · {o.get('sample_masked')}")
    if report.get("apply"):
        L("")
        L(f"Apply: {report['apply'].get('verdict') or report['apply']}")
    snap = report.get("maintain_snapshot") or {}
    if snap:
        L("")
        L(f"Maintain snapshot: ghn_ready={snap.get('ghn_ready')} · {snap.get('verdict')}")
        g = snap.get("ghn") or {}
        if g:
            L(f"  ghn alive={g.get('alive')} token={g.get('token_masked')} need={g.get('need')}")
    L("")
    L("Policy: owned-only · dump ghn_tokens* → _skipped_dumps · no dump-login")
    if report.get("next"):
        L("Next:")
        for n in report["next"]:
            L(f"· {n}")
    return "\n".join(lines)


def _write(report: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "telegram_ghn_token_maintain.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (REPORTS / "telegram_ghn_token_maintain.txt").write_text(
        format_text(report) + "\n", encoding="utf-8"
    )
    SECRETS.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {
                "checked_at": report.get("checked_at"),
                "verdict": report.get("verdict"),
                "files_n": len(report.get("files") or []),
                "dump_n": sum(1 for f in (report.get("files") or []) if f.get("blocked_for_login")),
                "owned_n": len(report.get("owned_candidates") or []),
                "ghn_ready": (report.get("maintain_snapshot") or {}).get("ghn_ready"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Telegram tìm ghn_token → duy trì live (owned-only)")
    ap.add_argument("--lookback", type=int, default=500)
    ap.add_argument("--apply", action="store_true", help="Nhúng owned candidate → ensure/maintain")
    ap.add_argument("--no-apply", action="store_true", help="Chỉ quét/phân loại")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try_apply = bool(args.apply) and not args.no_apply
    # default: auto-apply if owned found
    if not args.apply and not args.no_apply:
        try_apply = True

    report = run(lookback=args.lookback, try_apply=try_apply)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
