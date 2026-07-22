/**
 * Bootstrap — nạp thứ tự module đã được include bằng script tags.
 * File này gọi core.boot() sau khi mọi module đã register.
 */
(function (global) {
  "use strict";

  function boot() {
    const core = global.MaMoA11y?.core;
    if (!core) {
      console.error("MaMoA11y.core missing");
      return;
    }
    core.boot({ surface: "assist" });
    const list = core.list();
    console.info(`[Mã Mở A11y] ready — modules: ${list.join(", ")}`);
    const badge = document.getElementById("a11y-arch-badge");
    if (badge) {
      badge.textContent = `${list.length} module · v${core.version}`;
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})(window);
