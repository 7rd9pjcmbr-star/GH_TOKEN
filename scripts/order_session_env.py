#!/usr/bin/env python3
"""Rà soát key lấy đơn / login → gom biến môi trường → duy trì phiên.

Chỉ credential sở hữu trong secrets/. Không dump-login · không in raw token.

CLI:
  python3 scripts/order_session_env.py audit
  python3 scripts/order_session_env.py export   # → secrets/order_session.env
  python3 scripts/order_session_env.py ensure   # probe/refresh phiên
  python3 scripts/order_session_env.py status
  python3 scripts/order_session_env.py apply-shell  # in lệnh export (masked check)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SECRETS = ROOT / "secrets"
REPORTS = ROOT / "reports" / "telegram-classify"
SESSION_ENV = SECRETS / "order_session.env"
SESSION_STATE = SECRETS / "order_session.state.json"
SESSION_EXAMPLE = ROOT / "secrets" / "order_session.env.example"
# example also at repo root-friendly path for docs
SESSION_EXAMPLE_DOCS = ROOT / "order_session.env.example"

# Categories for order-fetch + login session
KEY_CATALOG: dict[str, tuple[str, ...]] = {
    "pancake_login_session": (
        "PANCAKE_USER",
        "PANCAKE_API_KEY_USER",
        "PANCAKE_POS_ACCESS_TOKEN",
        "PANCAKE_POS_SECONDARY_ACCESS_TOKEN",
        "PANCAKE_POS_SECONDARY_USER",
        "PANCAKE_POS_ACCESS_TOKENS",
        "PANCAKE_ACCOUNT_UID",
        "PANCAKE_POS_COUNTRY",
    ),
    "pancake_order_api": (
        "PANCAKE_POS_API_KEY",
        "PANCAKE_API_KEY",
        "PANCAKE_SHOP_ID",
        "PANCAKE_POS_SHOP_IDS",
        "PANCAKE_SECONDARY_SHOP_IDS",
        "PANCAKE_PAGE_ID",
        "PANCAKE_PAGE_IDS",
        "PANCAKE_PAGE_ID_SECONDARY",
        "PANCAKE_WAREHOUSE_ID",
    ),
    "ghn": ("GHN_USER", "GHN_API_TOKEN", "GHN_SHOP_ID", "OWNED_MAP_GHN"),
    "viettelpost_login": (
        "VIETTELPOST_USER",
        "VIETTELPOST_PASSWORD",
        "VIETTELPOST_TOKEN",
        "VIETTELPOST_SHOP_ID",
        "VTP_USER",
        "VTP_PASSWORD",
        "VTP_TOKEN",
    ),
    "tpos": ("TPOS_USER", "TPOS_BASE_URL", "TPOS_ACCESS_TOKEN", "TPOS_SHOP_ID"),
    "sapo": ("SAPO_USER", "SAPO_ACCESS_TOKEN", "SAPO_STORE", "SAPO_BASE_URL"),
    "nhanh": ("NHANH_USER", "NHANH_API_KEY", "NHANH_BUSINESS_ID", "NHANH_APP_ID"),
    "shopee_spx": (
        "SHOPEE_USER",
        "SHOPEE_ACCESS_TOKEN",
        "SHOPEE_SHOP_ID",
        "SPX_USER",
        "SPX_TOKEN",
        "SPX_SHOP_ID",
        "SPX_PARTNER_ID",
        "SPX_3PL",
        "SPX_SENDER_NAME",
    ),
    "vnpost": ("VNPOST_USER", "VNPOST_TOKEN", "VNPOST_CUSTOMER_CODE"),
    "aship": ("ASHIP_USER", "ASHIP_API_KEY"),
    "telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_USERNAME", "TELEGRAM_CHAT_ID"),
    "crypto_session": ("MAPPER_AES_KEY_B64", "MAPPER_ICON_AES_KEY_B64"),
    "meta_order": (
        "ORDER_API_HOSTS",
        "ORDER_PLATFORMS_SEEN",
        "ORDER_SOURCES_SEEN",
        "ORDER_TOKEN_SOURCE_LABELS",
        "OWNED_ACCOUNTS_JSON",
        "PLATFORM_USER",
        "PLATFORM_TOKEN",
        "PLATFORM_SHOP_ID",
    ),
}

SOURCE_FILES = (
    SECRETS / "backend_pipes.env",
    SECRETS / "mapper_icon_aes.env",
    SECRETS / "telegram.env",
    SECRETS / "pancake.env",
    SECRETS / "owned_accounts.env",
    SESSION_ENV,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mask_secret(value: str | None, *, keep: int = 4) -> str | None:
    from owned_credentials import mask_secret as _mask

    return _mask(value, keep=keep)


def _quote_env(v: str) -> str:
    """Quote value if needed for .env round-trip (spaces / #)."""
    if re.search(r'[\s#"\\]', v) or v != v.strip():
        esc = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{esc}"'
    return v


def load_all_env() -> dict[str, str]:
    from owned_credentials import load_env

    return load_env(extra_files=(SECRETS / "mapper_icon_aes.env", SESSION_ENV))


def catalog_keys() -> list[str]:
    keys: set[str] = set()
    for group in KEY_CATALOG.values():
        keys.update(group)
    # scan secret files for additional auth-ish keys
    for path in SOURCE_FILES:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k = t.split("=", 1)[0].strip()
            up = k.upper()
            if any(
                x in up
                for x in (
                    "KEY",
                    "TOKEN",
                    "COOKIE",
                    "PASSWORD",
                    "SESSION",
                    "SECRET",
                    "AUTH",
                    "BEARER",
                    "USER",
                    "SHOP",
                    "UID",
                    "PAGE",
                    "WAREHOUSE",
                )
            ):
                keys.add(k)
    return sorted(keys)


def classify_key(key: str) -> str:
    for cat, keys in KEY_CATALOG.items():
        if key in keys:
            return cat
    up = key.upper()
    if "PANCAKE" in up and ("TOKEN" in up or "USER" in up or "UID" in up):
        return "pancake_login_session"
    if "PANCAKE" in up:
        return "pancake_order_api"
    if "MAPPER" in up or "AES" in up:
        return "crypto_session"
    if "TELEGRAM" in up:
        return "telegram"
    return "other"


def role_for_key(key: str) -> str:
    up = key.upper()
    if any(x in up for x in ("PASSWORD",)):
        return "login_password"
    if "ACCESS_TOKEN" in up or up.endswith("_TOKEN") or "BEARER" in up:
        return "session_token"
    if "API_KEY" in up or up.endswith("_KEY") or "AES_KEY" in up:
        return "api_key"
    if "COOKIE" in up:
        return "session_cookie"
    if "USER" in up or "UID" in up:
        return "identity"
    if "SHOP" in up or "PAGE" in up or "WAREHOUSE" in up or "BUSINESS" in up:
        return "scope_id"
    return "meta"


def audit(env: dict[str, str] | None = None) -> dict[str, Any]:
    env = env or load_all_env()
    keys = catalog_keys()
    by_cat: dict[str, list[dict]] = {}
    rows = []
    for k in keys:
        v = (env.get(k) or "").strip()
        cat = classify_key(k)
        row = {
            "key": k,
            "category": cat,
            "role": role_for_key(k),
            "set": bool(v),
            "len": len(v) if v else 0,
            "masked": mask_secret(v) if v else None,
        }
        rows.append(row)
        by_cat.setdefault(cat, []).append(row)

    from owned_credentials import owned_map

    platforms = {
        plat: [a.public_dict() for a in accs]
        for plat, accs in owned_map(env).items()
    }
    session_ready = {
        "pancake_bearer": bool((env.get("PANCAKE_POS_ACCESS_TOKEN") or "").strip()),
        "pancake_api_key": bool(
            (env.get("PANCAKE_POS_API_KEY") or env.get("PANCAKE_API_KEY") or "").strip()
        ),
        "pancake_secondary_bearer": bool(
            (env.get("PANCAKE_POS_SECONDARY_ACCESS_TOKEN") or "").strip()
        ),
        "ghn": bool((env.get("GHN_API_TOKEN") or "").strip()),
        "viettelpost": bool((env.get("VIETTELPOST_TOKEN") or "").strip()),
        "mapper_aes": bool(
            (env.get("MAPPER_ICON_AES_KEY_B64") or env.get("MAPPER_AES_KEY_B64") or "").strip()
        ),
        "telegram": bool((env.get("TELEGRAM_BOT_TOKEN") or "").strip()),
    }
    set_n = sum(1 for r in rows if r["set"])
    return {
        "ok": True,
        "module": "order_session_env",
        "checked_at": utc_now(),
        "summary": {
            "keys_total": len(rows),
            "keys_set": set_n,
            "keys_empty": len(rows) - set_n,
            "session_ready": session_ready,
        },
        "by_category": {
            cat: {
                "total": len(items),
                "set": sum(1 for i in items if i["set"]),
                "keys": items,
            }
            for cat, items in sorted(by_cat.items())
        },
        "platforms": platforms,
        "source_files": [str(p) for p in SOURCE_FILES if p.is_file()],
        "session_env": str(SESSION_ENV),
        "policy": {
            "owned_only": True,
            "no_dump_login": True,
            "no_raw_token_in_report": True,
            "secrets_gitignored": True,
        },
        "verdict": (
            f"Session env: set={set_n}/{len(rows)} · "
            f"Pancake bearer={'OK' if session_ready['pancake_bearer'] else '∅'} · "
            f"api_key={'OK' if session_ready['pancake_api_key'] else '∅'} · "
            f"AES={'OK' if session_ready['mapper_aes'] else '∅'}"
        ),
        "next_actions": [
            "python3 scripts/order_session_env.py export",
            "python3 scripts/order_session_env.py ensure",
            "python3 scripts/access_token_rotate.py ensure --direct",
            "PYTHONPATH=scripts python3 -m order_pipe --fetch-orders",
        ],
    }


def export_session_env(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Ghi secrets/order_session.env — gom mọi key liên quan (giá trị owned hiện có)."""
    env = env or load_all_env()
    SECRETS.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        "# order_session.env — gom key lấy đơn + login/session (OWNED ONLY)",
        f"# generated_at={utc_now()}",
        "# Do NOT commit. Maintains session via ACCESS_TOKEN / API_KEY / AES.",
        "",
    ]
    written = 0
    empty = 0
    for cat, keys in KEY_CATALOG.items():
        lines.append(f"# ── {cat} ──")
        for k in keys:
            v = (env.get(k) or "").strip()
            if v:
                lines.append(f"{k}={_quote_env(v)}")
                written += 1
            else:
                lines.append(f"# {k}=")
                empty += 1
        lines.append("")

    # extras found but not in catalog
    catalog = set(catalog_keys())
    extras = []
    for k in sorted(catalog):
        if any(k in g for g in KEY_CATALOG.values()):
            continue
        v = (env.get(k) or "").strip()
        if v:
            extras.append((k, v))
    if extras:
        lines.append("# ── other_discovered ──")
        for k, v in extras:
            lines.append(f"{k}={_quote_env(v)}")
            written += 1
        lines.append("")

    SESSION_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(SESSION_ENV, 0o600)
    except OSError:
        pass

    state = {
        "updated_at": utc_now(),
        "session_env": str(SESSION_ENV),
        "keys_written": written,
        "keys_empty_placeholders": empty,
        "categories": list(KEY_CATALOG.keys()),
    }
    SESSION_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    # also write committed example (names only) if missing or refresh
    _write_example()

    return {
        "ok": True,
        "path": str(SESSION_ENV),
        "state": str(SESSION_STATE),
        "keys_written": written,
        "keys_empty_placeholders": empty,
        "checked_at": utc_now(),
        "verdict": f"Exported {written} set keys → {SESSION_ENV.name} (chmod 600)",
    }


def _write_example() -> Path:
    lines = [
        "# Sao chép: cp order_session.env.example secrets/order_session.env",
        "# Chỉ credential OWNED. Không paste dump/stealer/cookie lạ.",
        "# Dùng: python3 scripts/order_session_env.py export|ensure|status",
        "",
    ]
    for cat, keys in KEY_CATALOG.items():
        lines.append(f"# ── {cat} ──")
        for k in keys:
            lines.append(f"{k}=")
        lines.append("")
    text = "\n".join(lines) + "\n"
    # prefer secrets example (gitignored dir) + root example for docs
    ex1 = ROOT / "order_session.env.example"
    ex1.write_text(text, encoding="utf-8")
    return ex1


def apply_to_environ(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Nạp session env vào os.environ (duy trì phiên process hiện tại)."""
    env = env or load_all_env()
    # Prefer order_session.env overlay last
    if SESSION_ENV.is_file():
        for line in SESSION_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, _, v = t.partition("=")
            os.environ[k.strip()] = v.strip().strip('"').strip("'")
    applied = 0
    for cat, keys in KEY_CATALOG.items():
        for k in keys:
            v = (env.get(k) or os.environ.get(k) or "").strip()
            if v:
                os.environ[k] = v
                applied += 1
    return {
        "ok": True,
        "applied": applied,
        "checked_at": utc_now(),
        "verdict": f"Applied {applied} keys into os.environ",
    }


def ensure_session(*, via_nginx: bool = False) -> dict[str, Any]:
    """Duy trì phiên: export → apply → access_token ensure → keepalive probe."""
    exported = export_session_env()
    applied = apply_to_environ()

    ensure_report: dict[str, Any] = {"skipped": True}
    try:
        from access_token_rotate import ensure_tokens, ensure_tokens_via_nginx

        if via_nginx:
            ensure_report = ensure_tokens_via_nginx()
        else:
            ensure_report = ensure_tokens()
    except Exception as e:  # noqa: BLE001
        ensure_report = {"ok": False, "error": str(e)}

    keepalive: dict[str, Any] = {"skipped": True}
    try:
        from backend_pipe_keepalive import load_env as ka_load, run_once

        # run_once expects notify bool
        keepalive = run_once(ka_load(), notify=False)
    except Exception as e:  # noqa: BLE001
        keepalive = {"ok": False, "error": str(e)}

    audit_rep = audit()
    state = {
        "updated_at": utc_now(),
        "export": {
            "keys_written": exported.get("keys_written"),
            "path": exported.get("path"),
        },
        "applied": applied.get("applied"),
        "ensure_ok": bool(ensure_report.get("ok", True)) and "error" not in ensure_report,
        "keepalive_ok": bool(keepalive.get("ok", True)) and "error" not in keepalive,
        "session_ready": (audit_rep.get("summary") or {}).get("session_ready"),
        "verdict": audit_rep.get("verdict"),
    }
    # scrub ensure/keepalive of any accidental tokens
    SESSION_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    REPORTS.mkdir(parents=True, exist_ok=True)
    public = {
        "ok": True,
        "module": "order_session_env.ensure",
        "checked_at": utc_now(),
        "export": exported,
        "applied": applied,
        "ensure": {
            "ok": state["ensure_ok"],
            "verdict": ensure_report.get("verdict") or ensure_report.get("error"),
            "platforms": list((ensure_report.get("platforms") or {}).keys())
            if isinstance(ensure_report.get("platforms"), dict)
            else None,
        },
        "keepalive": {
            "ok": state["keepalive_ok"],
            "verdict": keepalive.get("verdict") or keepalive.get("error"),
            "risks": keepalive.get("session_risk_count")
            or keepalive.get("risks")
            or keepalive.get("summary"),
        },
        "session_ready": state["session_ready"],
        "verdict": (
            f"Session maintain: export={exported.get('keys_written')} "
            f"ensure={'OK' if state['ensure_ok'] else 'FAIL'} "
            f"keepalive={'OK' if state['keepalive_ok'] else 'FAIL'} · "
            f"{audit_rep.get('verdict')}"
        ),
        "policy": audit_rep.get("policy"),
        "next_actions": [
            "PYTHONPATH=scripts python3 -m order_pipe --fetch-orders --limit 80",
            "PYTHONPATH=scripts python3 -m order_pipe --unmask-assist",
            "python3 scripts/order_session_env.py status",
        ],
    }
    (REPORTS / "order_session_env.json").write_text(
        json.dumps(public, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (REPORTS / "order_session_env.txt").write_text(format_text(public), encoding="utf-8")
    return public


def format_text(report: dict) -> str:
    lines: list[str] = []
    L = lines.append
    L("🔐 ORDER SESSION ENV · KEY LẤY ĐƠN + LOGIN")
    L(f"Lúc: {report.get('checked_at')}")
    L(str(report.get("verdict") or ""))
    L("")
    if report.get("summary"):
        s = report["summary"]
        L(
            f"keys set={s.get('keys_set')}/{s.get('keys_total')} · "
            f"ready={s.get('session_ready')}"
        )
    if report.get("by_category"):
        L("=== Theo nhóm ===")
        for cat, info in report["by_category"].items():
            L(f"· {cat}: set={info.get('set')}/{info.get('total')}")
            for row in info.get("keys") or []:
                if not row.get("set"):
                    continue
                L(
                    f"  - [{row.get('role')}] {row.get('key')}={row.get('masked')}"
                )
    if report.get("platforms"):
        L("")
        L("=== Platforms ===")
        for plat, accs in report["platforms"].items():
            for a in accs:
                L(
                    f"· {plat}: user={a.get('user')} shop={a.get('shop_id')} "
                    f"token={a.get('token_masked')} ready={a.get('ready')}"
                )
    if report.get("export"):
        L("")
        L(f"export: {report['export']}")
    if report.get("ensure"):
        L(f"ensure: {report['ensure']}")
    if report.get("keepalive"):
        L(f"keepalive: {report['keepalive']}")
    if report.get("session_ready"):
        L(f"session_ready: {report['session_ready']}")
    L("")
    L("Policy: owned-only · no dump-login · no raw token in report")
    L("Next:")
    for a in report.get("next_actions") or []:
        L(f"· {a}")
    return "\n".join(lines) + "\n"


def write_audit_outputs(report: dict) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "order_session_env.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (REPORTS / "order_session_env.txt").write_text(format_text(report), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rà soát / gom / duy trì session env lấy đơn")
    ap.add_argument(
        "cmd",
        nargs="?",
        default="status",
        choices=["audit", "export", "ensure", "status", "apply-shell"],
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--via-nginx", action="store_true", help="ensure qua nginx gateway")
    args = ap.parse_args(argv)

    if args.cmd == "audit":
        report = audit()
        write_audit_outputs(report)
    elif args.cmd == "export":
        report = export_session_env()
        write_audit_outputs({**audit(), "export": report, "verdict": report.get("verdict")})
    elif args.cmd == "ensure":
        report = ensure_session(via_nginx=bool(args.via_nginx))
    elif args.cmd == "apply-shell":
        # Print shell exports only for SET keys as masked commentary + real via sourced file
        applied = apply_to_environ()
        report = {
            "ok": True,
            "checked_at": utc_now(),
            "verdict": (
                f"Source file: set -a; source {SESSION_ENV}; set +a  "
                f"(applied={applied.get('applied')})"
            ),
            "next_actions": [
                f"set -a && source {SESSION_ENV} && set +a",
                "python3 scripts/order_session_env.py status",
            ],
            "applied": applied,
        }
        if not SESSION_ENV.is_file():
            export_session_env()
            apply_to_environ()
            report["verdict"] = f"Created + source {SESSION_ENV}"
    else:  # status
        report = audit()
        if SESSION_STATE.is_file():
            try:
                report["session_state"] = json.loads(SESSION_STATE.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                report["session_state"] = {"error": "unreadable"}
        write_audit_outputs(report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report) if "by_category" in report else format_text(
            {**report, "next_actions": report.get("next_actions") or [
                "python3 scripts/order_session_env.py audit",
                "python3 scripts/order_session_env.py ensure",
            ]}
        ))
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
