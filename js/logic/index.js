/**
 * index — chỉ mục thống nhất (concept/lib/group/lang/hub)
 * Logic: resolve tên/id → ref chuẩn + entity.
 */
(function (global) {
  "use strict";

  const state = {
    byRef: new Map(),
    byNormName: new Map(),
    ready: false,
  };

  function schema() {
    return global.MaMoLogicModules.schema;
  }

  function put(ref, entity) {
    state.byRef.set(ref, entity);
    const n = schema().normalize(entity.name || entity.label || entity.id);
    if (!n) return;
    if (!state.byNormName.has(n)) state.byNormName.set(n, []);
    state.byNormName.get(n).push(ref);
  }

  const Index = {
    rebuild() {
      state.byRef.clear();
      state.byNormName.clear();
      const atlas = global.CRYPTO_ATLAS;
      const S = schema();
      if (!atlas) return;

      (atlas.taxonomy || []).forEach((t) => {
        put(S.ref("group", t.id), {
          kind: "group",
          id: t.id,
          name: t.name,
          summary: t.summary,
        });
      });
      (atlas.concepts || []).forEach((c) => {
        put(S.ref("concept", c.id), {
          kind: "concept",
          id: c.id,
          name: c.name,
          summary: c.summary,
          category: c.category,
          level: c.level,
          details: c.details,
          related: c.related,
        });
      });
      (atlas.libraries || []).forEach((l) => {
        put(S.ref("lib", l.id), {
          kind: "lib",
          id: l.id,
          name: l.name,
          summary: l.summary,
          category: l.category,
          tier: l.tier,
          languages: l.languages,
          provides: l.provides,
          url: l.url,
        });
      });

      // langs from libraries
      const langs = new Set();
      (atlas.libraries || []).forEach((l) =>
        (l.languages || []).forEach((x) => langs.add(x))
      );
      langs.forEach((lang) => {
        put(S.ref("lang", lang), {
          kind: "lang",
          id: lang,
          name: lang,
          summary: `Ngôn ngữ ${lang}`,
        });
      });

      put("hub:crypto-libs", {
        kind: "hub",
        id: "crypto-libs",
        name: "Thư viện mật mã",
        summary: "Hub trung tâm mọi đường tới thư viện",
      });

      state.ready = true;
    },

    get(refOrId) {
      if (!refOrId) return null;
      if (state.byRef.has(refOrId)) return state.byRef.get(refOrId);
      const S = schema();
      // try common kinds
      for (const kind of ["lib", "concept", "group", "lang"]) {
        const r = S.ref(kind, refOrId);
        if (state.byRef.has(r)) return state.byRef.get(r);
      }
      // by name
      const n = S.normalize(refOrId);
      const refs = state.byNormName.get(n) || [];
      if (refs.length) return state.byRef.get(refs[0]);
      return null;
    },

    resolve(refOrId) {
      const entity = Index.get(refOrId);
      if (!entity) return null;
      const kind = entity.kind === "lib" ? "lib" : entity.kind;
      return {
        ref: schema().ref(kind === "lib" ? "lib" : kind, entity.id),
        entity,
      };
    },

    list(kind) {
      return [...state.byRef.values()].filter((e) => !kind || e.kind === kind);
    },

    stats() {
      const counts = {};
      state.byRef.forEach((e) => {
        counts[e.kind] = (counts[e.kind] || 0) + 1;
      });
      return { ready: state.ready, total: state.byRef.size, counts };
    },

    start() {
      Index.rebuild();
    },
  };

  global.MaMoLogicModules.index = Index;
})(window);
