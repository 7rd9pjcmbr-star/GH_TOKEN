#!/usr/bin/env python3
"""Fetch đơn từ J&T Lên đơn portal (lendon.jtexpress.vn/order) → KET_QUA.

Owned-only: secrets/jt_lendon.env (JT_LENDON_USER + JT_LENDON_PASSWORD).
Không login bằng password từ jt_parsed dump.

API nội bộ portal (October CMS):
  - Login: POST /home-page  handler onSigninV2
  - Danh sách: POST /admin/posts/get-data  (DataTables server-side)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
INBOX = ROOT / "quarantine" / "telegram"

TOKEN_RE = re.compile(r'name="_token"\s+type="hidden"\s+value="([^"]+)"', re.I)
FILE_ID_RE = re.compile(r'id="file_id"[^>]*value="([^"]*)"', re.I)
LOGIN_FORM_RE = re.compile(r'id="form-login"|name="login"', re.I)
BILL_IN_HTML_RE = re.compile(r">(\s*84[0-9]{10,12}\s*)<", re.I)
PHONE_RE = re.compile(r"(?:0|\+84)(?:3|5|7|8|9)\d{8}")
TAG_RE = re.compile(r"<[^>]+>")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chmod600(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def import_lendon_files_from_inbox() -> list[str]:
    imported: list[str] = []
    for src in sorted(INBOX.glob("*jt_lendon*.env")) + [INBOX / "jt_lendon.env"]:
        if src.is_file() and src.stat().st_size > 0:
            dest = SECRETS / "jt_lendon.env"
            dest.write_bytes(src.read_bytes())
            _chmod600(dest)
            imported.append(src.name)
            break
    for pat in ("*jt_lendon_session*.json", "*lendon*session*.json", "*lendon*cookies*.txt"):
        for src in sorted(INBOX.glob(pat)):
            if src.is_file() and src.stat().st_size > 0:
                try:
                    import_session_file(src)
                    imported.append(src.name)
                except Exception:  # noqa: BLE001
                    pass
    return imported


JT_CUSTOMER_CODE_RE = re.compile(r"\b(\d{3}[A-Z]{2}\d{4,8})\b", re.I)
_LENDON_USER_LABEL_RE = re.compile(
    r"^(?:user(?:name)?|login|tk|tài\s*khoản|tai\s*khoan|mã\s*kh|ma\s*kh|mã\s*khách\s*hàng|ma\s*khach\s*hang)\s*[:=]\s*(.+)$",
    re.I,
)
_LENDON_PASS_LABEL_RE = re.compile(
    r"^(?:pass(?:word)?|mk|pwd|mật\s*khẩu|mat\s*khau)\s*[:=]\s*(.+)$",
    re.I,
)


PAIR_STATE_PATH = SECRETS / "jt_lendon_pair.state.json"


def _load_pair_state() -> dict[str, str]:
    if not PAIR_STATE_PATH.is_file():
        return {}
    try:
        return json.loads(PAIR_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _save_pair_state(user: str) -> None:
    SECRETS.mkdir(parents=True, exist_ok=True)
    PAIR_STATE_PATH.write_text(
        json.dumps({"user": user, "saved_at": utc_now()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _chmod600(PAIR_STATE_PATH)


def _clear_pair_state() -> None:
    try:
        PAIR_STATE_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def parse_lendon_credentials_text(text: str) -> dict[str, str] | None:
    """Parse JT_LENDON_USER/PASSWORD từ nhiều format Telegram (env, user:pass, 2 dòng)."""
    text = (text or "").strip()
    if not text or len(text) > 8000:
        return None
    if "hotcleaner" in text.lower():
        return None

    user = ""
    password = ""

    for line in text.splitlines():
        t = line.strip().rstrip(",")
        if not t or t.startswith("#"):
            continue
        if "JT_LENDON_USER" in t and "=" in t:
            user = t.split("=", 1)[1].strip().strip('"').strip("'")
        elif "JT_LENDON_PASSWORD" in t and "=" in t:
            password = t.split("=", 1)[1].strip().strip('"').strip("'")
        elif "JT_LENDON_LOGIN" in t and "=" in t and not user:
            user = t.split("=", 1)[1].strip().strip('"').strip("'")
        elif '"username"' in t.lower() or '"user"' in t.lower():
            m = re.search(r'"username"\s*:\s*"([^"]+)"', t, re.I) or re.search(r'"user"\s*:\s*"([^"]+)"', t, re.I)
            if m:
                user = m.group(1).strip()
        elif '"password"' in t.lower() or '"pass"' in t.lower():
            m = re.search(r'"password"\s*:\s*"([^"]+)"', t, re.I) or re.search(r'"pass"\s*:\s*"([^"]+)"', t, re.I)
            if m:
                password = m.group(1).strip()

    if user and password:
        return {"JT_LENDON_USER": user.upper() if JT_CUSTOMER_CODE_RE.fullmatch(user.strip()) else user.strip(), "JT_LENDON_PASSWORD": password}

    for line in text.splitlines():
        ln = line.strip()
        if not ln:
            continue
        mu = _LENDON_USER_LABEL_RE.match(ln)
        if mu:
            user = mu.group(1).strip()
            continue
        mp = _LENDON_PASS_LABEL_RE.match(ln)
        if mp:
            password = mp.group(1).strip()

    if user and password:
        return {"JT_LENDON_USER": user, "JT_LENDON_PASSWORD": password}

    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or raw.startswith("http"):
            continue
        low = raw.lower()
        if any(x in low for x in ("october_session", "ci_session", "cookie")) and "jtexpress" in low:
            continue
        if raw.startswith(("{", "[")):
            continue
        # lendon.jtexpress.vn/home-page:USER:PASS hoặc url:USER:PASS
        if "jtexpress" in low and raw.count(":") >= 2:
            parts = raw.split(":")
            # host/path:user:pass — lấy 2 segment cuối
            cand_user, cand_pass = parts[-2].strip(), parts[-1].strip()
            if JT_CUSTOMER_CODE_RE.fullmatch(cand_user) and len(cand_pass) >= 3:
                return {"JT_LENDON_USER": cand_user, "JT_LENDON_PASSWORD": cand_pass}
        if ":" not in raw:
            continue
        left, right = raw.split(":", 1)
        left, right = left.strip(), right.strip()
        if not right or len(right) < 3:
            continue
        if JT_CUSTOMER_CODE_RE.fullmatch(left) or re.fullmatch(r"[A-Za-z0-9]{4,24}", left):
            return {"JT_LENDON_USER": left, "JT_LENDON_PASSWORD": right}

    lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#") and not ln.strip().startswith("http")
    ]
    if len(lines) == 2:
        a, b = lines[0], lines[1]
        if JT_CUSTOMER_CODE_RE.fullmatch(a) and len(b) >= 3 and ":" not in b:
            return {"JT_LENDON_USER": a, "JT_LENDON_PASSWORD": b}

    return None


def save_lendon_credentials(user: str, password: str) -> Path:
    """Ghi user/pass vào secrets/jt_lendon.env (giữ comment và key khác)."""
    dest = SECRETS / "jt_lendon.env"
    if not dest.is_file():
        ensure_lendon_env()
    lines = dest.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[str] = []
    seen_user = seen_pass = False
    for ln in lines:
        if ln.startswith("JT_LENDON_USER="):
            out.append(f"JT_LENDON_USER={user}")
            seen_user = True
        elif ln.startswith("JT_LENDON_PASSWORD="):
            out.append(f"JT_LENDON_PASSWORD={password}")
            seen_pass = True
        else:
            out.append(ln)
    if not seen_user:
        out.append(f"JT_LENDON_USER={user}")
    if not seen_pass:
        out.append(f"JT_LENDON_PASSWORD={password}")
    dest.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    _chmod600(dest)
    return dest


def clear_stale_lendon_session() -> bool:
    """Xóa session cookie cũ trước login user/pass."""
    path = _session_path()
    if not path.is_file():
        return False
    bak = path.with_suffix(".json.bak")
    try:
        bak.write_bytes(path.read_bytes())
        path.unlink()
        _chmod600(bak)
        return True
    except OSError:
        return False


def import_credentials_message(text: str, *, source: str = "telegram") -> dict[str, Any]:
    """Parse credential từ 1 tin — hỗ trợ gửi mã KH và mật khẩu ở 2 tin riêng."""
    creds = parse_lendon_credentials_text(text)
    if creds:
        _clear_pair_state()
        return import_credentials_paste(text, source=source)

    line = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    if not line or line.startswith("http") or line.startswith(("{", "[")):
        return {"ok": False, "error": "no_credentials_parsed", "source": source}

    if JT_CUSTOMER_CODE_RE.fullmatch(line):
        _save_pair_state(line.upper())
        return {
            "ok": False,
            "error": "awaiting_password",
            "user": line.upper(),
            "hint": "Đã nhận mã KH — gửi tin tiếp theo chỉ mật khẩu",
            "source": source,
        }

    state = _load_pair_state()
    pending_user = (state.get("user") or "").strip()
    if pending_user and ":" not in line and len(line) >= 3:
        _clear_pair_state()
        return import_credentials_paste(f"{pending_user}\n{line}", source=source)

    return {"ok": False, "error": "no_credentials_parsed", "source": source}


def import_credentials_paste(text: str, *, source: str = "paste") -> dict[str, Any]:
    creds = parse_lendon_credentials_text(text)
    if not creds:
        return {"ok": False, "error": "no_credentials_parsed", "source": source}
    user = creds["JT_LENDON_USER"]
    password = creds["JT_LENDON_PASSWORD"]
    save_lendon_credentials(user, password)
    cleared = clear_stale_lendon_session()
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    inbox_copy = INBOX / f"{day}_jt_lendon.env"
    inbox_copy.write_text(
        f"JT_LENDON_BASE_URL=https://lendon.jtexpress.vn\n"
        f"JT_LENDON_USER={user}\n"
        f"JT_LENDON_PASSWORD={password}\n",
        encoding="utf-8",
    )
    return {
        "ok": True,
        "source": source,
        "user": user,
        "password_len": len(password),
        "session_cleared": cleared,
        "inbox_copy": inbox_copy.name,
    }


def import_credentials_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"ok": False, "error": "file_not_found", "file": str(path)}
    text = path.read_text(encoding="utf-8", errors="replace")
    rep = import_credentials_paste(text, source=path.name)
    rep["file"] = path.name
    return rep


def _lendon_domain(domain: str) -> str:
    d = (domain or "").strip() or "lendon.jtexpress.vn"
    if d in ("/", "TRUE", "FALSE"):
        return "lendon.jtexpress.vn"
    if not d.startswith(".") and "jtexpress" in d:
        return d.lstrip(".")
    return d


def _is_lendon_cookie(name: str, domain: str) -> bool:
    n = (name or "").lower()
    d = (domain or "").lower()
    if n in {"october_session", "ci_session", "xsrf-token", "cookie_prefix"}:
        return True
    return "jtexpress" in d


def _cookie_row(name: str, value: str, *, domain: str = "", path: str = "/", expires: str = "") -> dict[str, str]:
    return {
        "name": name.strip(),
        "value": value.strip(),
        "domain": _lendon_domain(domain),
        "path": path or "/",
        **({"expires": expires} if expires else {}),
    }


def parse_cookie_editor_export(text: str) -> list[dict[str, str]]:
    """Cookie Editor extension — JSON array, Header String, hoặc Playwright storage."""
    text = (text or "").strip()
    if not text:
        return []

    cookies: list[dict[str, str]] = []

    # JSON: Cookie Editor export `[{name,value,domain,...}]` hoặc `{"cookies":[...]}`
    if text.startswith("{") or text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if data is not None:
            raw: list = []
            if isinstance(data, list):
                raw = data
            elif isinstance(data, dict):
                if isinstance(data.get("cookies"), list):
                    raw = data["cookies"]
                elif "platforms" in data:
                    for plat in data.get("platforms", {}).values():
                        if isinstance(plat, dict):
                            raw.extend(plat.get("cookies") or [])
            for c in raw:
                if not isinstance(c, dict):
                    continue
                name = str(c.get("name") or "")
                domain = str(c.get("domain") or c.get("host") or "")
                if not _is_lendon_cookie(name, domain):
                    continue
                exp = c.get("expirationDate") or c.get("expires") or c.get("expiry") or ""
                cookies.append(
                    _cookie_row(
                        name,
                        str(c.get("value") or ""),
                        domain=domain,
                        path=str(c.get("path") or "/"),
                        expires=str(exp) if exp else "",
                    )
                )
            if cookies:
                return _dedupe_cookies(cookies)

    # Header String: october_session=...; XSRF-TOKEN=...
    if "october_session=" in text.lower() or ("jtexpress" in text.lower() and "=" in text and ";" in text):
        for part in re.split(r"[;\n]", text):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, value = part.split("=", 1)
            name, value = name.strip(), value.strip()
            if not name or not value:
                continue
            if not _is_lendon_cookie(name, "jtexpress.vn"):
                continue
            cookies.append(_cookie_row(name, value))
        if cookies:
            return _dedupe_cookies(cookies)

    return []


def _dedupe_cookies(cookies: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for c in cookies:
        key = (c["name"], c.get("domain", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def parse_cookie_paste(text: str) -> list[dict[str, str]]:
    """Parse cookie paste: Cookie Editor JSON/Header, Netscape tab, DevTools dòng đơn."""
    text = (text or "").strip()
    if not text:
        return []

    editor = parse_cookie_editor_export(text)
    if editor:
        return editor

    cookies: list[dict[str, str]] = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]

    for line in lines:
        if "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 7:
                cookies.append(
                    {
                        "name": parts[5],
                        "value": parts[6],
                        "domain": parts[0] if parts[0].startswith(".") else f".{parts[0].lstrip('.')}",
                        "path": parts[2] or "/",
                        "expires": parts[4],
                    }
                )
            continue

        # '/ TRUE 1705230539 cookie_prefix <value>' hoặc tương tự
        m = re.match(
            r"^(?:(?P<domain>[^\s]+)\s+)?(?P<flag>TRUE|FALSE)\s+(?P<exp>\d+)\s+(?P<name>[^\s]+)\s+(?P<value>.+)$",
            line,
            re.I,
        )
        if m:
            name = m.group("name")
            value = m.group("value").strip()
            domain = m.group("domain") or ".jtexpress.vn"
            if domain in ("/", "TRUE", "FALSE"):
                domain = ".jtexpress.vn"
            if not domain.startswith(".") and "jtexpress" in domain:
                domain = f".{domain.lstrip('.')}"
            cookies.append(
                {
                    "name": name,
                    "value": value,
                    "domain": domain if domain.startswith(".") else ".jtexpress.vn",
                    "path": "/",
                    "expires": m.group("exp"),
                }
            )
            # CodeIgniter: cookie_prefix label → thử ci_session
            if name.lower() in {"cookie_prefix", "prefix"}:
                cookies.append(
                    {
                        "name": "ci_session",
                        "value": value,
                        "domain": ".jtexpress.vn",
                        "path": "/",
                        "expires": m.group("exp"),
                    }
                )
            continue

        # URL/query paste: october_session+eyJ...&qs=... hoặc october_session=eyJ...&...
        if "october_session" in line.lower() and "eyj" in line.lower():
            m_url = re.search(
                r"october_session[+=%\s]+(?P<val>eyJ[A-Za-z0-9%_.+/=-]+)",
                line,
                re.I,
            )
            if m_url:
                from urllib.parse import unquote

                val = unquote(unquote(m_url.group("val").split("&")[0].strip()))
                cookies.append(
                    {
                        "name": "october_session",
                        "value": val,
                        "domain": ".jtexpress.vn",
                        "path": "/",
                    }
                )
                continue

        # raw: october_session=value hoặc october_session <value>
        if "=" in line and line.count(" ") < 3 and not line.strip().startswith("http"):
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().split("&")[0]
            if name.lower().startswith("october_session") and name.lower() != "october_session":
                # october_session+eyJ... malformed name
                m_fix = re.search(r"eyJ[A-Za-z0-9%_.+/=-]+", name + "=" + value, re.I)
                if m_fix:
                    from urllib.parse import unquote

                    val = unquote(unquote(m_fix.group(0).split("&")[0]))
                    cookies.append(
                        {"name": "october_session", "value": val, "domain": ".jtexpress.vn", "path": "/"}
                    )
                    continue
            cookies.append({"name": name, "value": value, "domain": ".jtexpress.vn", "path": "/"})
            continue

        # 'october_session eyJ...' hoặc 'ci_session eyJ...' (space-separated — hay gặp khi copy từ DevTools)
        m2 = re.match(
            r"^(?P<name>october_session|ci_session|cookie_prefix|xsrf-token|XSRF-TOKEN)\s+(?P<value>.+)$",
            line,
            re.I,
        )
        if m2:
            name = m2.group("name").lower()
            if name == "cookie_prefix":
                name = "cookie_prefix"
            cookies.append(
                {
                    "name": name,
                    "value": m2.group("value").strip(),
                    "domain": "lendon.jtexpress.vn",
                    "path": "/",
                }
            )
            continue

    return _dedupe_cookies(cookies)


def import_cookie_paste(text: str, *, source: str = "paste") -> dict[str, Any]:
    cookies = parse_cookie_paste(text)
    if not cookies:
        return {"ok": False, "error": "unparseable_cookie_paste"}

    # Bổ sung alias phổ biến cho J&T portals
    extra: list[dict[str, str]] = []
    for c in cookies:
        val = c.get("value") or ""
        name = (c.get("name") or "").lower()
        if name in {"october_session", "ci_session", "cookie_prefix"} or val.startswith(("a%3A", "a:", "eyJ")):
            for alias in ("ci_session", "october_session"):
                extra.append({**c, "name": alias, "domain": c.get("domain") or "lendon.jtexpress.vn"})
        if name in {"xsrf-token", "x-xsrf-token"}:
            extra.append({**c, "name": "XSRF-TOKEN", "domain": c.get("domain") or "lendon.jtexpress.vn"})
    cookies = _dedupe_cookies(cookies + extra)

    dest = _session_path()
    SECRETS.mkdir(parents=True, exist_ok=True)
    payload = {"saved_at": utc_now(), "source": source, "cookies": cookies}
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _chmod600(dest)

    exp_notes = []
    for c in cookies:
        exp = c.get("expires")
        if exp and str(exp).isdigit() and int(exp) < int(datetime.now(timezone.utc).timestamp()):
            exp_notes.append(f"{c.get('name')}_expired_{exp}")

    return {
        "ok": True,
        "cookies": len(cookies),
        "path": str(dest),
        "names": sorted({c["name"] for c in cookies}),
        "warnings": exp_notes or None,
    }


def _is_hotcleaner_encrypted_export(text: str) -> bool:
    """Cookie Manager / Hot Cleaner — blob mã hóa, không phải Cookie Editor chuẩn."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    blob = data.get("data")
    url = str(data.get("url") or "").lower()
    return isinstance(blob, str) and len(blob) > 80 and ("hotcleaner" in url or data.get("version") == 2)


def import_session_file(path: Path) -> dict[str, Any]:
    """Import october_session từ Cookie Editor JSON, Playwright storage_state, cookies.txt."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if _is_hotcleaner_encrypted_export(text):
        return {
            "ok": False,
            "error": "hotcleaner_encrypted_export",
            "hint": (
                "File cookies.json là bản mã hóa Hot Cleaner — không dùng được. "
                "Mở lendon.jtexpress.vn (đã login) → Cookie Editor → Export → chọn JSON "
                "(phải thấy october_session trong danh sách)."
            ),
            "file": path.name,
        }
    cookies = parse_cookie_editor_export(text) or parse_cookie_paste(text)

    if not cookies and path.suffix.lower() != ".json":
        for line in text.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 7 and _is_lendon_cookie(parts[5], parts[0]):
                cookies.append(
                    _cookie_row(parts[5], parts[6], domain=parts[0], path=parts[2] or "/", expires=parts[4])
                )
        cookies = _dedupe_cookies(cookies)

    if not cookies:
        return {"ok": False, "error": "no_cookies_parsed", "file": path.name}

    rep = import_cookie_paste(
        json.dumps([{"name": c["name"], "value": c["value"], "domain": c.get("domain", ""), "path": c.get("path", "/")} for c in cookies]),
        source=path.name,
    )
    return rep if rep.get("ok") else {"ok": False, "error": rep.get("error", "import_failed"), "file": path.name}


def load_lendon_env() -> dict[str, str]:
    try:
        import_lendon_files_from_inbox()
    except OSError:
        pass
    env = dict(os.environ)
    for path in (
        SECRETS / "jt_lendon.env",
        SECRETS / "backend_pipes.env",
        SECRETS / "jt_api.env",
    ):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    base = (env.get("JT_LENDON_BASE_URL") or "https://lendon.jtexpress.vn").strip().rstrip("/")
    env["JT_LENDON_BASE_URL"] = base
    if not (env.get("JT_LENDON_USER") or "").strip():
        hint = (env.get("JT_CUSTOMER_CODE") or "").strip()
        if hint:
            env["JT_LENDON_USER"] = hint
    if not (env.get("JT_LENDON_PASSWORD") or "").strip():
        hint = (env.get("JT_PASSWORD") or env.get("JTEXPRESS_PASSWORD") or "").strip()
        if hint:
            env["JT_LENDON_PASSWORD"] = hint
    return env


def lendon_ready(env: dict[str, str] | None = None) -> bool:
    env = env or load_lendon_env()
    user = (env.get("JT_LENDON_USER") or env.get("JT_LENDON_LOGIN") or "").strip()
    password = (env.get("JT_LENDON_PASSWORD") or "").strip()
    if user and password:
        return True
    # Session cookie đã import (user đăng nhập browser rồi gửi cookie)
    path = _session_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for c in data.get("cookies") or []:
                if c.get("name") and c.get("value"):
                    return True
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return False


def _session_path() -> Path:
    return SECRETS / "jt_lendon_session.json"


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = unescape(TAG_RE.sub(" ", str(text)))
    return re.sub(r"\s+", " ", text).strip()


def _extract_bill(raw: str) -> str:
    raw = str(raw or "")
    m = BILL_IN_HTML_RE.search(raw)
    if m:
        return m.group(1).strip()
    plain = _strip_html(raw)
    m2 = re.search(r"\b84[0-9]{10,12}\b", plain)
    return m2.group(0) if m2 else plain


class LendonClient:
    def __init__(self, env: dict[str, str] | None = None) -> None:
        import requests

        self.env = env or load_lendon_env()
        self.base = self.env["JT_LENDON_BASE_URL"]
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "AssassinTool-Lendon/1.0",
                "Accept": "text/html,application/json,*/*",
                "Accept-Language": "vi-VN,vi;q=0.9",
            }
        )
        self._load_session()

    def _load_session(self) -> None:
        path = _session_path()
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for c in data.get("cookies") or []:
                self.session.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            pass

    def _save_session(self) -> None:
        SECRETS.mkdir(parents=True, exist_ok=True)
        cookies = []
        for c in self.session.cookies:
            cookies.append({"name": c.name, "value": c.value, "domain": c.domain, "path": c.path})
        payload = {"saved_at": utc_now(), "cookies": cookies}
        path = _session_path()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _chmod600(path)

    def _extract_token(self, html: str) -> str:
        m = TOKEN_RE.search(html or "")
        return m.group(1) if m else ""

    def _is_login_page(self, html: str) -> bool:
        return bool(LOGIN_FORM_RE.search(html or ""))

    def _get_page(self, path: str) -> tuple[int, str]:
        url = f"{self.base}{path}"
        r = self.session.get(url, timeout=45, allow_redirects=True)
        return r.status_code, r.text

    def session_valid(self) -> bool:
        code, html = self._get_page("/order")
        if code != 200:
            return False
        return not self._is_login_page(html)

    def login(self) -> dict[str, Any]:
        user = (self.env.get("JT_LENDON_USER") or self.env.get("JT_LENDON_LOGIN") or "").strip()
        password = (self.env.get("JT_LENDON_PASSWORD") or "").strip()

        if self.session_valid():
            return {"ok": True, "via": "cached_session"}

        if not user or not password:
            path = _session_path()
            if path.is_file():
                return {
                    "ok": False,
                    "error": "session_expired_or_invalid",
                    "hint": "Cookie đã import nhưng không còn hiệu lực — export cookie october_session MỚI từ lendon.jtexpress.vn sau khi login",
                }
            return {"ok": False, "error": "missing_JT_LENDON_USER_or_PASSWORD"}

        # Cookie cũ có thể chặn login — dùng session sạch khi có user/pass
        self.session.cookies.clear()
        clear_stale_lendon_session()

        code, html = self._get_page("/home-page")
        if code != 200:
            return {"ok": False, "error": f"home_page_http_{code}"}
        token = self._extract_token(html)
        if not token:
            return {"ok": False, "error": "missing_csrf_token"}

        r = self.session.post(
            f"{self.base}/home-page",
            data={
                "_handler": "onSigninV2",
                "_token": token,
                "login": user,
                "password": password,
            },
            headers={
                "X-OCTOBER-REQUEST-HANDLER": "onSigninV2",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.base}/home-page",
            },
            timeout=45,
            allow_redirects=True,
        )

        # October có thể trả JSON rỗng khi sai pass — kiểm tra session thực tế
        if self.session_valid():
            self._save_session()
            return {"ok": True, "via": "login", "http": r.status_code}

        return {
            "ok": False,
            "error": "login_failed",
            "http": r.status_code,
            "hint": "Kiểm tra JT_LENDON_USER (mã KH) và JT_LENDON_PASSWORD trong secrets/jt_lendon.env",
        }

    def _datatables_payload(self, *, start: int, length: int, file_id: str = "", search: str = "") -> dict[str, str]:
        payload: dict[str, str] = {
            "draw": str(1 + start // max(length, 1)),
            "start": str(start),
            "length": str(length),
            "search[value]": search,
            "search[regex]": "false",
            "file_id": file_id or "",
        }
        columns = [
            ("checkbox", False),
            ("bill", True),
            ("receiver_name", True),
            ("sender_name", True),
            ("info", True),
            ("status_print", True),
            ("date", True),
            ("action", False),
        ]
        for i, (name, orderable) in enumerate(columns):
            payload[f"columns[{i}][data]"] = name
            payload[f"columns[{i}][name]"] = name
            payload[f"columns[{i}][searchable]"] = "true"
            payload[f"columns[{i}][orderable]"] = "true" if orderable else "false"
            payload[f"columns[{i}][search][value]"] = ""
            payload[f"columns[{i}][search][regex]"] = "false"
        payload["order[0][column]"] = "6"
        payload["order[0][dir]"] = "desc"
        return payload

    def fetch_orders_page(
        self,
        *,
        start: int = 0,
        length: int = 100,
        file_id: str = "",
        search: str = "",
    ) -> dict[str, Any]:
        code, html = self._get_page("/order")
        if self._is_login_page(html):
            return {"ok": False, "error": "not_authenticated"}
        if not file_id:
            m = FILE_ID_RE.search(html)
            file_id = m.group(1) if m else ""

        token = self._extract_token(html)
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.base}/order",
            "Origin": self.base,
        }
        if token:
            headers["X-CSRF-TOKEN"] = token

        r = self.session.post(
            f"{self.base}/admin/posts/get-data",
            data=self._datatables_payload(start=start, length=length, file_id=file_id, search=search),
            headers=headers,
            timeout=60,
        )
        if r.status_code != 200:
            return {"ok": False, "error": f"get_data_http_{r.status_code}", "body_head": (r.text or "")[:200]}

        try:
            data = r.json()
        except ValueError:
            if self._is_login_page(r.text):
                return {"ok": False, "error": "session_expired"}
            return {"ok": False, "error": "invalid_json", "body_head": (r.text or "")[:200]}

        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return {"ok": False, "error": "unexpected_shape", "keys": list(data.keys())[:12] if isinstance(data, dict) else []}

        total = int(data.get("recordsTotal") or data.get("recordsFiltered") or len(rows) or 0)
        return {"ok": True, "rows": rows, "total": total, "draw": data.get("draw")}

    def fetch_all_orders(self, *, limit: int = 500) -> dict[str, Any]:
        login = self.login()
        if not login.get("ok"):
            return login

        all_rows: list[dict] = []
        page_size = min(150, limit)
        start = 0
        total = 0
        while start < limit:
            chunk = self.fetch_orders_page(start=start, length=page_size)
            if not chunk.get("ok"):
                if all_rows:
                    break
                return chunk
            rows = chunk.get("rows") or []
            total = int(chunk.get("total") or total)
            if not rows:
                break
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            start += page_size

        return {"ok": True, "rows": all_rows[:limit], "total": total, "login": login}


def lendon_row_to_ket_qua(item: dict[str, Any]) -> dict[str, str]:
    bill_raw = str(item.get("bill") or "")
    tracking = _extract_bill(bill_raw)
    receiver = _strip_html(str(item.get("receiver_name") or ""))
    sender = _strip_html(str(item.get("sender_name") or ""))
    info = _strip_html(str(item.get("info") or ""))
    status = _strip_html(str(item.get("status_print") or item.get("status") or ""))
    date = _strip_html(str(item.get("date") or item.get("created_at") or ""))

    phone = ""
    for blob in (info, receiver):
        m = PHONE_RE.search(blob.replace(" ", ""))
        if m:
            phone = m.group(0)
            break

    order_key = tracking or receiver or f"lendon_{hash(json.dumps(item, sort_keys=True, default=str)) & 0xFFFFFFFF:08x}"
    return {
        "order_key": order_key,
        "remote_id": tracking or order_key,
        "tracking_code": tracking,
        "customer_name": receiver,
        "customer_phone": phone,
        "full_address": info,
        "status_normalized": status,
        "status_raw": status,
        "carrier": "J&T",
        "platform": "J&T-Lendon",
        "source": "jt_lendon_portal",
        "channel": "lendon.jtexpress.vn/order",
        "creator": sender,
        "order_created_at": date,
        "synced_at": utc_now(),
    }


def run_fetch(*, apply: bool = True, limit: int | None = None) -> dict[str, Any]:
    # Đồng bộ cookie từ Chrome profile (Cookie Editor / session đang login trên VM)
    try:
        from jt_lendon_chrome_sync import sync_and_fetch as chrome_sync

        chrome_rep = chrome_sync()
        if chrome_rep.get("ok") and int(chrome_rep.get("orders_mapped") or 0) > 0:
            return chrome_rep
    except Exception:  # noqa: BLE001
        pass

    env = load_lendon_env()
    report: dict[str, Any] = {
        "ok": False,
        "module": "jt_lendon_fetch",
        "checked_at": utc_now(),
        "portal": env.get("JT_LENDON_BASE_URL"),
        "orders_mapped": 0,
        "ket_qua_rows": 0,
    }

    if not lendon_ready(env):
        report["blockers"] = [
            "Thiếu secrets/jt_lendon.env — điền JT_LENDON_USER (mã KH) + JT_LENDON_PASSWORD",
            "Hoặc gửi jt_lendon_session.json / cookies.txt (october_session) sau khi đăng nhập browser",
            "Portal: https://lendon.jtexpress.vn/order",
            "Gửi file jt_lendon.env qua Telegram hoặc copy từ secrets/jt_lendon.env.example",
        ]
        report["verdict"] = "Chưa có credential Lendon portal"
        _write_report(report)
        return report

    lim = limit or int(env.get("JT_LENDON_LIMIT") or "500")
    client = LendonClient(env)
    fetched = client.fetch_all_orders(limit=lim)
    report["login"] = fetched.get("login") or {}
    if not fetched.get("ok"):
        report["error"] = fetched.get("error")
        report["hint"] = fetched.get("hint")
        report["verdict"] = "Lendon login/fetch thất bại"
        _write_report(report)
        return report

    raw_rows = fetched.get("rows") or []
    bill_filter = {
        x.strip().upper()
        for x in (env.get("JT_LENDON_BILL_FILTER") or "").replace("\n", ",").split(",")
        if x.strip()
    }

    mapped: list[dict[str, str]] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        row = lendon_row_to_ket_qua(item)
        if bill_filter and row.get("tracking_code", "").upper() not in bill_filter:
            continue
        if row.get("order_key") or row.get("tracking_code"):
            mapped.append(row)

    # dedupe
    out: dict[str, dict] = {}
    for r in mapped:
        key = r.get("tracking_code") or r.get("order_key") or ""
        if key:
            out[key] = r
    rows = list(out.values())
    report["orders_mapped"] = len(rows)
    report["portal_total"] = fetched.get("total", 0)
    report["raw_fetched"] = len(raw_rows)

    if not rows:
        report["verdict"] = "Đăng nhập OK nhưng chưa có đơn trên portal (hoặc filter rỗng)"
        _write_report(report)
        return report

    report["ok"] = True
    report["verdict"] = f"Lendon portal: {len(rows)} đơn"

    if apply:
        try:
            from flex_local_ingest import dedupe_rows, write_exports
            from export_orders_detailed import CSV_FIELDS

            full = []
            for r in rows:
                base = {f: "" for f in CSV_FIELDS}
                base.update({k: str(v) if v is not None else "" for k, v in r.items()})
                full.append(base)
            rep = write_exports(dedupe_rows(full))
            report["ket_qua_rows"] = int(rep.get("ket_qua_rows") or 0)
        except Exception as e:  # noqa: BLE001
            report["ket_qua_error"] = str(e)[:120]

    _write_report(report)
    return report


def _write_report(report: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "jt_lendon_fetch.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def ensure_lendon_env(customer_codes: list[str] | None = None) -> dict[str, Any]:
    """Tạo secrets/jt_lendon.env nếu chưa có; gợi ý user từ customer codes."""
    dest = SECRETS / "jt_lendon.env"
    example = SECRETS / "jt_lendon.env.example"
    created = False
    if not dest.is_file():
        if example.is_file():
            import shutil

            shutil.copy2(example, dest)
        else:
            dest.write_text(
                "JT_LENDON_BASE_URL=https://lendon.jtexpress.vn\nJT_LENDON_USER=\nJT_LENDON_PASSWORD=\n",
                encoding="utf-8",
            )
        created = True
        _chmod600(dest)

    lines = dest.read_text(encoding="utf-8", errors="replace").splitlines()
    has_user = any(ln.startswith("JT_LENDON_USER=") and ln.split("=", 1)[1].strip() for ln in lines)
    hint_written = any("jt_parsed customer code hint" in ln for ln in lines)
    codes = customer_codes or []
    changed = False
    if codes and not has_user and not hint_written:
        lines.append(f"# jt_parsed customer code hint (portal login — KHÔNG dùng password dump): {codes[0]}")
        changed = True
    if created or changed:
        dest.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        _chmod600(dest)

    imported = import_lendon_files_from_inbox()
    filled = {
        "JT_LENDON_USER": False,
        "JT_LENDON_PASSWORD": False,
    }
    for ln in dest.read_text(encoding="utf-8").splitlines():
        if "=" in ln and not ln.strip().startswith("#"):
            k, v = ln.split("=", 1)
            if k.strip() in filled:
                filled[k.strip()] = bool(v.strip())
    return {
        "path": str(dest),
        "created": created,
        "imported": imported,
        "fields": filled,
        "ready": all(filled.values()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="J&T Lendon portal → KET_QUA")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--probe-login", action="store_true", help="Chỉ kiểm tra đăng nhập")
    ap.add_argument("--import-session", type=str, default="", help="Import cookie/session file")
    ap.add_argument("--cookie-paste", type=str, default="", help="Import 1 dòng cookie paste")
    ap.add_argument("--ensure-env", action="store_true", help="Tạo jt_lendon.env nếu thiếu")
    args = ap.parse_args()

    if args.cookie_paste:
        rep = import_cookie_paste(args.cookie_paste, source="cli_paste")
        if args.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
        else:
            print(f"import cookie ok={rep.get('ok')} warnings={rep.get('warnings')}")
        if rep.get("ok"):
            rep2 = run_fetch(apply=True)
            if args.json:
                print(json.dumps(rep2, ensure_ascii=False, indent=2))
            return 0 if rep2.get("ok") else 1
        return 1

    if args.import_session:
        rep = import_session_file(Path(args.import_session))
        if args.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
        else:
            print(f"import session ok={rep.get('ok')} cookies={rep.get('cookies')}")
        if rep.get("ok"):
            rep2 = run_fetch(apply=True)
            if args.json:
                print(json.dumps(rep2, ensure_ascii=False, indent=2))
            return 0 if rep2.get("ok") else 1
        return 1

    if args.ensure_env:
        rep = ensure_lendon_env()
        if args.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
        else:
            print(f"jt_lendon env ready={rep.get('ready')} path={rep.get('path')}")
        return 0

    if args.probe_login:
        env = load_lendon_env()
        if not lendon_ready(env):
            print(json.dumps({"ok": False, "error": "missing_credentials"}, ensure_ascii=False))
            return 1
        rep = LendonClient(env).login()
        if args.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
        else:
            print(f"login ok={rep.get('ok')} via={rep.get('via')} error={rep.get('error')}")
        return 0 if rep.get("ok") else 1

    rep = run_fetch(apply=not args.no_apply, limit=args.limit or None)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(
            f"jt_lendon_fetch ok={rep.get('ok')} mapped={rep.get('orders_mapped')} "
            f"ket_qua={rep.get('ket_qua_rows')}"
        )
        for b in rep.get("blockers") or []:
            print(f"  · {b}")
        if rep.get("error"):
            print(f"  error: {rep.get('error')}")
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
