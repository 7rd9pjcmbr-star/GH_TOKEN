/**
 * analyze — phân tích sâu nhận dạng & phân loại định dạng mã
 * Mỗi định dạng có chữ ký cấu trúc RIÊNG (không dùng chung một schema).
 * Owns: format-detect, format-classify (không đụng resolve/recommend).
 */
(function (global) {
  "use strict";

  /**
   * Catalog định dạng — mỗi mục = cấu trúc duy nhất + detector riêng.
   * Không gộp chung regex “một kiểu cho tất cả”.
   */
  const FORMATS = [
    {
      id: "nginx-queue",
      label: "Nginx queue (upstream)",
      family: "ops-config",
      uniqueness: "Chỉ thị queue number [timeout=] trong upstream — hàng đợi khi chưa chọn peer",
      structure: {
        directive: "queue",
        context: ["upstream"],
        since: "1.5.12",
        commercial: true,
      },
      test(s) {
        const raw = s.trim();
        if (!/\bqueue\b/i.test(raw)) return null;
        const head = raw.toLowerCase().replace(/;.*/, "").trim();
        const isLine =
          /^queue(\s|$)/i.test(head) ||
          /queue\s+\d+/i.test(raw) ||
          head === "queue";
        if (!isLine) return null;
        const num = (raw.match(/queue\s+(\d+)/i) || [])[1] || null;
        const timeout = (raw.match(/timeout\s*=\s*([^\s;]+)/i) || [])[1] || null;
        return {
          confidence: /queue\s+\d+/i.test(raw) ? 0.96 : 0.85,
          features: {
            directive: "queue",
            context: "upstream",
            number: num ? Number(num) : null,
            timeout: timeout || "60s (default)",
            commercial: true,
            onFullOrTimeout: "502 Bad Gateway",
            note: "Commercial; bật load-balancing method (≠ round-robin) trước queue nếu dùng method khác.",
          },
        };
      },
    },
    {
      id: "nginx-resolver",
      label: "Nginx resolver (upstream)",
      family: "ops-config",
      uniqueness: "Chỉ thị resolver trong block upstream — DNS nameserver + valid/ipv4/ipv6",
      structure: {
        directive: "resolver",
        context: ["upstream"],
        since: "1.27.3",
      },
      test(s) {
        const raw = s.trim();
        if (!/\bresolver\b/i.test(raw)) return null;
        const head = raw.toLowerCase().replace(/;.*/, "").trim();
        const isDirectiveLine =
          /^resolver(\s|$)/i.test(head) ||
          /resolver\s+[^\s;]+/i.test(raw) ||
          head === "resolver";
        if (!isDirectiveLine && !/valid\s*=\s*\d|ipv[46]\s*=\s*off|status_zone\s*=/i.test(raw)) {
          return null;
        }
        const hasValid = /valid\s*=\s*\S+/i.test(raw);
        const ipv4Off = /ipv4\s*=\s*off/i.test(raw);
        const ipv6Off = /ipv6\s*=\s*off/i.test(raw);
        const hasZone = /status_zone\s*=/i.test(raw);
        return {
          confidence: /^resolver\s+\S+/i.test(raw) ? 0.96 : 0.88,
          features: {
            directive: "resolver",
            context: "upstream",
            hasValid,
            ipv4Off,
            ipv6Off,
            statusZone: hasZone,
            note: hasZone ? "status_zone là tham số commercial" : null,
          },
        };
      },
    },
    {
      id: "nginx-upstream-var",
      label: "Nginx upstream variable",
      family: "ops-config",
      uniqueness: "Tên biến $upstream_* (ngx_http_upstream_module)",
      structure: { prefix: "$upstream_", module: "ngx_http_upstream_module" },
      test(s) {
        const raw = s.trim();
        if (!/^\$?upstream_[a-z0-9_]+$/i.test(raw) && !/ngx_http_upstream/i.test(raw)) {
          return null;
        }
        const name = raw.startsWith("$") ? raw : `$${raw.replace(/^\$/, "")}`;
        const hit = global.MaMoLogicModules?.vars?.get?.(name);
        if (hit || /^\$upstream_/i.test(name) || /^upstream_/i.test(raw)) {
          return {
            confidence: hit ? 0.95 : 0.8,
            features: {
              variable: hit?.name || (name.startsWith("$") ? name : `$${name}`),
              category: hit?.category || null,
              commercial: hit?.commercial || false,
            },
          };
        }
        return null;
      },
    },
    {
      id: "jwt",
      label: "JWT (JWS compact)",
      family: "token",
      uniqueness: "3 đoạn base64url ngăn bằng dấu chấm + JSON header",
      structure: { parts: 3, sep: ".", charset: "base64url" },
      test(s) {
        const raw = s.trim();
        if (!/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(raw)) {
          return null;
        }
        const [h] = raw.split(".");
        try {
          const json = JSON.parse(b64urlDecode(h));
          if (json && typeof json === "object" && (json.alg || json.typ)) {
            return {
              confidence: 0.97,
              features: {
                segments: 3,
                headerAlg: json.alg || null,
                headerTyp: json.typ || null,
              },
            };
          }
        } catch {
          return { confidence: 0.72, features: { segments: 3, headerParse: false } };
        }
        return null;
      },
    },
    {
      id: "pem-armor",
      label: "PEM / OpenPGP armor",
      family: "armor",
      uniqueness: "BEGIN/END banner + base64 thân",
      structure: { banners: true, body: "base64-lines" },
      test(s) {
        const raw = s.trim();
        const m = raw.match(
          /-----BEGIN ([A-Z0-9 ]+)-----([\s\S]+?)-----END \1-----/
        );
        if (!m) return null;
        return {
          confidence: 0.99,
          features: { banner: m[1], bodyLen: m[2].replace(/\s/g, "").length },
        };
      },
    },
    {
      id: "braille-unicode",
      label: "Braille Unicode (U+2800)",
      family: "tactile",
      uniqueness: "Chỉ codepoint Braille ⠁–⣿ (+ khoảng trắng)",
      structure: { script: "braille", block: "U+2800..U+28FF" },
      test(s) {
        const raw = s.trim();
        if (!raw) return null;
        const chars = [...raw];
        const braille = chars.filter((c) => {
          const cp = c.codePointAt(0);
          return (cp >= 0x2800 && cp <= 0x28ff) || c === " " || c === "\n";
        });
        const ratio = braille.length / chars.length;
        if (ratio < 0.85) return null;
        const cells = chars.filter((c) => {
          const cp = c.codePointAt(0);
          return cp >= 0x2800 && cp <= 0x28ff;
        }).length;
        if (cells < 1) return null;
        return {
          confidence: 0.5 + Math.min(0.49, ratio * 0.5),
          features: { cells, ratio: Number(ratio.toFixed(3)) },
        };
      },
    },
    {
      id: "morse",
      label: "Morse",
      family: "signal",
      uniqueness: "Chỉ . - khoảng / (hoặc · −) — không chữ cái Latin",
      structure: { alphabet: [".", "-", " ", "/"], tokens: "variable-length" },
      test(s) {
        const raw = s.trim().replace(/[·•]/g, ".").replace(/[−–—_]/g, "-");
        if (!raw || raw.length < 1) return null;
        if (!/^[\.\-\s\/]+$/.test(raw)) return null;
        if (!/[.\-]/.test(raw)) return null;
        const tokens = raw.split(/[\s\/]+/).filter(Boolean);
        const valid = tokens.every((t) => /^[.\-]{1,7}$/.test(t));
        if (!valid) return { confidence: 0.55, features: { tokens: tokens.length, valid: false } };
        return {
          confidence: tokens.length >= 2 ? 0.93 : 0.7,
          features: { tokens: tokens.length, words: raw.split("/").length },
        };
      },
    },
    {
      id: "uuid",
      label: "UUID",
      family: "id",
      uniqueness: "8-4-4-4-12 hex + version nibble",
      structure: { groups: [8, 4, 4, 4, 12], radix: 16 },
      test(s) {
        const raw = s.trim();
        const m = raw.match(
          /^[0-9a-f]{8}-[0-9a-f]{4}-([1-5])[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
        );
        if (!m) return null;
        return {
          confidence: 0.98,
          features: { version: Number(m[1]), variant: "RFC4122" },
        };
      },
    },
    {
      id: "hash-hex",
      label: "Hash digest (hex)",
      family: "digest",
      uniqueness: "Độ dài cố định 32/40/64/128 hex — không separator",
      structure: { fixedLengths: [32, 40, 64, 128], charset: "hex" },
      test(s) {
        const raw = s.trim().replace(/\s/g, "");
        if (!/^[0-9a-fA-F]+$/.test(raw)) return null;
        const map = {
          32: "MD5-or-similar",
          40: "SHA-1",
          64: "SHA-256",
          128: "SHA-512",
        };
        if (!map[raw.length]) return null;
        // tránh nhầm hex ngắn/UUID đã bắt
        return {
          confidence: 0.88,
          features: { length: raw.length, likely: map[raw.length] },
        };
      },
    },
    {
      id: "hex-blob",
      label: "Hex dump / binary hex",
      family: "binary-text",
      uniqueness: "Chỉ hex, độ dài chẵn, không khớp hash cố định",
      structure: { charset: "hex", evenLength: true },
      test(s) {
        const raw = s.trim().replace(/[\s:]/g, "");
        if (raw.length < 8 || raw.length % 2) return null;
        if (!/^[0-9a-fA-F]+$/.test(raw)) return null;
        if ([32, 40, 64, 128].includes(raw.length)) return null; // hash-hex ưu tiên
        return {
          confidence: 0.74,
          features: { bytes: raw.length / 2 },
        };
      },
    },
    {
      id: "base64",
      label: "Base64",
      family: "transport",
      uniqueness: "A-Za-z0-9+/ padding = ; độ dài %4==0",
      structure: { alphabet: "b64", padding: ["", "=", "=="] },
      test(s) {
        const raw = s.trim().replace(/\s/g, "");
        if (raw.length < 8) return null;
        if (!/^[A-Za-z0-9+/]+={0,2}$/.test(raw)) return null;
        if (raw.length % 4 !== 0) return null;
        // tránh JWT (có dấu chấm)
        if (s.includes(".")) return null;
        // Pure A–Z (không digit, +, /, =) → classical-alpha, không phải Base64 điển hình
        if (/^[A-Za-z]+$/.test(raw)) return null;
        let decodedOk = false;
        try {
          atob(raw);
          decodedOk = true;
        } catch {
          return null;
        }
        const entropy = shannon(raw);
        const hasTransportMark = /[0-9+/=]/.test(raw);
        return {
          confidence: hasTransportMark && entropy > 4.2 ? 0.86 : 0.7,
          features: { length: raw.length, entropy: Number(entropy.toFixed(3)), decodedOk },
        };
      },
    },
    {
      id: "base64url",
      label: "Base64URL",
      family: "transport",
      uniqueness: "A-Za-z0-9_- không padding bắt buộc — khác Base64 chuẩn",
      structure: { alphabet: "b64url", padding: "optional" },
      test(s) {
        const raw = s.trim();
        if (raw.includes(".") || raw.includes("+") || raw.includes("/")) return null;
        if (!/^[A-Za-z0-9_-]+={0,2}$/.test(raw) || raw.length < 12) return null;
        if (!/[_-]/.test(raw)) return null; // cần dấu hiệu url-safe
        return {
          confidence: 0.78,
          features: { length: raw.length, urlSafe: true },
        };
      },
    },
    {
      id: "json",
      label: "JSON",
      family: "structured-data",
      uniqueness: "Cây object/array parse được — khác cipher",
      structure: { root: ["object", "array"] },
      test(s) {
        const raw = s.trim();
        if (!/^[\[{]/.test(raw)) return null;
        try {
          const v = JSON.parse(raw);
          const root = Array.isArray(v) ? "array" : typeof v;
          return {
            confidence: 0.96,
            features: { root, keys: v && typeof v === "object" && !Array.isArray(v) ? Object.keys(v).length : null },
          };
        } catch {
          return null;
        }
      },
    },
    {
      id: "url-encoded",
      label: "URL-encoded form",
      family: "web",
      uniqueness: "key=value&… + %HH",
      structure: { sep: ["=", "&"], escape: "%HH" },
      test(s) {
        const raw = s.trim();
        if (!/^[^=&]+=/.test(raw)) return null;
        if (!/&/.test(raw) && !/%[0-9A-Fa-f]{2}/.test(raw)) return null;
        const pairs = raw.split("&").filter(Boolean);
        const ok = pairs.every((p) => /^[^=]+=/.test(p) || p.includes("="));
        if (!ok) return null;
        return {
          confidence: 0.84,
          features: { pairs: pairs.length, hasPercent: /%[0-9A-Fa-f]{2}/.test(raw) },
        };
      },
    },
    {
      id: "bitstring",
      label: "Bitstring (0/1)",
      family: "binary-text",
      uniqueness: "Chỉ 0 và 1 — khác hex/base64",
      structure: { alphabet: ["0", "1"] },
      test(s) {
        const raw = s.trim().replace(/\s/g, "");
        if (raw.length < 8 || !/^[01]+$/.test(raw)) return null;
        return {
          confidence: 0.9,
          features: { bits: raw.length, bytesGuess: Math.floor(raw.length / 8) },
        };
      },
    },
    {
      id: "jose-json",
      label: "JOSE JSON (JWK/JWE-like)",
      family: "token",
      uniqueness: "JSON có kty/alg/ciphertext — khác JWT compact & JSON thường",
      structure: { keys: ["kty", "alg", "ciphertext", "encrypted_key", "enc", "protected"] },
      test(s) {
        const raw = s.trim();
        if (!raw.startsWith("{")) return null;
        try {
          const v = JSON.parse(raw);
          const marks = [
            "kty",
            "alg",
            "enc",
            "ciphertext",
            "encrypted_key",
            "iv",
            "tag",
            "recipients",
            "protected",
            "payload",
            "signature",
            "x5c",
            "crv",
            "n",
            "e",
          ];
          const hit = marks.filter((k) => v && Object.prototype.hasOwnProperty.call(v, k));
          if (hit.length < 2) return null;
          // Cao hơn JSON thuần để thắng khi cùng parse được
          return {
            confidence: 0.985,
            features: { joseKeys: hit },
          };
        } catch {
          return null;
        }
      },
    },
    {
      id: "classical-alpha",
      label: "Bản mã chữ cái (cổ điển / monoalphabetic-like)",
      family: "classical",
      uniqueness: "Chỉ A-Z khoảng trắng; phân bố chữ cái — khác Base64/hex",
      structure: { alphabet: "A-Z", noDigits: true },
      test(s) {
        const raw = s.trim();
        if (raw.length < 12) return null;
        if (!/^[A-Za-z\s]+$/.test(raw)) return null;
        if (/\d/.test(raw)) return null;
        const letters = raw.replace(/[^A-Za-z]/g, "").toUpperCase();
        if (letters.length < 10) return null;
        const freq = letterFreq(letters);
        const ioc = indexOfCoincidence(letters);
        // tiếng Anh ~0.066; random ~0.038
        return {
          confidence: ioc > 0.055 ? 0.72 : letters.length >= 24 ? 0.58 : 0.48,
          features: {
            ioc: Number(ioc.toFixed(4)),
            topLetters: Object.entries(freq)
              .sort((a, b) => b[1] - a[1])
              .slice(0, 5)
              .map(([k]) => k),
            note: "Heuristic học thuật — không khẳng định cipher cụ thể",
          },
        };
      },
    },
    {
      id: "high-entropy-text",
      label: "Chuỗi entropy cao (ciphertext-like)",
      family: "opaque",
      uniqueness: "Ký tự hỗn hợp, entropy cao, không khớp định dạng có cấu trúc",
      structure: { metric: "shannon-entropy", threshold: 4.5 },
      test(s) {
        const raw = s.trim();
        if (raw.length < 16) return null;
        // chỉ khi không phải các dạng có cấu trúc rõ
        if (/^[0-9a-fA-F]+$/.test(raw.replace(/\s/g, ""))) return null;
        if (/^[A-Za-z0-9+/]+={0,2}$/.test(raw.replace(/\s/g, ""))) return null;
        const entropy = shannon(raw);
        if (entropy < 4.5) return null;
        return {
          confidence: Math.min(0.8, 0.45 + (entropy - 4.5) * 0.15),
          features: {
            entropy: Number(entropy.toFixed(3)),
            length: raw.length,
            note: "Có thể là ciphertext/binary text — không suy ra thuật toán",
          },
        };
      },
    },
  ];

  function b64urlDecode(seg) {
    const pad = "=".repeat((4 - (seg.length % 4)) % 4);
    const b64 = (seg + pad).replace(/-/g, "+").replace(/_/g, "/");
    return atob(b64);
  }

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

  function letterFreq(letters) {
    const f = Object.create(null);
    for (const c of letters) f[c] = (f[c] || 0) + 1;
    return f;
  }

  function indexOfCoincidence(letters) {
    const f = letterFreq(letters);
    const n = letters.length;
    if (n < 2) return 0;
    let num = 0;
    Object.values(f).forEach((c) => {
      num += c * (c - 1);
    });
    return num / (n * (n - 1));
  }

  /**
   * Phân tích sâu: chạy TỪNG detector cấu trúc riêng → xếp hạng.
   * Không ép mọi format qua cùng một parser.
   */
  function analyzeDeep(input, opts = {}) {
    const text = String(input ?? "");
    const limit = opts.limit || 5;
    const hits = [];

    FORMATS.forEach((fmt) => {
      let result = null;
      try {
        result = fmt.test(text);
      } catch {
        result = null;
      }
      if (!result) return;
      hits.push({
        id: fmt.id,
        label: fmt.label,
        family: fmt.family,
        uniqueness: fmt.uniqueness,
        structure: fmt.structure,
        confidence: Number(result.confidence.toFixed(3)),
        features: result.features || {},
        relatedConcepts: suggestConcepts(fmt.id),
      });
    });

    hits.sort((a, b) => b.confidence - a.confidence);
    const top = hits.slice(0, limit);
    const primary = top[0] || null;

    return {
      ok: true,
      inputPreview: text.length > 120 ? `${text.slice(0, 117)}…` : text,
      inputLength: text.length,
      primary,
      candidates: top,
      candidateCount: hits.length,
      discriminated: top.length > 1
        ? {
            note: "Nhiều ứng viên — mỗi cái khác cấu trúc; chọn theo confidence + uniqueness",
            vs: top.slice(0, 3).map((c) => ({
              id: c.id,
              uniqueness: c.uniqueness,
              confidence: c.confidence,
            })),
          }
        : null,
      meta: {
        analyzer: "MaMoLogic.analyze",
        version: "1.0.0",
        formatCatalogSize: FORMATS.length,
      },
    };
  }

  function suggestConcepts(formatId) {
    const map = {
      "nginx-queue": ["tls", "encryption-vs-encoding"],
      "nginx-resolver": ["tls", "encryption-vs-encoding"],
      "nginx-upstream-var": ["tls", "encryption-vs-encoding"],
      jwt: ["jose", "hmac", "digital-signature"],
      "pem-armor": ["openpgp", "rsa", "tls"],
      "braille-unicode": ["base64", "encryption-vs-encoding"],
      morse: ["base64", "encryption-vs-encoding"],
      "hash-hex": ["sha-256", "hmac"],
      base64: ["base64", "encryption-vs-encoding"],
      base64url: ["jose", "base64"],
      "jose-json": ["jose", "hybrid-encryption"],
      "classical-alpha": ["caesar", "encryption-vs-encoding"],
      "high-entropy-text": ["aead", "aes-gcm"],
      uuid: ["encryption-vs-encoding"],
      json: ["jose"],
      "url-encoded": ["encryption-vs-encoding"],
      bitstring: ["encryption-vs-encoding"],
      "hex-blob": ["aes-gcm", "encryption-vs-encoding"],
    };
    return map[formatId] || [];
  }

  const Analyze = {
    formats: FORMATS.map((f) => ({
      id: f.id,
      label: f.label,
      family: f.family,
      uniqueness: f.uniqueness,
      structure: f.structure,
    })),

    analyze: analyzeDeep,

    classify(input) {
      const r = analyzeDeep(input, { limit: 1 });
      return r.primary
        ? {
            id: r.primary.id,
            label: r.primary.label,
            family: r.primary.family,
            confidence: r.primary.confidence,
            uniqueness: r.primary.uniqueness,
          }
        : { id: "unknown", label: "Không xác định", confidence: 0 };
    },

    discriminate(input) {
      return analyzeDeep(input, { limit: 8 });
    },

    start() {},
  };

  global.MaMoLogicModules = global.MaMoLogicModules || {};
  global.MaMoLogicModules.analyze = Analyze;
})(window);
