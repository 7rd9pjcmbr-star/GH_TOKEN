/**
 * Super Icon Network Mapper — mọi đường dẫn icon tới thư viện mật mã.
 */
window.NETWORK_MAP = {
  meta: {
    title: "Super Icon Mapper",
    version: "2.0.0",
    summary:
      "Icon trên mọi đường dẫn đến thư viện mật mã: nhóm → khái niệm → thư viện → ngôn ngữ.",
  },

  iconByKind: {
    group: "layers",
    concept: "key",
    library: "cube",
    lang: "code",
    hub: "spark",
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

  /** Icon riêng từng thư viện mật mã */
  iconByLibrary: {
    openssl: "cpu",
    boringssl: "cpu",
    "aws-lc": "cpu",
    libsodium: "spark",
    nacl: "spark",
    webcrypto: "monitor",
    "pyca-cryptography": "code",
    pycryptodome: "code",
    "go-crypto": "code",
    "rust-crypto": "code",
    "java-jca": "code",
    dotnet: "code",
    "apple-crypto": "monitor",
    "node-crypto": "code",
    "libsodium-js": "spark",
    "tweetnacl-js": "spark",
    openpgpjs: "network",
    sequoia: "network",
    gnupg: "network",
    liboqs: "atom",
    botan: "cpu",
    cryptopp: "cpu",
    mbedtls: "chip",
    wolfssl: "chip",
    age: "wrench",
    tink: "spark",
    jose: "network",
  },

  /** Icon trên cạnh theo loại quan hệ */
  iconByEdge: {
    contains: "layers",
    related: "key",
    "implemented-by": "cube",
    "written-in": "code",
    depends: "wrench",
    "hub-link": "spark",
    "provides-match": "lock",
  },

  /**
   * Quân đội icon — tên gọi khi mapper phản hồi trên dòng chảy dữ liệu
   * name = id SVG; call = tên gọi; role = vị trí trên flow
   */
  iconArmy: {
    spark: { call: "Tia Lửa Hub", role: "hub", motto: "mở cổng thư viện" },
    layers: { call: "Lớp Khiên", role: "group", motto: "xếp nhóm kiến thức" },
    key: { call: "Chìa Khái Niệm", role: "concept", motto: "mở khái niệm" },
    lock: { call: "Ổ Khóa AEAD", role: "edge", motto: "khớp cung cấp an toàn" },
    keypair: { call: "Đôi Chìa", role: "asymmetric", motto: "công khai / bí mật" },
    hash: { call: "Dấu Băm", role: "digest", motto: "toàn vẹn dữ liệu" },
    network: { call: "Mạch Mạng", role: "protocol", motto: "luồng giao thức" },
    atom: { call: "Hạt PQC", role: "post-quantum", motto: "sau lượng tử" },
    text: { call: "Dòng Chữ", role: "encoding", motto: "biểu diễn / mã hóa hình thức" },
    cube: { call: "Khối Thư Viện", role: "library", motto: "đích thư viện mật mã" },
    code: { call: "Mã Nguồn", role: "lang", motto: "ngôn ngữ triển khai" },
    compass: { call: "La Bàn Nền", role: "foundations", motto: "định hướng nền tảng" },
    scroll: { call: "Cuộn Cổ Điển", role: "classical", motto: "mã cổ điển" },
    cpu: { call: "Nhân Engine", role: "engine", motto: "động cơ mật mã" },
    monitor: { call: "Màn Nền", role: "platform", motto: "API nền tảng" },
    chip: { call: "Chip Nhúng", role: "embedded", motto: "thiết bị / IoT" },
    wrench: { call: "Cờ Lê Công Cụ", role: "tool", motto: "phụ thuộc / công cụ" },
  },

  /**
   * Icon phản hồi truy vấn thời gian thực (OMS / pipe / sync)
   * status → icon; channel → lead icon
   */
  iconByRealtimeStatus: {
    connected: "monitor",
    alive: "monitor",
    ok: "monitor",
    missing_cred: "key",
    auth_fail: "lock",
    error: "wrench",
    stale: "wrench",
    blocked: "lock",
  },

  iconByRealtimeChannel: {
    telegram: "spark",
    pancake: "layers",
    ghn: "network",
    viettelpost: "network",
    tracking: "compass",
    tpos: "cpu",
    direct_api: "code",
    spx_local: "cube",
    vnpost_local: "code",
    oms_bus: "spark",
  },

  /** Icon gọi khi phân tích định dạng (analyze) */
  iconByFormat: {
    "nginx-queue": "layers",
    "nginx-resolver": "compass",
    "nginx-upstream-var": "network",
    jwt: "network",
    "jose-json": "network",
    "pem-armor": "keypair",
    "braille-unicode": "text",
    morse: "text",
    uuid: "hash",
    "hash-hex": "hash",
    "hex-blob": "cpu",
    base64: "text",
    base64url: "text",
    json: "code",
    "url-encoded": "network",
    bitstring: "cpu",
    "classical-alpha": "scroll",
    "high-entropy-text": "lock",
  },

  conceptLibraryEdges: [
    ["aes-gcm", "openssl"],
    ["aes-gcm", "webcrypto"],
    ["aes-gcm", "pyca-cryptography"],
    ["aes-gcm", "go-crypto"],
    ["aes-gcm", "rust-crypto"],
    ["aes-gcm", "dotnet"],
    ["aes-gcm", "apple-crypto"],
    ["aes-gcm", "tink"],
    ["aes-gcm", "node-crypto"],
    ["aes-gcm", "botan"],
    ["aes-gcm", "cryptopp"],
    ["aes-gcm", "mbedtls"],
    ["aes-gcm", "wolfssl"],
    ["chacha20-poly1305", "libsodium"],
    ["chacha20-poly1305", "libsodium-js"],
    ["chacha20-poly1305", "go-crypto"],
    ["chacha20-poly1305", "apple-crypto"],
    ["chacha20-poly1305", "tink"],
    ["aead", "libsodium"],
    ["aead", "tink"],
    ["aead", "webcrypto"],
    ["aead", "openssl"],
    ["rsa", "openssl"],
    ["rsa", "pyca-cryptography"],
    ["rsa", "java-jca"],
    ["rsa", "pycryptodome"],
    ["rsa", "dotnet"],
    ["ecc", "libsodium"],
    ["ecc", "openssl"],
    ["ecc", "nacl"],
    ["ecc", "apple-crypto"],
    ["digital-signature", "libsodium"],
    ["digital-signature", "webcrypto"],
    ["digital-signature", "sequoia"],
    ["argon2", "libsodium"],
    ["argon2", "go-crypto"],
    ["argon2", "libsodium-js"],
    ["sha-256", "openssl"],
    ["sha-256", "webcrypto"],
    ["sha-256", "node-crypto"],
    ["hmac", "openssl"],
    ["hmac", "webcrypto"],
    ["hmac", "dotnet"],
    ["tls", "openssl"],
    ["tls", "boringssl"],
    ["tls", "aws-lc"],
    ["tls", "go-crypto"],
    ["tls", "rust-crypto"],
    ["tls", "mbedtls"],
    ["tls", "wolfssl"],
    ["tls", "botan"],
    ["openpgp", "gnupg"],
    ["openpgp", "sequoia"],
    ["openpgp", "openpgpjs"],
    ["hpke", "rust-crypto"],
    ["hpke", "tink"],
    ["ml-kem", "liboqs"],
    ["ml-dsa", "liboqs"],
    ["ml-kem", "aws-lc"],
    ["hybrid-encryption", "tink"],
    ["hybrid-encryption", "libsodium"],
    ["base64", "webcrypto"],
    ["signal-protocol", "libsodium"],
    ["openpgp", "jose"],
    ["digital-signature", "jose"],
    ["encryption-vs-encoding", "age"],
  ],

  libraryDepends: [
    ["pyca-cryptography", "openssl"],
    ["pyca-cryptography", "aws-lc"],
    ["node-crypto", "openssl"],
    ["libsodium-js", "libsodium"],
    ["tweetnacl-js", "nacl"],
    ["boringssl", "openssl"],
    ["aws-lc", "boringssl"],
    ["openpgpjs", "webcrypto"],
    ["jose", "webcrypto"],
  ],
};

/**
 * Build graph: mọi thư viện có ít nhất một đường icon từ khái niệm/hub.
 */
window.buildCryptoNetwork = function buildCryptoNetwork(atlas, net) {
  const nodes = [];
  const edges = [];
  const seen = new Set();
  const edgeSeen = new Set();

  function addNode(node) {
    if (seen.has(node.id)) return;
    seen.add(node.id);
    nodes.push(node);
  }

  function addEdge(source, target, kind, label, extra = {}) {
    if (!seen.has(source) || !seen.has(target)) return;
    const id = `${source}->${target}:${kind}`;
    if (edgeSeen.has(id)) return;
    edgeSeen.add(id);
    const toLibrary =
      target.startsWith("lib:") || source.startsWith("lib:") || kind === "hub-link";
    edges.push({
      id,
      source,
      target,
      kind,
      label: label || kind,
      icon: net.iconByEdge?.[kind] || (toLibrary ? "cube" : "key"),
      toLibrary: !!toLibrary,
      ...extra,
    });
  }

  // Hub trung tâm — mọi đường đến thư viện
  addNode({
    id: "hub:crypto-libs",
    label: "Thư viện mật mã",
    kind: "hub",
    category: "hub",
    icon: "spark",
    summary: "Hub: mọi đường dẫn icon tới thư viện mật mã học.",
    searchText: "thu vien mat ma hub library crypto",
    ring: 1.5,
  });

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

  const linkedLibs = new Set();

  (atlas.libraries || []).forEach((lib, i) => {
    const icon =
      net.iconByLibrary?.[lib.id] ||
      net.iconByCategory[lib.category] ||
      "cube";
    addNode({
      id: `lib:${lib.id}`,
      label: lib.name,
      kind: "library",
      category: lib.category,
      icon,
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
      angle: (i / Math.max(atlas.libraries.length, 1)) * Math.PI * 2,
    });
    // Mọi thư viện nối hub
    addEdge("hub:crypto-libs", `lib:${lib.id}`, "hub-link", "đến thư viện", {
      pathRole: "to-library",
    });
  });

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

  (net.conceptLibraryEdges || []).forEach(([cid, lid]) => {
    addEdge(`concept:${cid}`, `lib:${lid}`, "implemented-by", "cung cấp", {
      pathRole: "to-library",
    });
    linkedLibs.add(lid);
  });

  // Tự nối concept ↔ library còn thiếu theo từ khoá provides
  const norm = (s) =>
    String(s || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();

  (atlas.libraries || []).forEach((lib) => {
    const blob = norm(
      [lib.name, ...(lib.provides || []), lib.category, lib.summary].join(" ")
    );
    (atlas.concepts || []).forEach((c) => {
      const keys = [c.id, c.name, ...(c.details || [])].map(norm);
      const hit = keys.some((k) => k.length > 2 && blob.includes(k.replace(/-/g, "")));
      const hit2 = keys.some((k) => {
        const compact = k.replace(/[^a-z0-9]/g, "");
        return compact.length > 3 && blob.replace(/[^a-z0-9]/g, "").includes(compact);
      });
      if (hit || hit2) {
        addEdge(`concept:${c.id}`, `lib:${lib.id}`, "provides-match", "khớp cung cấp", {
          pathRole: "to-library",
        });
        linkedLibs.add(lib.id);
      }
    });
  });

  (net.libraryDepends || []).forEach(([a, b]) => {
    addEdge(`lib:${a}`, `lib:${b}`, "depends", "phụ thuộc", {
      pathRole: "to-library",
    });
  });

  // Đảm bảo thư viện chưa có concept-edge vẫn có đường từ foundations
  (atlas.libraries || []).forEach((lib) => {
    if (linkedLibs.has(lib.id)) return;
    const fallback =
      lib.category === "post-quantum"
        ? "ml-kem"
        : lib.category === "protocol"
          ? "tls"
          : "aead";
    if (seen.has(`concept:${fallback}`)) {
      addEdge(`concept:${fallback}`, `lib:${lib.id}`, "implemented-by", "đường mặc định", {
        pathRole: "to-library",
      });
    }
  });

  return { nodes, edges };
};

/** Liệt kê mọi cạnh/node trên đường tới thư viện từ một node nguồn */
window.pathsToLibraries = function pathsToLibraries(graph, fromId) {
  const libs = graph.nodes.filter((n) => n.kind === "library").map((n) => n.id);
  const adj = new Map();
  graph.edges.forEach((e) => {
    if (!adj.has(e.source)) adj.set(e.source, []);
    if (!adj.has(e.target)) adj.set(e.target, []);
    adj.get(e.source).push({ to: e.target, edge: e });
    adj.get(e.target).push({ to: e.source, edge: e });
  });

  const allPaths = [];
  libs.forEach((goal) => {
    const queue = [fromId];
    const prev = new Map([[fromId, null]]);
    const prevEdge = new Map();
    while (queue.length) {
      const cur = queue.shift();
      if (cur === goal) break;
      (adj.get(cur) || []).forEach(({ to, edge }) => {
        if (!prev.has(to)) {
          prev.set(to, cur);
          prevEdge.set(to, edge);
          queue.push(to);
        }
      });
    }
    if (!prev.has(goal)) return;
    const nodes = [];
    const edges = [];
    for (let at = goal; at; at = prev.get(at)) {
      nodes.push(at);
      if (prevEdge.has(at)) edges.push(prevEdge.get(at));
    }
    nodes.reverse();
    edges.reverse();
    allPaths.push({
      to: goal,
      label: graph.nodes.find((n) => n.id === goal)?.label || goal,
      nodes,
      edges,
      length: edges.length,
      /** Quân đội icon trên dòng chảy path */
      icons: edges.map((e) => {
        const target = graph.nodes.find((n) => n.id === e.target);
        return (
          e.icon ||
          (target && target.kind === "library" ? target.icon : null) ||
          "cube"
        );
      }),
      iconFlow: nodes.map((id) => {
        const n = graph.nodes.find((x) => x.id === id);
        return { id, icon: n?.icon || "cube", kind: n?.kind, label: n?.label };
      }),
    });
  });

  allPaths.sort((a, b) => a.length - b.length || a.label.localeCompare(b.label));
  return allPaths;
};
