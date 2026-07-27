/**
 * Search — tra cứu thông minh (module 3/9)
 * Hỗ trợ từ đồng nghĩa, điểm xếp hạng, lọc kind/category/language.
 */
(function (global) {
  "use strict";

  const SYNONYMS = {
    password: ["mat khau", "argon2", "bcrypt", "scrypt", "pwhash", "kdf"],
    encrypt: ["ma hoa", "encryption", "cipher", "aead", "secretbox"],
    decrypt: ["giai ma", "decryption"],
    hash: ["bam", "digest", "sha", "blake"],
    signature: ["chu ky", "sign", "ed25519", "ecdsa"],
    tls: ["https", "ssl", "channel", "kenh"],
    quantum: ["pqc", "hau luong tu", "ml-kem", "kyber", "dilithium"],
    browser: ["webcrypto", "javascript", "web"],
    python: ["pyca", "pynacl", "pycryptodome"],
    rust: ["rustls", "ring", "rustcrypto"],
    recommend: ["nen dung", "chon", "khuyen nghi"],
    encoding: ["base64", "hex", "morse", "braille", "bieu dien"],
  };

  function expandQuery(q, normalize) {
    const tokens = normalize(q).split(/\s+/).filter(Boolean);
    const expanded = new Set(tokens);
    tokens.forEach((t) => {
      Object.entries(SYNONYMS).forEach(([key, vals]) => {
        if (t === key || vals.some((v) => v.includes(t) || t.includes(v))) {
          expanded.add(normalize(key));
          vals.forEach((v) => expanded.add(normalize(v)));
        }
      });
    });
    return [...expanded];
  }

  function scoreItem(item, tokens, normalize) {
    const hay = item._norm || normalize(item.name || "");
    let score = 0;
    tokens.forEach((t) => {
      if (!t) return;
      if (normalize(item.id) === t) score += 40;
      if (normalize(item.name) === t) score += 35;
      if (normalize(item.name).startsWith(t)) score += 18;
      if (normalize(item.id).includes(t)) score += 16;
      if (normalize(item.name).includes(t)) score += 14;
      if (hay.includes(t)) score += 8;
      if ((item.provides || []).some((p) => normalize(p).includes(t))) score += 10;
      if ((item.languages || []).some((l) => normalize(l).includes(t))) score += 12;
      if (normalize(item.tier || "").includes(t)) score += 6;
      if (normalize(item.category || "").includes(t)) score += 6;
    });
    if (item.tier === "khuyến nghị") score += 3;
    return score;
  }

  const Search = {
    /**
     * @param {string} query
     * @param {{ kind?: 'concept'|'library'|'all', category?: string, language?: string, limit?: number }} [opts]
     */
    query(query, opts = {}) {
      const cat = global.MaMoCryptoCore.get("catalog");
      if (!cat) return [];
      const normalize = cat.normalize;
      const tokens = expandQuery(query || "", normalize);
      const kind = opts.kind || "all";
      const limit = opts.limit || 20;

      let pool = [];
      if (kind === "all" || kind === "concept") pool = pool.concat(cat.concepts);
      if (kind === "all" || kind === "library") pool = pool.concat(cat.libraries);

      if (opts.category) {
        const c = normalize(opts.category);
        pool = pool.filter((item) => normalize(item.category) === c || normalize(item.category).includes(c));
      }
      if (opts.language) {
        const lang = normalize(opts.language);
        pool = pool.filter(
          (item) =>
            item.kind === "concept" ||
            (item.languages || []).some((l) => normalize(l).includes(lang))
        );
      }

      const ranked = pool
        .map((item) => ({ item, score: scoreItem(item, tokens, normalize) }))
        .filter((r) => (query ? r.score > 0 : true))
        .sort((a, b) => b.score - a.score || String(a.item.name).localeCompare(b.item.name))
        .slice(0, limit)
        .map((r) => ({
          id: r.item.id,
          kind: r.item.kind,
          name: r.item.name,
          summary: r.item.summary,
          category: r.item.category,
          tier: r.item.tier,
          level: r.item.level,
          languages: r.item.languages,
          score: r.score,
          url: r.item.url,
          ref: r.item.kind === "library" ? `lib:${r.item.id}` : `concept:${r.item.id}`,
        }));

      global.MaMoCryptoCore.emit("search:done", { query, count: ranked.length });
      return ranked;
    },

    suggest(prefix, limit = 8) {
      if (!prefix) return [];
      return Search.query(prefix, { limit }).map((r) => ({
        label: r.name,
        kind: r.kind,
        id: r.id,
        score: r.score,
      }));
    },

    start() {},
  };

  global.MaMoCryptoCore.register("search", Search);
})(window);
