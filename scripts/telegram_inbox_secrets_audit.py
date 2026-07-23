#!/usr/bin/env python3
"""Rà soát API token · cookie/session · secret/id key liên quan lấy đơn
trong toàn bộ file hộp thoại Telegram (quarantine/telegram).

- Inventory có mặt / loại / platform / số lượng
- Che giá trị thô (mask) — không dump plaintext vào báo cáo
- Không login bằng dump; gợi ý chỉ đưa credential SỞ HỮU vào secrets/backend_pipes.env
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

INBOX = ROOT / "quarantine" / "telegram"
REPORTS = ROOT / "reports" / "telegram-classify"
DB_PATH = REPORTS / "telegram_inbox_secrets_audit.db"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Key names liên quan auth / lấy đơn
SECRET_KEY_NAMES = re.compile(
    r"(api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|bearer|"
    r"secret[_-]?key|client[_-]?secret|app[_-]?secret|private[_-]?key|"
    r"session[_-]?id|session[_-]?key|session[_-]?token|csrf|"
    r"cookie|set-cookie|authorization|x-api-key|x-token|"
    r"password|passwd|pwd|otp|pin)",
    re.I,
)
ID_KEY_NAMES = re.compile(
    r"(shop[_-]?id|store[_-]?id|client[_-]?id|app[_-]?id|partner[_-]?id|"
    r"business[_-]?id|merchant[_-]?id|warehouse[_-]?id|kho[_-]?id|"
    r"seller[_-]?id|account[_-]?id|user[_-]?id|customer[_-]?code|"
    r"token[_-]?id|api[_-]?id)",
    re.I,
)
ORDER_ID_KEYS = re.compile(
    r"(order[_-]?id|order[_-]?code|tracking|ma[_-]?van[_-]?don|provider[_-]?code|"
    r"extend[_-]?code|bill[_-]?code)",
    re.I,
)

JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")
BEARER_RE = re.compile(r"\bBearer\s+([A-Za-z0-9_\-\.=]{16,})", re.I)
HEX_TOKEN_RE = re.compile(r"\b[a-f0-9]{32,64}\b", re.I)
OPAQUE_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")
COOKIE_PAIR_RE = re.compile(
    r"\b((?:PHPSESSID|session|sid|token|auth|jwt|access|refresh|csrftoken|"
    r"__Secure-[^=]+|__Host-[^=]+)[=:][^\s;,]{8,})",
    re.I,
)
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
USER_PASS_RE = re.compile(r"^([^:\s]{2,80}):([^:\s]{2,200})$")
USER_PASS_TOKEN_RE = re.compile(r"^([^:\s]{2,80}):([^:\s]{0,200}):([A-Za-z0-9_\-\.]{16,})$")

PLATFORM_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("GHN", ("ghn.vn", "ghn.com", "api.ghn", "shiip")),
    ("Nhanh", ("nhanh.vn",)),
    ("Sapo", ("sapo.vn", "sapo.com")),
    ("Shopee", ("shopee.vn", "shopee.com", "spx")),
    ("Pancake", ("pancake.vn", "pos.pages.fm", "pancake")),
    ("ViettelPost", ("viettelpost", "vtp")),
    ("VNPost", ("vnpost",)),
    ("Haravan", ("haravan",)),
    ("TPOS", ("tpos",)),
    ("Aship", ("aship.app",)),
    ("Sendo", ("sendo.vn",)),
    ("Tiki", ("tiki.vn",)),
]

KIND_LABELS = {
    "api_token": "API token",
    "jwt": "JWT",
    "bearer": "Bearer token",
    "cookie_session": "Cookie/session",
    "secret_key": "Secret key",
    "password": "Password (credential dump)",
    "shop_id": "Shop/Store ID",
    "client_id": "Client/App/Partner ID",
    "order_id_key": "Order/Tracking ID field",
    "user_ident": "User identifier",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mask(value: str, *, keep: int = 4, kind: str | None = None) -> str:
    v = str(value or "")
    if not v:
        return ""
    if kind == "password":
        return f"**(redacted,len={len(v)})**"
    if len(v) <= keep * 2:
        return "*" * min(len(v), 12)
    return f"{v[:keep]}…{v[-keep:]}(len={len(v)})"


def sha8(value: str) -> str:
    return hashlib.sha1(str(value).encode("utf-8", errors="replace")).hexdigest()[:8]


def detect_platforms(text: str) -> list[str]:
    t = (text or "").lower()
    hits = []
    for name, needles in PLATFORM_RULES:
        if any(n in t for n in needles):
            hits.append(name)
    return hits


def classify_secret_value(value: str, *, key_hint: str = "") -> str | None:
    v = (value or "").strip()
    if not v or len(v) < 6:
        return None
    kh = (key_hint or "").lower()
    if SECRET_KEY_NAMES.search(kh):
        if "password" in kh or "passwd" in kh or kh in {"pwd", "pin", "otp"}:
            return "password"
        if "cookie" in kh or "session" in kh:
            return "cookie_session"
        if "secret" in kh:
            return "secret_key"
        if "token" in kh or "api" in kh or "auth" in kh or "bearer" in kh or "key" in kh:
            return "api_token"
    if JWT_RE.search(v) or v.startswith("eyJ"):
        return "jwt"
    if v.lower().startswith("bearer "):
        return "bearer"
    if COOKIE_PAIR_RE.search(v) and ("=" in v or ":" in v) and len(v) < 2000:
        if any(x in v.lower() for x in ("session", "phpsessid", "cookie", "csrf", "sid=")):
            return "cookie_session"
    if ID_KEY_NAMES.search(kh):
        if any(x in kh for x in ("shop", "store", "warehouse", "kho", "seller", "merchant", "business")):
            return "shop_id"
        return "client_id"
    if ORDER_ID_KEYS.search(kh):
        return "order_id_key"
    # bare long tokens
    if re.fullmatch(r"[A-Za-z0-9_\-\.]{32,}", v) and not re.fullmatch(r"\d+", v):
        return "api_token"
    return None


def add_finding(
    findings: list[dict],
    *,
    file: str,
    kind: str,
    platform: str | None,
    key: str | None,
    value: str,
    context: str = "",
    dump_source: bool = False,
) -> None:
    if not value:
        return
    findings.append(
        {
            "file": file,
            "kind": kind,
            "platform": platform or "unknown",
            "key": key,
            "masked": mask(value, kind=kind),
            "fp": sha8(value),
            "length": len(value),
            "context": (context or "")[:120],
            "dump_source": dump_source,
            "usable_for_order_pipe": False if dump_source else None,
            "note": (
                "DUMP — không auto-login; chỉ đưa vào secrets nếu xác nhận sở hữu"
                if dump_source
                else "Ứng viên mapping owned → secrets/backend_pipes.env"
            ),
        }
    )


def is_dump_filename(name: str) -> bool:
    n = name.lower()
    return any(
        x in n
        for x in (
            "acc_all",
            "stealer",
            "internal_search",
            "ghn_tokens",
            "valid_accounts",
            "password",
            "assassin",
            "dump",
            "results_cookies",
            "vnpost_ok",
        )
    ) or n in {"ghn.txt"}  # url:user:pass dump


# —— scanners ——————————————————————————————


def scan_text(path: Path, *, max_lines: int = 20000) -> list[dict]:
    findings: list[dict] = []
    dump = is_dump_filename(path.name)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:max_lines]
    except Exception as e:  # noqa: BLE001
        return [
            {
                "file": path.name,
                "kind": "error",
                "platform": "unknown",
                "key": None,
                "masked": str(e)[:80],
                "fp": "",
                "length": 0,
                "context": "read_error",
                "dump_source": dump,
                "usable_for_order_pipe": False,
                "note": "read error",
            }
        ]

    platforms_file = detect_platforms(path.name + "\n" + "\n".join(lines[:50]))
    default_plat = platforms_file[0] if platforms_file else None
    if "ghn" in path.name.lower():
        default_plat = "GHN"
    if "pancake" in path.name.lower() or path.name.startswith("Acc_all"):
        # Acc_all may contain multiple sections
        pass

    section = default_plat
    for i, ln in enumerate(lines):
        raw = ln.strip()
        if not raw or raw.startswith("#"):
            # section headers like PANCAKE
            if raw.isupper() and 2 < len(raw) < 40:
                section = normalize_section(raw) or section
            continue

        plats = detect_platforms(raw) or ([section] if section else [])
        plat = plats[0] if plats else (section or default_plat)

        # username:password:token
        m3 = USER_PASS_TOKEN_RE.match(raw)
        if m3:
            add_finding(
                findings,
                file=path.name,
                kind="user_ident",
                platform=plat,
                key="username",
                value=m3.group(1),
                context=f"L{i+1}:user:pass:token",
                dump_source=dump,
            )
            add_finding(
                findings,
                file=path.name,
                kind="password",
                platform=plat,
                key="password",
                value=m3.group(2),
                context=f"L{i+1}:user:pass:token",
                dump_source=True,
            )
            add_finding(
                findings,
                file=path.name,
                kind="api_token",
                platform=plat,
                key="token",
                value=m3.group(3),
                context=f"L{i+1}:user:pass:token",
                dump_source=dump,
            )
            continue

        # url:user:pass or user:pass
        if raw.count(":") >= 2 and "://" in raw.split(":", 1)[0]:
            # url:user:pass
            try:
                url, rest = raw.split(":", 1)
                # actually format is https://host/path:user:pass
                parts = raw.rsplit(":", 2)
                if len(parts) == 3 and parts[0].startswith("http"):
                    url, user, pw = parts
                    add_finding(
                        findings,
                        file=path.name,
                        kind="user_ident",
                        platform=detect_platforms(url)[0] if detect_platforms(url) else plat,
                        key="username",
                        value=user,
                        context=f"L{i+1}:url:user:pass",
                        dump_source=True,
                    )
                    add_finding(
                        findings,
                        file=path.name,
                        kind="password",
                        platform=detect_platforms(url)[0] if detect_platforms(url) else plat,
                        key="password",
                        value=pw,
                        context=f"L{i+1} host={urlparse(url).hostname}",
                        dump_source=True,
                    )
                    continue
            except ValueError:
                pass

        m2 = USER_PASS_RE.match(raw)
        if m2 and "://" not in raw:
            add_finding(
                findings,
                file=path.name,
                kind="user_ident",
                platform=plat,
                key="username",
                value=m2.group(1),
                context=f"L{i+1}:user:pass",
                dump_source=dump or True,
            )
            add_finding(
                findings,
                file=path.name,
                kind="password",
                platform=plat,
                key="password",
                value=m2.group(2),
                context=f"L{i+1}:user:pass",
                dump_source=True,
            )
            continue

        for jm in JWT_RE.finditer(raw):
            add_finding(
                findings,
                file=path.name,
                kind="jwt",
                platform=plat,
                key="jwt",
                value=jm.group(0),
                context=f"L{i+1}",
                dump_source=dump,
            )
        for bm in BEARER_RE.finditer(raw):
            add_finding(
                findings,
                file=path.name,
                kind="bearer",
                platform=plat,
                key="Authorization",
                value=bm.group(1),
                context=f"L{i+1}",
                dump_source=dump,
            )
        for cm in COOKIE_PAIR_RE.finditer(raw):
            add_finding(
                findings,
                file=path.name,
                kind="cookie_session",
                platform=plat,
                key="cookie",
                value=cm.group(1),
                context=f"L{i+1}",
                dump_source=dump,
            )

    return findings


def normalize_section(name: str) -> str | None:
    n = name.strip().upper()
    mapping = {
        "PANCAKE": "Pancake",
        "GHN": "GHN",
        "VTP": "ViettelPost",
        "VIETTELPOST": "ViettelPost",
        "VNPOST": "VNPost",
        "TPOS": "TPOS",
        "SAPO": "Sapo",
        "NHANH": "Nhanh",
        "SHOPEE": "Shopee",
        "SPX": "SPX",
    }
    return mapping.get(n)


def scan_json(path: Path, *, max_nodes: int = 8000) -> list[dict]:
    findings: list[dict] = []
    dump = is_dump_filename(path.name)
    try:
        # large files: stream via ijson? keep simple — load with size guard
        size = path.stat().st_size
        if size > 12_000_000:
            # sample head
            text = path.read_text(encoding="utf-8", errors="replace")[:2_000_000]
            data = json.loads(text)
        else:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        return [
            {
                "file": path.name,
                "kind": "error",
                "platform": "unknown",
                "key": None,
                "masked": str(e)[:80],
                "fp": "",
                "length": 0,
                "context": "json_parse",
                "dump_source": dump,
                "usable_for_order_pipe": False,
                "note": "json parse/sample error",
            }
        ]

    count = 0

    def walk(obj, path_keys: str = ""):
        nonlocal count
        if count >= max_nodes:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                count += 1
                if count >= max_nodes:
                    return
                key_path = f"{path_keys}.{k}" if path_keys else str(k)
                if isinstance(v, (dict, list)):
                    walk(v, key_path)
                    continue
                if v is None:
                    continue
                sval = str(v)
                kind = classify_secret_value(sval, key_hint=str(k))
                if not kind and SECRET_KEY_NAMES.search(str(k)):
                    kind = "secret_key" if "secret" in str(k).lower() else "api_token"
                if not kind and ID_KEY_NAMES.search(str(k)):
                    kind = "shop_id" if any(x in str(k).lower() for x in ("shop", "store", "kho", "ware")) else "client_id"
                if not kind and ORDER_ID_KEYS.search(str(k)) and sval:
                    kind = "order_id_key"
                if kind:
                    plats = detect_platforms(f"{key_path} {sval}") or detect_platforms(path.name)
                    add_finding(
                        findings,
                        file=path.name,
                        kind=kind,
                        platform=plats[0] if plats else "OMS/local",
                        key=str(k),
                        value=sval,
                        context=key_path[:100],
                        dump_source=dump or kind == "password",
                    )
        elif isinstance(obj, list):
            for it in obj[:3000]:
                walk(it, path_keys)
                if count >= max_nodes:
                    return

    walk(data)
    return findings


def _load_shared(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    try:
        ss = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except ET.ParseError:
        return []
    out = []
    for si in ss.findall(f"{NS}si"):
        texts = [(t.text or "") for t in si.iter(f"{NS}t")]
        out.append("".join(texts))
    return out


def _cell_text(c: ET.Element, shared: list[str]) -> str:
    t = c.attrib.get("t")
    if t == "inlineStr":
        is_node = c.find(f"{NS}is")
        if is_node is None:
            return ""
        return "".join((n.text or "") for n in is_node.iter(f"{NS}t"))
    v = c.find(f"{NS}v")
    if v is None or v.text is None:
        return ""
    if t == "s":
        try:
            return shared[int(v.text)]
        except (ValueError, IndexError):
            return v.text
    return str(v.text)


def scan_xlsx(path: Path, *, max_rows: int = 4000, max_sheets: int = 6) -> list[dict]:
    findings: list[dict] = []
    dump = is_dump_filename(path.name)
    try:
        with zipfile.ZipFile(path) as zf:
            shared = _load_shared(zf)
            wb = ET.fromstring(zf.read("xl/workbook.xml"))
            sheets = []
            for sh in wb.findall(f"{NS}sheets/{NS}sheet"):
                sheets.append(
                    {
                        "name": sh.attrib.get("name") or "",
                        "rId": sh.attrib.get(
                            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                        ),
                        "sheetId": sh.attrib.get("sheetId"),
                    }
                )
            rels = {}
            if "xl/_rels/workbook.xml.rels" in zf.namelist():
                root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
                for rel in root:
                    rels[rel.attrib.get("Id")] = rel.attrib.get("Target")
            for meta in sheets[:max_sheets]:
                target = rels.get(meta["rId"]) or f"worksheets/sheet{meta['sheetId']}.xml"
                if not target.startswith("worksheets"):
                    target = "worksheets/" + target.split("/")[-1]
                path_in = "xl/" + target.lstrip("/")
                if path_in not in zf.namelist():
                    continue
                root = ET.fromstring(zf.read(path_in))
                headers: list[str] = []
                n = 0
                for row in root.findall(f"{NS}sheetData/{NS}row"):
                    cells = [_cell_text(c, shared) for c in row.findall(f"{NS}c")]
                    if not headers:
                        headers = [str(x).strip() for x in cells]
                        # header-level findings
                        for h in headers:
                            if SECRET_KEY_NAMES.search(h) or ID_KEY_NAMES.search(h):
                                add_finding(
                                    findings,
                                    file=path.name,
                                    kind="secret_key" if SECRET_KEY_NAMES.search(h) else "client_id",
                                    platform=(detect_platforms(path.name) or ["unknown"])[0],
                                    key=h,
                                    value=f"<column:{h}>",
                                    context=f"sheet={meta['name']} header",
                                    dump_source=dump,
                                )
                        continue
                    while len(cells) < len(headers):
                        cells.append("")
                    for h, val in zip(headers, cells):
                        if not val:
                            continue
                        kind = classify_secret_value(val, key_hint=h)
                        if not kind and SECRET_KEY_NAMES.search(h):
                            kind = "password" if "pass" in h.lower() else "api_token"
                        if not kind and ID_KEY_NAMES.search(h):
                            kind = "shop_id" if "shop" in h.lower() or "store" in h.lower() else "client_id"
                        if not kind and h.lower() in {"user", "username", "login", "email"}:
                            kind = "user_ident"
                        if not kind and h.lower() in {"url", "filtered line", "name"}:
                            # URL may embed tokens rarely; extract jwt/bearer
                            for jm in JWT_RE.finditer(val):
                                add_finding(
                                    findings,
                                    file=path.name,
                                    kind="jwt",
                                    platform=(detect_platforms(val) or detect_platforms(path.name) or ["unknown"])[0],
                                    key=h,
                                    value=jm.group(0),
                                    context=f"sheet={meta['name']}",
                                    dump_source=dump,
                                )
                            continue
                        if kind:
                            plats = detect_platforms(val) or detect_platforms(path.name)
                            add_finding(
                                findings,
                                file=path.name,
                                kind=kind,
                                platform=plats[0] if plats else "unknown",
                                key=h,
                                value=val,
                                context=f"sheet={meta['name']}",
                                dump_source=dump or kind == "password",
                            )
                    n += 1
                    if n >= max_rows:
                        break
    except Exception as e:  # noqa: BLE001
        findings.append(
            {
                "file": path.name,
                "kind": "error",
                "platform": "unknown",
                "key": None,
                "masked": str(e)[:80],
                "fp": "",
                "length": 0,
                "context": "xlsx_error",
                "dump_source": dump,
                "usable_for_order_pipe": False,
                "note": "xlsx error",
            }
        )
    return findings


def scan_csv(path: Path, *, max_rows: int = 5000) -> list[dict]:
    findings: list[dict] = []
    dump = is_dump_filename(path.name)
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            headers = reader.fieldnames or []
            for i, row in enumerate(reader):
                if i >= max_rows:
                    break
                for h, val in row.items():
                    if not val:
                        continue
                    kind = classify_secret_value(str(val), key_hint=str(h))
                    if not kind and h and SECRET_KEY_NAMES.search(h):
                        kind = "api_token"
                    if not kind and h and ID_KEY_NAMES.search(h):
                        kind = "shop_id" if "shop" in h.lower() else "client_id"
                    if not kind and h and ORDER_ID_KEYS.search(h):
                        kind = "order_id_key"
                    if kind:
                        add_finding(
                            findings,
                            file=path.name,
                            kind=kind,
                            platform=(detect_platforms(path.name) or ["OMS/local"])[0],
                            key=str(h),
                            value=str(val),
                            context=f"csv_row={i+1}",
                            dump_source=dump or kind == "password",
                        )
    except Exception as e:  # noqa: BLE001
        findings.append(
            {
                "file": path.name,
                "kind": "error",
                "platform": "unknown",
                "key": None,
                "masked": str(e)[:80],
                "fp": "",
                "length": 0,
                "context": "csv_error",
                "dump_source": dump,
                "usable_for_order_pipe": False,
                "note": "csv error",
            }
        )
    return findings


def list_inbox_files() -> list[Path]:
    out: list[Path] = []
    if not INBOX.is_dir():
        return out
    for p in INBOX.rglob("*"):
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        out.append(p)
    return sorted(out, key=lambda x: x.as_posix())


def scan_file(path: Path) -> list[dict]:
    ext = path.suffix.lower()
    if ext in {".txt", ".log", ".lst", ".tsv"}:
        return scan_text(path)
    if ext == ".json":
        return scan_json(path)
    if ext in {".xlsx", ".xlsm"}:
        return scan_xlsx(path)
    if ext == ".csv":
        return scan_csv(path)
    # unknown: try text sample
    try:
        sample = path.read_bytes()[:50_000]
        if b"\x00" in sample[:1000]:
            return []
        tmp = path.with_suffix(path.suffix + ".txtscan")
        # don't write; scan from decode
        text = sample.decode("utf-8", errors="replace")
        # lightweight
        findings = []
        dump = is_dump_filename(path.name)
        for jm in JWT_RE.finditer(text):
            add_finding(
                findings,
                file=path.name,
                kind="jwt",
                platform=(detect_platforms(path.name) or ["unknown"])[0],
                key="jwt",
                value=jm.group(0),
                dump_source=dump,
            )
        return findings
    except Exception:  # noqa: BLE001
        return []


def dedupe_findings(findings: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out = []
    for f in findings:
        key = (f.get("file"), f.get("kind"), f.get("platform"), f.get("key"), f.get("fp"))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def build_report() -> dict:
    files = list_inbox_files()
    all_findings: list[dict] = []
    per_file: list[dict] = []

    for p in files:
        rel = str(p.relative_to(INBOX)) if p.is_relative_to(INBOX) else p.name
        findings = scan_file(p)
        findings = dedupe_findings(findings)
        # rewrite file field to relative
        for f in findings:
            f["file"] = rel
        all_findings.extend(findings)
        by_kind = Counter(f["kind"] for f in findings if f.get("kind") != "error")
        per_file.append(
            {
                "file": rel,
                "size": p.stat().st_size,
                "dump_file": is_dump_filename(p.name) or "/_skipped_dumps/" in rel,
                "findings": len(findings),
                "by_kind": dict(by_kind),
                "platforms": sorted(
                    {f.get("platform") for f in findings if f.get("platform") and f.get("kind") != "error"}
                ),
            }
        )

    all_findings = dedupe_findings(all_findings)
    by_kind = Counter(f["kind"] for f in all_findings if f.get("kind") != "error")
    by_platform = Counter(f["platform"] for f in all_findings if f.get("kind") != "error")
    dump_count = sum(1 for f in all_findings if f.get("dump_source") and f.get("kind") != "error")
    auth_kinds = {"api_token", "jwt", "bearer", "cookie_session", "secret_key", "password"}
    id_kinds = {"shop_id", "client_id", "order_id_key", "user_ident"}

    # unique fingerprints per kind
    unique_fp = defaultdict(set)
    for f in all_findings:
        if f.get("kind") == "error":
            continue
        unique_fp[f["kind"]].add(f.get("fp"))

    # owned mapping hint: which platforms have tokens present
    token_platforms = sorted(
        {
            f["platform"]
            for f in all_findings
            if f.get("kind") in {"api_token", "jwt", "bearer"} and f.get("platform") != "unknown"
        }
    )

    materialize(all_findings, per_file)

    return {
        "ok": True,
        "query": "Rà soát API token · cookie/session · secret/id key liên quan lấy đơn trong inbox Telegram",
        "checked_at": utc_now(),
        "inbox": str(INBOX),
        "files_scanned": len(files),
        "findings_total": len([f for f in all_findings if f.get("kind") != "error"]),
        "errors": len([f for f in all_findings if f.get("kind") == "error"]),
        "stats": {
            "by_kind": dict(by_kind),
            "by_platform": dict(by_platform),
            "unique_by_kind": {k: len(v) for k, v in unique_fp.items()},
            "dump_sourced": dump_count,
            "auth_related": sum(by_kind[k] for k in auth_kinds if k in by_kind),
            "id_related": sum(by_kind[k] for k in id_kinds if k in by_kind),
        },
        "token_platforms": token_platforms,
        "per_file": per_file,
        "samples": [
            {
                "file": f.get("file"),
                "kind": f.get("kind"),
                "kind_label": KIND_LABELS.get(f.get("kind") or "", f.get("kind")),
                "platform": f.get("platform"),
                "key": f.get("key"),
                "masked": f.get("masked"),
                "dump_source": f.get("dump_source"),
                "note": f.get("note"),
            }
            for f in all_findings
            if f.get("kind") != "error"
        ][:80],
        "db": str(DB_PATH),
        "verdict": (
            f"Đã rà {len(files)} file · findings={len([f for f in all_findings if f.get('kind') != 'error'])} "
            f"· auth={sum(by_kind[k] for k in auth_kinds if k in by_kind)} "
            f"· id={sum(by_kind[k] for k in id_kinds if k in by_kind)} "
            f"· dump_sourced={dump_count} · platforms_token={token_platforms}"
        ),
        "safety": {
            "values_masked": True,
            "no_dump_login": True,
            "no_plaintext_in_report": True,
            "owned_only_for_pipe": True,
        },
        "next_actions": [
            "Chỉ đưa token/user SỞ HỮU vào secrets/backend_pipes.env (xem owned_credentials)",
            "Không dùng Acc_all / stealer / ghn_tokens dump để login",
            "python3 scripts/owned_credentials.py status",
            f"SQL: SELECT kind, platform, COUNT(*) FROM secrets_findings GROUP BY 1,2 — {DB_PATH}",
        ],
    }


def materialize(findings: list[dict], per_file: list[dict]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript(
        """
        CREATE TABLE secrets_findings (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          file TEXT,
          kind TEXT,
          platform TEXT,
          key TEXT,
          masked TEXT,
          fp TEXT,
          length INTEGER,
          context TEXT,
          dump_source INTEGER,
          note TEXT
        );
        CREATE TABLE files_scanned (
          file TEXT PRIMARY KEY,
          size INTEGER,
          dump_file INTEGER,
          findings INTEGER,
          by_kind_json TEXT,
          platforms_json TEXT
        );
        CREATE INDEX idx_sf_kind ON secrets_findings(kind);
        CREATE INDEX idx_sf_plat ON secrets_findings(platform);
        CREATE INDEX idx_sf_file ON secrets_findings(file);
        """
    )
    for f in findings:
        if f.get("kind") == "error":
            continue
        conn.execute(
            "INSERT INTO secrets_findings(file,kind,platform,key,masked,fp,length,context,dump_source,note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                f.get("file"),
                f.get("kind"),
                f.get("platform"),
                f.get("key"),
                f.get("masked"),
                f.get("fp"),
                f.get("length"),
                f.get("context"),
                1 if f.get("dump_source") else 0,
                f.get("note"),
            ),
        )
    for pf in per_file:
        conn.execute(
            "INSERT OR REPLACE INTO files_scanned VALUES (?,?,?,?,?,?)",
            (
                pf["file"],
                pf["size"],
                1 if pf.get("dump_file") else 0,
                pf["findings"],
                json.dumps(pf.get("by_kind") or {}, ensure_ascii=False),
                json.dumps(pf.get("platforms") or [], ensure_ascii=False),
            ),
        )
    conn.commit()
    conn.close()


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔎 TELEGRAM INBOX — RÀ SOÁT TOKEN/COOKIE/SECRET/ID (LẤY ĐƠN)")
    L(f"Lúc: {report['checked_at']}")
    L(report["verdict"])
    L("")
    st = report.get("stats") or {}
    L(f"files={report.get('files_scanned')} findings={report.get('findings_total')} errors={report.get('errors')}")
    L(f"by_kind={st.get('by_kind')}")
    L(f"unique_by_kind={st.get('unique_by_kind')}")
    L(f"by_platform={st.get('by_platform')}")
    L(f"auth_related={st.get('auth_related')} id_related={st.get('id_related')} dump_sourced={st.get('dump_sourced')}")
    L(f"token_platforms={report.get('token_platforms')}")
    L("")
    L("=== Theo file ===")
    for pf in report.get("per_file") or []:
        mark = "⚠DUMP" if pf.get("dump_file") else "·"
        L(f"{mark} {pf.get('file')} size={pf.get('size')} findings={pf.get('findings')} {pf.get('by_kind')}")
        if pf.get("platforms"):
            L(f"    platforms={pf.get('platforms')}")
    L("")
    L("=== Mẫu (đã mask, không hiện password) ===")
    shown = 0
    for s in report.get("samples") or []:
        if s.get("kind") == "password":
            continue
        L(
            f"· [{s.get('kind_label')}] {s.get('platform')} · {s.get('file')} · "
            f"key={s.get('key')} · {s.get('masked')} · dump={s.get('dump_source')}"
        )
        shown += 1
        if shown >= 40:
            break
    L("")
    L("Safety: values masked · passwords fully redacted · no dump-login · owned-only for pipe")
    for a in report.get("next_actions") or []:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    # strip huge samples duplication — keep report as built
    slim = dict(report)
    # keep samples but not full findings list (already not included)
    payload = json.dumps(slim, ensure_ascii=False, indent=2)
    text = format_text(report)
    paths = {
        "json": REPORTS / "telegram_inbox_secrets_audit.json",
        "txt": REPORTS / "telegram_inbox_secrets_audit.txt",
        "db": DB_PATH,
    }
    paths["json"].write_text(payload, encoding="utf-8")
    paths["txt"].write_text(text, encoding="utf-8")
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Rà soát token/cookie/secret/id trong Telegram inbox")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = build_report()
    write_outputs(report)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
