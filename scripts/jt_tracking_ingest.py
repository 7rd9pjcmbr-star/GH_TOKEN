#!/usr/bin/env python3
"""Nạp mã vận đơn J&T từ chat Telegram / text — không cần file riêng.

Hỗ trợ:
  billcode=851160187277 cellphone=6146
  JNTMP0017449883:6146
  Một dòng chỉ mã
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"
REFS = SECRETS / "jt_tracking_refs.txt"

BILL_TOKEN_RE = re.compile(
    r"(?:bill\s*code|billcode|mã\s*vận\s*đơn|ma\s*van\s*don|tracking)\s*[=:]\s*"
    r"([A-Za-z0-9]{10,20})",
    re.I,
)
PHONE_TOKEN_RE = re.compile(
    r"(?:cell\s*phone|cellphone|cellphong|phone|sdt|điện\s*thoại)\s*[=:]\s*(\d{4})",
    re.I,
)
LINE_REF_RE = re.compile(
    r"^\s*([A-Za-z0-9]{10,20})\s*(?:[:,\s]\s*(\d{4}))?\s*$",
)
INLINE_BILL_RE = re.compile(
    r"\b(84[0-9]{10,12}|85[0-9]{10,12}|JNTMP[0-9]{10,14}|JT[0-9A-Z]{10,16}|"
    r"JO[0-9]{8,14}|JD[0-9]{8,14})\b",
    re.I,
)


def normalize_bill(raw: str) -> str:
    return raw.strip().upper()


def parse_chat_text(text: str) -> list[tuple[str, str]]:
    """Trả về [(bill, phone4)] — phone4 có thể rỗng."""
    text = (text or "").strip()
    if not text or text.startswith("/"):
        return []

    out: list[tuple[str, str]] = []
    bill_m = BILL_TOKEN_RE.search(text)
    phone_m = PHONE_TOKEN_RE.search(text)
    if bill_m:
        bill = normalize_bill(bill_m.group(1))
        phone = phone_m.group(1) if phone_m else ""
        out.append((bill, phone))
        return out

    for line in text.splitlines():
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        m = LINE_REF_RE.match(t)
        if m:
            out.append((normalize_bill(m.group(1)), (m.group(2) or "").strip()))
            continue
        for b in INLINE_BILL_RE.findall(t):
            out.append((normalize_bill(b), ""))

    if not out:
        for b in INLINE_BILL_RE.findall(text):
            out.append((normalize_bill(b), ""))

    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for bill, phone in out:
        if bill in seen:
            continue
        seen.add(bill)
        deduped.append((bill, phone))
    return deduped


def _read_lines() -> list[str]:
    if not REFS.is_file():
        return [
            "# J&T billCode tra cứu — mỗi dòng: BILL hoặc BILL:1234",
            "# Dòng # stale — mã cũ, bỏ qua khi tra batch",
        ]
    return REFS.read_text(encoding="utf-8", errors="replace").splitlines()


def _write_lines(lines: list[str]) -> None:
    REFS.parent.mkdir(parents=True, exist_ok=True)
    REFS.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    try:
        REFS.chmod(0o600)
    except OSError:
        pass


def mark_stale(bills: list[str]) -> list[str]:
    """Đánh dấu mã cũ — không xóa, chỉ comment stale."""
    targets = {normalize_bill(b) for b in bills}
    if not targets:
        return []
    lines = _read_lines()
    marked: list[str] = []
    new_lines: list[str] = []
    for ln in lines:
        t = ln.strip()
        if not t:
            new_lines.append(ln)
            continue
        if t.startswith("#"):
            new_lines.append(ln)
            continue
        bill = t.split(":")[0].split(",")[0].strip().upper()
        if bill in targets and not t.lower().startswith("# stale"):
            new_lines.append(f"# stale {ln}")
            marked.append(bill)
        else:
            new_lines.append(ln)
    if marked:
        _write_lines(new_lines)
    return marked


def append_refs(pairs: list[tuple[str, str]], *, unmark_stale: bool = True) -> list[str]:
    """Thêm refs mới; trả về danh sách dòng đã thêm."""
    if not pairs:
        return []
    lines = _read_lines()
    existing: dict[str, str] = {}
    for ln in lines:
        t = ln.strip()
        if not t or t.startswith("#"):
            continue
        core = t[7:].strip() if t.lower().startswith("# stale") else t
        bill = core.split(":")[0].split(",")[0].strip().upper()
        phone = ""
        if ":" in core:
            phone = core.split(":", 1)[1].strip()[:4]
        existing[bill] = phone

    added: list[str] = []
    for bill, phone in pairs:
        phone = re.sub(r"\D", "", phone)[-4:]
        if bill in existing and (not phone or existing[bill] == phone):
            if unmark_stale:
                # bỏ stale nếu user gửi lại mã
                for i, ln in enumerate(lines):
                    if ln.strip().lower().startswith("# stale") and bill in ln.upper():
                        lines[i] = ln.replace("# stale", "#", 1).strip()
                        if not lines[i].startswith("# "):
                            lines[i] = lines[i].lstrip("# ").strip()
            continue
        line = f"{bill}:{phone}" if phone else bill
        if bill in existing and phone and existing[bill] != phone:
            line = f"{bill}:{phone}"
        if bill not in existing or (phone and existing.get(bill) != phone):
            lines.append(line)
            added.append(line)
            existing[bill] = phone

    if added:
        _write_lines(lines)
    return added


def ingest_chat_text(text: str) -> dict:
    pairs = parse_chat_text(text)
    added = append_refs(pairs)
    return {"parsed": len(pairs), "added": added, "pairs": pairs}
