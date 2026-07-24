/**
 * bootstrap — facade MaMoLab (surface tách biệt)
 */
(function (global) {
  "use strict";

  const order = ["policy", "static", "indicators", "harden", "report", "sandbox"];

  function start() {
    order.forEach((name) => {
      const mod = global.MaMoLabModules?.[name];
      if (mod && typeof mod.start === "function") mod.start();
    });

    const facade = {
      version: "1.0.0",
      mode: "defensive-isolated",
      policy: global.MaMoLabModules.policy,
      analyze: (text, opts) => global.MaMoLabModules.sandbox.analyze(text, opts),
      static: (text, opts) => global.MaMoLabModules.static.analyze(text, opts),
      indicators: (text, opts) => global.MaMoLabModules.indicators.extract(text, opts),
      audit: () => global.MaMoLabModules.harden.audit(),
      wipe: () => global.MaMoLabModules.sandbox.wipe(),
      describe() {
        return {
          name: "MaMoLab",
          version: facade.version,
          mode: facade.mode,
          isolation: global.MaMoLabModules.policy.describe(),
          modules: Object.keys(global.MaMoLabModules || {}),
          guarantees: [
            "neverExecuteSample",
            "noNetworkOnAnalyze",
            "workerPreferred",
            "separateFromA11ySurface",
          ],
        };
      },
    };

    global.MaMoLab = facade;
    if (typeof console !== "undefined") {
      console.info(
        `[MaMoLab] ready v${facade.version} — isolated defensive lab (no sample execution)`
      );
    }
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", start);
    } else {
      start();
    }
  } else {
    start();
  }
})(typeof window !== "undefined" ? window : globalThis);
