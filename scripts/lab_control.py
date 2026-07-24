#!/usr/bin/env python3
"""MaMoLab Control v2 — nâng cấp phòng thí nghiệm phòng thủ.

Lệnh:
  status     — tình trạng lab / quarantine / risk
  analyze    — batch phân tích tĩnh quarantine/lab (và tùy chọn telegram)
  validate   — kiểm chứng phòng thủ (audit policy + inventory + high-risk)
  upgrade    — áp nâng cấp v2 (seed dirs, self-check, báo cáo)

Không thực thi mẫu · không dump-login · không exploit/msfvenom.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lab_static_engine import analyze_path, write_report  # noqa: E402

LAB_Q = ROOT / "quarantine" / "lab"
TG_Q = ROOT / "quarantine" / "telegram"
REPORTS = ROOT / "reports" / "lab"
LEGACY = ROOT / "reports" / "telegram-classify" / "lab-static"
SECRETS = ROOT / "secrets"
STATE = SECRETS / "lab_control.state.json"

LAB_VERSION = "2.0.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_layout() -> dict[str, str]:
    dirs = {
        "quarantine_lab": LAB_Q,
        "quarantine_telegram": TG_Q,
        "reports_lab": REPORTS,
        "reports_lab_static": REPORTS / "static",
        "reports_lab_validate": REPORTS / "validate",
        "stubs": LAB_Q / "_skipped_dumps" / "_onlylogs_meta",
    }
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    (ROOT / "quarantine" / ".gitkeep").touch(exist_ok=True)
    return {k: str(v.relative_to(ROOT)) for k, v in dirs.items()}


def iter_samples(roots: list[Path], *, max_files: int = 500) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            if p.name.startswith("."):
                continue
            # skip huge binaries for full read — still analyze head via engine cap
            out.append(p)
            if len(out) >= max_files:
                return out
    return out


def cmd_status() -> dict[str, Any]:
    ensure_layout()
    samples = iter_samples([LAB_Q])
    stubs = list((LAB_Q / "_skipped_dumps").rglob("*.STUB.txt")) if (LAB_Q / "_skipped_dumps").is_dir() else []
    prev = {}
    if STATE.is_file():
        try:
            prev = json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}
    docker = shutil.which("docker")
    report = {
        "ok": True,
        "module": "lab_control",
        "lab_version": LAB_VERSION,
        "checked_at": utc_now(),
        "surfaces": {
            "ui": "lab/index.html (/lab/)",
            "host_engine": "scripts/lab_static_engine.py",
            "control": "scripts/lab_control.py",
            "docker": "docker/lab/ (optional)",
            "telegram_bridge": "scripts/telegram_to_lab_analyze.py",
        },
        "counts": {
            "lab_files": len(samples),
            "stub_meta": len(stubs),
            "tg_inbox_files": len(iter_samples([TG_Q], max_files=5000)),
        },
        "docker_available": bool(docker),
        "docker_bin": docker,
        "last_analyze": prev.get("last_analyze"),
        "last_validate": prev.get("last_validate"),
        "policy": {
            "no_execute": True,
            "no_dump_login": True,
            "no_exploit": True,
            "control_validation": True,
        },
        "verdict": (
            f"✅ Lab v{LAB_VERSION} · files={len(samples)} · stubs={len(stubs)} · "
            f"docker={'yes' if docker else 'host-only'}"
        ),
        "cli": [
            "python3 scripts/lab_control.py status",
            "python3 scripts/lab_control.py analyze",
            "python3 scripts/lab_control.py validate",
            "python3 scripts/lab_control.py upgrade",
            "python3 scripts/telegram_to_lab_analyze.py",
        ],
    }
    return report


def cmd_analyze(*, include_telegram: bool = False, max_files: int = 400) -> dict[str, Any]:
    ensure_layout()
    roots = [LAB_Q]
    if include_telegram:
        roots.append(TG_Q)
    files = iter_samples(roots, max_files=max_files)
    analyses: list[dict[str, Any]] = []
    out_dir = REPORTS / "static"
    for p in files:
        try:
            # skip repeating copy loops under reports
            if "reports" in p.parts:
                continue
            ana = analyze_path(p, root=ROOT, surface="lab-control-v2")
            analyses.append(ana)
            safe = p.name.replace("/", "_")
            write_report(ana, out_dir / f"{safe}.v2.json")
            # also mirror legacy path for TG panel compatibility
            write_report(ana, LEGACY / f"{safe}.lab.json")
        except OSError as e:
            analyses.append({"ok": False, "error": str(e), "file": {"name": p.name}})

    bands = Counter(
        (a.get("summary") or {}).get("riskBand", "unknown") for a in analyses if a.get("ok")
    )
    high = [
        a
        for a in analyses
        if a.get("ok") and (a.get("summary") or {}).get("riskBand") in {"high", "critical"}
    ]
    report = {
        "ok": True,
        "module": "lab_control.analyze",
        "lab_version": LAB_VERSION,
        "checked_at": utc_now(),
        "analyzed_n": len(analyses),
        "risk_bands": dict(bands),
        "high_critical_n": len(high),
        "top_risky": [
            {
                "name": (a.get("file") or {}).get("name"),
                "band": (a.get("summary") or {}).get("riskBand"),
                "score": (a.get("summary") or {}).get("riskScore"),
                "findings": [f.get("id") for f in (a.get("findings") or [])[:8]],
                "path": (a.get("isolation") or {}).get("path"),
            }
            for a in sorted(
                high, key=lambda x: -((x.get("summary") or {}).get("riskScore") or 0)
            )[:30]
        ],
        "verdict": (
            f"✅ Analyze v{LAB_VERSION} · n={len(analyses)} · "
            f"high/critical={len(high)} · bands={dict(bands)}"
        ),
    }
    write_report(report, REPORTS / "analyze_summary.json")
    (REPORTS / "analyze_summary.txt").write_text(format_analyze(report) + "\n", encoding="utf-8")
    _patch_state({"last_analyze": report["checked_at"], "last_analyze_verdict": report["verdict"]})
    return report


def cmd_validate() -> dict[str, Any]:
    """Kiểm chứng phòng thủ: audit policy + inventory + risk gate (không exploit)."""
    ensure_layout()
    checks: list[dict[str, Any]] = []

    def add(cid: str, title: str, ok: bool, detail: str, level: str = "high") -> None:
        checks.append({"id": cid, "title": title, "pass": ok, "detail": detail, "level": level})

    # 1) policy.js
    policy = ROOT / "js" / "lab" / "policy.js"
    raw = policy.read_text(encoding="utf-8") if policy.is_file() else ""
    add(
        "policy-exists",
        "Lab policy file",
        policy.is_file(),
        str(policy.relative_to(ROOT)) if policy.is_file() else "missing",
        "critical",
    )
    add(
        "policy-no-exploit",
        "noExploitGeneration",
        "noExploitGeneration" in raw and "true" in raw,
        "js/lab/policy.js rules",
        "critical",
    )
    add(
        "policy-never-eval",
        "neverEvalInput / neverExecuteSample",
        "neverEvalInput" in raw and "neverExecuteSample" in raw,
        "js/lab/policy.js",
        "critical",
    )

    # 2) quarantine layout
    add("q-lab", "quarantine/lab exists", LAB_Q.is_dir(), str(LAB_Q.relative_to(ROOT)))
    add(
        "q-ro-docs",
        "SECURITY-LAB.md",
        (ROOT / "docs" / "SECURITY-LAB.md").is_file(),
        "docs/SECURITY-LAB.md",
    )

    # 3) engine + control present
    add(
        "engine-v2",
        "lab_static_engine.py",
        (ROOT / "scripts" / "lab_static_engine.py").is_file(),
        "scripts/lab_static_engine.py",
        "critical",
    )

    # 4) dump-login denial: Acc_all / stealer must not appear as assigned secret values
    secrets_env = SECRETS / "backend_pipes.env"
    leaked = False
    if secrets_env.is_file():
        for line in secrets_env.read_text(encoding="utf-8", errors="ignore").splitlines():
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            _, val = t.split("=", 1)
            vl = val.strip().strip('"').strip("'").lower()
            if any(x in vl for x in ("acc_all", "stealer", "onlylogs", "assassin")):
                leaked = True
                break
    add(
        "no-stealer-in-secrets",
        "Secrets không nhúng stealer/Acc_all",
        not leaked,
        "secrets/backend_pipes.env values (comments ignored)",
        "critical",
    )

    # 5) re-triage high risk presence (informational)
    ana = cmd_analyze(include_telegram=False, max_files=300)
    high_n = ana.get("high_critical_n") or 0
    add(
        "high-risk-inventory",
        "High/critical samples inventoried",
        True,
        f"high_critical={high_n} (triage only — no login)",
        "medium",
    )

    # 6) docker compose file present (optional)
    add(
        "docker-compose",
        "docker/lab compose định nghĩa cô lập",
        (ROOT / "docker" / "lab" / "docker-compose.yml").is_file(),
        "network_mode=none expected",
        "medium",
    )

    failed = [c for c in checks if not c["pass"]]
    report = {
        "ok": len(failed) == 0,
        "module": "lab_control.validate",
        "lab_version": LAB_VERSION,
        "checked_at": utc_now(),
        "passed": sum(1 for c in checks if c["pass"]),
        "failed": len(failed),
        "checks": checks,
        "analyze_snapshot": {
            "analyzed_n": ana.get("analyzed_n"),
            "high_critical_n": high_n,
            "bands": ana.get("risk_bands"),
        },
        "verdict": (
            f"{'✅' if not failed else '⚠'} Validate v{LAB_VERSION} · "
            f"pass={sum(1 for c in checks if c['pass'])}/{len(checks)} · "
            f"high_samples={high_n}"
        ),
        "note": (
            "Validate = kiểm chứng phòng thủ (policy/inventory). "
            "Không chạy exploit — đối chứng bằng phát hiện misconfig/policy."
        ),
    }
    write_report(report, REPORTS / "validate" / "validate_summary.json")
    (REPORTS / "validate" / "validate_summary.txt").write_text(
        format_validate(report) + "\n", encoding="utf-8"
    )
    _patch_state({"last_validate": report["checked_at"], "last_validate_verdict": report["verdict"]})
    return report


def cmd_upgrade() -> dict[str, Any]:
    layout = ensure_layout()
    # sync engine into docker analyze entry for container parity when docker exists
    engine_src = ROOT / "scripts" / "lab_static_engine.py"
    docker_analyze = ROOT / "docker" / "lab" / "analyze-static.py"
    # rewrite docker analyzer as thin wrapper noting host engine
    docker_analyze.write_text(
        '''#!/usr/bin/env python3
"""Docker lab analyze entry — lab_static_v2 compatible.

Accepts /quarantine/* paths. Static-only. No execution.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Inline minimal bootstrap if engine not mounted
ROOT_CANDIDATES = [Path("/lab"), Path("/workspace"), Path(__file__).resolve().parent]

def main() -> int:
    if len(sys.argv) < 2:
        print("usage: analyze-static.py <file>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1]).resolve()
    allowed = str(path).startswith("/quarantine/")
    if not allowed:
        print("refusing path outside /quarantine", file=sys.stderr)
        return 3
    # Prefer copied engine next to this file
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from lab_static_engine import analyze_path, write_report
    except ImportError:
        # fallback: copy-free minimal
        print(json.dumps({"ok": False, "error": "lab_static_engine missing in image"}))
        return 4
    report = analyze_path(path, surface="docker-lab-v2")
    out = Path("/reports") / "lab" / "static" / f"{path.name}.v2.json"
    write_report(report, out)
    # legacy mirror
    write_report(report, Path("/reports") / f"{path.name}.report.json")
    print(json.dumps(report, indent=2))
    print(f"\\n# wrote {out}", file=sys.stderr)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
''',
        encoding="utf-8",
    )
    # copy engine beside docker analyze for image build context
    shutil.copy2(engine_src, ROOT / "docker" / "lab" / "lab_static_engine.py")

    status = cmd_status()
    validate = cmd_validate()
    report = {
        "ok": bool(validate.get("ok")),
        "module": "lab_control.upgrade",
        "lab_version": LAB_VERSION,
        "checked_at": utc_now(),
        "layout": layout,
        "status": status,
        "validate": {
            "ok": validate.get("ok"),
            "verdict": validate.get("verdict"),
            "passed": validate.get("passed"),
            "failed": validate.get("failed"),
        },
        "upgrades": [
            "lab_static_engine v2 (richer rules, textish gating)",
            "lab_control CLI: status/analyze/validate/upgrade",
            "reports/lab/ dashboard outputs",
            "docker/lab analyze wraps engine v2",
            "control-validation (policy audit) — đối chứng thủ không cần exploit",
        ],
        "verdict": (
            f"✅ Lab nâng cấp v{LAB_VERSION} · validate="
            f"{'pass' if validate.get('ok') else 'fail'} · "
            f"files={status.get('counts', {}).get('lab_files')}"
        ),
        "next": [
            "python3 scripts/lab_control.py status",
            "python3 scripts/lab_control.py analyze",
            "python3 scripts/telegram_to_lab_analyze.py",
            "Mở /lab/ → Kiểm thử bảo mật",
        ],
    }
    write_report(report, REPORTS / "upgrade_report.json")
    (REPORTS / "upgrade_report.txt").write_text(format_upgrade(report) + "\n", encoding="utf-8")
    (ROOT / "reports" / "telegram-classify" / "lab_upgrade.txt").write_text(
        format_upgrade(report) + "\n", encoding="utf-8"
    )
    _patch_state(
        {
            "upgraded_at": report["checked_at"],
            "lab_version": LAB_VERSION,
            "last_upgrade_verdict": report["verdict"],
        }
    )
    return report


def _patch_state(patch: dict[str, Any]) -> None:
    SECRETS.mkdir(parents=True, exist_ok=True)
    cur: dict[str, Any] = {}
    if STATE.is_file():
        try:
            cur = json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cur = {}
    cur.update(patch)
    cur["updated_at"] = utc_now()
    STATE.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def format_status(r: dict[str, Any]) -> str:
    lines = [
        f"🧬 LAB CONTROL v{r.get('lab_version')}",
        f"Lúc: {r.get('checked_at')}",
        f"Verdict: {r.get('verdict')}",
        f"Docker: {r.get('docker_available')} · files={r.get('counts', {}).get('lab_files')} · stubs={r.get('counts', {}).get('stub_meta')}",
        "",
        "=== Surfaces ===",
    ]
    for k, v in (r.get("surfaces") or {}).items():
        lines.append(f"· {k}: {v}")
    lines.append("")
    lines.append("=== CLI ===")
    for c in r.get("cli") or []:
        lines.append(f"$ {c}")
    return "\n".join(lines)


def format_analyze(r: dict[str, Any]) -> str:
    lines = [
        f"🔬 LAB ANALYZE v{r.get('lab_version')}",
        f"Lúc: {r.get('checked_at')}",
        f"Verdict: {r.get('verdict')}",
        f"Bands: {r.get('risk_bands')}",
        "",
        "=== Top risky ===",
    ]
    for t in r.get("top_risky") or []:
        lines.append(f"· [{t.get('band')}/{t.get('score')}] {t.get('name')}")
        lines.append(f"  {', '.join(t.get('findings') or [])}")
        lines.append(f"  {t.get('path')}")
    return "\n".join(lines)


def format_validate(r: dict[str, Any]) -> str:
    lines = [
        f"🛡 LAB VALIDATE v{r.get('lab_version')}",
        f"Lúc: {r.get('checked_at')}",
        f"Verdict: {r.get('verdict')}",
        r.get("note") or "",
        "",
        "=== Checks ===",
    ]
    for c in r.get("checks") or []:
        mark = "✓" if c.get("pass") else "✗"
        lines.append(f"{mark} [{c.get('level')}] {c.get('title')}: {c.get('detail')}")
    return "\n".join(lines)


def format_upgrade(r: dict[str, Any]) -> str:
    lines = [
        f"🚀 LAB UPGRADE v{r.get('lab_version')}",
        f"Lúc: {r.get('checked_at')}",
        f"Verdict: {r.get('verdict')}",
        "",
        "=== Đã nâng ===",
    ]
    for u in r.get("upgrades") or []:
        lines.append(f"· {u}")
    lines.append("")
    lines.append(f"Validate: {(r.get('validate') or {}).get('verdict')}")
    lines.append("")
    for n in r.get("next") or []:
        lines.append(f"Next: {n}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MaMoLab Control v2")
    ap.add_argument(
        "command",
        choices=("status", "analyze", "validate", "upgrade"),
        help="status|analyze|validate|upgrade",
    )
    ap.add_argument("--include-telegram", action="store_true")
    ap.add_argument("--max-files", type=int, default=400)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.command == "status":
        report = cmd_status()
        text = format_status(report)
    elif args.command == "analyze":
        report = cmd_analyze(include_telegram=args.include_telegram, max_files=args.max_files)
        text = format_analyze(report)
    elif args.command == "validate":
        report = cmd_validate()
        text = format_validate(report)
    else:
        report = cmd_upgrade()
        text = format_upgrade(report)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(text)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
