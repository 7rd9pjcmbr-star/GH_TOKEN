/**
 * bootstrap logic kernel
 * Order: config → schema → index → rules → paths → pipeline → router → orchestrator
 */
(function (global) {
  "use strict";

  function boot() {
    const mods = global.MaMoLogicModules;
    if (!mods?.schema && !mods?.config) {
      console.error("MaMoLogic modules missing");
      return;
    }
    const order = [
      "config",
      "schema",
      "optimize",
      "analyze",
      "index",
      "rules",
      "vars",
      "paths",
      "icons",
      "pipeline",
      "router",
      "orchestrator",
    ];
    order.forEach((name) => {
      const m = mods[name];
      if (m && typeof m.start === "function") {
        try {
          m.start();
        } catch (err) {
          console.error(`[MaMoLogic] start ${name}`, err);
        }
      }
    });
    console.info(
      `[MaMoLogic] ready v${global.MaMoLogic?.version} —`,
      Object.keys(mods).join(", ")
    );
    const badge = document.getElementById("logic-badge");
    if (badge && global.MaMoLogic) {
      const s = global.MaMoLogic.stats();
      const ok = s.config?.conflicts?.ok ? "no-conflict" : "conflict!";
      badge.textContent = `Logic v${s.logic} · ${ok} · index ${s.index?.total || 0} · policy ${s.config?.policy}`;
    }
    const out = document.getElementById("logic-demo-output");
    if (out && global.MaMoLogic) {
      out.textContent = JSON.stringify(global.MaMoLogic.describe(), null, 2);
    }
    // paint config panel if present
    if (typeof global.__mamoRenderLogicConfig === "function") {
      global.__mamoRenderLogicConfig();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})(window);
