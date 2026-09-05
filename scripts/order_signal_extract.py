#!/usr/bin/env python3
"""Trích tín hiệu quan trọng liên quan lấy đơn hàng từ file inbox.

Không bỏ qua: URL/domain/host API, path order/tracking, shop_id, mã vận đơn,
platform (GHN/Sapo/Nhanh/Shopee/Pancake/SPX/VTP…), endpoint gợi ý pipe.

Không làm: dump-login, lưu plaintext password vào báo cáo, mass Acc_all login.
Password/secret chỉ đếm hiện diện + độ dài/prefix ngắn (audit), không dùng gọi API.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]

# Cột / khóa được giữ — liên quan lấy đơn
ORDER_VALUE_KEYS = (
    "url",
    "host",
    "domain",
    "endpoint",
    "path",
    "shop",
    "shop_id",
    "store",
    "store_id",
    "warehouse",
    "kho",
    "buucuc",
    "tracking",
    "tracking_code",
    "order",
    "order_id",
    "order_code",
    "ma_van_don",
    "van_don",
    "provider",
    "carrier",
    "backend",
    "token_kind",
    "bucket",
    "date",
    "source",
    "filtered line",
    "name",
    "systemid",
)

# Không xuất giá trị thô
SECRET_KEYS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "otp",
    "cookie",
)

PLATFORM_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("GHN", ("ghn.vn", "ghn.com", "api.ghn")),
    ("Nhanh", ("nhanh.vn", "nhanh.com")),
    ("Sapo", ("sapo.vn", "sapo.com")),
    ("Shopee", ("shopee.vn", "shopee.com", "spx", "shopeexpress")),
    ("Pancake", ("pancake.vn", "pancake.com", "pos.pages.fm")),
    ("ViettelPost", ("viettelpost", "vtp.vn")),
    ("VNPost", ("vnpost",)),
    ("Haravan", ("haravan",)),
    ("TPOS", ("tpos",)),
    ("Aship", ("aship.app", "tracking.aship")),
    ("Sendo", ("sendo.vn",)),
    ("Tiki", ("tiki.vn",)),
]

ORDER_PATH_RE = re.compile(
    r"(order|orders|don.?hang|tracking|van.?don|shipment|fulfill|warehouse|kho|buu.?cuc|shop|store|/api/)",
    re.I,
)
URL_RE = re.compile(r"https?://[^\s\"'<>]+|(?:www\.)?[a-z0-9.-]+\.(?:vn|com|app|net|io)(?:/[^\s\"'<>]*)?", re.I)
TOKENISH_RE = re.compile(r"^(?:Bearer\s+)?[A-Za-z0-9_\-.]{20,}$")
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def detect_platform(text: str) -> list[str]:
    t = (text or "").lower()
    hits = []
    for name, needles in PLATFORM_RULES:
        if any(n in t for n in needles):
            hits.append(name)
    return hits


def host_of(url: str) -> str | None:
    u = (url or "").strip()
    if not u:
        return None
    if "://" not in u:
        u = "https://" + u
    try:
        p = urlparse(u)
        return (p.hostname or "").lower() or None
    except Exception:  # noqa: BLE001
        return None


def path_of(url: str) -> str | None:
    u = (url or "").strip()
    if not u:
        return None
    if "://" not in u:
        u = "https://" + u
    try:
        p = urlparse(u)
        path = p.path or "/"
        if p.query:
            # giữ tên query quan trọng, bỏ giá trị dài
            qparts = []
            for part in p.query.split("&")[:8]:
                if "=" in part:
                    k, v = part.split("=", 1)
                    if any(x in k.lower() for x in ("order", "shop", "track", "provider", "code", "id")):
                        qparts.append(f"{k}={'…' if len(v) > 24 else v}")
                    else:
                        qparts.append(k)
                else:
                    qparts.append(part)
            path = path + "?" + "&".join(qparts)
        return path[:180]
    except Exception:  # noqa: BLE001
        return None


def token_audit(value: str) -> dict | None:
    """Chỉ metadata token — không lộ full secret."""
    v = (value or "").strip()
    if not v or len(v) < 16:
        return None
    if not TOKENISH_RE.match(v) and "eyJ" not in v[:5]:
        return None
    kind = "jwt" if v.startswith("eyJ") or v.startswith("Bearer eyJ") else "opaque"
    bare = v[7:] if v.lower().startswith("bearer ") else v
    return {
        "kind": kind,
        "length": len(bare),
        "prefix8": bare[:8],
        "suffix4": bare[-4:] if len(bare) >= 4 else "",
        "usable_for_order_pipe": False,  # dump → không auto gắn secrets
        "note": "Chỉ dùng nếu đây là credential sở hữu — điền thủ công secrets/backend_pipes.env",
    }


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


def iter_xlsx_rows(path: Path, max_rows_per_sheet: int = 4000, max_sheets: int = 6):
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
            count = 0
            for row in root.findall(f"{NS}sheetData/{NS}row"):
                cells = []
                for c in row.findall(f"{NS}c"):
                    cells.append(_cell_text(c, shared))
                if not headers:
                    headers = [str(x).strip() for x in cells]
                    continue
                # pad
                while len(cells) < len(headers):
                    cells.append("")
                yield meta["name"], headers, cells
                count += 1
                if count >= max_rows_per_sheet:
                    break


def row_to_dict(headers: list[str], cells: list[str]) -> dict[str, str]:
    out = {}
    for i, h in enumerate(headers):
        key = (h or f"col{i}").strip()
        out[key] = cells[i] if i < len(cells) else ""
    return out


def is_secret_header(name: str) -> bool:
    n = (name or "").lower().strip()
    return any(s == n or s in n for s in SECRET_KEYS)


def is_order_value_header(name: str) -> bool:
    n = (name or "").lower().strip()
    if is_secret_header(n):
        return False
    return any(k in n for k in ORDER_VALUE_KEYS) or n in {"url", "user", "username", "login", "email"}


def extract_from_xlsx(path: Path, *, row_limit: int = 5000) -> dict:
    hosts: Counter = Counter()
    paths: Counter = Counter()
    platforms: Counter = Counter()
    orderish_lines = 0
    urls_kept: list[str] = []
    users_kept: Counter = Counter()  # identifier liên quan shop/login — giữ để map pipe (không kèm password)
    token_meta: list[dict] = []
    sheets_touch: Counter = Counter()
    important_samples: list[dict] = []
    filtered_order_hits: list[str] = []

    for sheet, headers, cells in iter_xlsx_rows(path, max_rows_per_sheet=row_limit):
        sheets_touch[sheet] += 1
        row = row_to_dict(headers, cells)
        # gather text blobs from order-relevant columns only (+ URL/FILTERED LINE always)
        blobs = []
        rec_keep: dict[str, str] = {}
        for h, v in row.items():
            if not v or not str(v).strip():
                continue
            hl = h.lower()
            if is_secret_header(hl):
                # chỉ audit token-like; password thường không phải API token
                ta = token_audit(str(v))
                if ta:
                    token_meta.append(ta)
                continue
            if is_order_value_header(h) or hl in {"filtered line", "name", "url", "user", "username"}:
                blobs.append(str(v))
                # User/email giữ — quan trọng gắn shop/pipe (không gắn password)
                if hl in {"user", "username", "login", "email"}:
                    users_kept[str(v).strip()[:80]] += 1
                    rec_keep["user"] = str(v).strip()[:80]
                elif "url" in hl or hl == "filtered line" or hl == "name":
                    rec_keep[h] = str(v).strip()[:200]
                elif any(k in hl for k in ("shop", "track", "order", "bucket", "source", "date")):
                    rec_keep[h] = str(v).strip()[:120]

        text = " | ".join(blobs)
        if ORDER_PATH_RE.search(text):
            orderish_lines += 1
        for plat in detect_platform(text):
            platforms[plat] += 1
        for m in URL_RE.findall(text):
            url = m.rstrip(").,;]")
            hst = host_of(url)
            if hst:
                hosts[hst] += 1
            pth = path_of(url)
            if pth and ORDER_PATH_RE.search(pth):
                paths[f"{hst or ''}{pth}"] += 1
                if len(urls_kept) < 40:
                    urls_kept.append(url[:180])
            elif hst and any(n in hst for _, needles in PLATFORM_RULES for n in needles):
                if len(urls_kept) < 40:
                    urls_kept.append(url[:180])
        # internal_search FILTERED LINE may be plain text without scheme
        fl = row.get("FILTERED LINE") or row.get("Filtered Line") or ""
        if fl and ORDER_PATH_RE.search(fl):
            if len(filtered_order_hits) < 30:
                filtered_order_hits.append(str(fl)[:160])
        if rec_keep and len(important_samples) < 25:
            # gắn platform
            plats = detect_platform(" ".join(rec_keep.values()))
            if plats or rec_keep.get("url") or any("order" in k.lower() or "track" in k.lower() for k in rec_keep):
                important_samples.append({"sheet": sheet, "platforms": plats, **rec_keep})

    return {
        "hosts": hosts.most_common(40),
        "order_paths": paths.most_common(40),
        "platforms": dict(platforms),
        "orderish_row_hits": orderish_lines,
        "urls_sample": urls_kept,
        "users_top": users_kept.most_common(30),  # identifier quan trọng — không skip
        "token_audits": token_meta[:20],
        "token_audit_count": len(token_meta),
        "sheets_rows_scanned": dict(sheets_touch),
        "important_samples": important_samples,
        "filtered_order_hits": filtered_order_hits,
    }


def extract_from_text(path: Path, *, line_limit: int = 8000) -> dict:
    hosts: Counter = Counter()
    platforms: Counter = Counter()
    paths: Counter = Counter()
    users: Counter = Counter()
    token_meta: list[dict] = []
    urls: list[str] = []
    orderish = 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[:line_limit]
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}
    for ln in lines:
        if not ln.strip():
            continue
        if ORDER_PATH_RE.search(ln):
            orderish += 1
        for plat in detect_platform(ln):
            platforms[plat] += 1
        # identifier:password — giữ identifier, bỏ password
        if ":" in ln and "://" not in ln.split(":", 1)[0]:
            ident, pw = ln.split(":", 1)
            ident = ident.strip()
            if ident:
                users[ident[:80]] += 1
            ta = token_audit(pw.strip())
            if ta:
                token_meta.append(ta)
            continue
        for m in URL_RE.findall(ln):
            url = m.rstrip(").,;]")
            hst = host_of(url)
            if hst:
                hosts[hst] += 1
            pth = path_of(url)
            if pth and ORDER_PATH_RE.search(pth):
                paths[f"{hst or ''}{pth}"] += 1
            if hst and len(urls) < 40:
                if any(n in hst for _, needles in PLATFORM_RULES for n in needles):
                    urls.append(url[:180])
    return {
        "hosts": hosts.most_common(40),
        "order_paths": paths.most_common(40),
        "platforms": dict(platforms),
        "orderish_row_hits": orderish,
        "urls_sample": urls,
        "users_top": users.most_common(30),
        "token_audits": token_meta[:20],
        "token_audit_count": len(token_meta),
    }


def extract_from_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace")[:5_000_000])
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:120]}
    hosts: Counter = Counter()
    platforms: Counter = Counter()
    keys_seen: Counter = Counter()
    tracking = 0
    shop_ids: Counter = Counter()

    def walk(obj, depth=0):
        nonlocal tracking
        if depth > 8:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                keys_seen[str(k)] += 1
                kl = str(k).lower()
                if any(x in kl for x in ("track", "order", "shop", "warehouse", "kho", "buucuc", "carrier")):
                    if isinstance(v, (str, int)) and str(v).strip():
                        if "shop" in kl:
                            shop_ids[str(v)[:64]] += 1
                        if "track" in kl:
                            tracking += 1
                if isinstance(v, str):
                    for plat in detect_platform(v):
                        platforms[plat] += 1
                    for m in URL_RE.findall(v):
                        hst = host_of(m)
                        if hst:
                            hosts[hst] += 1
                walk(v, depth + 1)
        elif isinstance(obj, list):
            for it in obj[:2000]:
                walk(it, depth + 1)

    walk(data)
    return {
        "hosts": hosts.most_common(40),
        "platforms": dict(platforms),
        "order_related_keys": [k for k, _ in keys_seen.most_common(80) if any(
            x in k.lower() for x in ("order", "track", "shop", "kho", "ware", "carrier", "status", "phone", "address")
        )],
        "shop_ids_top": shop_ids.most_common(40),
        "tracking_field_hits": tracking,
        "users_top": [],
        "order_paths": [],
        "urls_sample": [],
        "token_audits": [],
        "token_audit_count": 0,
        "orderish_row_hits": tracking + sum(shop_ids.values()),
    }


def extract_order_signals(path: Path) -> dict:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in {".xlsx", ".xlsm"}:
        raw = extract_from_xlsx(path)
    elif ext in {".txt", ".log", ".lst", ".tsv"}:
        raw = extract_from_text(path)
    elif ext == ".csv":
        # CSV: đọc như text có cấu trúc nhẹ
        raw = extract_from_text(path)
    elif ext == ".json":
        raw = extract_from_json(path)
    else:
        raw = {"note": f"no extractor for {ext}"}

    platforms = raw.get("platforms") or {}
    backend_hints = []
    for plat, n in sorted(platforms.items(), key=lambda x: -x[1]):
        backend_hints.append(
            {
                "platform": plat,
                "hits": n,
                "pipe_hint": {
                    "GHN": "GHN_API_TOKEN / shop token → realtime_order_sync",
                    "Nhanh": "Nhanh API key (owned) → OMS pipe",
                    "Sapo": "Sapo access token (owned) → order export",
                    "Shopee": "SPX/Shopee local xlsx hoặc partner API",
                    "Pancake": "PANCAKE_POS_API_KEY + shop_id",
                    "ViettelPost": "VTP token owned",
                    "VNPost": "vnpost export",
                    "Aship": "tracking.aship.app provider_code",
                }.get(plat, "điền secrets/backend_pipes.env nếu sở hữu"),
            }
        )

    return {
        "file": path.name,
        "ok": "error" not in raw,
        "signals": raw,
        "backend_hints": backend_hints,
        "kept_order_values": True,
        "skipped_secrets_only": list(SECRET_KEYS),
        "policy": {
            "keep_urls_hosts_paths_shop_tracking": True,
            "keep_user_identifiers": True,
            "redact_passwords": True,
            "no_dump_login": True,
            "tokens_audit_only_until_owned": True,
        },
        "verdict": (
            f"Giữ tín hiệu lấy đơn: platforms={dict(platforms)} "
            f"hosts={len(raw.get('hosts') or [])} users={len(raw.get('users_top') or [])} "
            f"orderish={raw.get('orderish_row_hits', 0)}"
        ),
    }


def format_signals(block: dict) -> str:
    lines = []
    L = lines.append
    L(f"📡 ORDER SIGNALS · {block.get('file')}")
    L(block.get("verdict") or "")
    sig = block.get("signals") or {}
    L(f"platforms: {sig.get('platforms')}")
    L(f"hosts top: {sig.get('hosts')[:12]}")
    L(f"order_paths: {sig.get('order_paths')[:10]}")
    L(f"users_top (giữ): {sig.get('users_top')[:10]}")
    if sig.get("shop_ids_top"):
        L(f"shop_ids: {sig.get('shop_ids_top')[:10]}")
    if sig.get("order_related_keys"):
        L(f"order_keys: {sig.get('order_related_keys')[:20]}")
    L(f"urls_sample: {sig.get('urls_sample')[:8]}")
    L(f"filtered_order_hits: {(sig.get('filtered_order_hits') or [])[:5]}")
    L(f"token_audit_count={sig.get('token_audit_count')} (không auto-login)")
    for h in block.get("backend_hints") or []:
        L(f"· pipe {h['platform']} hits={h['hits']}: {h['pipe_hint']}")
    L(f"policy: {block.get('policy')}")
    return "\n".join(lines)


def main() -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = [extract_order_signals(Path(p)) for p in args.paths]
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for b in out:
            print(format_signals(b))
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
