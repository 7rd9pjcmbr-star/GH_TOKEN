"""Path taxonomy helpers."""

from __future__ import annotations

from .constants import PathId

PATH_LABELS: dict[str, str] = {
    PathId.CLEAR.value: "Đủ dữ liệu / đã map — monitor",
    PathId.WAIT.value: "Chờ ship / extend_code — không ép VĐ",
    PathId.MISSING.value: "Hard gap — không bịa timestamp",
    PathId.ACCEPT.value: "Accept có chủ đích (soft/commune/canceled)",
    PathId.MASK.value: "PII redaction **** — không AES-unmask",
}


def label(path_id: str | PathId | None) -> str:
    if path_id is None:
        return ""
    key = path_id.value if isinstance(path_id, PathId) else str(path_id)
    return PATH_LABELS.get(key, key)


def normalize_path(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip().upper()
    for p in PathId:
        if s == p.value or s == p.name:
            return p.value
    if s.startswith("PATH-"):
        return s
    return raw
