/**
 * optimize — logic hệ thống tối ưu nâng cao
 * Cache LRU · adaptive pipeline · memo paths · rank refine · metrics
 * Owns: query-cache, adaptive-plan, rank-refine (enrichment — không đè primaryOwner)
 */
(function (global) {
  "use strict";

  const DEFAULT_CACHE = 64;

  const state = {
    cache: new Map(), // key → { at, decision }
    pathMemo: new Map(),
    adjMemo: null,
    graphEpoch: 0,
    metrics: {
      queries: 0,
      cacheHits: 0,
      cacheMisses: 0,
      avgMs: 0,
      stageTotals: Object.create(null),
      lastPlan: null,
    },
  };

  function cfg() {
    return global.MaMoLogicModules?.config;
  }

  function feat(name, fallback) {
    const c = cfg();
    if (!c) return fallback;
    return c.feature("optimize", name, fallback);
  }

  function S() {
    return global.MaMoLogicModules?.schema;
  }

  function cacheLimit() {
    const mod = cfg()?.getModule?.("optimize");
    const n = Number(mod?.cacheSize);
    return Number.isFinite(n) && n > 0 ? n : DEFAULT_CACHE;
  }

  function cacheKey(input) {
    const text =
      typeof input === "string"
        ? input
        : input?.text || input?.need || input?.query || input?.raw || "";
    const lang =
      typeof input === "object" && input ? input.language || "" : "";
    const from = typeof input === "object" && input ? input.from || "" : "";
    const norm = S()?.normalize?.(text) || String(text).toLowerCase().trim();
    return `${norm}::${S()?.normalize?.(lang) || lang}::${from}`;
  }

  function cacheGet(key) {
    if (!feat("queryCache", true)) return null;
    const hit = state.cache.get(key);
    if (!hit) {
      state.metrics.cacheMisses += 1;
      return null;
    }
    // LRU refresh
    state.cache.delete(key);
    state.cache.set(key, hit);
    state.metrics.cacheHits += 1;
    return {
      ...hit.decision,
      meta: {
        ...(hit.decision.meta || {}),
        optimized: {
          ...(hit.decision.meta?.optimized || {}),
          cacheHit: true,
          cachedAt: hit.at,
        },
      },
    };
  }

  function cacheSet(key, decision) {
    if (!feat("queryCache", true)) return;
    if (state.cache.has(key)) state.cache.delete(key);
    state.cache.set(key, { at: Date.now(), decision });
    const limit = cacheLimit();
    while (state.cache.size > limit) {
      const oldest = state.cache.keys().next().value;
      state.cache.delete(oldest);
    }
  }

  /**
   * Kế hoạch sàng lọc mềm — thu hẹp / xếp ưu tiên, KHÔNG loại bỏ năng lực hệ thống.
   * softScreen (mặc định): luôn chạy stage; chỉ giới hạn độ sâu & xếp hạng.
   */
  function plan(ctx) {
    const soft = feat("softScreen", true);
    const text = String(ctx.text || "");
    const len = text.length;
    const looksFormat =
      len >= 8 &&
      (/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(text.trim()) ||
        /-----BEGIN /.test(text) ||
        /^[\.\-\s\/]+$/.test(text.trim()) ||
        /[\u2800-\u28FF]/.test(text) ||
        /^\$?upstream_[a-z0-9_]+$/i.test(text.trim()) ||
        /^(resolver|queue)\b/i.test(text.trim()));

    const shortLookup = len > 0 && len < 48 && !looksFormat;
    const intent = ctx.intent || null;

    const planOut = {
      mode: soft ? "soft-screen" : "adaptive-hard",
      // Soft: luôn giữ true — không loại bỏ stage
      runAnalyze: true,
      runPaths: true,
      runIcons: feat("callIcons", true) !== false,
      analyzeDepth: "full", // full | light
      analyzeLimit: 5,
      pathLimit: 8,
      iconPathLimit: 6,
      iconMaxCalled: 24,
      // Sàng lọc kết quả: demote (hạ) chứ không xoá khỏi hệ thống
      screen: {
        keepAllCapabilities: true,
        demoteKinds: [],
        preferKinds: [],
        maxPrimary: null, // null = không cắt; chỉ xếp hạng
      },
      skipSearchOnRule: true,
      reason: [],
    };

    if (feat("adaptivePipeline", true) || soft) {
      if (intent === "assist") {
        // Soft: vẫn chạy paths/icons nhưng rất mỏng — không skip
        planOut.pathLimit = soft ? 2 : 0;
        planOut.iconPathLimit = soft ? 1 : 0;
        planOut.iconMaxCalled = soft ? 4 : 0;
        planOut.analyzeDepth = "light";
        planOut.analyzeLimit = 2;
        planOut.screen.preferKinds = ["assist", "upstream-var"];
        planOut.screen.demoteKinds = ["lib", "library"];
        planOut.reason.push(
          soft ? "assist→sàng lọc mỏng (giữ paths/icons)" : "assist→skip paths/icons"
        );
        if (!soft) {
          planOut.runPaths = false;
          planOut.runIcons = false;
        }
      } else if (intent === "encode") {
        planOut.pathLimit = 4;
        planOut.iconPathLimit = 3;
        planOut.iconMaxCalled = 12;
        planOut.screen.preferKinds = ["concept"];
        planOut.reason.push("encode→thu hẹp enrichment");
      } else if (intent === "upstream-vars") {
        planOut.pathLimit = 3;
        planOut.iconPathLimit = 2;
        planOut.iconMaxCalled = 8;
        planOut.analyzeDepth = "full";
        planOut.analyzeLimit = 3;
        planOut.screen.preferKinds = ["upstream-var", "upstream-directive"];
        planOut.screen.demoteKinds = [];
        planOut.reason.push("upstream-vars→ưu tiên docs (không loại bỏ module khác)");
      } else if (intent === "path") {
        planOut.pathLimit = 40;
        planOut.iconPathLimit = 12;
        planOut.iconMaxCalled = 36;
        planOut.reason.push("path→làm đầy routes");
      } else if (shortLookup && !looksFormat) {
        planOut.pathLimit = 6;
        planOut.iconPathLimit = 4;
        planOut.iconMaxCalled = 16;
        planOut.analyzeDepth = soft ? "light" : "full";
        planOut.analyzeLimit = soft && !looksFormat ? 2 : 5;
        planOut.reason.push("short-lookup→sàng lọc nhẹ (vẫn giữ analyze)");
      } else if (looksFormat) {
        planOut.analyzeDepth = "full";
        planOut.analyzeLimit = 5;
        planOut.reason.push("format-like→analyze đầy đủ");
      }

      // Soft: không bao giờ tắt runAnalyze chỉ vì “không giống mẫu mã”
      if (!soft && !looksFormat && intent !== "upstream-vars" && len < 64) {
        planOut.runAnalyze = false;
        planOut.reason.push("hard→skip-analyze");
      } else if (soft && !looksFormat && intent !== "upstream-vars" && shortLookup) {
        planOut.reason.push("soft→analyze light (không loại bỏ)");
      }
    }

    planOut.reason.unshift(
      soft
        ? "chế độ sàng lọc mềm — không loại bỏ năng lực"
        : "chế độ adaptive cứng (có thể skip stage)"
    );

    state.metrics.lastPlan = planOut;
    return planOut;
  }

  function shouldAnalyze(ctx) {
    // Soft screen: luôn cho chạy
    if (feat("softScreen", true)) return true;
    if (!feat("adaptivePipeline", true)) return true;
    return ctx.optPlan ? ctx.optPlan.runAnalyze !== false : true;
  }

  function pathLimit(ctx) {
    return ctx.optPlan?.pathLimit ?? 8;
  }

  function analyzeLimit(ctx) {
    return ctx.optPlan?.analyzeLimit ?? 5;
  }

  function shouldPaths(ctx) {
    if (feat("softScreen", true)) return true;
    if (ctx.optPlan && ctx.optPlan.runPaths === false) return false;
    return true;
  }

  function shouldIcons(ctx) {
    if (feat("callIcons", true) === false) return false;
    if (feat("softScreen", true)) return true;
    if (ctx.optPlan && ctx.optPlan.runIcons === false) return false;
    return true;
  }

  /**
   * Sàng lọc mềm kết quả: demote / ưu tiên — giữ toàn bộ trong enrichment,
   * primary chỉ sắp xếp lại (không xoá năng lực khỏi hệ thống).
   */
  function softScreenResults(ctx) {
    if (!feat("softScreen", true)) return;
    const screen = ctx.optPlan?.screen || {};
    if (!Array.isArray(ctx.primary) || !ctx.primary.length) return;

    const prefer = new Set(screen.preferKinds || []);
    const demote = new Set(screen.demoteKinds || []);

    const tagged = ctx.primary.map((r, i) => {
      let screenScore = typeof r.score === "number" ? r.score : 50;
      const kind = r.kind || "";
      if (prefer.has(kind)) screenScore += 40;
      if (demote.has(kind)) screenScore -= 25;
      screenScore += Math.max(0, 6 - i);
      return {
        ...r,
        screenScore,
        screened: demote.has(kind) ? "demoted" : prefer.has(kind) ? "preferred" : "kept",
      };
    });

    tagged.sort((a, b) => (b.screenScore || 0) - (a.screenScore || 0));

    // Không cắt bỏ — chỉ xếp; tuỳ chọn maxPrimary chỉ thu hẹp hiển thị primary
    const maxP = screen.maxPrimary;
    if (maxP && maxP > 0 && tagged.length > maxP) {
      ctx.enrichment = ctx.enrichment || {};
      ctx.enrichment.screenedTail = tagged.slice(maxP);
      ctx.primary = tagged.slice(0, maxP);
      ctx.log.push({
        stage: "optimize",
        softScreen: true,
        shown: maxP,
        retainedInEnrichment: tagged.length - maxP,
        note: "không loại bỏ — phần còn lại ở enrichment.screenedTail",
      });
    } else {
      ctx.primary = tagged;
      ctx.log.push({
        stage: "optimize",
        softScreen: true,
        count: tagged.length,
        demoted: tagged.filter((x) => x.screened === "demoted").length,
        preferred: tagged.filter((x) => x.screened === "preferred").length,
      });
    }
  }

  /** Memo paths theo origin */
  function memoPaths(from, builder) {
    if (!feat("pathMemo", true)) return builder();
    const key = String(from || "hub:crypto-libs");
    if (state.pathMemo.has(key)) return state.pathMemo.get(key);
    const value = builder();
    state.pathMemo.set(key, value);
    if (state.pathMemo.size > 48) {
      const oldest = state.pathMemo.keys().next().value;
      state.pathMemo.delete(oldest);
    }
    return value;
  }

  function clearMemos() {
    state.pathMemo.clear();
    state.adjMemo = null;
    global.__MAMO_ICON_ATLAS__ = null;
  }

  /**
   * Rank refine — sắp xếp lại results theo tier / language / score
   * Không đổi primaryOwner; chỉ tối ưu thứ tự hiển thị.
   */
  function rankRefine(ctx) {
    if (!feat("rankRefine", true)) return;
    if (!Array.isArray(ctx.primary) || ctx.primary.length < 2) return;

    const tierW = {
      "khuyến nghị": 30,
      "nền tảng": 22,
      "phổ biến": 16,
      "nghiên cứu→sản xuất": 14,
      nhẹ: 10,
      "tham chiếu": 8,
    };
    const lang = S()?.normalize?.(ctx.language || "") || "";

    ctx.primary = ctx.primary
      .map((r, i) => {
        let boost = 0;
        boost += tierW[r.tier] || 0;
        if (lang && (r.languages || []).some((l) => S().normalize(l).includes(lang))) {
          boost += 25;
        }
        if (r.kind === "concept" || r.kind === "lib") boost += 2;
        if (typeof r.score === "number") boost += Math.min(40, r.score / 3);
        // ổn định: giữ vị trí gốc nhẹ
        boost += Math.max(0, 8 - i);
        return { ...r, _optScore: boost, score: r.score != null ? r.score : boost };
      })
      .sort((a, b) => (b._optScore || 0) - (a._optScore || 0))
      .map(({ _optScore, ...rest }) => rest);

    ctx.log.push({ stage: "optimize", rankRefine: true, count: ctx.primary.length });
  }

  /** Deduplicate results by ref/id */
  function dedupeResults(ctx) {
    if (!feat("dedupeResults", true)) return;
    const seen = new Set();
    const out = [];
    (ctx.primary || []).forEach((r) => {
      const key = r.ref || `${r.kind}:${r.id}` || r.name;
      if (seen.has(key)) return;
      seen.add(key);
      out.push(r);
    });
    if (out.length !== (ctx.primary || []).length) {
      ctx.log.push({
        stage: "optimize",
        dedupe: true,
        before: ctx.primary.length,
        after: out.length,
      });
    }
    ctx.primary = out;
  }

  function markStage(ctx, name, ms) {
    if (!feat("stageMetrics", true)) return;
    ctx.stageTiming = ctx.stageTiming || {};
    ctx.stageTiming[name] = ms;
    state.metrics.stageTotals[name] =
      (state.metrics.stageTotals[name] || 0) + ms;
  }

  function beginQuery(input) {
    state.metrics.queries += 1;
    const key = cacheKey(input);
    const cached = cacheGet(key);
    return {
      key,
      cached,
      t0: typeof performance !== "undefined" ? performance.now() : Date.now(),
    };
  }

  function endQuery(session, decision) {
    const t1 = typeof performance !== "undefined" ? performance.now() : Date.now();
    const ms = Math.round((t1 - session.t0) * 100) / 100;
    const n = state.metrics.queries;
    state.metrics.avgMs = Math.round(((state.metrics.avgMs * (n - 1) + ms) / n) * 100) / 100;

    const enriched = {
      ...decision,
      meta: {
        ...(decision.meta || {}),
        optimized: {
          ...(decision.meta?.optimized || {}),
          cacheHit: false,
          elapsedMs: ms,
          plan: state.metrics.lastPlan,
          cacheSize: state.cache.size,
        },
      },
    };
    cacheSet(session.key, enriched);
    return enriched;
  }

  function stats() {
    return {
      queries: state.metrics.queries,
      cacheHits: state.metrics.cacheHits,
      cacheMisses: state.metrics.cacheMisses,
      hitRate:
        state.metrics.queries > 0
          ? Math.round(
              (state.metrics.cacheHits / Math.max(1, state.metrics.cacheHits + state.metrics.cacheMisses)) *
                1000
            ) / 10
          : 0,
      avgMs: state.metrics.avgMs,
      cacheSize: state.cache.size,
      pathMemoSize: state.pathMemo.size,
      stageTotals: { ...state.metrics.stageTotals },
      lastPlan: state.metrics.lastPlan,
    };
  }

  const Optimize = {
    plan,
    shouldAnalyze,
    shouldPaths,
    shouldIcons,
    pathLimit,
    analyzeLimit,
    memoPaths,
    clearMemos,
    rankRefine,
    dedupeResults,
    softScreenResults,
    markStage,
    beginQuery,
    endQuery,
    cacheKey,
    cacheGet,
    cacheSet,
    stats,
    invalidate() {
      state.cache.clear();
      clearMemos();
      return { ok: true };
    },
    start() {
      clearMemos();
    },
  };

  global.MaMoLogicModules = global.MaMoLogicModules || {};
  global.MaMoLogicModules.optimize = Optimize;
})(typeof window !== "undefined" ? window : globalThis);
