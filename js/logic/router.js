/**
 * router — ủy quyền cho pipeline (trật tự + ownership)
 * Không tự chạy song song rules/search nữa → tránh dẫm chân.
 */
(function (global) {
  "use strict";

  const Router = {
    handle(input) {
      const pipeline = global.MaMoLogicModules.pipeline;
      const cfg = global.MaMoLogicModules.config;
      if (!pipeline) {
        return global.MaMoLogicModules.schema.decision({
          ok: false,
          action: "error",
          reason: "Pipeline chưa sẵn sàng",
        });
      }
      if (cfg && !cfg.isEnabled("router")) {
        return global.MaMoLogicModules.schema.decision({
          ok: false,
          action: "disabled",
          reason: "Router đang tắt trong config",
        });
      }
      return pipeline.run(input);
    },

    start() {},
  };

  global.MaMoLogicModules.router = Router;
})(window);
