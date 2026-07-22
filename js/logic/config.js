/**
 * config — cấu hình tính năng nâng cao theo module
 * Mỗi module: priority, owns (độc quyền), features, enabled.
 * Policy: first-wins trên cùng domain → không dẫm chân nhau.
 */
(function (global) {
  "use strict";

  const STORAGE_KEY = "mamo.logic.config.v1";

  /** Cấu hình mặc định — trật tự priority tăng dần = chạy sau */
  const DEFAULTS = {
    version: 1,
    conflictPolicy: "first-wins", // domain đã claim thì module sau bỏ qua
    enrichmentPolicy: "attach-only", // paths/search chỉ gắn thêm, không ghi đè owner
      pipeline: [
      "validate",
      "analyze",
      "resolve",
      "classify",
      "rules",
      "search",
      "paths",
      "icons",
      "finalize",
    ],
    modules: {
      schema: {
        enabled: true,
        priority: 5,
        label: "Schema",
        owns: ["contracts", "normalize"],
        features: {
          validateDecision: true,
          normalizeIds: true,
        },
      },
      analyze: {
        enabled: true,
        priority: 8,
        label: "Format analyze",
        owns: ["format-detect", "format-classify"],
        features: {
          deepStructuralDetect: true,
          multiCandidateDiscriminate: true,
          attachToDecision: true,
          // không chiếm resolve/recommend
          neverOverridePrimary: true,
        },
      },
      lab: {
        enabled: true,
        priority: 70,
        label: "Security lab (isolated)",
        owns: ["malware-static", "security-audit", "sandbox-policy", "ioc-triage"],
        features: {
          // Surface tách /lab/ — không xen pipeline crypto/a11y
          isolatedSurface: true,
          neverExecuteSample: true,
          workerSandbox: true,
          dockerOptional: true,
        },
      },
      icons: {
        enabled: true,
        priority: 45,
        label: "Icon army (mapper call)",
        owns: ["icon-call", "icon-flow"],
        features: {
          callOnQuery: true,
          callOnAnalyze: true,
          attachFeedback: true,
          // enrichment only — không đè primaryOwner
          neverOverridePrimary: true,
        },
      },
      optimize: {
        enabled: true,
        priority: 3,
        label: "Logic optimize",
        owns: ["query-cache", "adaptive-plan", "rank-refine"],
        cacheSize: 64,
        features: {
          queryCache: true,
          softScreen: true, // sàng lọc mềm — không loại bỏ năng lực
          adaptivePipeline: true,
          pathMemo: true,
          rankRefine: true,
          dedupeResults: true,
          stageMetrics: true,
          callIcons: true,
        },
      },
      vars: {
        enabled: true,
        priority: 28,
        label: "Embedded vars (nginx upstream)",
        owns: ["embedded-vars", "upstream-var-lookup"],
        features: {
          lookupOnQuery: true,
          attachToEnrichment: true,
          neverOverridePrimary: true,
        },
      },
      index: {
        enabled: true,
        priority: 10,
        label: "Index",
        owns: ["resolve", "entity-lookup"],
        features: {
          exactResolve: true,
          nameResolve: true,
        },
      },
      rules: {
        enabled: true,
        priority: 20,
        label: "Rules",
        owns: ["intent", "need-match"],
        features: {
          classifyIntent: true,
          needRules: true,
          shortCircuitOnMatch: true, // khớp rule → không để search chiếm recommend
        },
      },
      search: {
        enabled: true,
        priority: 30,
        label: "Search",
        owns: ["ranked-search"],
        features: {
          synonymExpand: true,
          rankedResults: true,
          // chỉ chạy khi rules không short-circuit
          runIfNoRuleMatch: true,
        },
      },
      paths: {
        enabled: true,
        priority: 40,
        label: "Paths",
        owns: ["graph-paths", "lib-routes"],
        features: {
          allPathsToLibraries: true,
          edgeIcons: true,
          attachAsEnrichment: true, // không ghi đè results của rules/search
          forceOnPathIntent: true,
        },
      },
      encode: {
        enabled: true,
        priority: 50,
        label: "Encode",
        owns: ["representation-encode"],
        features: {
          morse: true,
          braille: true,
          base64: true,
          disclaimer: true,
        },
      },
      recommend: {
        enabled: true,
        priority: 25,
        label: "Recommend",
        owns: ["recommendation"],
        features: {
          // recommend đi qua rules; module này chỉ bổ sung ngôn ngữ
          languageFilter: true,
          cheatSheet: true,
        },
      },
      a11y: {
        enabled: true,
        priority: 60,
        label: "A11y assist",
        owns: ["switch-scan", "tts-assist"],
        features: {
          // không xen vào crypto lookup
          isolatedSurface: true,
          respondToAssistIntent: true,
        },
      },
      router: {
        enabled: true,
        priority: 1,
        label: "Router",
        owns: ["dispatch"],
        features: {
          exclusiveDispatch: true,
        },
      },
    },
  };

  function deepClone(obj) {
    return JSON.parse(JSON.stringify(obj));
  }

  function loadStored() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  function mergeConfig(base, patch) {
    const out = deepClone(base);
    if (!patch) return out;
    if (patch.conflictPolicy) out.conflictPolicy = patch.conflictPolicy;
    if (patch.enrichmentPolicy) out.enrichmentPolicy = patch.enrichmentPolicy;
    if (Array.isArray(patch.pipeline)) out.pipeline = patch.pipeline.slice();
    if (patch.modules) {
      Object.entries(patch.modules).forEach(([name, modPatch]) => {
        out.modules[name] = { ...(out.modules[name] || {}), ...modPatch };
        if (modPatch.features) {
          out.modules[name].features = {
            ...(base.modules[name]?.features || {}),
            ...modPatch.features,
          };
        }
        if (modPatch.owns) out.modules[name].owns = modPatch.owns.slice();
      });
    }
    return out;
  }

  let config = mergeConfig(DEFAULTS, loadStored());

  const Config = {
    defaults: deepClone(DEFAULTS),

    get() {
      return deepClone(config);
    },

    getModule(name) {
      return deepClone(config.modules[name] || null);
    },

    feature(moduleName, featureName, fallback = false) {
      const mod = config.modules[moduleName];
      if (!mod || !mod.enabled) return false;
      if (!featureName) return !!mod.enabled;
      return mod.features?.[featureName] ?? fallback;
    },

    isEnabled(moduleName) {
      return !!config.modules[moduleName]?.enabled;
    },

    /** Danh sách module theo priority tăng dần */
    orderedModules() {
      return Object.entries(config.modules)
        .map(([id, mod]) => ({ id, ...mod }))
        .sort((a, b) => a.priority - b.priority);
    },

    /** Phát hiện ownership trùng (dẫm chân) */
    detectConflicts() {
      const domainOwners = {};
      const conflicts = [];
      Object.entries(config.modules).forEach(([id, mod]) => {
        if (!mod.enabled) return;
        (mod.owns || []).forEach((domain) => {
          if (domainOwners[domain]) {
            conflicts.push({
              domain,
              modules: [domainOwners[domain], id],
              message: `Domain "${domain}" bị ${domainOwners[domain]} và ${id} cùng owns`,
            });
          } else {
            domainOwners[domain] = id;
          }
        });
      });
      return { ok: conflicts.length === 0, conflicts, domainOwners };
    },

    setModuleEnabled(name, enabled) {
      if (!config.modules[name]) return false;
      config.modules[name].enabled = !!enabled;
      Config.persist();
      return true;
    },

    setFeature(moduleName, featureName, value) {
      if (!config.modules[moduleName]) return false;
      config.modules[moduleName].features =
        config.modules[moduleName].features || {};
      config.modules[moduleName].features[featureName] = !!value;
      Config.persist();
      return true;
    },

    setConflictPolicy(policy) {
      if (!["first-wins", "merge-enrichment-only"].includes(policy)) return false;
      config.conflictPolicy = policy;
      Config.persist();
      return true;
    },

    reset() {
      config = deepClone(DEFAULTS);
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch {
        /* ignore */
      }
      return Config.get();
    },

    persist() {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
      } catch {
        /* private mode */
      }
    },

    patch(partial) {
      config = mergeConfig(config, partial);
      Config.persist();
      return Config.get();
    },

    start() {
      const check = Config.detectConflicts();
      if (!check.ok) {
        console.warn("[MaMoLogic.config] ownership conflicts", check.conflicts);
      }
    },
  };

  global.MaMoLogicModules = global.MaMoLogicModules || {};
  global.MaMoLogicModules.config = Config;
})(window);
