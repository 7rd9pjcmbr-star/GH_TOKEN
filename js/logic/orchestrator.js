/**
 * orchestrator — facade MaMoLogic + cấu hình nâng cao
 */
(function (global) {
  "use strict";

  function M(name) {
    return global.MaMoLogicModules?.[name];
  }

  const Orchestrator = {
    version: "1.2.0",

    query(input) {
      return M("router").handle(input);
    },

    decide(input) {
      return M("router").handle(input);
    },

    resolve(idOrName) {
      return M("index").resolve(idOrName);
    },

    pathsToLibraries(from) {
      return M("paths").toLibraries(from);
    },

    libraryRouteMap(from) {
      return M("paths").allLibraryRouteEdges(from);
    },

    rules() {
      return M("rules").all();
    },

    classify(text) {
      return M("rules").classifyIntent(text);
    },

    /** Phân tích sâu định dạng mã (cấu trúc duy nhất từng loại) + gọi icon */
    analyze(input, opts) {
      const report = M("analyze")?.analyze(input, opts) || null;
      if (!report) return null;
      if (opts?.skipIcons) return report;
      const iconCall = M("icons")?.callForFormat?.(report) || null;
      return {
        ...report,
        icons: iconCall,
        feedback: iconCall?.feedback || null,
      };
    },

    classifyFormat(input) {
      return M("analyze")?.classify(input) || null;
    },

    /** Mapper gọi tên quân đội icon trên dòng chảy */
    callIcons(input, opts) {
      return M("icons")?.call(input, opts) || null;
    },

    iconArmy() {
      return M("icons")?.army?.() || [];
    },

    /** Mapping đầy đủ icon → thư viện có tài liệu (không bỏ sót) */
    mapIconLibraries() {
      return M("icons")?.mapAllLibraries?.() || null;
    },

    iconDocs(name) {
      return M("icons")?.docsFor?.(name) || null;
    },

    iconCoverage() {
      return M("icons")?.coverage?.() || null;
    },

    /** Biến nhúng nginx upstream */
    vars: {
      all: () => M("vars")?.all?.() || [],
      allDirectives: () => M("vars")?.allDirectives?.() || [],
      get: (name) => M("vars")?.get?.(name) || null,
      getDirective: (name) => M("vars")?.getDirective?.(name) || null,
      search: (q, opts) => M("vars")?.search?.(q, opts) || [],
      describe: () => M("vars")?.describe?.() || null,
      logFormat: () => M("vars")?.logFormat?.() || null,
    },

    /** Tối ưu nâng cao */
    optimize: {
      stats: () => M("optimize")?.stats?.() || null,
      invalidate: () => M("optimize")?.invalidate?.() || null,
      planPreview: (text) =>
        M("optimize")?.plan?.({ text: String(text || ""), intent: M("rules")?.classifyIntent?.(text) }) ||
        null,
    },

    /** —— Config nâng cao —— */
    config: {
      get: () => M("config").get(),
      conflicts: () => M("config").detectConflicts(),
      setEnabled: (mod, on) => M("config").setModuleEnabled(mod, on),
      setFeature: (mod, feat, on) => M("config").setFeature(mod, feat, on),
      setConflictPolicy: (p) => M("config").setConflictPolicy(p),
      reset: () => M("config").reset(),
      ordered: () => M("config").orderedModules(),
    },

    stats() {
      return {
        logic: Orchestrator.version,
        index: M("index")?.stats(),
        paths: M("paths")?.stats(),
        optimize: M("optimize")?.stats?.() || null,
        config: {
          conflicts: M("config")?.detectConflicts(),
          policy: M("config")?.get()?.conflictPolicy,
          pipeline: M("config")?.get()?.pipeline,
        },
        modules: Object.keys(global.MaMoLogicModules || {}),
        crypto: global.MaMoCrypto?.stats?.() || null,
      };
    },

    describe() {
      return {
        name: "MaMoLogic",
        version: Orchestrator.version,
        layers: ["UI", "Logic", "Domain(crypto|a11y)", "Data"],
        conflictPolicy: M("config")?.get()?.conflictPolicy,
        pipeline: M("config")?.get()?.pipeline,
        ownership: M("config")?.detectConflicts()?.domainOwners,
        flow: [
          "validate(+opt plan) → analyze? → resolve → classify(+refine plan) → rules → search → paths? → icons? → finalize(rank/dedupe)",
          "optimize: soft-screen (sàng lọc, không loại bỏ) · LRU · path memo · rank",
          "icons: mapper gọi tên quân đội icon trên dòng chảy dữ liệu",
          "first-wins trên domain owns → không dẫm chân",
        ],
        api: [
          "query / decide",
          "analyze / classifyFormat",
          "callIcons / mapIconLibraries / iconCoverage",
          "vars.get / vars.search / vars.describe",
          "optimize.stats / optimize.invalidate / optimize.planPreview",
          "resolve / pathsToLibraries",
          "config.* / stats / describe",
        ],
        formatCatalog: M("analyze")?.formats?.length || 0,
        iconArmySize: M("icons")?.army?.()?.length || 0,
        upstreamVars: M("vars")?.describe?.() || null,
        optimize: M("optimize")?.stats?.() || null,
      };
    },

    start() {
      const facade = {
        version: Orchestrator.version,
        query: Orchestrator.query,
        decide: Orchestrator.decide,
        resolve: Orchestrator.resolve,
        pathsToLibraries: Orchestrator.pathsToLibraries,
        libraryRouteMap: Orchestrator.libraryRouteMap,
        rules: Orchestrator.rules,
        classify: Orchestrator.classify,
        analyze: Orchestrator.analyze,
        classifyFormat: Orchestrator.classifyFormat,
        callIcons: Orchestrator.callIcons,
        iconArmy: Orchestrator.iconArmy,
        mapIconLibraries: Orchestrator.mapIconLibraries,
        iconDocs: Orchestrator.iconDocs,
        iconCoverage: Orchestrator.iconCoverage,
        vars: Orchestrator.vars,
        optimize: Orchestrator.optimize,
        config: Orchestrator.config,
        stats: Orchestrator.stats,
        describe: Orchestrator.describe,
        modules: global.MaMoLogicModules,
      };
      global.MaMoLogic = facade;
    },
  };

  global.MaMoLogicModules.orchestrator = Orchestrator;
})(window);
