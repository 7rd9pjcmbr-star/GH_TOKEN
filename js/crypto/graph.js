/**
 * Graph — quan hệ network khái niệm ↔ thư viện (module 7/9)
 */
(function (global) {
  "use strict";

  let cached = null;

  const Graph = {
    build() {
      const atlas = global.CRYPTO_ATLAS;
      const net = global.NETWORK_MAP;
      if (!atlas || !net || !global.buildCryptoNetwork) {
        return { nodes: [], edges: [] };
      }
      cached = global.buildCryptoNetwork(atlas, net);
      return cached;
    },

    get() {
      return cached || Graph.build();
    },

    neighbors(ref) {
      // ref: concept:id | lib:id | raw id
      const g = Graph.get();
      let id = ref;
      if (!ref.includes(":")) {
        if (global.MaMoCryptoCore.get("catalog")?.getConcept(ref)) {
          id = `concept:${ref}`;
        } else if (global.MaMoCryptoCore.get("catalog")?.getLibrary(ref)) {
          id = `lib:${ref}`;
        }
      }
      return g.edges
        .filter((e) => e.source === id || e.target === id)
        .map((e) => {
          const other = e.source === id ? e.target : e.source;
          const node = g.nodes.find((n) => n.id === other);
          return {
            id: other,
            label: node?.label || other,
            kind: node?.kind,
            relation: e.label || e.kind,
          };
        });
    },

    path(fromId, toId) {
      const g = Graph.get();
      const resolve = (x) => {
        if (x.includes(":")) return x;
        if (global.MaMoCryptoCore.get("catalog")?.getConcept(x)) return `concept:${x}`;
        if (global.MaMoCryptoCore.get("catalog")?.getLibrary(x)) return `lib:${x}`;
        return x;
      };
      const start = resolve(fromId);
      const goal = resolve(toId);
      const adj = new Map();
      g.edges.forEach((e) => {
        if (!adj.has(e.source)) adj.set(e.source, []);
        if (!adj.has(e.target)) adj.set(e.target, []);
        adj.get(e.source).push(e.target);
        adj.get(e.target).push(e.source);
      });
      const queue = [start];
      const prev = new Map([[start, null]]);
      while (queue.length) {
        const cur = queue.shift();
        if (cur === goal) break;
        (adj.get(cur) || []).forEach((n) => {
          if (!prev.has(n)) {
            prev.set(n, cur);
            queue.push(n);
          }
        });
      }
      if (!prev.has(goal)) return [];
      const path = [];
      for (let at = goal; at; at = prev.get(at)) path.push(at);
      return path.reverse();
    },

    stats() {
      const g = Graph.get();
      return { nodes: g.nodes.length, edges: g.edges.length };
    },

    start() {
      Graph.build();
    },
  };

  global.MaMoCryptoCore.register("graph", Graph);
})(window);
