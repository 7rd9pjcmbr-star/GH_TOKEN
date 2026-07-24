/**
 * Bootstrap MaMoCrypto — gọi sau khi đã load data + 9 module.
 */
(function (global) {
  "use strict";

  function boot() {
    const core = global.MaMoCryptoCore;
    if (!core) {
      console.error("MaMoCryptoCore missing");
      return;
    }
    if (!global.CRYPTO_ATLAS) {
      console.warn("CRYPTO_ATLAS missing — catalog sẽ trống");
    }
    core.boot({ surface: "crypto-lib" });
    const stats = global.MaMoCrypto?.stats?.();
    console.info(
      `[MaMoCrypto] ready v${global.MaMoCrypto?.version} — modules: ${(stats?.modules || []).join(", ")}`
    );
    const badge = document.getElementById("crypto-api-badge");
    if (badge && stats) {
      badge.textContent = `API v${stats.api} · ${stats.modules.length} module · ${stats.catalog.concepts || 0} khái niệm · ${stats.catalog.libraries || 0} thư viện`;
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})(window);
