/**
 * Super Icon Network Mapper — quan hệ thư viện ↔ khái niệm ↔ nhóm.
 * Sinh graph từ CRYPTO_ATLAS + cạnh tường minh.
 */
window.NETWORK_MAP = {
  meta: {
    title: "Super Icon Mapper",
    version: "1.0.0",
    summary: "Sơ đồ network toàn diện: icon node, tìm kiếm, quan hệ cung cấp / liên quan.",
  },

  /** Icon key theo loại node */
  iconByKind: {
    group: "layers",
    concept: "key",
    library: "cube",
    lang: "code",
  },

  iconByCategory: {
    foundations: "compass",
    classical: "scroll",
    symmetric: "lock",
    asymmetric: "keypair",
    "hash-mac-kdf": "hash",
    protocols: "network",
    "post-quantum": "atom",
    encoding: "text",
    engine: "cpu",
    "modern-api": "spark",
    platform: "monitor",
    language: "code",
    protocol: "network",
    "post-quantum-lib": "atom",
    embedded: "chip",
    tool: "wrench",
  },

  /** Cạnh tường minh: concept → library (provides / implements) */
  conceptLibraryEdges: [
    ["aes-gcm", "openssl"],
    ["aes-gcm", "webcrypto"],
    ["aes-gcm", "pyca-cryptography"],
    ["aes-gcm", "go-crypto"],
    ["aes-gcm", "rust-crypto"],
    ["aes-gcm", "dotnet"],
    ["aes-gcm", "apple-crypto"],
    ["aes-gcm", "tink"],
    ["chacha20-poly1305", "libsodium"],
    ["chacha20-poly1305", "libsodium-js"],
    ["chacha20-poly1305", "go-crypto"],
    ["chacha20-poly1305", "apple-crypto"],
    ["aead", "libsodium"],
    ["aead", "tink"],
    ["aead", "webcrypto"],
    ["rsa", "openssl"],
    ["rsa", "pyca-cryptography"],
    ["rsa", "java-jca"],
    ["ecc", "libsodium"],
    ["ecc", "openssl"],
    ["ecc", "nacl"],
    ["digital-signature", "libsodium"],
    ["digital-signature", "webcrypto"],
    ["argon2", "libsodium"],
    ["argon2", "go-crypto"],
    ["sha-256", "openssl"],
    ["sha-256", "webcrypto"],
    ["hmac", "openssl"],
    ["hmac", "webcrypto"],
    ["tls", "openssl"],
    ["tls", "boringssl"],
    ["tls", "aws-lc"],
    ["tls", "go-crypto"],
    ["tls", "rust-crypto"],
    ["tls", "mbedtls"],
    ["tls", "wolfssl"],
    ["openpgp", "gnupg"],
    ["openpgp", "sequoia"],
    ["openpgp", "openpgpjs"],
    ["hpke", "rust-crypto"],
    ["ml-kem", "liboqs"],
    ["ml-dsa", "liboqs"],
    ["hybrid-encryption", "tink"],
    ["hybrid-encryption", "libsodium"],
    ["base64", "webcrypto"],
    ["signal-protocol", "libsodium"],
  ],

  /** Cạnh thư viện → ngôn ngữ */
  // sinh động từ library.languages trong builder

  /** Nhóm phụ thuộc engine */
  libraryDepends: [
    ["pyca-cryptography", "openssl"],
    ["pyca-cryptography", "aws-lc"],
    ["node-crypto", "openssl"],
    ["libsodium-js", "libsodium"],
    ["tweetnacl-js", "nacl"],
    ["boringssl", "openssl"],
    ["aws-lc", "boringssl"],
  ],
};

/**
 * Build full graph nodes/edges from atlas + NETWORK_MAP.
 */
window.buildCryptoNetwork = function buildCryptoNetwork(atlas, net) {
  const nodes = [];
  const edges = [];
  const seen = new Set();

  function addNode(node) {
    if (seen.has(node.id)) return;
    seen.add(node.id);
    nodes.push(node);
  }

  function addEdge(source, target, kind, label) {
    if (!seen.has(source) || !seen.has(target)) return;
    edges.push({
      id: `${source}->${target}:${kind}`,
      source,
      target,
      kind,
      label: label || kind,
    });
  }

  // Groups (taxonomy)
  (atlas.taxonomy || []).forEach((t, i) => {
    addNode({
      id: `group:${t.id}`,
      label: t.name,
      kind: "group",
      category: t.id,
      icon: net.iconByCategory[t.id] || "layers",
      summary: t.summary,
      searchText: `${t.name} ${t.summary} ${t.id}`,
      ring: 0,
      angle: (i / atlas.taxonomy.length) * Math.PI * 2,
    });
  });

  // Concepts
  (atlas.concepts || []).forEach((c) => {
    addNode({
      id: `concept:${c.id}`,
      label: c.name,
      kind: "concept",
      category: c.category,
      icon: net.iconByCategory[c.category] || "key",
      summary: c.summary,
      level: c.level,
      details: c.details,
      related: c.related,
      searchText: `${c.name} ${c.summary} ${c.level} ${(c.details || []).join(" ")} ${c.id}`,
      ring: 1,
    });
    addEdge(`group:${c.category}`, `concept:${c.id}`, "contains", "nhóm");
  });

  (atlas.concepts || []).forEach((c) => {
    (c.related || []).forEach((rid) => {
      addEdge(`concept:${c.id}`, `concept:${rid}`, "related", "liên quan");
    });
  });

  // Libraries
  (atlas.libraries || []).forEach((lib) => {
    const catIcon =
      net.iconByCategory[lib.category] ||
      (lib.category === "post-quantum" ? "atom" : "cube");
    addNode({
      id: `lib:${lib.id}`,
      label: lib.name,
      kind: "library",
      category: lib.category,
      icon: catIcon,
      summary: lib.summary,
      tier: lib.tier,
      languages: lib.languages,
      provides: lib.provides,
      url: lib.url,
      notes: lib.notes,
      searchText: [
        lib.name,
        lib.summary,
        lib.tier,
        lib.category,
        ...(lib.languages || []),
        ...(lib.provides || []),
        ...(lib.bindings || []),
        lib.notes || "",
        lib.id,
      ].join(" "),
      ring: 2,
    });
  });

  // Languages as nodes
  const langs = new Set();
  (atlas.libraries || []).forEach((lib) => {
    (lib.languages || []).forEach((l) => langs.add(l));
  });
  [...langs].forEach((lang, i) => {
    addNode({
      id: `lang:${lang}`,
      label: lang,
      kind: "lang",
      category: "language",
      icon: "code",
      summary: `Ngôn ngữ / nền: ${lang}`,
      searchText: lang,
      ring: 3,
      angle: (i / Math.max(langs.size, 1)) * Math.PI * 2,
    });
  });

  (atlas.libraries || []).forEach((lib) => {
    (lib.languages || []).forEach((lang) => {
      addEdge(`lib:${lib.id}`, `lang:${lang}`, "written-in", "ngôn ngữ");
    });
  });

  // Concept ↔ library
  (net.conceptLibraryEdges || []).forEach(([cid, lid]) => {
    addEdge(`concept:${cid}`, `lib:${lid}`, "implemented-by", "cung cấp");
  });

  // Library dependencies
  (net.libraryDepends || []).forEach(([a, b]) => {
    addEdge(`lib:${a}`, `lib:${b}`, "depends", "phụ thuộc");
  });

  return { nodes, edges };
};
