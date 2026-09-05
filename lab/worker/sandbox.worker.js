/**
 * sandbox.worker.js — Worker cô lập
 * Chỉ phân tích tĩnh; KHÔNG importScripts từ URL ngoài; KHÔNG fetch; KHÔNG eval.
 */
/* eslint-disable no-restricted-globals */
"use strict";

function shannon(str) {
  const map = Object.create(null);
  for (const c of str) map[c] = (map[c] || 0) + 1;
  const len = str.length || 1;
  let h = 0;
  Object.values(map).forEach((n) => {
    const p = n / len;
    h -= p * Math.log2(p);
  });
  return h;
}

const RULES = [
  { id: "dyn-eval", severity: "high", family: "execution", title: "eval / Function động", re: /\beval\s*\(|\bnew\s+Function\s*\(/g },
  { id: "doc-write", severity: "high", family: "dom-injection", title: "document.write / innerHTML", re: /document\.write\s*\(|\.innerHTML\s*=/g },
  { id: "powershell-download", severity: "critical", family: "dropper", title: "PowerShell tải / IEX", re: /\bIEX\b|Invoke-Expression|DownloadString|Invoke-WebRequest|FromBase64String/gi },
  { id: "bash-curl-pipe", severity: "critical", family: "dropper", title: "curl|bash", re: /curl[^\n|]{0,80}\|\s*(ba)?sh|wget[^\n|;]{0,80}\|\s*(ba)?sh/gi },
  { id: "webshell-php", severity: "critical", family: "webshell", title: "Webshell PHP", re: /eval\s*\(\s*\$_(POST|GET|REQUEST)|system\s*\(\s*\$_(POST|GET)|passthru\s*\(/gi },
  { id: "macro-autoopen", severity: "high", family: "office", title: "Macro auto-open", re: /\bAutoOpen\b|\bDocument_Open\b|\bWorkbook_Open\b|\bShell\s*\(/gi },
  { id: "crypto-miner-hint", severity: "medium", family: "abuse", title: "Miner keyword", re: /\bstratum\+tcp\b|\bcoinhive\b|\bxmrig\b/gi },
  { id: "credential-harvest", severity: "high", family: "credential", title: "Credential harvest pattern", re: /(password|passwd|api[_-]?key|secret|token).{0,40}(fetch|XMLHttpRequest|send|POST)/gi },
  { id: "suspicious-tld-url", severity: "low", family: "network", title: "URL nghi ngờ", re: /https?:\/\/(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?:\/\S*)?/gi },
];

function runStatic(text) {
  const findings = [];
  RULES.forEach((rule) => {
    rule.re.lastIndex = 0;
    const matches = text.match(rule.re);
    if (!matches) return;
    findings.push({
      id: rule.id,
      severity: rule.severity,
      family: rule.family,
      title: rule.title,
      evidence: { count: matches.length, samples: matches.slice(0, 5).map((m) => String(m).slice(0, 80)) },
    });
  });
  const esc = text.match(/\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}/g) || [];
  if (esc.length >= 12) {
    findings.push({
      id: "obfuscation-escape",
      severity: "medium",
      family: "obfuscation",
      title: "Escape dày",
      evidence: { hits: esc.length },
    });
  }
  const b64 = text.match(/[A-Za-z0-9+/]{200,}={0,2}/g) || [];
  if (b64.length) {
    findings.push({
      id: "long-base64-blob",
      severity: "medium",
      family: "payload",
      title: "Blob Base64 dài",
      evidence: { blobs: b64.length, maxLen: Math.max(...b64.map((x) => x.length)) },
    });
  }
  const weight = { critical: 40, high: 22, medium: 12, low: 5 };
  let score = findings.reduce((s, f) => s + (weight[f.severity] || 0), 0);
  const entropy = shannon(text.slice(0, 8000));
  if (entropy > 5.2 && text.length > 80) score += 8;
  score = Math.min(100, score);
  return {
    ok: true,
    mode: "static-only",
    executed: false,
    worker: true,
    inputLength: text.length,
    risk: {
      score,
      entropy: Number(entropy.toFixed(3)),
      band: score >= 70 ? "critical" : score >= 40 ? "high" : score >= 18 ? "medium" : "low",
    },
    findings,
    families: [...new Set(findings.map((f) => f.family))],
  };
}

function extractIocs(text) {
  const uniq = (a) => [...new Set(a)];
  const ipv4 = text.match(/\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b/g) || [];
  const urls = text.match(/https?:\/\/[^\s"'<>]+/gi) || [];
  const sha256 = text.match(/\b[a-fA-F0-9]{64}\b/g) || [];
  const md5 = text.match(/\b[a-fA-F0-9]{32}\b/g) || [];
  return {
    ok: true,
    networkCalls: false,
    iocs: {
      ipv4: uniq(ipv4).slice(0, 40),
      urls: uniq(urls).slice(0, 40),
      hashes: { md5: uniq(md5).slice(0, 20), sha256: uniq(sha256).slice(0, 20) },
    },
    counts: {
      ipv4: uniq(ipv4).length,
      urls: uniq(urls).length,
      hashes: uniq(md5).length + uniq(sha256).length,
    },
  };
}

self.onmessage = (ev) => {
  const msg = ev.data || {};
  if (msg.type !== "analyze") {
    self.postMessage({ id: msg.id, error: "unsupported" });
    return;
  }
  try {
    const max = msg.opts?.maxChars || 200000;
    const text = String(msg.text || "").slice(0, max);
    const staticReport = runStatic(text);
    const indicators = extractIocs(text);
    const result = {
      ok: true,
      generatedAt: new Date().toISOString(),
      isolation: {
        surface: "lab-worker",
        executedSample: false,
        networkUsed: false,
      },
      summary: {
        riskBand: staticReport.risk.band,
        riskScore: staticReport.risk.score,
        findingCount: staticReport.findings.length,
        iocCount: indicators.counts,
      },
      static: staticReport,
      indicators,
      topFindings: staticReport.findings.slice(0, 12),
      disclaimer: "Worker static-only — không thực thi mẫu.",
    };
    self.postMessage({ id: msg.id, result });
  } catch (err) {
    self.postMessage({ id: msg.id, error: String(err && err.message ? err.message : err) });
  }
};
