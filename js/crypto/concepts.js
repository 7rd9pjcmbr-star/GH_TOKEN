/**
 * Concepts — API tra cứu khái niệm mật mã (module 4/9)
 */
(function (global) {
  "use strict";

  const Concepts = {
    list(filter = {}) {
      const cat = global.MaMoCryptoCore.get("catalog");
      let items = cat?.concepts || [];
      if (filter.category) {
        items = items.filter((c) => c.category === filter.category);
      }
      if (filter.level) {
        items = items.filter((c) => c.level === filter.level);
      }
      return items.map(publicConcept);
    },

    get(id) {
      const c = global.MaMoCryptoCore.get("catalog")?.getConcept(id);
      return c ? publicConcept(c) : null;
    },

    related(id) {
      const c = global.MaMoCryptoCore.get("catalog")?.getConcept(id);
      if (!c) return [];
      return (c.related || [])
        .map((rid) => Concepts.get(rid))
        .filter(Boolean);
    },

    byCategory(categoryId) {
      return Concepts.list({ category: categoryId });
    },

    taxonomy() {
      return global.MaMoCryptoCore.get("catalog")?.taxonomy || [];
    },

    start() {},
  };

  function publicConcept(c) {
    return {
      id: c.id,
      kind: "concept",
      name: c.name,
      category: c.category,
      level: c.level,
      summary: c.summary,
      details: c.details || [],
      related: c.related || [],
    };
  }

  global.MaMoCryptoCore.register("concepts", Concepts);
})(window);
