/**
 * vars — tra cứu biến nhúng + chỉ thị nginx upstream
 * Owns: embedded-vars, upstream-var-lookup (enrichment)
 */
(function (global) {
  "use strict";

  function catalog() {
    return global.NGINX_UPSTREAM_VARS || null;
  }

  function normalize(s) {
    return String(s || "")
      .trim()
      .toLowerCase()
      .replace(/^\$/, "")
      .replace(/-/g, "_");
  }

  function allVars() {
    return (catalog()?.variables || []).map((v) => ({
      ...v,
      kind: v.kind || "variable",
    }));
  }

  function allDirectives() {
    return (catalog()?.directives || []).map((d) => ({
      ...d,
      kind: "directive",
    }));
  }

  function allEntries() {
    return [...allVars(), ...allDirectives()];
  }

  function withIcons(list) {
    const cats = catalog()?.categories || [];
    const iconByCat = Object.fromEntries(cats.map((c) => [c.id, c.icon]));
    const army = global.NETWORK_MAP?.iconArmy || {};
    return (list || []).map((v) => {
      const icon = v.icon || iconByCat[v.category] || "network";
      const meta = army[icon] || {};
      return {
        ...v,
        icon,
        iconCall: meta.call || icon,
        iconMotto: meta.motto || "",
      };
    });
  }

  function getVar(nameOrId) {
    const n = normalize(nameOrId);
    if (!n) return null;
    return (
      allVars().find((v) => {
        const id = normalize(v.id);
        const name = normalize(v.name);
        if (id === n || name === n || name === `$${n}`) return true;
        if (v.dynamic && n.startsWith(id)) return true;
        return false;
      }) || null
    );
  }

  function getDirective(nameOrId) {
    const n = normalize(nameOrId);
    if (!n) return null;
    // "resolver address..." → lấy token đầu
    const head = n.split(/[\s;]+/)[0];
    return (
      allDirectives().find((d) => normalize(d.id) === head || normalize(d.name) === head) ||
      null
    );
  }

  function get(nameOrId) {
    return getDirective(nameOrId) || getVar(nameOrId);
  }

  function scoreEntry(entry, query) {
    const blob = normalize(
      [
        entry.name,
        entry.id,
        entry.summary,
        entry.category,
        entry.syntax || "",
        entry.context ? entry.context.join(" ") : "",
        ...(entry.details || []),
        ...(entry.examples || []),
        entry.logUse || "",
        entry.security || "",
        entry.commercialHistory || "",
        ...(entry.enum || []),
        ...(entry.parameters || []).map((p) => `${p.name} ${p.summary}`),
      ].join(" ")
    );
    let score = 0;
    const id = normalize(entry.id);
    const name = normalize(entry.name);
    if (name === query || id === query || name === `$${query}`) score += 100;
    if (name.includes(query) || id.includes(query)) score += 40;
    if (blob.includes(query)) score += 10;
    if (entry.category === query) score += 15;
    if (entry.kind === "directive" && query.includes("resolver") && id === "resolver") {
      score += 50;
    }
    return score;
  }

  function search(q, opts = {}) {
    const query = normalize(q);
    const limit = opts.limit || 20;
    const kind = opts.kind; // variable | directive | undefined
    let pool = allEntries();
    if (kind === "variable") pool = allVars();
    if (kind === "directive") pool = allDirectives();
    if (!query) return pool.slice(0, limit);
    return pool
      .map((e) => ({ ...e, score: scoreEntry(e, query) }))
      .filter((e) => e.score > 0)
      .sort((a, b) => b.score - a.score || a.name.localeCompare(b.name))
      .slice(0, limit);
  }

  function describe() {
    const c = catalog();
    if (!c) return { ok: false, error: "NGINX_UPSTREAM_VARS missing" };
    const vars = allVars();
    const dirs = allDirectives();
    return {
      ok: true,
      module: c.meta.module,
      title: c.meta.title,
      variableCount: vars.length,
      directiveCount: dirs.length,
      count: vars.length + dirs.length,
      commercial: [
        ...vars.filter((v) => v.commercial).map((v) => v.name),
        ...dirs
          .filter((d) => d.commercial)
          .map((d) => d.name),
        ...dirs.flatMap((d) =>
          (d.parameters || [])
            .filter((p) => p.commercial)
            .map((p) => `${d.name}.${p.name}`)
        ),
      ],
      openSource: vars.filter((v) => !v.commercial).map((v) => v.name),
      directives: dirs.map((d) => ({
        name: d.name,
        since: d.since,
        context: d.context,
        commercial: d.commercial,
      })),
      categories: c.categories,
      separatorNote: c.meta.separatorNote,
      logFormatExample: c.logFormatExample,
    };
  }

  const Vars = {
    all: () => withIcons(allVars()),
    allDirectives: () => withIcons(allDirectives()),
    allEntries: () => withIcons(allEntries()),
    get(name) {
      const v = get(name);
      return v ? withIcons([v])[0] : null;
    },
    getDirective(name) {
      const d = getDirective(name);
      return d ? withIcons([d])[0] : null;
    },
    search(q, opts) {
      return withIcons(search(q, opts));
    },
    byCategory(cat) {
      return withIcons(allEntries().filter((e) => e.category === cat));
    },
    describe,
    logFormat() {
      return catalog()?.logFormatExample || null;
    },
    start() {},
  };

  global.MaMoLogicModules = global.MaMoLogicModules || {};
  global.MaMoLogicModules.vars = Vars;
})(typeof window !== "undefined" ? window : globalThis);
