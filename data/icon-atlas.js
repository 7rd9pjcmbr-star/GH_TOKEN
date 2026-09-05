/**
 * Icon Atlas — tài liệu đầy đủ mọi icon xuất hiện trên không gian mạng
 * + ánh xạ thư viện (docs đủ: name, summary, url, provides, notes, tier).
 * Không bỏ sót icon nào có trên graph / army / format / SVG.
 */
(function (global) {
  "use strict";

  /** SVG path registry — phải khớp mapper/mapper.js ICONS */
  const ICON_SVG = {
    layers: "M4 8l8-4 8 4-8 4-8-4zm0 4l8 4 8-4M4 16l8 4 8-4",
    key: "M14 8a4 4 0 11-4 4h-4v3H4v-3H2v-2h8a4 4 0 014-2zm2 2a1.5 1.5 0 100-3 1.5 1.5 0 000 3z",
    lock: "M7 10V7a5 5 0 0110 0v3h1a1 1 0 011 1v8a1 1 0 01-1 1H6a1 1 0 01-1-1v-8a1 1 0 011-1h1zm2 0h6V7a3 3 0 00-6 0v3z",
    keypair: "M8 10a3 3 0 110-6 3 3 0 010 6zm0-2a1 1 0 100-2 1 1 0 000 2zm8 8a3 3 0 110-6 3 3 0 010 6zm-1-3h-6v2h6v-2z",
    hash: "M9 4l-1 16M16 4l-1 16M5 9h14M4 15h14",
    network: "M5 12a2 2 0 110-4 2 2 0 010 4zm14 0a2 2 0 110-4 2 2 0 010 4zM12 19a2 2 0 110-4 2 2 0 010 4zM6.5 10.5l11-3M17.5 10.5l-4 5M6.7 11.5l4 5",
    atom: "M12 12m-2 0a2 2 0 104 0 2 2 0 10-4 0M12 4c4 3 4 13 0 16M12 4c-4 3-4 13 0 16M4 10c5-4 11-4 16 0M4 14c5 4 11 4 16 0",
    text: "M5 7h14M5 12h10M5 17h12",
    cube: "M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zm0 2.2L6.5 8.4v7.2L12 18.8l5.5-3.2V8.4L12 5.2zM12 12l5.2-3M12 12v6.5M12 12L6.8 9",
    code: "M8 8l-4 4 4 4M16 8l4 4-4 4M13 6l-2 12",
    compass: "M12 4a8 8 0 100 16 8 8 0 000-16zm2.5 5.5l-5 2 2 5 5-2-2-5z",
    scroll: "M7 5h9a2 2 0 012 2v11H8a2 2 0 01-2-2V5zm1 2v10h1V7H8zm3 2h5v2h-5V9zm0 4h5v2h-5v-2z",
    cpu: "M8 8h8v8H8V8zm2 2v4h4v-4h-4zM11 4v3M13 4v3M11 17v3M13 17v3M4 11h3M4 13h3M17 11h3M17 13h3",
    spark: "M12 3l1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5L12 3z",
    monitor: "M4 6h16v10H4V6zm2 12h12v1H6v-1z",
    chip: "M8 8h8v8H8V8zm-2 2H4v1h2v-1zm0 3H4v1h2v-1zm12-3h2v1h-2v-1zm0 3h2v1h-2v-1zM10 4v2h1V4h-1zm3 0v2h1V4h-1zM10 18v2h1v-2h-1zm3 0v2h1v-2h-1z",
    wrench: "M14.5 5.5a3.5 3.5 0 00-4.6 4.6L4 16v4h4l5.9-5.9a3.5 3.5 0 004.6-4.6l-2.5 1.5-2-2 1.5-2.5z",
  };

  /** Tài liệu mở rộng theo icon — khái niệm / cách đọc trên mạng */
  const ICON_DOCS = {
    spark: {
      title: "Tia Lửa Hub",
      doc: "Icon hub trung tâm và API hiện đại (libsodium, Tink…). Điểm mở cổng tới mọi thư viện.",
      seeAlso: ["cube", "lock"],
    },
    layers: {
      title: "Lớp Khiên",
      doc: "Icon nhóm taxonomy (foundations…encoding). Mỗi lớp chứa khái niệm → đường tới thư viện.",
      seeAlso: ["key", "compass"],
    },
    key: {
      title: "Chìa Khái Niệm",
      doc: "Icon mặc định khái niệm / cạnh related. Theo path concept→lib để tới tài liệu thư viện.",
      seeAlso: ["lock", "cube"],
    },
    lock: {
      title: "Ổ Khóa AEAD",
      doc: "Đối xứng / khớp provides-match. Liên hệ AES-GCM, ChaCha, Tink, WebCrypto.",
      seeAlso: ["spark", "cpu"],
    },
    keypair: {
      title: "Đôi Chìa",
      doc: "Bất đối xứng (RSA/ECC). Thư viện: OpenSSL, pyca, JCA, libsodium (X25519/Ed25519).",
      seeAlso: ["network", "atom"],
    },
    hash: {
      title: "Dấu Băm",
      doc: "Băm / MAC / KDF. Thư viện: OpenSSL, WebCrypto, Go crypto, libsodium (BLAKE2/Argon2).",
      seeAlso: ["cpu", "code"],
    },
    network: {
      title: "Mạch Mạng",
      doc: "Giao thức & JOSE/OpenPGP. Thư viện: OpenPGP.js, Sequoia, GnuPG, JOSE.",
      seeAlso: ["cube", "wrench"],
    },
    atom: {
      title: "Hạt PQC",
      doc: "Hậu lượng tử. Thư viện chính: liboqs; hybrid với classical engines.",
      seeAlso: ["cpu", "keypair"],
    },
    text: {
      title: "Dòng Chữ",
      doc: "Encoding (Base64/Morse/Braille) — không phải encryption. Liên hệ assistive + docs encoding.",
      seeAlso: ["scroll", "key"],
    },
    cube: {
      title: "Khối Thư Viện",
      doc: "Icon cạnh implemented-by / đích thư viện. Đại diện mọi lib trên mạng.",
      seeAlso: ["spark", "code"],
    },
    code: {
      title: "Mã Nguồn",
      doc: "Thư viện theo ngôn ngữ (Python/Go/Rust/Java/.NET/Node) và node lang.",
      seeAlso: ["monitor", "cpu"],
    },
    compass: {
      title: "La Bàn Nền",
      doc: "Nhóm foundations — định hướng CIA, encryption≠encoding. Path tới lib khuyến nghị.",
      seeAlso: ["key", "spark"],
    },
    scroll: {
      title: "Cuộn Cổ Điển",
      doc: "Mật mã cổ điển (học thuật). Không dùng cho bảo mật thật; tham chiếu giáo dục.",
      seeAlso: ["text", "compass"],
    },
    cpu: {
      title: "Nhân Engine",
      doc: "Engine C/C++: OpenSSL, BoringSSL, AWS-LC, Botan, Crypto++.",
      seeAlso: ["chip", "monitor"],
    },
    monitor: {
      title: "Màn Nền",
      doc: "Platform API: WebCrypto, Apple CryptoKit.",
      seeAlso: ["spark", "code"],
    },
    chip: {
      title: "Chip Nhúng",
      doc: "Embedded/IoT: Mbed TLS, wolfSSL.",
      seeAlso: ["cpu", "network"],
    },
    wrench: {
      title: "Cờ Lê Công Cụ",
      doc: "Công cụ / phụ thuộc (age, depends edges).",
      seeAlso: ["cube", "network"],
    },
  };

  function libDoc(lib) {
    if (!lib) return null;
    const complete = !!(
      lib.id &&
      lib.name &&
      lib.summary &&
      lib.url &&
      (lib.provides || []).length &&
      lib.tier &&
      lib.category
    );
    return {
      id: lib.id,
      ref: `lib:${lib.id}`,
      name: lib.name,
      category: lib.category,
      tier: lib.tier,
      summary: lib.summary,
      provides: (lib.provides || []).slice(),
      languages: (lib.languages || []).slice(),
      bindings: (lib.bindings || []).slice(),
      url: lib.url || null,
      notes: lib.notes || null,
      docsComplete: complete,
      documentation: {
        primary: lib.url || null,
        notes: lib.notes || null,
        provides: (lib.provides || []).slice(),
        languages: (lib.languages || []).slice(),
      },
    };
  }

  function collectUsedIcons(graph, net) {
    const used = new Set();
    (graph.nodes || []).forEach((n) => n.icon && used.add(n.icon));
    (graph.edges || []).forEach((e) => e.icon && used.add(e.icon));
    Object.values(net.iconByKind || {}).forEach((i) => used.add(i));
    Object.values(net.iconByCategory || {}).forEach((i) => used.add(i));
    Object.values(net.iconByLibrary || {}).forEach((i) => used.add(i));
    Object.values(net.iconByEdge || {}).forEach((i) => used.add(i));
    Object.values(net.iconByFormat || {}).forEach((i) => used.add(i));
    Object.keys(net.iconArmy || {}).forEach((i) => used.add(i));
    Object.keys(ICON_SVG).forEach((i) => used.add(i));
    Object.keys(ICON_DOCS).forEach((i) => used.add(i));
    return used;
  }

  function librariesReachableFrom(graph, startId, limit = 40) {
    const libs = [];
    const seen = new Set([startId]);
    const q = [startId];
    const adj = new Map();
    graph.edges.forEach((e) => {
      if (!adj.has(e.source)) adj.set(e.source, []);
      if (!adj.has(e.target)) adj.set(e.target, []);
      adj.get(e.source).push(e.target);
      adj.get(e.target).push(e.source);
    });
    while (q.length && libs.length < limit) {
      const cur = q.shift();
      const node = graph.nodes.find((n) => n.id === cur);
      if (node?.kind === "library") {
        libs.push(cur);
        continue;
      }
      (adj.get(cur) || []).forEach((n) => {
        if (!seen.has(n)) {
          seen.add(n);
          q.push(n);
        }
      });
    }
    return [...new Set(libs)];
  }

  /**
   * Mapping đầy đủ: mọi icon trên mạng → tài liệu + thư viện (docs đủ)
   */
  function buildIconLibraryAtlas(atlas, net, graph) {
    const libById = Object.fromEntries(
      (atlas.libraries || []).map((l) => [l.id, l])
    );
    const used = collectUsedIcons(graph, net);
    const army = net.iconArmy || {};
    const formatByIcon = {};
    Object.entries(net.iconByFormat || {}).forEach(([fmt, icon]) => {
      if (!formatByIcon[icon]) formatByIcon[icon] = [];
      formatByIcon[icon].push(fmt);
    });

    const entries = [];
    [...used].sort().forEach((icon) => {
      const nodes = (graph.nodes || []).filter((n) => n.icon === icon);
      const edges = (graph.edges || []).filter((e) => e.icon === icon);
      const directLibs = nodes
        .filter((n) => n.kind === "library")
        .map((n) => n.id.replace(/^lib:/, ""));

      // Thư viện qua dòng chảy từ mọi node mang icon này
      const flowLibIds = new Set(directLibs);
      nodes.forEach((n) => {
        if (n.kind === "library") return;
        librariesReachableFrom(graph, n.id, 30).forEach((ref) => {
          flowLibIds.add(ref.replace(/^lib:/, ""));
        });
      });
      // Icon trên cạnh (contains/related/…) — BFS từ hai đầu cạnh
      edges.forEach((e) => {
        [e.source, e.target].forEach((nid) => {
          const node = (graph.nodes || []).find((n) => n.id === nid);
          if (node?.kind === "library") {
            flowLibIds.add(nid.replace(/^lib:/, ""));
            return;
          }
          librariesReachableFrom(graph, nid, 30).forEach((ref) => {
            flowLibIds.add(ref.replace(/^lib:/, ""));
          });
        });
      });
      // Icon chỉ có trong army/kind map (chưa gắn node/cạnh) → hub
      if (flowLibIds.size === 0) {
        librariesReachableFrom(graph, "hub:crypto-libs", 40).forEach((ref) => {
          flowLibIds.add(ref.replace(/^lib:/, ""));
        });
        Object.keys(libById).forEach((id) => flowLibIds.add(id));
      }
      // Icon cube / spark hub: gắn mọi thư viện có tài liệu
      if (icon === "cube" || icon === "spark") {
        Object.keys(libById).forEach((id) => flowLibIds.add(id));
      }

      const libraries = [...flowLibIds]
        .map((id) => libDoc(libById[id]))
        .filter(Boolean)
        .sort((a, b) => a.name.localeCompare(b.name));

      const concepts = nodes
        .filter((n) => n.kind === "concept")
        .map((n) => ({ id: n.id, label: n.label, category: n.category }));
      const groups = nodes
        .filter((n) => n.kind === "group" || n.kind === "hub")
        .map((n) => ({ id: n.id, label: n.label, kind: n.kind }));

      const meta = army[icon] || {};
      const doc = ICON_DOCS[icon] || {};
      const hasSvg = !!ICON_SVG[icon];
      const libsComplete =
        libraries.length > 0 && libraries.every((l) => l.docsComplete);
      const docComplete = !!(
        hasSvg &&
        (meta.call || doc.title) &&
        (doc.doc || meta.motto) &&
        libraries.length > 0 &&
        libsComplete
      );

      entries.push({
        icon,
        call: meta.call || doc.title || icon,
        role: meta.role || "unit",
        motto: meta.motto || "",
        documentation: {
          title: doc.title || meta.call || icon,
          body: doc.doc || meta.motto || "",
          seeAlso: doc.seeAlso || [],
          svg: hasSvg,
          svgPath: ICON_SVG[icon] || null,
        },
        appears: {
          nodes: nodes.length,
          edges: edges.length,
          kinds: [...new Set(nodes.map((n) => n.kind))],
          edgeKinds: [...new Set(edges.map((e) => e.kind))],
          formats: formatByIcon[icon] || [],
          nodeIds: nodes.slice(0, 40).map((n) => n.id),
        },
        libraries,
        libraryCount: libraries.length,
        directLibraryIds: directLibs,
        concepts,
        groups,
        docsComplete: docComplete,
      });
    });

    const missingDocs = entries.filter((e) => !e.docsComplete).map((e) => e.icon);
    const missingSvg = entries.filter((e) => !e.documentation.svg).map((e) => e.icon);
    const noLibraries = entries.filter((e) => e.libraryCount === 0).map((e) => e.icon);
    const incompleteLibs = (atlas.libraries || [])
      .map(libDoc)
      .filter((l) => !l.docsComplete)
      .map((l) => l.id);

    return {
      ok: missingDocs.length === 0 && incompleteLibs.length === 0,
      version: "1.0.0",
      generatedAt: new Date().toISOString(),
      iconCount: entries.length,
      libraryCount: (atlas.libraries || []).length,
      icons: entries,
      byIcon: Object.fromEntries(entries.map((e) => [e.icon, e])),
      coverage: {
        allIconsDocumented: missingDocs.length === 0,
        allIconsHaveSvg: missingSvg.length === 0,
        allIconsHaveLibraries: noLibraries.length === 0,
        allLibrariesDocumented: incompleteLibs.length === 0,
        missingDocs,
        missingSvg,
        noLibraries,
        incompleteLibs,
      },
      disclaimer:
        "Atlas giáo dục — mapping icon ↔ thư viện có URL/tài liệu. Không phải endorsement bảo mật tuyệt đối.",
    };
  }

  global.ICON_SVG = ICON_SVG;
  global.ICON_DOCS = ICON_DOCS;
  global.buildIconLibraryAtlas = buildIconLibraryAtlas;
  global.collectNetworkIcons = collectUsedIcons;
})(typeof window !== "undefined" ? window : globalThis);
