/**
 * Catalog — nạp atlas + chuẩn hoá chỉ mục (module 2/9)
 */
(function (global) {
  "use strict";

  function normalize(s) {
    return String(s || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
  }

  const Catalog = {
    raw: null,
    concepts: [],
    libraries: [],
    taxonomy: [],
    guide: [],
    byId: new Map(),

    normalize,

    start() {
      const atlas = global.CRYPTO_ATLAS;
      if (!atlas) {
        console.warn("[MaMoCrypto.catalog] CRYPTO_ATLAS missing");
        return;
      }
      Catalog.raw = atlas;
      Catalog.taxonomy = atlas.taxonomy || [];
      Catalog.concepts = (atlas.concepts || []).map((c) => ({
        ...c,
        kind: "concept",
        _norm: normalize(
          [c.id, c.name, c.summary, c.level, c.category, ...(c.details || []), ...(c.related || [])].join(" ")
        ),
      }));
      Catalog.libraries = (atlas.libraries || []).map((lib) => ({
        ...lib,
        kind: "library",
        _norm: normalize(
          [
            lib.id,
            lib.name,
            lib.summary,
            lib.tier,
            lib.category,
            lib.notes || "",
            ...(lib.languages || []),
            ...(lib.bindings || []),
            ...(lib.provides || []),
          ].join(" ")
        ),
      }));
      Catalog.guide = atlas.decisionGuide || [];
      Catalog.byId.clear();
      Catalog.concepts.forEach((c) => Catalog.byId.set(`concept:${c.id}`, c));
      Catalog.libraries.forEach((l) => Catalog.byId.set(`lib:${l.id}`, l));
      Catalog.taxonomy.forEach((t) => Catalog.byId.set(`tax:${t.id}`, { ...t, kind: "taxonomy" }));
      global.MaMoCryptoCore.emit("catalog:ready", {
        concepts: Catalog.concepts.length,
        libraries: Catalog.libraries.length,
      });
    },

    getConcept(id) {
      return Catalog.byId.get(`concept:${id}`) || null;
    },

    getLibrary(id) {
      return Catalog.byId.get(`lib:${id}`) || null;
    },

    getTaxonomy(id) {
      return Catalog.byId.get(`tax:${id}`) || null;
    },

    stats() {
      return {
        concepts: Catalog.concepts.length,
        libraries: Catalog.libraries.length,
        taxonomy: Catalog.taxonomy.length,
        guide: Catalog.guide.length,
      };
    },
  };

  global.MaMoCryptoCore.register("catalog", Catalog);
})(window);
