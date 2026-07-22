/**
 * Libraries — API bản đồ thư viện mật mã (module 5/9)
 */
(function (global) {
  "use strict";

  const Libraries = {
    list(filter = {}) {
      const cat = global.MaMoCryptoCore.get("catalog");
      let items = cat?.libraries || [];
      const norm = cat?.normalize || ((s) => String(s || "").toLowerCase());
      if (filter.language) {
        const lang = norm(filter.language);
        items = items.filter((l) =>
          (l.languages || []).some((x) => norm(x).includes(lang))
        );
      }
      if (filter.category) {
        items = items.filter((l) => l.category === filter.category);
      }
      if (filter.tier) {
        items = items.filter((l) => l.tier === filter.tier);
      }
      if (filter.provides) {
        const p = norm(filter.provides);
        items = items.filter((l) =>
          (l.provides || []).some((x) => norm(x).includes(p))
        );
      }
      return items.map(publicLib);
    },

    get(id) {
      const lib = global.MaMoCryptoCore.get("catalog")?.getLibrary(id);
      return lib ? publicLib(lib) : null;
    },

    languages() {
      const set = new Set();
      (global.MaMoCryptoCore.get("catalog")?.libraries || []).forEach((l) => {
        (l.languages || []).forEach((x) => set.add(x));
      });
      return [...set].sort((a, b) => a.localeCompare(b));
    },

    recommendForLanguage(language) {
      return Libraries.list({ language, tier: "khuyến nghị" });
    },

    start() {},
  };

  function publicLib(l) {
    return {
      id: l.id,
      kind: "library",
      name: l.name,
      category: l.category,
      tier: l.tier,
      summary: l.summary,
      languages: l.languages || [],
      bindings: l.bindings || [],
      provides: l.provides || [],
      url: l.url,
      notes: l.notes,
    };
  }

  global.MaMoCryptoCore.register("libraries", Libraries);
})(window);
