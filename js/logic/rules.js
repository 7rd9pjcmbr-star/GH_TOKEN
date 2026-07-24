/**
 * rules — luật quyết định có thứ tự ưu tiên
 */
(function (global) {
  "use strict";

  const INTENT_PATTERNS = [
    { intent: "upstream-vars", re: /\$upstream_|upstream_addr|upstream_status|bien nhung|embedded variable|ngx_http_upstream|\bresolver\b|status_zone|valid=\d|\bqueue\s+\d|queue\s+number|timeout=\d/i },
    { intent: "path", re: /duong dan|path|toi thu vien|route|mapper/i },
    { intent: "recommend", re: /goi y|nen dung|recommend|chon gi|khuyen nghi/i },
    { intent: "library", re: /thu vien|library|libsodium|openssl|webcrypto/i },
    { intent: "concept", re: /khai niem|aes|aead|tls|argon|hash|cipher/i },
    { intent: "encode", re: /morse|braille|base64|encoding|bieu dien/i },
    { intent: "assist", re: /ho tro|cong tac|quet|khiem khuyet|accessibility/i },
    { intent: "lookup", re: /.+/i },
  ];

  const NEED_RULES = [
    {
      id: "R-UPSTREAM-VAR",
      priority: 12,
      re: /\$upstream_|upstream_addr|upstream_status|upstream_cache|upstream_response_time|bien nhung|embedded variable|ngx_http_upstream|\bresolver\b\s|resolver\s+\d|status_zone\s*=|\bqueue\s+\d|queue\s+\d+\s+timeout/i,
      action: "upstream-vars",
      libs: [],
      concepts: [],
      reason: "ngx_http_upstream_module — $upstream_* / resolver / queue",
    },
    {
      id: "R-PASSWORD",
      priority: 10,
      re: /password|mat khau|argon|bcrypt|pwhash/i,
      action: "recommend",
      libs: ["libsodium", "go-crypto"],
      concepts: ["argon2"],
      reason: "Băm mật khẩu: Argon2id qua libsodium / x/crypto",
    },
    {
      id: "R-BROWSER",
      priority: 20,
      re: /browser|webcrypto|javascript|frontend|trinh duyet/i,
      action: "recommend",
      libs: ["webcrypto", "libsodium-js"],
      concepts: ["aes-gcm", "aead"],
      reason: "Trình duyệt: Web Crypto; thiếu primitive thì libsodium.js",
    },
    {
      id: "R-TLS",
      priority: 20,
      re: /tls|https|ssl|kenh mang|network channel/i,
      action: "recommend",
      libs: ["openssl", "boringssl", "go-crypto", "rust-crypto"],
      concepts: ["tls", "hybrid-encryption"],
      reason: "Kênh mạng: TLS 1.3 qua stack đã kiểm chứng",
    },
    {
      id: "R-PQC",
      priority: 15,
      re: /quantum|pqc|hau luong tu|kyber|ml-kem|dilithium/i,
      action: "recommend",
      libs: ["liboqs", "aws-lc"],
      concepts: ["ml-kem", "ml-dsa"],
      reason: "PQC: liboqs / hybrid ML-KEM + classical",
    },
    {
      id: "R-JWT",
      priority: 18,
      re: /\bjwt\b|\bjwe\b|\bjws\b|\bjose\b|json web token/i,
      action: "recommend",
      libs: ["jose", "webcrypto"],
      concepts: ["digital-signature", "hmac"],
      reason: "Token web: thư viện JOSE đã kiểm chứng — tránh alg=none",
    },
    {
      id: "R-FILE",
      priority: 25,
      re: /\bage\b|ma hoa file|file encryption|encrypt file/i,
      action: "recommend",
      libs: ["age", "sequoia", "libsodium"],
      concepts: ["hybrid-encryption", "openpgp"],
      reason: "Mã hoá file: age (đơn giản) hoặc Sequoia/OpenPGP",
    },
    {
      id: "R-APPDATA",
      priority: 30,
      re: /du lieu|aead|secretbox|ma hoa ung dung|file data/i,
      action: "recommend",
      libs: ["libsodium", "tink", "webcrypto"],
      concepts: ["aead", "chacha20-poly1305", "aes-gcm"],
      reason: "Dữ liệu ứng dụng: AEAD high-level",
    },
    {
      id: "R-ENCODE",
      priority: 40,
      re: /morse|braille|base64|encoding/i,
      action: "encode-info",
      libs: [],
      concepts: ["base64", "encryption-vs-encoding"],
      reason: "Encoding ≠ encryption — Morse/Braille/Base64 chỉ biểu diễn",
    },
    {
      id: "R-ASSIST",
      priority: 5,
      re: /cong tac|quet|locked-in|khong van dong|accessibility|ho tro dac biet/i,
      action: "assist",
      libs: [],
      concepts: [],
      reason: "Chế độ đặc biệt: một công tắc + quét + TTS (js/a11y)",
    },
  ];

  const Rules = {
    classifyIntent(text) {
      const raw = String(text || "");
      for (const p of INTENT_PATTERNS) {
        if (p.re.test(raw)) return p.intent;
      }
      return "lookup";
    },

    matchNeed(text) {
      const raw = String(text || "");
      const norm = global.MaMoLogicModules.schema.normalize(raw);
      const hits = NEED_RULES.filter((r) => r.re.test(raw) || r.re.test(norm));
      if (!hits.length) return null;
      hits.sort((a, b) => (a.priority || 100) - (b.priority || 100));
      return hits[0];
    },

    all() {
      return NEED_RULES.map((r) => ({
        id: r.id,
        priority: r.priority,
        reason: r.reason,
        libs: r.libs,
        concepts: r.concepts,
        action: r.action,
      }));
    },

    start() {},
  };

  global.MaMoLogicModules.rules = Rules;
})(window);
