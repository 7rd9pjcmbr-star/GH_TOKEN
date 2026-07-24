/**
 * paths — logic mọi đường dẫn tới thư viện mật mã
 */
(function (global) {
  "use strict";

  function getGraph() {
    if (global.__MAMO_GRAPH__) return global.__MAMO_GRAPH__;
    const atlas = global.CRYPTO_ATLAS;
    const net = global.NETWORK_MAP;
    if (atlas && net && global.buildCryptoNetwork) {
      global.__MAMO_GRAPH__ = global.buildCryptoNetwork(atlas, net);
      return global.__MAMO_GRAPH__;
    }
    return { nodes: [], edges: [] };
  }

  function resolveFrom(from) {
    const idx = global.MaMoLogicModules.index;
    const S = global.MaMoLogicModules.schema;
    if (!from) return "hub:crypto-libs";
    if (String(from).includes(":")) return from;
    const hit = idx?.resolve(from);
    if (hit) return hit.ref;
    // try prefixes
    for (const kind of ["concept", "lib", "group", "lang"]) {
      const r = S.ref(kind, from);
      const g = getGraph();
      if (g.nodes.some((n) => n.id === r)) return r;
    }
    return from;
  }

  const Paths = {
    graph() {
      return getGraph();
    },

    rebuild() {
      global.__MAMO_GRAPH__ = null;
      global.MaMoLogicModules?.optimize?.clearMemos?.();
      return getGraph();
    },

    /** Mọi đường ngắn nhất từ `from` tới từng thư viện (có memo) */
    toLibraries(from) {
      const start = resolveFrom(from);
      const O = global.MaMoLogicModules?.optimize;
      const build = () => {
        const g = getGraph();
        if (typeof global.pathsToLibraries === "function") {
          return global.pathsToLibraries(g, start);
        }
        // fallback BFS inline
        const libs = g.nodes.filter((n) => n.kind === "library").map((n) => n.id);
        const adj = new Map();
        g.edges.forEach((e) => {
          if (!adj.has(e.source)) adj.set(e.source, []);
          if (!adj.has(e.target)) adj.set(e.target, []);
          adj.get(e.source).push({ to: e.target, edge: e });
          adj.get(e.target).push({ to: e.source, edge: e });
        });
        const out = [];
        libs.forEach((goal) => {
          const queue = [start];
          const prev = new Map([[start, null]]);
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
          out.push({
            to: goal,
            label: g.nodes.find((n) => n.id === goal)?.label || goal,
            nodes,
            edges,
            length: edges.length,
            icons: edges.map(
              (e) => e.icon || g.nodes.find((n) => n.id === e.target)?.icon || "cube"
            ),
            iconFlow: nodes.map((id) => {
              const n = g.nodes.find((x) => x.id === id);
              return { id, icon: n?.icon || "cube", kind: n?.kind, label: n?.label };
            }),
          });
        });
        return out.sort((a, b) => a.length - b.length || a.label.localeCompare(b.label));
      };
      if (O?.memoPaths) return O.memoPaths(start, build);
      return build();
    },

    /** Tập cạnh nằm trên mọi đường tới thư viện (từ hub mặc định) */
    allLibraryRouteEdges(from = "hub:crypto-libs") {
      const paths = Paths.toLibraries(from);
      const edgeIds = new Set();
      const nodeIds = new Set();
      paths.forEach((p) => {
        p.nodes.forEach((n) => nodeIds.add(n));
        p.edges.forEach((e) => edgeIds.add(e.id));
      });
      return { edgeIds: [...edgeIds], nodeIds: [...nodeIds], pathCount: paths.length };
    },

    stats() {
      const g = getGraph();
      return {
        nodes: g.nodes.length,
        edges: g.edges.length,
        libraries: g.nodes.filter((n) => n.kind === "library").length,
      };
    },

    start() {
      Paths.rebuild();
      global.MaMoLogicModules?.optimize?.clearMemos?.();
    },
  };

  global.MaMoLogicModules.paths = Paths;
})(window);
