#!/usr/bin/env python3
"""Rà soát cửa sổ hệ thống kinh doanh Proxy SaaS (ProxyFlow / ScanToolmanus).

Nguồn chính (không nằm trong GH_TOKEN):
  github.com/7rd9pjcmbr-star/kubernetes2
    proxy-production-system/website/          → ProxyFlow SaaS landing
    proxy-production-system/website-plan/     → kế hoạch KD online
    proxy-production-system/website-v3/       → ScanToolmanus V3 (Proxy Pool + Đơn hàng)
    proxy-production-system/internal/proxy/   → Go upstream pool (round-robin)

Cầu nối GH_TOKEN:
  Proxy Pool (V3) → secrets/proxies.owned.txt → token_proxy_bind → nginx gọi đơn

Owned-only · không dump credential.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

REPORTS = ROOT / "reports" / "telegram-classify"
SECRETS = ROOT / "secrets"
K8S2_REPO = os.environ.get("PROXY_SAAS_REPO", "https://github.com/7rd9pjcmbr-star/kubernetes2.git")
CACHE_DIR = ROOT / "quarantine" / "proxy-saas-windows"

WINDOWS_SPEC = (
    {
        "id": "proxyflow-saas",
        "brand": "ProxyFlow",
        "role": "landing_saas",
        "branch": "master",
        "path": "proxy-production-system/website/index.html",
        "title_hint": "ProxyFlow SaaS",
        "sections_expect": ["features", "pricing", "model", "faq", "cta"],
    },
    {
        "id": "proxyflow-plan",
        "brand": "ProxyFlow",
        "role": "business_plan",
        "branch": "cursor/online-business-plan-6d95",
        "path": "proxy-production-system/website-plan/index.html",
        "title_hint": "Kế hoạch kinh doanh",
        "sections_expect": ["tom-tat", "thi-truong", "doanh-thu", "du-phong", "lo-trinh"],
    },
    {
        "id": "scantoolmanus-v3",
        "brand": "ScanToolmanus V3",
        "role": "ops_console_modules",
        "branch": "master",
        "path": "proxy-production-system/website-v3/index.html",
        "title_hint": "ScanToolmanus V3",
        "sections_expect": ["overview", "modules", "workflow", "api-health", "v3", "cta"],
    },
)

PRICING = [
    {"plan": "Starter", "price_usd": 99, "bandwidth": "50 GB", "regions": 2},
    {"plan": "Growth", "price_usd": 399, "bandwidth": "500 GB", "regions": 6, "popular": True},
    {"plan": "Business", "price_usd": 999, "bandwidth": "2 TB", "regions": "all", "sla": "99.95%"},
]

V3_MODULES = [
    "Dashboard",
    "Tai Khoan",
    "Account Checker",
    "Proxy Pool",
    "Don Hang",
    "Activity Log",
    "Thong Ke",
    "Cau Hinh",
    "Monitoring",
]


class _HeadingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capture: str | None = None
        self._buf: list[str] = []
        self.headings: list[dict[str, str]] = []
        self.title = ""
        self.brand = ""
        self.section_ids: list[str] = []
        self.forms: list[str] = []
        self._in_title = False
        self._in_brand = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = dict(attrs)
        if tag in {"h1", "h2", "h3"}:
            self._capture = tag
            self._buf = []
        elif tag == "title":
            self._in_title = True
            self._buf = []
        elif tag == "a" and "brand" in (ad.get("class") or ""):
            self._in_brand = True
            self._buf = []
        elif tag == "section" and ad.get("id"):
            self.section_ids.append(ad["id"] or "")
        elif tag == "form":
            self.forms.append(ad.get("class") or ad.get("id") or "form")

    def handle_endtag(self, tag: str) -> None:
        text = " ".join("".join(self._buf).split()).strip()
        if tag in {"h1", "h2", "h3"} and self._capture == tag:
            if text:
                self.headings.append({"tag": tag, "text": text})
            self._capture = None
            self._buf = []
        elif tag == "title" and self._in_title:
            self.title = text
            self._in_title = False
            self._buf = []
        elif tag == "a" and self._in_brand:
            self.brand = text
            self._in_brand = False
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._capture or self._in_title or self._in_brand:
            self._buf.append(data)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def fetch_windows(*, refresh: bool = False) -> dict[str, Any]:
    """Lấy HTML cửa sổ từ kubernetes2 vào quarantine cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {
        "ok": False,
        "repo": K8S2_REPO,
        "cache": str(CACHE_DIR),
        "files": [],
        "error": None,
    }

    with tempfile.TemporaryDirectory(prefix="proxy-saas-") as tmp:
        tmp_p = Path(tmp)
        # shallow clone master sparse
        code, out = _run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--filter=blob:none",
                "--sparse",
                K8S2_REPO,
                str(tmp_p / "repo"),
            ],
            timeout=180,
        )
        if code != 0:
            meta["error"] = out[:300]
            return meta
        repo = tmp_p / "repo"
        _run(
            ["git", "sparse-checkout", "set", "proxy-production-system"],
            cwd=repo,
            timeout=60,
        )
        # also fetch plan branch file
        _run(
            ["git", "fetch", "origin", "cursor/online-business-plan-6d95", "--depth", "1"],
            cwd=repo,
            timeout=120,
        )

        for spec in WINDOWS_SPEC:
            rel = spec["path"]
            dest = CACHE_DIR / spec["id"] / "index.html"
            dest.parent.mkdir(parents=True, exist_ok=True)
            src = repo / rel
            content = None
            if spec["branch"] != "master":
                code2, out2 = _run(
                    ["git", "show", f"FETCH_HEAD:{rel}"],
                    cwd=repo,
                    timeout=30,
                )
                if code2 == 0 and out2.strip().startswith("<!"):
                    content = out2
                elif code2 == 0 and "<html" in out2.lower():
                    content = out2
            if content is None and src.is_file():
                content = src.read_text(encoding="utf-8", errors="ignore")
            if content is None and not refresh and dest.is_file():
                meta["files"].append({"id": spec["id"], "path": str(dest), "cached": True})
                continue
            if content is None:
                meta["files"].append({"id": spec["id"], "error": f"missing {rel}"})
                continue
            if not refresh and dest.is_file() and dest.read_text(encoding="utf-8", errors="ignore") == content:
                meta["files"].append({"id": spec["id"], "path": str(dest), "unchanged": True})
            else:
                dest.write_text(content, encoding="utf-8")
                meta["files"].append({"id": spec["id"], "path": str(dest), "bytes": len(content)})

        # also cache pool.go + README snippet paths
        for extra in (
            "proxy-production-system/internal/proxy/pool.go",
            "proxy-production-system/.env.example",
            "proxy-production-system/README.md",
        ):
            src = repo / extra
            if src.is_file():
                dest = CACHE_DIR / "runtime" / Path(extra).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(src.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
                meta["files"].append({"id": "runtime", "path": str(dest)})

    meta["ok"] = any(f.get("path") for f in meta["files"] if f.get("id") != "runtime" or True)
    # stricter: at least one window html
    meta["ok"] = any(
        (CACHE_DIR / s["id"] / "index.html").is_file() for s in WINDOWS_SPEC
    )
    return meta


def parse_window(spec: dict[str, Any]) -> dict[str, Any]:
    path = CACHE_DIR / spec["id"] / "index.html"
    out: dict[str, Any] = {
        "id": spec["id"],
        "brand": spec["brand"],
        "role": spec["role"],
        "branch": spec["branch"],
        "source_path": spec["path"],
        "local_path": str(path) if path.is_file() else None,
        "ok": False,
    }
    if not path.is_file():
        out["error"] = "missing_cache_html"
        return out
    html = path.read_text(encoding="utf-8", errors="ignore")
    parser = _HeadingParser()
    parser.feed(html)
    h1 = [h["text"] for h in parser.headings if h["tag"] == "h1"]
    h2 = [h["text"] for h in parser.headings if h["tag"] == "h2"]
    h3 = [h["text"] for h in parser.headings if h["tag"] == "h3"]
    missing_sections = [s for s in spec["sections_expect"] if s not in parser.section_ids]
    out.update(
        {
            "ok": True,
            "title": parser.title,
            "brand_text": parser.brand or spec["brand"],
            "h1": h1,
            "h2": h2,
            "h3": h3,
            "section_ids": parser.section_ids,
            "forms": parser.forms,
            "missing_sections": missing_sections,
            "has_proxy_pool_module": any(re.search(r"(?i)proxy\s*pool", x) for x in h3),
            "has_orders_module": any(re.search(r"(?i)don\s*hang|order", x) for x in h3),
            "pricing_mentions": [
                p["plan"] for p in PRICING if any(p["plan"].lower() in (x or "").lower() for x in h3 + h2)
            ],
        }
    )
    return out


def runtime_audit() -> dict[str, Any]:
    """Đọc pool.go / .env.example đã cache."""
    pool = CACHE_DIR / "runtime" / "pool.go"
    env = CACHE_DIR / "runtime" / ".env.example"
    readme = CACHE_DIR / "runtime" / "README.md"
    out: dict[str, Any] = {
        "proxy_upstreams_env": "PROXY_UPSTREAMS",
        "pool_algo": "round_robin_atomic",
        "listen_default": ":8080",
        "ui_routes": ["/v2/", "/v3/"],
        "note": "Đây là reverse-proxy upstream pool (Go), không phải list SOCKS egress client.",
    }
    if pool.is_file():
        text = pool.read_text(encoding="utf-8", errors="ignore")
        out["pool_go_has_next"] = "func (p *upstreamPool) next()" in text or "func (p *upstreamPool) next()" in text.replace(
            " ", ""
        )
        out["pool_go_lines"] = text.count("\n") + 1
    if env.is_file():
        out["env_example"] = [
            ln.strip()
            for ln in env.read_text(encoding="utf-8", errors="ignore").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
    if readme.is_file():
        out["readme_has_saas_section"] = "Proxy SaaS" in readme.read_text(encoding="utf-8", errors="ignore")
    return out


def gh_token_bridge() -> dict[str, Any]:
    """Map cửa sổ SaaS → pipeline GH_TOKEN hiện có."""
    proxies_owned = SECRETS / "proxies.owned.txt"
    bind = SECRETS / "token_proxy_bind.json"
    bind_n = 0
    proxy_n = 0
    if bind.is_file():
        try:
            data = json.loads(bind.read_text(encoding="utf-8"))
            bind_n = len(data.get("bindings") or [])
            proxy_n = sum(1 for b in data.get("bindings") or [] if b.get("proxy"))
        except json.JSONDecodeError:
            pass
    return {
        "proxy_pool_window": "scantoolmanus-v3 · module Proxy Pool",
        "orders_window": "scantoolmanus-v3 · module Don Hang",
        "saas_landing": "proxyflow-saas · Starter/Growth/Business",
        "business_plan": "proxyflow-plan · MRR $18k · margin 65–70%",
        "gh_token_ingest": "secrets/proxies.owned.txt → scripts/token_proxy_bind.py",
        "nginx_route": "POST /v1/ghn/token-proxy-orders",
        "proxies_owned_present": proxies_owned.is_file() and proxies_owned.stat().st_size > 0,
        "bind_rows": bind_n,
        "bind_with_proxy": proxy_n,
        "gap": (
            None
            if proxies_owned.is_file() and proxies_owned.stat().st_size > 20
            else "Chưa có list egress proxy owned — Proxy Pool V3 chưa xuất sang secrets/proxies.owned.txt"
        ),
        "next": [
            "Export proxy từ Proxy Pool (ScanToolmanus V3) → secrets/proxies.owned.txt",
            "python3 scripts/token_proxy_bind.py bind",
            "python3 scripts/token_proxy_bind.py nginx-orders --limit-tokens 10",
            "python3 scripts/nginx_order_embed.py ghn-token-proxy-orders --keep",
        ],
    }


def audit(*, refresh: bool = True) -> dict[str, Any]:
    fetch = fetch_windows(refresh=refresh)
    windows = [parse_window(s) for s in WINDOWS_SPEC]
    runtime = runtime_audit()
    bridge = gh_token_bridge()

    ok_n = sum(1 for w in windows if w.get("ok"))
    report: dict[str, Any] = {
        "ok": ok_n >= 2,
        "module": "proxy_saas_windows_audit",
        "checked_at": utc_now(),
        "query": "Rà soát cửa sổ repo hệ thống kinh doanh proxy saas",
        "source_repo": "7rd9pjcmbr-star/kubernetes2",
        "source_tree": "proxy-production-system/",
        "fetch": {
            "ok": fetch.get("ok"),
            "error": fetch.get("error"),
            "files_n": len(fetch.get("files") or []),
        },
        "windows": windows,
        "pricing_catalog": PRICING,
        "v3_modules": V3_MODULES,
        "runtime": runtime,
        "bridge_gh_token": bridge,
        "architecture": {
            "layers": [
                {
                    "layer": "SaaS marketing",
                    "window": "website/ (ProxyFlow)",
                    "job": "Bán subscription proxy · KPI MRR/margin",
                },
                {
                    "layer": "Business plan",
                    "window": "website-plan/",
                    "job": "GTM · dự phóng 12 tháng · persona",
                },
                {
                    "layer": "Ops console",
                    "window": "website-v3/ (ScanToolmanus)",
                    "job": "Proxy Pool + Đơn hàng + Account Checker + 8 nền tảng TMDT",
                },
                {
                    "layer": "Runtime proxy",
                    "window": "internal/proxy/pool.go",
                    "job": "Round-robin PROXY_UPSTREAMS (reverse proxy) — khác egress SOCKS client",
                },
                {
                    "layer": "GH_TOKEN order pipe",
                    "window": "token_proxy_bind + nginx:18080",
                    "job": "1 egress proxy / 1 GHN token → gọi đơn",
                },
            ]
        },
        "verdict": "",
        "policy": {"owned_only": True, "no_dump_login": True},
    }

    if ok_n == 0:
        report["verdict"] = (
            "❌ Không lấy được cửa sổ Proxy SaaS từ kubernetes2 — "
            f"{fetch.get('error') or 'cache trống'}"
        )
    elif bridge.get("gap"):
        report["verdict"] = (
            f"⚠ Đã rà {ok_n}/3 cửa sổ Proxy SaaS (ProxyFlow + ScanToolmanus V3) · "
            f"{bridge['gap']}"
        )
        report["ok"] = True  # audit itself succeeded
    else:
        report["verdict"] = (
            f"✅ Rà soát {ok_n}/3 cửa sổ Proxy SaaS · "
            f"bridge bind_with_proxy={bridge.get('bind_with_proxy')}"
        )

    write_outputs(report)
    return report


def format_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    L = lines.append
    L("🪟 PROXY SAAS · RÀ SOÁT CỬA SỔ KINH DOANH")
    L(f"Lúc: {report.get('checked_at') or utc_now()}")
    L(report.get("verdict") or "")
    L(f"Repo nguồn: {report.get('source_repo')} · {report.get('source_tree')}")
    L("")
    L("=== Cửa sổ ===")
    for w in report.get("windows") or []:
        mark = "✅" if w.get("ok") else "❌"
        L(f"{mark} [{w.get('id')}] {w.get('brand')} · role={w.get('role')} · branch={w.get('branch')}")
        if w.get("h1"):
            L(f"   H1: {w['h1'][0][:100]}")
        if w.get("section_ids"):
            L(f"   sections: {', '.join(w['section_ids'])}")
        if w.get("h3"):
            L(f"   modules/h3: {', '.join(w['h3'][:10])}")
        if w.get("missing_sections"):
            L(f"   missing: {w['missing_sections']}")
        if w.get("has_proxy_pool_module"):
            L("   ★ có module Proxy Pool")
        if w.get("has_orders_module"):
            L("   ★ có module Đơn hàng")
    L("")
    L("=== Bảng giá ProxyFlow ===")
    for p in report.get("pricing_catalog") or []:
        pop = " · phổ biến" if p.get("popular") else ""
        L(f"· {p['plan']}: ${p['price_usd']}/tháng · {p['bandwidth']} · regions={p['regions']}{pop}")
    L("")
    L("=== Runtime (Go pool) ===")
    rt = report.get("runtime") or {}
    L(f"env: {rt.get('proxy_upstreams_env')} · algo={rt.get('pool_algo')} · UI={rt.get('ui_routes')}")
    L(f"note: {rt.get('note')}")
    L("")
    L("=== Cầu nối GH_TOKEN ===")
    b = report.get("bridge_gh_token") or {}
    L(f"Proxy Pool window → {b.get('gh_token_ingest')}")
    L(f"nginx: {b.get('nginx_route')}")
    L(f"proxies.owned present={b.get('proxies_owned_present')} · bind_with_proxy={b.get('bind_with_proxy')}")
    if b.get("gap"):
        L(f"GAP: {b['gap']}")
    for n in b.get("next") or []:
        L(f"· {n}")
    L("")
    L("=== Kiến trúc lớp ===")
    for layer in (report.get("architecture") or {}).get("layers") or []:
        L(f"· {layer.get('layer')}: {layer.get('window')} — {layer.get('job')}")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "proxy_saas_windows_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (REPORTS / "proxy_saas_windows_audit.txt").write_text(format_text(report) + "\n", encoding="utf-8")
    SECRETS.mkdir(parents=True, exist_ok=True)
    (SECRETS / "proxy_saas_windows_audit.state.json").write_text(
        json.dumps(
            {
                "checked_at": report.get("checked_at"),
                "verdict": report.get("verdict"),
                "windows_ok": sum(1 for w in (report.get("windows") or []) if w.get("ok")),
                "gap": (report.get("bridge_gh_token") or {}).get("gap"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Rà soát cửa sổ Proxy SaaS (ProxyFlow)")
    ap.add_argument("--no-refresh", action="store_true", help="Dùng cache quarantine nếu có")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = audit(refresh=not args.no_refresh)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
