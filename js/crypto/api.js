/**
 * API — facade tích hợp tra cứu (module 9/9)
 * window.MaMoCrypto.* dành cho ứng dụng / console / Atlas.
 */
(function (global) {
  "use strict";

  function mod(name) {
    return global.MaMoCryptoCore.get(name);
  }

  const Api = {
    version: "1.0.0",

    /** Tra cứu thông minh đa loại */
    lookup(query, opts) {
      if (!query) return { query: "", results: [] };
      const exactConcept = mod("concepts")?.get(query);
      const exactLib = mod("libraries")?.get(query);
      const results = mod("search")?.query(query, opts) || [];
      return {
        query,
        exact: exactConcept || exactLib || null,
        results,
        neighbors: exactConcept || exactLib
          ? mod("graph")?.neighbors(query) || []
          : [],
      };
    },

    search(query, opts) {
      return mod("search")?.query(query, opts) || [];
    },

    suggest(prefix, limit) {
      return mod("search")?.suggest(prefix, limit) || [];
    },

    getConcept(id) {
      return mod("concepts")?.get(id) || null;
    },

    getLibrary(id) {
      return mod("libraries")?.get(id) || null;
    },

    listConcepts(filter) {
      return mod("concepts")?.list(filter) || [];
    },

    listLibraries(filter) {
      return mod("libraries")?.list(filter) || [];
    },

    languages() {
      return mod("libraries")?.languages() || [];
    },

    taxonomy() {
      return mod("concepts")?.taxonomy() || [];
    },

    recommend(needOrOpts) {
      return mod("recommend")?.forNeed(needOrOpts) || null;
    },

    related(id) {
      const conceptR = mod("concepts")?.related(id) || [];
      const graphN = mod("graph")?.neighbors(id) || [];
      return { concepts: conceptR, graph: graphN };
    },

    path(fromId, toId) {
      return mod("graph")?.path(fromId, toId) || [];
    },

    encode: {
      toMorse: (t) => mod("encode")?.toMorse(t),
      fromMorse: (t) => mod("encode")?.fromMorse(t),
      toBraille: (t) => mod("encode")?.toBraille(t),
      fromBraille: (t) => mod("encode")?.fromBraille(t),
      toBase64: (t) => mod("encode")?.toBase64(t),
      fromBase64: (t) => mod("encode")?.fromBase64(t),
      explain: (k) => mod("encode")?.explain(k),
    },

    cheatSheet() {
      return mod("recommend")?.cheatSheet() || { do: [], dont: [] };
    },

    stats() {
      return {
        api: Api.version,
        core: global.MaMoCryptoCore.version,
        modules: global.MaMoCryptoCore.list(),
        catalog: mod("catalog")?.stats() || {},
        graph: mod("graph")?.stats() || {},
      };
    },

    /** OpenAPI-like mô tả để tích hợp */
    describe() {
      return {
        name: "MaMoCrypto",
        version: Api.version,
        endpoints: [
          { name: "lookup", args: ["query", "opts?"], desc: "Tra cứu thông minh + neighbors" },
          { name: "search", args: ["query", "opts?"], desc: "Tìm kiếm xếp hạng" },
          { name: "suggest", args: ["prefix", "limit?"], desc: "Gợi ý autocomplete" },
          { name: "getConcept", args: ["id"], desc: "Chi tiết khái niệm" },
          { name: "getLibrary", args: ["id"], desc: "Chi tiết thư viện" },
          { name: "listLibraries", args: ["filter?"], desc: "Lọc thư viện theo ngôn ngữ/tier" },
          { name: "recommend", args: ["need|opts"], desc: "Gợi ý theo nhu cầu" },
          { name: "related", args: ["id"], desc: "Quan hệ khái niệm/graph" },
          { name: "path", args: ["from", "to"], desc: "Đường đi trên network" },
          { name: "encode.*", args: ["text"], desc: "Morse/Braille/Base64 (không bảo mật)" },
          { name: "stats", args: [], desc: "Thống kê module/catalog" },
        ],
      };
    },

    start() {
      // Expose stable facade
      const facade = {
        version: Api.version,
        lookup: Api.lookup,
        search: Api.search,
        suggest: Api.suggest,
        getConcept: Api.getConcept,
        getLibrary: Api.getLibrary,
        listConcepts: Api.listConcepts,
        listLibraries: Api.listLibraries,
        languages: Api.languages,
        taxonomy: Api.taxonomy,
        recommend: Api.recommend,
        related: Api.related,
        path: Api.path,
        encode: Api.encode,
        cheatSheet: Api.cheatSheet,
        stats: Api.stats,
        describe: Api.describe,
        core: global.MaMoCryptoCore,
        modules: () => global.MaMoCryptoCore.list(),
      };
      global.MaMoCrypto = Object.assign(global.MaMoCrypto || {}, facade);
      global.MaMoCryptoCore.emit("api:ready", Api.describe());
    },
  };

  global.MaMoCryptoCore.register("api", Api);
})(window);
