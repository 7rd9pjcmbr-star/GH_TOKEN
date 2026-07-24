#!/usr/bin/env python3
"""GHN SSO JWT (sso-v2) — parse login URL / ingest id_token (owned).

URL mẫu (Hợp đồng điện tử):
  https://sso-v2.ghn.vn/sso/jwt/login
    ?app_key=64046186-c1d1-4628-b2b7-1d1b6383c603
    &response_type=id_token
    &redirect_uri=http://hopdongdientu.ghn.vn/authorize

Luồng SPA (từ sso-v2 static JS):
  staff login → token
  → POST online-gateway /sso-v2/public-api/staff/gen-service-token
    (app_key + response_type=jwt_token)
  → redirect_uri#id_token=… | ?id_token=…

Owned-only · không dump-login · không bypass OTP.
Không tự login password trừ khi có GHN_USER+GHN_PASSWORD owned + --i-own-this.
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
from urllib.parse import parse_qs, unquote, urlencode, urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SECRETS = ROOT / "secrets"
REPORTS = ROOT / "reports" / "telegram-classify"
PENDING_URL = SECRETS / "ghn_sso_login.url"
READY_URL = SECRETS / "ghn_sso_login.ready.url"
APP_META = SECRETS / "ghn_sso_app.json"
PENDING_TOKEN = SECRETS / "ghn_sso_id_token.pending"
PENDING_CODE = SECRETS / "ghn_sso_auth_code.pending"
STATE_PATH = SECRETS / "ghn_sso_jwt.state.json"

SSO_HOST = "sso-v2.ghn.vn"
DEFAULT_REDIRECT_URI = "http://hopdongdientu.ghn.vn/authorize"
# app_key chính thức từ Portal247 ServerSSOLoginUrl (hopdongdientu)
HOPDONG_APP_KEY = "e2b09bde-9b9d-41aa-bc89-7289eaff48ea"
GATEWAY = "https://online-gateway.ghn.vn"
API = {
    "login": f"{GATEWAY}/sso-v2/public-api/staff/login",
    "gen_service_token": f"{GATEWAY}/sso-v2/public-api/staff/gen-service-token",
    "refresh": f"{GATEWAY}/sso-v2/public-api/staff/refresh-sso-token",
    "detail": f"{GATEWAY}/sso-v2/public-api/staff/detail",
}

# response_type trên URL → giá trị API (từ SPA)
RESPONSE_TYPE_MAP = {
    "id_token": "jwt_token",
    "jwt_token": "jwt_token",
    "token": "access_token",
    "code": "authorization_code",
    "oidc": "oidc",
}

JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
UUID_RE = re.compile(
    r"(?i)\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mask(tok: str | None) -> str | None:
    if not tok:
        return None
    t = tok.strip()
    if len(t) <= 16:
        return "***"
    return f"{t[:10]}…{t[-6:]}(len={len(t)})"


def load_env() -> dict[str, str]:
    from owned_credentials import load_env as base

    return base(extra_files=(SECRETS / "order_session.env",))


def http_json(
    url: str,
    *,
    method: str = "POST",
    body: dict | None = None,
    headers: dict | None = None,
    timeout: int = 25,
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (owned-sso-bridge)",
    }
    if headers:
        h.update({k: str(v) for k, v in headers.items() if v is not None})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
        raw = e.read() if e.fp else b""
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)[:200]}
    try:
        return code, json.loads(raw.decode("utf-8", errors="replace") or "null")
    except json.JSONDecodeError:
        return code, {"raw": raw[:300].decode("utf-8", errors="replace")}


def parse_sso_login_url(raw: str) -> dict[str, Any]:
    """Parse sso-v2 /sso/jwt/login?... hoặc redirect callback có id_token."""
    text = (raw or "").strip()
    out: dict[str, Any] = {
        "ok": False,
        "kind": None,
        "url": None,
        "app_key": None,
        "client_id": None,
        "response_type": None,
        "response_type_api": None,
        "redirect_uri": None,
        "redirect_uri_empty": False,
        "id_token": None,
        "code": None,
        "previous_path": None,
        "previous_path_decoded": None,
        "state": None,
        "nonce": None,
        "scope": None,
        "host": None,
        "path": None,
    }
    if not text:
        out["error"] = "URL trống"
        return out

    # bare JWT
    if JWT_RE.fullmatch(text):
        out.update({"ok": True, "kind": "bare_jwt", "id_token": text})
        return out

    # bare auth code (UUID) — không nhầm với JWT
    if UUID_RE.fullmatch(text) and not text.startswith("eyJ"):
        out.update({"ok": True, "kind": "bare_auth_code", "code": text})
        return out

    # relative authorize?code=… → giả lập hopdong host
    if text.lower().startswith("authorize?") or text.lower().startswith("/authorize?"):
        text = "http://hopdongdientu.ghn.vn/" + text.lstrip("/")

    # fragment may hold id_token
    if "#" in text and ("://" in text or text.startswith("authorize")):
        base, frag = text.split("#", 1)
    else:
        base, frag = text, ""

    try:
        u = urlparse(base)
    except Exception:  # noqa: BLE001
        out["error"] = "URL không hợp lệ"
        return out

    q = parse_qs(u.query)
    # merge fragment params (id_token thường nằm ở hash)
    for k, v in parse_qs(frag).items():
        q.setdefault(k, v)

    host = (u.hostname or "").lower()
    path = u.path or ""
    out["host"] = host
    out["path"] = path
    out["url"] = text[:500]

    def first(*keys: str) -> str | None:
        for k in keys:
            vals = q.get(k) or []
            if vals and str(vals[0]).strip():
                return unquote(str(vals[0]).strip())
        return None

    out["app_key"] = first("app_key", "appKey")
    out["client_id"] = first("client_id", "clientId")
    if not out["app_key"] and out["client_id"] and UUID_RE.fullmatch(out["client_id"] or ""):
        # chỉ fallback khi client_id là UUID (một số app dùng client_id=app_key)
        out["app_key"] = out["client_id"]
    out["response_type"] = first("response_type", "responseType")
    out["redirect_uri"] = first("redirect_uri", "redirectUri", "callback_url")
    out["redirect_uri_empty"] = not bool(out["redirect_uri"])
    out["id_token"] = first("id_token", "idToken", "token")
    out["code"] = first("code")
    out["previous_path"] = first("previous_path", "previousPath")
    out["state"] = first("state")
    out["nonce"] = first("nonce")
    out["scope"] = first("scope")

    if out["previous_path"]:
        try:
            import base64

            raw_pp = out["previous_path"]
            pad = "=" * ((4 - len(raw_pp) % 4) % 4)
            out["previous_path_decoded"] = base64.urlsafe_b64decode(
                (raw_pp + pad).encode()
            ).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            out["previous_path_decoded"] = None

    # UUID trong id_token field → coi là auth code, không phải JWT
    if out["id_token"] and not JWT_RE.search(out["id_token"]) and UUID_RE.fullmatch(out["id_token"]):
        out["code"] = out["code"] or out["id_token"]
        out["id_token"] = None

    if out["id_token"] and JWT_RE.search(out["id_token"]):
        out["ok"] = True
        out["kind"] = "callback_id_token"
    elif out["code"] and UUID_RE.fullmatch(out["code"]):
        out["ok"] = True
        out["kind"] = "callback_auth_code"
    elif host == SSO_HOST or "sso" in host:
        out["ok"] = True
        out["kind"] = "sso_login_page"
    elif out["app_key"] or "hopdongdientu" in host or path.rstrip("/").endswith("authorize"):
        out["ok"] = True
        out["kind"] = "authorize_redirect"
    else:
        # still accept if looks like ghn sso query
        if out["app_key"] and out["redirect_uri"]:
            out["ok"] = True
            out["kind"] = "sso_params"
        else:
            m = JWT_RE.search(text)
            if m:
                out["ok"] = True
                out["kind"] = "embedded_jwt"
                out["id_token"] = m.group(0)
            else:
                out["error"] = "Không nhận diện SSO login / id_token"

    rt = (out.get("response_type") or "").lower()
    out["response_type_api"] = RESPONSE_TYPE_MAP.get(rt, rt or None)
    return out


def hopdong_login_url() -> str:
    return (
        f"https://{SSO_HOST}/sso/jwt/login?"
        + urlencode(
            {
                "app_key": HOPDONG_APP_KEY,
                "response_type": "id_token",
                "redirect_uri": DEFAULT_REDIRECT_URI,
            }
        )
    )


def stage_auth_code(code: str, *, meta: dict | None = None) -> dict[str, Any]:
    """Stage OAuth/SSO authorization code (UUID) — không phải JWT, không dùng làm shiip Token."""
    SECRETS.mkdir(parents=True, exist_ok=True)
    c = (code or "").strip()
    if not c:
        return {"ok": False, "error": "code trống"}
    PENDING_CODE.write_text(c + "\n", encoding="utf-8")
    try:
        os.chmod(PENDING_CODE, 0o600)
    except OSError:
        pass
    try:
        from access_token_rotate import upsert_env_values

        upsert_env_values({"GHN_SSO_AUTH_CODE": c})
    except Exception:  # noqa: BLE001
        pass
    # probe nhanh: code UUID ≠ staff Token / shiip Token
    probe = {"staff_detail": None, "shiip_shop": None}
    try:
        code_h, body = http_json(
            API["detail"],
            method="GET",
            headers={"Token": c},
        )
        probe["staff_detail"] = {
            "http": code_h,
            "message": (body.get("code_message_value") or body.get("message") or "")[:120]
            if isinstance(body, dict)
            else str(body)[:80],
        }
    except Exception as e:  # noqa: BLE001
        probe["staff_detail"] = {"error": str(e)[:120]}
    try:
        code_h, body = http_json(
            f"{GATEWAY}/shiip/public-api/v2/shop/all",
            method="GET",
            headers={"Token": c},
        )
        probe["shiip_shop"] = {
            "http": code_h,
            "message": (body.get("code_message_value") or body.get("message") or "")[:120]
            if isinstance(body, dict)
            else str(body)[:80],
        }
    except Exception as e:  # noqa: BLE001
        probe["shiip_shop"] = {"error": str(e)[:120]}
    return {
        "ok": True,
        "code_masked": _mask(c),
        "staged": str(PENDING_CODE),
        "probe": probe,
        "usable_as_shiip_token": False,
        "usable_as_staff_token": (probe.get("staff_detail") or {}).get("http") == 200,
        "meta": meta or {},
        "note": (
            "authorization_code / OIDC code — app backend đổi code→token. "
            "Hopdongdientu cần id_token JWT (eyJ…), không phải UUID code."
        ),
    }


def with_redirect_uri(url: str, redirect_uri: str = DEFAULT_REDIRECT_URI) -> str:
    """Điền redirect_uri khi URL login để trống (SPA hopdongdientu)."""
    text = (url or "").strip()
    if not text or not redirect_uri:
        return text
    u = urlparse(text)
    q = parse_qs(u.query, keep_blank_values=True)
    cur = (q.get("redirect_uri") or q.get("redirectUri") or [""])[0]
    if str(cur).strip():
        return text
    q["redirect_uri"] = [redirect_uri]
    # preserve order-ish: rebuild from items
    pairs: list[tuple[str, str]] = []
    for k, vals in q.items():
        for v in vals:
            pairs.append((k, v))
    return urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode(pairs), u.fragment))


def stage_app_meta(parsed: dict[str, Any], *, ready_url: str | None = None) -> Path:
    SECRETS.mkdir(parents=True, exist_ok=True)
    meta = {
        "updated_at": utc_now(),
        "app_key": parsed.get("app_key"),
        "client_id": parsed.get("client_id"),
        "response_type": parsed.get("response_type"),
        "scope": parsed.get("scope"),
        "redirect_uri": parsed.get("redirect_uri") or DEFAULT_REDIRECT_URI,
        "redirect_uri_was_empty": bool(parsed.get("redirect_uri_empty")),
        "login_url_path": str(PENDING_URL),
        "ready_url_path": str(READY_URL) if ready_url else None,
        "note": (
            "Owned SSO login URL staged. Complete login in browser then paste callback with id_token. "
            "SSO id_token ≠ GHN_API_TOKEN shiip."
        ),
    }
    APP_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return APP_META


def stage_login_url(url: str, *, fix_empty_redirect: bool = True) -> dict[str, Any]:
    SECRETS.mkdir(parents=True, exist_ok=True)
    raw = url.strip()
    PENDING_URL.write_text(raw + "\n", encoding="utf-8")
    try:
        os.chmod(PENDING_URL, 0o600)
    except OSError:
        pass
    parsed = parse_sso_login_url(raw)
    ready = raw
    fixed = False
    if fix_empty_redirect and parsed.get("kind") == "sso_login_page" and parsed.get("redirect_uri_empty"):
        ready = with_redirect_uri(raw, DEFAULT_REDIRECT_URI)
        fixed = ready != raw
    READY_URL.write_text(ready + "\n", encoding="utf-8")
    try:
        os.chmod(READY_URL, 0o600)
    except OSError:
        pass
    stage_app_meta(parsed, ready_url=ready)
    # sync env keys (owned app identity only — no secrets beyond app_key)
    try:
        from access_token_rotate import upsert_env_values

        updates = {}
        if parsed.get("app_key"):
            updates["GHN_SSO_APP_KEY"] = parsed["app_key"]
        if parsed.get("client_id"):
            updates["GHN_SSO_CLIENT_ID"] = parsed["client_id"]
        if updates:
            upsert_env_values(updates)
    except Exception:  # noqa: BLE001
        pass
    return {
        "staged": str(PENDING_URL),
        "ready": str(READY_URL),
        "ready_url": ready,
        "redirect_fixed": fixed,
        "redirect_uri": parsed.get("redirect_uri") or (DEFAULT_REDIRECT_URI if fixed else None),
    }


def apply_id_token(id_token: str, *, app_key: str | None = None, meta: dict | None = None) -> dict[str, Any]:
    """Ghi GHN_SSO_ID_TOKEN (+ optional app_key) vào secrets."""
    from access_token_rotate import upsert_env_values

    token = (id_token or "").strip()
    if not token:
        return {"ok": False, "error": "id_token trống"}
    updates = {"GHN_SSO_ID_TOKEN": token}
    if app_key:
        updates["GHN_SSO_APP_KEY"] = app_key
    path = upsert_env_values(updates)
    PENDING_TOKEN.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(PENDING_TOKEN, 0o600)
    except OSError:
        pass
    os.environ["GHN_SSO_ID_TOKEN"] = token
    if app_key:
        os.environ["GHN_SSO_APP_KEY"] = app_key
    return {
        "ok": True,
        "env_file": str(path),
        "token_masked": _mask(token),
        "app_key": app_key,
        "meta": meta or {},
    }


def probe_sso_apis() -> dict[str, Any]:
    """Probe endpoint công khai (không credential) — kiểm tra gateway sống."""
    probes = {}
    code, body = http_json(API["login"], body={})
    probes["login"] = {
        "http": code,
        "code_field": body.get("code") if isinstance(body, dict) else None,
        "message": (body.get("code_message_value") or body.get("message") or "")[:160]
        if isinstance(body, dict)
        else str(body)[:120],
        "expect": "400 thiếu UserID/DeviceID = API sống",
    }
    code, body = http_json(API["gen_service_token"], body={"token": "x"})
    probes["gen_service_token"] = {
        "http": code,
        "code_field": body.get("code") if isinstance(body, dict) else None,
        "message": (body.get("code_message_value") or body.get("message") or "")[:160]
        if isinstance(body, dict)
        else str(body)[:120],
        "expect": "401 TOKEN_IS_INVALID = API sống",
    }
    # SPA page
    try:
        req = urllib.request.Request(
            f"https://{SSO_HOST}/sso/jwt/login",
            headers={"User-Agent": "Mozilla/5.0"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            probes["spa"] = {"http": resp.status, "bytes": len(resp.read())}
    except Exception as e:  # noqa: BLE001
        probes["spa"] = {"http": 0, "error": str(e)[:120]}
    ok = (probes.get("login") or {}).get("http") in {400, 401, 200} and (
        probes.get("spa") or {}
    ).get("http") == 200
    return {"ok": ok, "apis": API, "probes": probes}


def gen_service_token(
    session_token: str,
    *,
    app_key: str,
    response_type: str = "jwt_token",
    redirect_uri: str | None = None,
    nonce: str | None = None,
    device_token: str | None = None,
) -> dict[str, Any]:
    """Đổi staff session token → service id_token (owned session)."""
    # SPA gửi body {token} + header/query app_key params — thử cả hai
    headers = {
        "Token": session_token,
        "app_key": app_key,
    }
    body: dict[str, Any] = {
        "token": session_token,
        "app_key": app_key,
        "response_type": RESPONSE_TYPE_MAP.get(response_type, response_type),
    }
    if redirect_uri:
        body["callback_url"] = redirect_uri
    if nonce:
        body["nonce"] = nonce
    if device_token:
        body["device_token"] = device_token
    code, data = http_json(API["gen_service_token"], body=body, headers=headers)
    out: dict[str, Any] = {
        "ok": False,
        "http": code,
        "api": API["gen_service_token"],
        "body_code": data.get("code") if isinstance(data, dict) else None,
        "message": (
            (data.get("code_message_value") or data.get("message") or "")[:200]
            if isinstance(data, dict)
            else str(data)[:160]
        ),
        "data_keys": list(data.get("data").keys())[:20]
        if isinstance(data, dict) and isinstance(data.get("data"), dict)
        else None,
    }
    payload = data.get("data") if isinstance(data, dict) else None
    token = None
    if isinstance(payload, dict):
        for k in ("id_token", "token", "access_token", "jwt_token", "code"):
            v = payload.get(k)
            if isinstance(v, str) and len(v) > 20:
                token = v
                break
    elif isinstance(payload, str) and len(payload) > 20:
        token = payload
    if token:
        out["ok"] = True
        out["id_token_masked"] = _mask(token)
        out["id_token"] = token  # caller must strip before write_outputs
    return out


def analyze_url(url: str, *, probe: bool = True) -> dict[str, Any]:
    parsed = parse_sso_login_url(url)
    report: dict[str, Any] = {
        "ok": bool(parsed.get("ok")),
        "module": "ghn_sso_jwt_bridge.analyze",
        "checked_at": utc_now(),
        "parsed": {k: v for k, v in parsed.items() if k != "id_token"},
        "policy": {
            "owned_only": True,
            "no_dump_login": True,
            "password_login_off_by_default": True,
        },
        "note": (
            "App Hợp đồng điện tử (hopdongdientu) · response_type=id_token → API jwt_token. "
            "id_token SSO ≠ GHN_API_TOKEN shiip — dùng cho app_key tương ứng."
        ),
    }
    if parsed.get("id_token"):
        report["parsed"]["id_token_masked"] = _mask(parsed["id_token"])
    if parsed.get("code"):
        report["parsed"]["code_masked"] = _mask(parsed["code"])
    if parsed.get("ok") and parsed.get("kind") == "sso_login_page":
        staged = stage_login_url(url)
        report["staged"] = staged.get("staged")
        report["ready_url_path"] = staged.get("ready")
        report["ready_url"] = staged.get("ready_url")
        report["redirect_fixed"] = staged.get("redirect_fixed")
        if staged.get("redirect_fixed"):
            report["warning"] = (
                "redirect_uri trống trên URL gốc — đã điền "
                f"{DEFAULT_REDIRECT_URI} vào secrets/ghn_sso_login.ready.url"
            )
            report["next"] = [
                "Mở URL đã sẵn (ready) trong trình duyệt — owned shop only",
                f"Ready: {staged.get('ready_url')}",
                "Login (mã NV + mật khẩu + OTP nếu có) → copy URL callback có id_token=…",
                "python3 scripts/ghn_sso_jwt_bridge.py ingest --url '<callback>'",
                "Lưu ý: SSO JWT ≠ Token cookie shiip — orders vẫn cần printA5 / Token owned",
            ]
        else:
            report["next"] = [
                "Đăng nhập owned trên trình duyệt (mã NV + mật khẩu + OTP nếu có)",
                "Copy URL redirect có id_token=… → "
                "python3 scripts/ghn_sso_jwt_bridge.py ingest --url '<callback>'",
                "hoặc dán JWT: python3 scripts/ghn_sso_jwt_bridge.py ingest --id-token '<JWT>'",
            ]
    if parsed.get("ok") and parsed.get("kind") in {"callback_auth_code", "bare_auth_code"}:
        staged = stage_auth_code(
            parsed.get("code") or "",
            meta={"via": "analyze", "previous_path_decoded": parsed.get("previous_path_decoded")},
        )
        report["auth_code"] = {k: v for k, v in staged.items() if k != "meta"}
        report["warning"] = (
            "Callback có authorization code (UUID), không phải id_token JWT. "
            "Hopdongdientu ParseJwt cần eyJ… — dùng đúng app_key Portal247."
        )
        report["next"] = [
            f"Login hopdong đúng app: {hopdong_login_url()}",
            "Copy FULL callback có #id_token=eyJ… (không chỉ ?code=UUID)",
            "python3 scripts/ghn_sso_jwt_bridge.py ingest --url '<full-callback>'",
        ]
    if probe:
        report["probe"] = probe_sso_apis()
        report["ok"] = bool(parsed.get("ok") and report["probe"].get("ok"))
    elif parsed.get("ok"):
        report["ok"] = True
    if parsed.get("ok"):
        report["verdict"] = (
            f"✅ SSO JWT URL · kind={parsed.get('kind')} · "
            f"app_key={(parsed.get('app_key') or '—')[:13]}… · "
            f"rt={parsed.get('response_type')}→{parsed.get('response_type_api')} · "
            f"redirect={parsed.get('redirect_uri') or ('(empty→fixed)' if report.get('redirect_fixed') else None)}"
        )
        if parsed.get("kind") in {"callback_auth_code", "bare_auth_code"}:
            report["verdict"] = (
                f"✅ Auth code staged · {_mask(parsed.get('code'))} · "
                f"prev={parsed.get('previous_path_decoded') or parsed.get('previous_path') or '—'} · "
                "chưa phải id_token JWT"
            )
        if not report.get("next"):
            report["next"] = [
                "Đăng nhập owned trên trình duyệt (mã NV + mật khẩu + OTP nếu có)",
                "Copy URL redirect có id_token=… → "
                "python3 scripts/ghn_sso_jwt_bridge.py ingest --url '<callback>'",
                "hoặc dán JWT: python3 scripts/ghn_sso_jwt_bridge.py ingest --id-token '<JWT>'",
            ]
    else:
        report["verdict"] = f"❌ {parsed.get('error') or 'parse fail'}"
    return report


def ingest(
    *,
    url: str | None = None,
    id_token: str | None = None,
    app_key: str | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ok": False,
        "module": "ghn_sso_jwt_bridge.ingest",
        "checked_at": utc_now(),
        "verdict": "",
        "next": [],
    }
    parsed = None
    token = (id_token or "").strip() or None
    key = (app_key or "").strip() or None
    if url:
        parsed = parse_sso_login_url(url)
        report["parsed"] = {k: v for k, v in parsed.items() if k != "id_token"}
        if parsed.get("id_token"):
            report["parsed"]["id_token_masked"] = _mask(parsed["id_token"])
            token = token or parsed["id_token"]
        key = key or parsed.get("app_key")
        if parsed.get("kind") == "sso_login_page":
            staged = stage_login_url(url)
            report["staged"] = staged.get("staged")
            report["ready_url"] = staged.get("ready_url")
            report["redirect_fixed"] = staged.get("redirect_fixed")
            report["verdict"] = (
                "✅ Đã stage SSO login URL — chưa có id_token. "
                + (
                    f"redirect_uri trống → đã sẵn với {DEFAULT_REDIRECT_URI}."
                    if staged.get("redirect_fixed")
                    else "Đăng nhập owned rồi ingest callback."
                )
            )
            report["next"] = [
                "Mở ready URL, login owned, copy redirect có id_token",
                f"Ready: {staged.get('ready_url')}",
                "python3 scripts/ghn_sso_jwt_bridge.py ingest --url '<callback>'",
            ]
            write_outputs(report)
            return report
        if parsed.get("kind") in {"callback_auth_code", "bare_auth_code"} or (
            parsed.get("code") and not token
        ):
            staged = stage_auth_code(
                parsed.get("code") or "",
                meta={
                    "via": "ingest",
                    "previous_path": parsed.get("previous_path"),
                    "previous_path_decoded": parsed.get("previous_path_decoded"),
                    "host": parsed.get("host"),
                },
            )
            report["auth_code"] = {
                k: v for k, v in staged.items() if k != "meta"
            }
            report["ok"] = bool(staged.get("ok"))
            report["verdict"] = (
                f"✅ Đã stage authorization code · {staged.get('code_masked')} — "
                "KHÔNG phải id_token JWT / KHÔNG dùng được làm Token shiip"
            )
            report["next"] = [
                "Hopdongdientu cần callback có id_token=eyJ… (JWT), không phải code=UUID",
                f"Login đúng app hopdong: {hopdong_login_url()}",
                "Sau login: copy FULL URL (kể cả phần #id_token=eyJ…) rồi ingest lại",
                "Hoặc DevTools → Application → Cookie `Token` / printA5 owned cho shiip orders",
            ]
            write_outputs(report)
            return report

    if not token:
        # try pending
        if PENDING_TOKEN.is_file():
            token = PENDING_TOKEN.read_text(encoding="utf-8").strip() or None
        if not token:
            report["verdict"] = "❌ Thiếu id_token / callback URL"
            report["next"] = [
                "python3 scripts/ghn_sso_jwt_bridge.py analyze --url '<sso login URL>'",
                "Sau login: ingest --url 'http://hopdongdientu.ghn.vn/authorize#id_token=…'",
            ]
            write_outputs(report)
            return report

    applied = apply_id_token(token, app_key=key, meta={"via": "ingest"})
    report["apply"] = {k: v for k, v in applied.items() if k != "meta"}
    report["ok"] = bool(applied.get("ok"))
    report["verdict"] = (
        f"✅ Đã nhúng GHN_SSO_ID_TOKEN · {_mask(token)} · app_key={key or '—'}"
        if report["ok"]
        else f"❌ {applied.get('error')}"
    )
    report["next"] = [
        "Token này cho app hopdongdientu / SSO service — không thay GHN_API_TOKEN shiip",
        "Shiip orders: cần printA5 / GHN_API_TOKEN owned còn hạn",
        "python3 scripts/ghn_sso_jwt_bridge.py status",
    ]
    write_outputs(report)
    return report


def build_report(url: str | None = None, *, probe: bool = True) -> dict[str, Any]:
    raw = (url or "").strip()
    if not raw and PENDING_URL.is_file():
        raw = PENDING_URL.read_text(encoding="utf-8").strip()
    if not raw:
        # default: user's hopdong URL if passed empty — require explicit
        report = {
            "ok": False,
            "module": "ghn_sso_jwt_bridge",
            "checked_at": utc_now(),
            "verdict": "❌ Thiếu SSO login URL",
            "next": [
                "python3 scripts/ghn_sso_jwt_bridge.py analyze --url "
                "'https://sso-v2.ghn.vn/sso/jwt/login?app_key=…&response_type=id_token&redirect_uri=…'"
            ],
            "probe": probe_sso_apis() if probe else None,
        }
        write_outputs(report)
        return report
    report = analyze_url(raw, probe=probe)
    write_outputs(report)
    return report


def write_outputs(report: dict[str, Any]) -> dict[str, str]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    SECRETS.mkdir(parents=True, exist_ok=True)
    slim = json.loads(json.dumps(report, ensure_ascii=False, default=str))
    # never persist raw id_token / auth code in reports
    if isinstance(slim.get("apply"), dict):
        slim["apply"].pop("id_token", None)
    parsed = slim.get("parsed")
    if isinstance(parsed, dict) and parsed.get("code"):
        parsed["code_masked"] = _mask(parsed.get("code"))
        parsed.pop("code", None)
    if isinstance(parsed, dict) and parsed.get("id_token"):
        parsed["id_token_masked"] = _mask(parsed.get("id_token"))
        parsed.pop("id_token", None)
    jp = REPORTS / "ghn_sso_jwt_bridge.json"
    tp = REPORTS / "ghn_sso_jwt_bridge.txt"
    jp.write_text(json.dumps(slim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tp.write_text(format_text(report) + "\n", encoding="utf-8")
    state = {
        "updated_at": report.get("checked_at") or utc_now(),
        "ok": report.get("ok"),
        "verdict": report.get("verdict"),
        "parsed": report.get("parsed"),
        "staged": report.get("staged"),
    }
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(STATE_PATH, 0o600)
    except OSError:
        pass
    return {"json": str(jp), "txt": str(tp)}


def format_text(report: dict[str, Any]) -> str:
    lines = [
        "🔐 GHN SSO JWT (sso-v2 → hopdongdientu)",
        f"Lúc: {report.get('checked_at') or utc_now()}",
        f"Verdict: {report.get('verdict')}",
    ]
    p = report.get("parsed") or {}
    if p:
        lines.append(
            f"kind={p.get('kind')} app_key={p.get('app_key')} "
            f"rt={p.get('response_type')}→{p.get('response_type_api')}"
        )
        lines.append(f"redirect={p.get('redirect_uri')}")
        if p.get("id_token_masked"):
            lines.append(f"id_token={p.get('id_token_masked')}")
        if p.get("code_masked") or p.get("code"):
            lines.append(f"code={p.get('code_masked') or _mask(p.get('code'))}")
        if p.get("previous_path_decoded") or p.get("previous_path"):
            lines.append(
                f"previous_path={p.get('previous_path_decoded') or p.get('previous_path')}"
            )
    if report.get("staged"):
        lines.append(f"staged: {report.get('staged')}")
    if report.get("auth_code"):
        ac = report["auth_code"]
        lines.append(f"auth_code_staged={ac.get('staged')} usable_shiip={ac.get('usable_as_shiip_token')}")
        for name, info in (ac.get("probe") or {}).items():
            lines.append(
                f"  · probe {name}: http={info.get('http')} {info.get('message') or info.get('error') or ''}"
            )
    if report.get("ready_url"):
        lines.append(f"ready: {report.get('ready_url')[:220]}")
    if report.get("warning"):
        lines.append(f"Warning: {report.get('warning')}")
    probe = report.get("probe") or {}
    if probe:
        lines.append(f"probe_ok={probe.get('ok')}")
        for name, info in (probe.get("probes") or {}).items():
            lines.append(
                f"  · {name}: http={info.get('http')} {info.get('message') or info.get('error') or ''}"
            )
    if report.get("note"):
        lines.append(f"Note: {report.get('note')}")
    for n in report.get("next") or []:
        lines.append(f"Next: {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GHN SSO JWT login URL → id_token (owned)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_an = sub.add_parser("analyze", help="Phân tích SSO login URL + probe API")
    p_an.add_argument("--url", required=True)
    p_an.add_argument("--no-probe", action="store_true")
    p_an.add_argument("--json", action="store_true")

    p_in = sub.add_parser("ingest", help="Nhúng id_token từ callback / JWT")
    p_in.add_argument("--url", default="", help="Callback URL có id_token")
    p_in.add_argument("--id-token", default="", help="JWT id_token thuần")
    p_in.add_argument("--app-key", default="")
    p_in.add_argument("--json", action="store_true")

    p_pr = sub.add_parser("probe", help="Probe API SSO (không login)")
    p_pr.add_argument("--json", action="store_true")

    p_st = sub.add_parser("status", help="Trạng thái SSO JWT đã nhúng")
    p_st.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "probe":
        report = {"ok": False, "checked_at": utc_now(), "probe": probe_sso_apis()}
        report["ok"] = bool(report["probe"].get("ok"))
        report["verdict"] = "✅ SSO APIs sống" if report["ok"] else "❌ SSO probe fail"
        write_outputs(report)
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_text(report))
        return 0 if report["ok"] else 1

    if args.cmd == "analyze":
        report = build_report(args.url, probe=not args.no_probe)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str) if args.json else format_text(report))
        return 0 if report.get("ok") else 1

    if args.cmd == "ingest":
        report = ingest(url=(args.url or None), id_token=(args.id_token or None), app_key=(args.app_key or None))
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str) if args.json else format_text(report))
        return 0 if report.get("ok") else 1

    # status
    env = load_env()
    tok = (env.get("GHN_SSO_ID_TOKEN") or "").strip()
    key = (env.get("GHN_SSO_APP_KEY") or "").strip()
    report = {
        "ok": bool(tok),
        "checked_at": utc_now(),
        "token_masked": _mask(tok) if tok else None,
        "app_key": key or None,
        "pending_url": PENDING_URL.read_text(encoding="utf-8").strip() if PENDING_URL.is_file() else None,
        "state": json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.is_file() else None,
        "verdict": (
            f"✅ GHN_SSO_ID_TOKEN · {_mask(tok)} · app_key={key or '—'}"
            if tok
            else "❌ Chưa có GHN_SSO_ID_TOKEN — analyze SSO URL rồi ingest callback"
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str) if args.json else report["verdict"])
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
