/**
 * schema — hợp đồng dữ liệu & chuẩn hoá id (logic layer)
 */
(function (global) {
  "use strict";

  const KINDS = ["concept", "lib", "group", "lang", "hub", "taxonomy"];

  function normalize(s) {
    return String(s || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .trim();
  }

  function ref(kind, id) {
    if (!id) return null;
    const s = String(id);
    if (s.includes(":")) return s;
    return `${kind}:${s}`;
  }

  function parseRef(value) {
    const s = String(value || "");
    const i = s.indexOf(":");
    if (i < 0) return { kind: null, id: s, raw: s };
    return { kind: s.slice(0, i), id: s.slice(i + 1), raw: s };
  }

  function isRef(value) {
    const { kind } = parseRef(value);
    return KINDS.includes(kind);
  }

  /** Validate Decision object shape */
  function decision(partial) {
    return {
      ok: partial.ok !== false,
      action: partial.action || "none",
      reason: partial.reason || "",
      ruleId: partial.ruleId || null,
      intent: partial.intent || "unknown",
      results: partial.results || [],
      paths: partial.paths || [],
      meta: partial.meta || {},
    };
  }

  const Schema = {
    KINDS,
    normalize,
    ref,
    parseRef,
    isRef,
    decision,
    start() {},
  };

  global.MaMoLogicModules = global.MaMoLogicModules || {};
  global.MaMoLogicModules.schema = Schema;
})(window);
