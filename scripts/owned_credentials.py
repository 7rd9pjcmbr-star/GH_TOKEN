#!/usr/bin/env python3
"""Owned users/tokens → biến môi trường → mapping khi chạy script sync/mapper.

Chỉ credential SỞ HỮU (điền secrets/backend_pipes.env). Không đọc Acc_all/stealer dumps.

API:
  from owned_credentials import load_env, owned_map, apply_owned_mapping, tokens_for

  env = load_env()
  m = owned_map(env)                 # {platform: [OwnedAccount, ...]}
  row = apply_owned_mapping(order)   # gắn owned_user / owned_token_set / shop
  tok = tokens_for(env, "GHN")       # token sẵn dùng cho API sync
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / "secrets"
REPORTS = ROOT / "reports" / "telegram-classify"
ENV_FILES = (
    SECRETS / "telegram.env",
    SECRETS / "backend_pipes.env",
    SECRETS / "pancake.env",
    SECRETS / "owned_accounts.env",
)

# platform → env key aliases
PLATFORM_SPECS: dict[str, dict[str, tuple[str, ...]]] = {
    "Pancake": {
        "user": ("PANCAKE_USER", "PANCAKE_OWNED_USER", "OWNED_PANCAKE_USER"),
        "token": (
            "PANCAKE_POS_API_KEY",
            "PANCAKE_POS_ACCESS_TOKEN",
            "PANCAKE_API_KEY",
            "PANCAKE_TOKEN",
            "OWNED_PANCAKE_TOKEN",
        ),
        "shop_id": ("PANCAKE_SHOP_ID", "PANCAKE_DEFAULT_SHOP_ID", "OWNED_PANCAKE_SHOP_ID"),
        "extra": (),
    },
    "GHN": {
        "user": ("GHN_USER", "GHN_OWNED_USER", "OWNED_GHN_USER"),
        "token": ("GHN_API_TOKEN", "GHN_TOKEN", "OWNED_GHN_TOKEN"),
        "shop_id": ("GHN_SHOP_ID", "GHN_CLIENT_ID", "OWNED_GHN_SHOP_ID"),
        "extra": (),
    },
    "ViettelPost": {
        "user": ("VIETTELPOST_USER", "VTP_USER", "OWNED_VTP_USER"),
        "token": ("VIETTELPOST_TOKEN", "VTP_TOKEN", "OWNED_VTP_TOKEN"),
        "shop_id": ("VIETTELPOST_SHOP_ID", "VTP_SHOP_ID"),
        "extra": ("VIETTELPOST_PASSWORD", "VTP_PASSWORD"),  # owned only — never from dump
    },
    "TPOS": {
        "user": ("TPOS_USER", "OWNED_TPOS_USER"),
        "token": ("TPOS_ACCESS_TOKEN", "TPOS_TOKEN", "OWNED_TPOS_TOKEN"),
        "shop_id": ("TPOS_SHOP_ID",),
        "extra": ("TPOS_BASE_URL",),
    },
    "Sapo": {
        "user": ("SAPO_USER", "OWNED_SAPO_USER"),
        "token": ("SAPO_ACCESS_TOKEN", "SAPO_API_KEY", "OWNED_SAPO_TOKEN"),
        "shop_id": ("SAPO_STORE", "SAPO_SHOP_ID", "OWNED_SAPO_STORE"),
        "extra": ("SAPO_BASE_URL",),
    },
    "Nhanh": {
        "user": ("NHANH_USER", "OWNED_NHANH_USER"),
        "token": ("NHANH_API_KEY", "NHANH_TOKEN", "OWNED_NHANH_TOKEN"),
        "shop_id": ("NHANH_BUSINESS_ID", "NHANH_SHOP_ID"),
        "extra": ("NHANH_APP_ID",),
    },
    "Shopee": {
        "user": ("SHOPEE_USER", "SPX_USER", "OWNED_SHOPEE_USER"),
        "token": ("SHOPEE_ACCESS_TOKEN", "SPX_TOKEN", "OWNED_SHOPEE_TOKEN"),
        "shop_id": ("SHOPEE_SHOP_ID", "SPX_SHOP_ID"),
        "extra": ("SHOPEE_PARTNER_ID", "SPX_PARTNER_ID"),
    },
    "SPX": {
        "user": ("SPX_USER", "OWNED_SPX_USER"),
        "token": ("SPX_TOKEN", "OWNED_SPX_TOKEN"),
        "shop_id": ("SPX_SHOP_ID",),
        "extra": ("SPX_PARTNER_ID",),
    },
    "VNPost": {
        "user": ("VNPOST_USER", "OWNED_VNPOST_USER"),
        "token": ("VNPOST_TOKEN", "OWNED_VNPOST_TOKEN"),
        "shop_id": ("VNPOST_CUSTOMER_CODE",),
        "extra": (),
    },
    "Telegram": {
        "user": ("TELEGRAM_BOT_USERNAME",),
        "token": ("TELEGRAM_BOT_TOKEN",),
        "shop_id": ("TELEGRAM_CHAT_ID",),
        "extra": (),
    },
    "Aship": {
        "user": ("ASHIP_USER",),
        "token": ("ASHIP_API_KEY", "OWNED_ASHIP_TOKEN"),
        "shop_id": (),
        "extra": (),
    },
}

PLATFORM_ALIASES = {
    "pancake": "Pancake",
    "ghn": "GHN",
    "viettelpost": "ViettelPost",
    "vtp": "ViettelPost",
    "tpos": "TPOS",
    "sapo": "Sapo",
    "nhanh": "Nhanh",
    "shopee": "Shopee",
    "spx": "SPX",
    "spx-local": "SPX",
    "vnpost": "VNPost",
    "vnpost-local": "VNPost",
    "telegram": "Telegram",
    "aship": "Aship",
    "tracking": "Aship",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_env(extra_files: tuple[Path, ...] | None = None) -> dict[str, str]:
    """Nạp os.environ + secrets/*.env (owned)."""
    env = dict(os.environ)
    files = ENV_FILES + (extra_files or ())
    for path in files:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return env


def _first(env: dict[str, str], keys: tuple[str, ...]) -> str:
    for k in keys:
        v = (env.get(k) or "").strip()
        if v:
            return v
    return ""


def mask_secret(value: str | None, *, keep: int = 4) -> str | None:
    if not value:
        return None
    v = str(value)
    if len(v) <= keep * 2:
        return "*" * len(v)
    return f"{v[:keep]}…{v[-keep:]}(len={len(v)})"


@dataclass
class OwnedAccount:
    platform: str
    user: str | None = None
    token: str | None = None
    shop_id: str | None = None
    extras: dict[str, str] = field(default_factory=dict)
    source: str = "env"
    label: str | None = None

    @property
    def ready(self) -> bool:
        return bool(self.token) or bool(self.user and self.extras.get("password"))

    @property
    def token_set(self) -> bool:
        return bool(self.token)

    def public_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "user": self.user,
            "shop_id": self.shop_id,
            "token_set": self.token_set,
            "token_masked": mask_secret(self.token),
            "extras_keys": sorted(self.extras.keys()),
            "ready": self.ready,
            "source": self.source,
            "label": self.label,
        }


def normalize_platform(name: str | None) -> str | None:
    if not name:
        return None
    n = str(name).strip()
    if n in PLATFORM_SPECS:
        return n
    return PLATFORM_ALIASES.get(n.lower())


def _parse_owned_json(env: dict[str, str]) -> list[OwnedAccount]:
    raw = (env.get("OWNED_ACCOUNTS_JSON") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out: list[OwnedAccount] = []
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict):
            continue
        plat = normalize_platform(item.get("platform") or item.get("backend"))
        if not plat:
            continue
        extras = {}
        for k, v in item.items():
            if k in {"platform", "backend", "user", "token", "shop_id", "label"}:
                continue
            if v:
                extras[str(k)] = str(v)
        out.append(
            OwnedAccount(
                platform=plat,
                user=(str(item["user"]).strip() if item.get("user") else None) or None,
                token=(str(item["token"]).strip() if item.get("token") else None) or None,
                shop_id=(str(item["shop_id"]).strip() if item.get("shop_id") else None) or None,
                extras=extras,
                source="OWNED_ACCOUNTS_JSON",
                label=(str(item["label"]).strip() if item.get("label") else None),
            )
        )
    return out


def _parse_map_lines(env: dict[str, str]) -> list[OwnedAccount]:
    """OWNED_MAP_<PLATFORM>=user|token|shop_id (pipe-separated)."""
    out: list[OwnedAccount] = []
    for k, v in env.items():
        m = re.match(r"^OWNED_MAP_([A-Z0-9_]+)$", k)
        if not m or not (v or "").strip():
            continue
        plat = normalize_platform(m.group(1).replace("_", "")) or normalize_platform(m.group(1))
        # try common names
        if not plat:
            key = m.group(1).lower()
            plat = PLATFORM_ALIASES.get(key) or PLATFORM_ALIASES.get(key.replace("_", ""))
        if not plat:
            # GHN, SAPO, etc.
            for name in PLATFORM_SPECS:
                if name.upper() == m.group(1) or name.upper().replace(" ", "") == m.group(1):
                    plat = name
                    break
        if not plat:
            continue
        parts = [p.strip() for p in v.split("|")]
        user = parts[0] or None if len(parts) > 0 else None
        token = parts[1] or None if len(parts) > 1 else None
        shop = parts[2] or None if len(parts) > 2 else None
        out.append(
            OwnedAccount(
                platform=plat,
                user=user,
                token=token,
                shop_id=shop,
                source=f"OWNED_MAP_{m.group(1)}",
            )
        )
    return out


def owned_accounts(env: dict[str, str] | None = None) -> list[OwnedAccount]:
    env = env or load_env()
    accounts: list[OwnedAccount] = []

    for plat, spec in PLATFORM_SPECS.items():
        user = _first(env, spec["user"]) or None
        token = _first(env, spec["token"]) or None
        shop = _first(env, spec["shop_id"]) or None
        extras: dict[str, str] = {}
        for ek in spec.get("extra") or ():
            val = (env.get(ek) or "").strip()
            if val:
                # don't store password plaintext in extras for reports — keep for runtime only
                extras[ek] = val
        if user or token or shop or extras:
            accounts.append(
                OwnedAccount(
                    platform=plat,
                    user=user,
                    token=token,
                    shop_id=shop,
                    extras=extras,
                    source="PLATFORM_ENV",
                )
            )

    accounts.extend(_parse_owned_json(env))
    accounts.extend(_parse_map_lines(env))
    return accounts


def owned_map(env: dict[str, str] | None = None) -> dict[str, list[OwnedAccount]]:
    out: dict[str, list[OwnedAccount]] = {}
    for acc in owned_accounts(env):
        out.setdefault(acc.platform, []).append(acc)
    return out


def tokens_for(env: dict[str, str] | None, platform: str) -> list[str]:
    plat = normalize_platform(platform) or platform
    return [a.token for a in owned_map(env).get(plat, []) if a.token]


def primary_owned(env: dict[str, str] | None, platform: str) -> OwnedAccount | None:
    plat = normalize_platform(platform) or platform
    accs = owned_map(env).get(plat) or []
    ready = [a for a in accs if a.ready]
    return (ready or accs or [None])[0]


def env_overlay_from_owned(env: dict[str, str] | None = None) -> dict[str, str]:
    """Đảm bảo key chuẩn (GHN_API_TOKEN, …) được set từ owned map nếu thiếu."""
    env = dict(env or load_env())
    m = owned_map(env)
    # canonical keys for sync scripts
    canon = {
        "GHN": ("GHN_API_TOKEN", "GHN_USER", "GHN_SHOP_ID"),
        "Pancake": ("PANCAKE_POS_API_KEY", "PANCAKE_USER", "PANCAKE_SHOP_ID"),
        "ViettelPost": ("VIETTELPOST_TOKEN", "VIETTELPOST_USER", "VIETTELPOST_SHOP_ID"),
        "TPOS": ("TPOS_ACCESS_TOKEN", "TPOS_USER", "TPOS_SHOP_ID"),
        "Sapo": ("SAPO_ACCESS_TOKEN", "SAPO_USER", "SAPO_STORE"),
        "Nhanh": ("NHANH_API_KEY", "NHANH_USER", "NHANH_BUSINESS_ID"),
        "Shopee": ("SHOPEE_ACCESS_TOKEN", "SHOPEE_USER", "SHOPEE_SHOP_ID"),
        "SPX": ("SPX_TOKEN", "SPX_USER", "SPX_SHOP_ID"),
        "VNPost": ("VNPOST_TOKEN", "VNPOST_USER", "VNPOST_CUSTOMER_CODE"),
    }
    for plat, (tok_k, user_k, shop_k) in canon.items():
        acc = primary_owned(env, plat)
        if not acc:
            continue
        if acc.token and not (env.get(tok_k) or "").strip():
            env[tok_k] = acc.token
        if acc.user and not (env.get(user_k) or "").strip():
            env[user_k] = acc.user
        if acc.shop_id and not (env.get(shop_k) or "").strip():
            env[shop_k] = acc.shop_id
        # pancake also accepts ACCESS_TOKEN
        if plat == "Pancake" and acc.token:
            env.setdefault("PANCAKE_POS_ACCESS_TOKEN", acc.token)
        if plat == "TPOS" and acc.extras.get("TPOS_BASE_URL"):
            env.setdefault("TPOS_BASE_URL", acc.extras["TPOS_BASE_URL"])
    return env


def match_owned_user(user: str | None, env: dict[str, str] | None = None) -> OwnedAccount | None:
    if not user:
        return None
    u = str(user).strip().lower()
    for acc in owned_accounts(env):
        if acc.user and acc.user.strip().lower() == u:
            return acc
    return None


def apply_owned_mapping(row: dict[str, Any], env: dict[str, str] | None = None) -> dict[str, Any]:
    """Gắn metadata owned vào record đơn khi chạy mapper/sync."""
    env = env or load_env()
    out = dict(row)
    backend = normalize_platform(
        out.get("backend") or out.get("carrier") or out.get("source") or out.get("platform")
    )
    shop = str(out.get("shop_id") or out.get("pancake_shop_id") or "").strip() or None
    user_hint = (
        str(out.get("owned_user_hint") or out.get("staff_creator") or out.get("creator") or "").strip()
        or None
    )

    acc: OwnedAccount | None = None
    if user_hint:
        acc = match_owned_user(user_hint, env)
    if acc is None and backend:
        # match by shop_id
        for a in owned_map(env).get(backend, []):
            if shop and a.shop_id and str(a.shop_id) == str(shop):
                acc = a
                break
        if acc is None:
            acc = primary_owned(env, backend)

    out["owned_map_platform"] = backend
    if acc:
        out["owned_user"] = acc.user
        out["owned_shop_id"] = acc.shop_id or shop
        out["owned_token_set"] = acc.token_set
        out["owned_ready"] = acc.ready
        out["owned_source"] = acc.source
        out["owned_label"] = acc.label
        if not out.get("shop_id") and acc.shop_id:
            out["shop_id"] = acc.shop_id
    else:
        out["owned_user"] = None
        out["owned_shop_id"] = shop
        out["owned_token_set"] = False
        out["owned_ready"] = False
        out["owned_source"] = None
        out["owned_label"] = None
    return out


def mapping_summary(env: dict[str, str] | None = None) -> dict[str, Any]:
    env = env_overlay_from_owned(env)
    m = owned_map(env)
    platforms = {}
    for plat, accs in m.items():
        platforms[plat] = {
            "accounts": len(accs),
            "ready": sum(1 for a in accs if a.ready),
            "with_token": sum(1 for a in accs if a.token_set),
            "users": [a.user for a in accs if a.user],
            "shop_ids": [a.shop_id for a in accs if a.shop_id],
            "public": [a.public_dict() for a in accs],
        }
    ready_platforms = [p for p, info in platforms.items() if info["ready"] > 0]
    return {
        "ok": True,
        "module": "owned_credentials",
        "checked_at": utc_now(),
        "env_files": [str(p) for p in ENV_FILES if p.is_file()],
        "platforms": platforms,
        "ready_platforms": ready_platforms,
        "total_accounts": sum(len(v) for v in m.values()),
        "verdict": (
            f"✅ Owned map sẵn sàng: {', '.join(ready_platforms)}"
            if ready_platforms
            else "⚠ Chưa có user/token sở hữu — điền secrets/backend_pipes.env"
        ),
        "policy": {
            "owned_only": True,
            "no_dump_login": True,
            "secrets_gitignored": True,
        },
        "next_actions": [
            "cp backend_pipes.env.example secrets/backend_pipes.env",
            "Điền USER/TOKEN/SHOP_ID sở hữu (hoặc OWNED_ACCOUNTS_JSON / OWNED_MAP_GHN=user|token|shop)",
            "python3 scripts/owned_credentials.py status",
            "python3 scripts/realtime_order_sync.py --once",
        ],
    }


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔐 OWNED USER/TOKEN → ENV MAPPING")
    L(f"Lúc: {report.get('checked_at')}")
    L(report.get("verdict") or "")
    L(f"env_files: {report.get('env_files')}")
    L(f"ready: {report.get('ready_platforms')} · total_accounts={report.get('total_accounts')}")
    L("")
    for plat, info in (report.get("platforms") or {}).items():
        L(f"· {plat}: ready={info.get('ready')}/{info.get('accounts')} token={info.get('with_token')}")
        L(f"  users={info.get('users')} shops={info.get('shop_ids')}")
        for pub in info.get("public") or []:
            L(
                f"  - user={pub.get('user')} shop={pub.get('shop_id')} "
                f"token={pub.get('token_masked')} ready={pub.get('ready')} src={pub.get('source')}"
            )
    L("")
    L("Policy: owned-only · no dump-login · secrets gitignored")
    for a in report.get("next_actions") or []:
        L(f"· {a}")
    return "\n".join(lines)


def write_outputs(report: dict) -> dict[str, Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": REPORTS / "owned_credentials_map.json",
        "txt": REPORTS / "owned_credentials_map.txt",
    }
    paths["json"].write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["txt"].write_text(format_text(report), encoding="utf-8")
    return paths


def ensure_env_file() -> Path:
    """Tạo secrets/backend_pipes.env từ example nếu chưa có."""
    dest = SECRETS / "backend_pipes.env"
    if dest.is_file():
        return dest
    SECRETS.mkdir(parents=True, exist_ok=True)
    for src in (ROOT / "backend_pipes.env.example", SECRETS / "backend_pipes.env.example"):
        if src.is_file():
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            try:
                os.chmod(dest, 0o600)
            except OSError:
                pass
            return dest
    dest.write_text("# owned credentials\n", encoding="utf-8")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="Owned user/token env mapping")
    ap.add_argument("command", nargs="?", default="status", choices=["status", "ensure", "json"])
    args = ap.parse_args()
    if args.command == "ensure":
        path = ensure_env_file()
        print(json.dumps({"ok": True, "path": str(path)}, ensure_ascii=False))
        return 0
    report = mapping_summary()
    write_outputs(report)
    if args.command == "json" or args.command == "status" and "--json" in __import__("sys").argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
