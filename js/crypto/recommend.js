/**
 * Recommend — gợi ý thông minh theo nhu cầu (module 6/9)
 */
(function (global) {
  "use strict";

  const RULES = [
    {
      id: "app-data",
      match: /du lieu|ung dung|general|aead|secretbox|ma hoa du lieu/i,
      pick: ["libsodium", "tink", "webcrypto"],
      concepts: ["aead", "chacha20-poly1305", "aes-gcm"],
      reason: "AEAD high-level an toàn cho dữ liệu ứng dụng",
    },
    {
      id: "https",
      match: /https|tls|kenh|mang|network/i,
      pick: ["openssl", "boringssl", "go-crypto", "rust-crypto"],
      concepts: ["tls", "hybrid-encryption"],
      reason: "TLS 1.3 qua stack đã kiểm chứng",
    },
    {
      id: "browser",
      match: /browser|web|javascript|frontend|client/i,
      pick: ["webcrypto", "libsodium-js"],
      concepts: ["aes-gcm", "webcrypto"],
      reason: "Web Crypto API; bổ sung libsodium.js nếu thiếu primitive",
    },
    {
      id: "password",
      match: /password|mat khau|argon|bcrypt|hash mat khau/i,
      pick: ["libsodium", "go-crypto"],
      concepts: ["argon2"],
      reason: "Argon2id — không dùng SHA thuần",
    },
    {
      id: "sign",
      match: /chu ky|signature|sign|xac minh/i,
      pick: ["libsodium", "webcrypto", "liboqs"],
      concepts: ["digital-signature", "ecc", "ml-dsa"],
      reason: "Ed25519 hoặc ML-DSA (PQC) qua thư viện audited",
    },
    {
      id: "pgp",
      match: /pgp|email|gpg|openpgp|file encrypt/i,
      pick: ["sequoia", "openpgpjs", "age", "gnupg"],
      concepts: ["openpgp"],
      reason: "OpenPGP hoặc age cho file đơn giản",
    },
    {
      id: "iot",
      match: /iot|embedded|nhung|mcu/i,
      pick: ["mbedtls", "wolfssl"],
      concepts: ["tls", "aes-gcm"],
      reason: "TLS/crypto nhẹ cho thiết bị hạn chế tài nguyên",
    },
    {
      id: "pqc",
      match: /quantum|pqc|hau luong tu|kyber|ml-kem/i,
      pick: ["liboqs", "aws-lc", "boringssl"],
      concepts: ["ml-kem", "ml-dsa"],
      reason: "Hybrid ML-KEM + classical trong giai đoạn chuyển",
    },
    {
      id: "encoding",
      match: /morse|braille|base64|encoding|bieu dien/i,
      pick: [],
      concepts: ["base64", "encryption-vs-encoding"],
      reason: "Encoding ≠ encryption — dùng Mã Mở assistive cho Morse/Braille",
    },
    {
      id: "unmask-redaction",
      match: /unmask|redaction|che pii|mask \*\*\*\*|pii mask|giai ma che/i,
      pick: ["pyca-cryptography", "tink", "libsodium"],
      concepts: ["encryption-vs-encoding", "aead", "aes-gcm"],
      reason:
        "**** là redaction — không decrypt được; PII nội bộ dùng AEAD (pyca/Tink/libsodium), export thì mask",
    },
  ];

  const Recommend = {
    /**
     * @param {string|{need?: string, language?: string}} input
     */
    forNeed(input) {
      const need = typeof input === "string" ? input : input?.need || "";
      const language = typeof input === "object" ? input.language : undefined;
      const cat = global.MaMoCryptoCore.get("catalog");
      const libsApi = global.MaMoCryptoCore.get("libraries");
      const conceptsApi = global.MaMoCryptoCore.get("concepts");

      const normNeed = cat?.normalize(need) || String(need).toLowerCase();
      const hit =
        RULES.find((r) => r.match.test(normNeed) || r.match.test(need)) || null;

      // Also merge decisionGuide fuzzy
      const guideHits = (cat?.guide || []).filter((g) => {
        const blob = cat.normalize(`${g.need} ${g.pick}`);
        return normNeed.split(/\s+/).some((t) => t && blob.includes(t));
      });

      const libraryIds = hit?.pick || [];
      let libraries = libraryIds
        .map((id) => libsApi?.get(id))
        .filter(Boolean);

      if (language) {
        const langLibs = libsApi?.list({ language }) || [];
        libraries = [
          ...libraries.filter((l) =>
            (l.languages || []).some((x) =>
              cat.normalize(x).includes(cat.normalize(language))
            )
          ),
          ...langLibs.filter((l) => l.tier === "khuyến nghị").slice(0, 3),
        ];
        // dedupe
        const seen = new Set();
        libraries = libraries.filter((l) => {
          if (seen.has(l.id)) return false;
          seen.add(l.id);
          return true;
        });
      }

      const concepts = (hit?.concepts || [])
        .map((id) => conceptsApi?.get(id))
        .filter(Boolean);

      const result = {
        need,
        language: language || null,
        reason: hit?.reason || guideHits[0]?.pick || "Không khớp rule — xem kết quả search",
        ruleId: hit?.id || null,
        libraries,
        concepts,
        guide: guideHits.slice(0, 3),
        searchFallback: global.MaMoCryptoCore.get("search")?.query(need, {
          limit: 8,
        }),
      };

      global.MaMoCryptoCore.emit("recommend:done", result);
      return result;
    },

    rules() {
      return RULES.map((r) => ({ id: r.id, reason: r.reason, pick: r.pick }));
    },

    cheatSheet() {
      return global.CRYPTO_ATLAS?.cheatSheet || { do: [], dont: [] };
    },

    start() {},
  };

  global.MaMoCryptoCore.register("recommend", Recommend);
})(window);
