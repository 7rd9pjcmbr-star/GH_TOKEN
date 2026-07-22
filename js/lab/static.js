/**
 * static — heuristic tĩnh phòng thủ (không thực thi mẫu)
 * Phát hiện dấu hiệu đáng ngờ trong văn bản/mã nguồn — giáo dục.
 */
(function (global) {
  "use strict";

  /** Mỗi rule = chữ ký riêng; không chạy code. */
  const RULES = [
    {
      id: "dyn-eval",
      severity: "high",
      family: "execution",
      title: "eval / Function động",
      uniqueness: "Gọi eval( hoặc new Function(",
      re: /\beval\s*\(|\bnew\s+Function\s*\(/g,
      hint: "Tránh thực thi chuỗi; dùng parser an toàn hoặc từ chối mẫu.",
    },
    {
      id: "doc-write",
      severity: "high",
      family: "dom-injection",
      title: "document.write / innerHTML gán",
      uniqueness: "Ghi DOM từ chuỗi",
      re: /document\.write\s*\(|\.innerHTML\s*=/g,
      hint: "Nguy cơ XSS nếu chuỗi không tin cậy.",
    },
    {
      id: "remote-script",
      severity: "high",
      family: "supply-chain",
      title: "Tải script từ URL",
      uniqueness: "createElement('script') + src=",
      re: /createElement\s*\(\s*['"]script['"]\s*\)[\s\S]{0,120}\.src\s*=/gi,
      hint: "Script từ ngoài sandbox phá vỡ cô lập.",
    },
    {
      id: "powershell-download",
      severity: "critical",
      family: "dropper",
      title: "PowerShell tải xuống / IEX",
      uniqueness: "IEX / DownloadString / Invoke-WebRequest",
      re: /\bIEX\b|Invoke-Expression|DownloadString|Invoke-WebRequest|FromBase64String/gi,
      hint: "Dấu hiệu dropper phổ biến — chỉ triage, không chạy.",
    },
    {
      id: "bash-curl-pipe",
      severity: "critical",
      family: "dropper",
      title: "curl|bash / wget|sh",
      uniqueness: "Pipe remote vào shell",
      re: /curl[^\n|]{0,80}\|\s*(ba)?sh|wget[^\n|;]{0,80}\|\s*(ba)?sh/gi,
      hint: "Không bao giờ pipe nội dung lạ vào shell trên máy host.",
    },
    {
      id: "obfuscation-escape",
      severity: "medium",
      family: "obfuscation",
      title: "Chuỗi escape dày (\\x / \\u)",
      uniqueness: "Mật độ escape hex/unicode cao",
      test(text) {
        const esc = text.match(/\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}/g) || [];
        if (esc.length < 12) return null;
        return { hits: esc.length, sample: esc.slice(0, 5) };
      },
      hint: "Obfuscation thường gặp ở mã độc / packer — giải mã tĩnh trong sandbox.",
    },
    {
      id: "long-base64-blob",
      severity: "medium",
      family: "payload",
      title: "Blob Base64 dài",
      uniqueness: "Chuỗi Base64 ≥ 200 ký tự",
      test(text) {
        const m = text.match(/[A-Za-z0-9+/]{200,}={0,2}/g) || [];
        if (!m.length) return null;
        return { blobs: m.length, maxLen: Math.max(...m.map((x) => x.length)) };
      },
      hint: "Có thể là payload nhúng — phân loại bằng format analyzer, không decode-and-run.",
    },
    {
      id: "crypto-miner-hint",
      severity: "medium",
      family: "abuse",
      title: "Gợi ý crypto-miner",
      uniqueness: "stratum / coinhive / xmrig từ khóa",
      re: /\bstratum\+tcp\b|\bcoinhive\b|\bxmrig\b|\bmonero\b.*\bpool\b/gi,
      hint: "Chỉ là heuristic chuỗi — xác minh thêm trong môi trường cô lập.",
    },
    {
      id: "credential-harvest",
      severity: "high",
      family: "credential",
      title: "Thu thập thông tin đăng nhập",
      uniqueness: "password|passwd|api_key gần send/post",
      re: /(password|passwd|api[_-]?key|secret|token).{0,40}(fetch|XMLHttpRequest|send|POST)/gi,
      hint: "Có thể là form hợp lệ hoặc stealer — kiểm tra ngữ cảnh.",
    },
    {
      id: "webshell-php",
      severity: "critical",
      family: "webshell",
      title: "Webshell PHP điển hình",
      uniqueness: "eval($_POST/GET) / system/passthru",
      re: /eval\s*\(\s*\$_(POST|GET|REQUEST)|assert\s*\(\s*\$_(POST|GET)|system\s*\(\s*\$_(POST|GET)|passthru\s*\(/gi,
      hint: "Mẫu webshell — quarantine, không mở trên host production.",
    },
    {
      id: "macro-autoopen",
      severity: "high",
      family: "office",
      title: "Macro Office tự chạy",
      uniqueness: "AutoOpen / Document_Open / Shell(",
      re: /\bAutoOpen\b|\bDocument_Open\b|\bWorkbook_Open\b|\bShell\s*\(/gi,
      hint: "Phân tích macro trong VM/container không mạng.",
    },
    {
      id: "suspicious-tld-url",
      severity: "low",
      family: "network",
      title: "URL nghi ngờ",
      uniqueness: "http(s) host ngắn / IP trần",
      re: /https?:\/\/(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?:\/\S*)?|https?:\/\/[a-z0-9-]{1,12}\.(?:xyz|top|club|tk|ml|ga|cf)\b/gi,
      hint: "IOC mạng — ghi nhận, không click từ máy phân tích chính.",
    },
  ];

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

  function runRules(text) {
    const findings = [];
    RULES.forEach((rule) => {
      try {
        if (typeof rule.test === "function") {
          const extra = rule.test(text);
          if (!extra) return;
          findings.push({
            id: rule.id,
            severity: rule.severity,
            family: rule.family,
            title: rule.title,
            uniqueness: rule.uniqueness,
            hint: rule.hint,
            evidence: extra,
          });
          return;
        }
        if (!rule.re) return;
        rule.re.lastIndex = 0;
        const matches = text.match(rule.re);
        if (!matches || !matches.length) return;
        findings.push({
          id: rule.id,
          severity: rule.severity,
          family: rule.family,
          title: rule.title,
          uniqueness: rule.uniqueness,
          hint: rule.hint,
          evidence: {
            count: matches.length,
            samples: matches.slice(0, 5).map((m) => String(m).slice(0, 80)),
          },
        });
      } catch {
        /* ignore single-rule failure */
      }
    });
    return findings;
  }

  function riskScore(findings, text) {
    const weight = { critical: 40, high: 22, medium: 12, low: 5 };
    let score = findings.reduce((s, f) => s + (weight[f.severity] || 0), 0);
    const ent = shannon(text.slice(0, 8000));
    if (ent > 5.2 && text.length > 80) score += 8;
    return {
      score: Math.min(100, score),
      entropy: Number(ent.toFixed(3)),
      band: score >= 70 ? "critical" : score >= 40 ? "high" : score >= 18 ? "medium" : "low",
    };
  }

  const Static = {
    rules: RULES.map((r) => ({
      id: r.id,
      severity: r.severity,
      family: r.family,
      title: r.title,
      uniqueness: r.uniqueness,
    })),

    analyze(input, opts = {}) {
      const max = opts.maxChars || 200_000;
      const text = String(input ?? "").slice(0, max);
      const findings = runRules(text);
      const risk = riskScore(findings, text);
      return {
        ok: true,
        mode: "static-only",
        executed: false,
        inputLength: text.length,
        truncated: String(input ?? "").length > max,
        risk,
        findings,
        families: [...new Set(findings.map((f) => f.family))],
        meta: { analyzer: "MaMoLab.static", version: "1.0.0" },
      };
    },
  };

  global.MaMoLabModules = global.MaMoLabModules || {};
  global.MaMoLabModules.static = Static;
})(typeof window !== "undefined" ? window : globalThis);
