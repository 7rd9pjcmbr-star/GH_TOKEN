/**
 * pipeline — thực thi logic theo trật tự stage
 * Claim domain: module sở hữu domain đã claim → stage sau không chiếm.
 * Enrichment (paths): chỉ gắn thêm, không đè results chính.
 */
(function (global) {
  "use strict";

  function cfg() {
    return global.MaMoLogicModules.config;
  }
  function S() {
    return global.MaMoLogicModules.schema;
  }
  function index() {
    return global.MaMoLogicModules.index;
  }
  function rules() {
    return global.MaMoLogicModules.rules;
  }
  function paths() {
    return global.MaMoLogicModules.paths;
  }
  function opt() {
    return global.MaMoLogicModules.optimize;
  }

  function enrichRefs(ids, kindHint) {
    return (ids || [])
      .map((id) => {
        const hit = index().resolve(id);
        if (hit) return { ref: hit.ref, ...hit.entity };
        return { id, kind: kindHint || "unknown", name: id };
      })
      .filter(Boolean);
  }

  /**
   * Context chạy pipeline — các stage ghi vào đây
   */
  function createCtx(input) {
    const text =
      typeof input === "string"
        ? input
        : input?.text || input?.need || input?.query || input?.raw || "";
    return {
      input:
        typeof input === "object" && input
          ? { ...input, raw: input.raw != null ? input.raw : text, text }
          : { text, raw: text },
      text,
      language: typeof input === "object" ? input.language : undefined,
      from: typeof input === "object" ? input.from : undefined,
      claims: Object.create(null),
      log: [],
      intent: null,
      rule: null,
      primary: [],
      primaryOwner: null,
      enrichment: { paths: [], neighbors: [] },
      action: "none",
      reason: "",
      ruleId: null,
      stopped: false,
      optPlan: null,
      stageTiming: Object.create(null),
    };
  }

  function timed(ctx, name, fn) {
    const t0 = typeof performance !== "undefined" ? performance.now() : Date.now();
    fn();
    const t1 = typeof performance !== "undefined" ? performance.now() : Date.now();
    const ms = Math.round((t1 - t0) * 100) / 100;
    ctx.stageTiming[name] = ms;
    opt()?.markStage?.(ctx, name, ms);
  }

  function claim(ctx, moduleId, domains) {
    const policy = cfg().get().conflictPolicy;
    const granted = [];
    const denied = [];
    (domains || []).forEach((d) => {
      if (ctx.claims[d] && ctx.claims[d] !== moduleId) {
        if (policy === "first-wins") {
          denied.push(d);
          return;
        }
      }
      if (!ctx.claims[d]) {
        ctx.claims[d] = moduleId;
        granted.push(d);
      }
    });
    return { granted, denied };
  }

  function canRun(moduleId) {
    return cfg().isEnabled(moduleId);
  }

  function feat(moduleId, name, fallback) {
    return cfg().feature(moduleId, name, fallback);
  }

  const stages = {
    validate(ctx) {
      if (!canRun("schema")) return;
      claim(ctx, "schema", ["contracts", "normalize"]);
      ctx.text = S().normalize(ctx.text) ? ctx.text : ctx.text;
      // Kế hoạch sơ bộ (trước classify) — đủ để skip analyze khi không cần
      if (canRun("optimize") && opt()) {
        ctx.optPlan = opt().plan(ctx);
      }
      ctx.log.push({ stage: "validate", ok: true });
    },

    analyze(ctx) {
      if (!canRun("analyze") || !feat("analyze", "deepStructuralDetect", true)) return;
      if (opt() && !opt().shouldAnalyze(ctx)) {
        ctx.log.push({ stage: "analyze", skipped: "adaptive-plan" });
        return;
      }
      const c = claim(ctx, "analyze", ["format-detect", "format-classify"]);
      if (c.denied.length) {
        ctx.log.push({ stage: "analyze", skipped: "domain-denied", denied: c.denied });
        return;
      }
      const analyzer = global.MaMoLogicModules.analyze;
      if (!analyzer) {
        ctx.log.push({ stage: "analyze", skipped: "module-missing" });
        return;
      }
      // Phân tích trên input gốc (giữ cấu trúc), không dùng bản normalize mất dấu cấu trúc
      const raw =
        typeof ctx.input === "object" && ctx.input.raw != null
          ? String(ctx.input.raw)
          : String(ctx.input.text || ctx.text || "");
      const report = analyzer.analyze(raw, {
        limit: opt()?.analyzeLimit?.(ctx) || 5,
      });
      ctx.enrichment.format = report;
      // Nếu rõ là mã/format đặc thù (không phải tên thư viện), đánh dấu intent phụ
      if (report.primary && report.primary.confidence >= 0.72) {
        ctx.formatId = report.primary.id;
        ctx.formatFamily = report.primary.family;
        if (
          ["morse", "braille-unicode", "base64", "jwt", "pem-armor", "hash-hex"].includes(
            report.primary.id
          )
        ) {
          // Không chiếm recommendation — chỉ gợi ý encode/concept liên quan
          if (!ctx.primaryOwner && feat("analyze", "neverOverridePrimary", true)) {
            const related = (report.primary.relatedConcepts || [])
              .map((id) => index().resolve(id))
              .filter(Boolean)
              .map((h) => ({ ref: h.ref, ...h.entity, score: 40 }));
            if (related.length && report.primary.family !== "opaque") {
              ctx.enrichment.formatConcepts = related;
            }
          }
        }
      }
      ctx.log.push({
        stage: "analyze",
        primary: report.primary?.id || null,
        confidence: report.primary?.confidence || 0,
        candidates: report.candidateCount,
      });
    },

    resolve(ctx) {
      if (!canRun("index") || !feat("index", "exactResolve", true)) return;
      const c = claim(ctx, "index", ["resolve", "entity-lookup"]);
      if (c.denied.length && cfg().get().conflictPolicy === "first-wins") {
        ctx.log.push({ stage: "resolve", skipped: "domain-denied", denied: c.denied });
        return;
      }
      const exact = index().resolve(ctx.text);
      if (!exact) {
        ctx.log.push({ stage: "resolve", hit: false });
        return;
      }
      const nameNorm = S().normalize(exact.entity.name);
      const textNorm = S().normalize(ctx.text);
      const exactEnough =
        textNorm === nameNorm ||
        textNorm === S().normalize(exact.entity.id) ||
        !ctx.text.includes(" ");
      if (!exactEnough && !feat("index", "nameResolve", true)) {
        ctx.log.push({ stage: "resolve", hit: false, reason: "name-resolve-off" });
        return;
      }
      if (exactEnough || textNorm === nameNorm) {
        ctx.primary = [{ ref: exact.ref, ...exact.entity, score: 100 }];
        ctx.primaryOwner = "index";
        ctx.action = "resolve";
        ctx.reason = `Khớp chính xác ${exact.ref}`;
        ctx.ruleId = "EXACT";
        ctx.resolvedRef = exact.ref;
        // Exact thắng — đánh dấu recommend/search đã có chủ
        claim(ctx, "index", ["ranked-search", "recommendation", "need-match"]);
        ctx.log.push({ stage: "resolve", hit: true, ref: exact.ref });
        if (feat("rules", "shortCircuitOnMatch", true)) {
          ctx.skipSearch = true;
          ctx.skipRules = true;
        }
      }
    },

    classify(ctx) {
      if (!canRun("rules") || !feat("rules", "classifyIntent", true)) return;
      claim(ctx, "rules", ["intent"]);
      ctx.intent = rules().classifyIntent(ctx.text);
      // Adaptive plan sau khi biết intent (và có thể đã analyze)
      if (canRun("optimize") && opt()) {
        ctx.optPlan = opt().plan(ctx);
        ctx.log.push({
          stage: "optimize-plan",
          plan: {
            mode: ctx.optPlan.mode,
            runAnalyze: ctx.optPlan.runAnalyze,
            runPaths: ctx.optPlan.runPaths,
            runIcons: ctx.optPlan.runIcons,
            analyzeDepth: ctx.optPlan.analyzeDepth,
            pathLimit: ctx.optPlan.pathLimit,
            keepAllCapabilities: ctx.optPlan.screen?.keepAllCapabilities,
            why: ctx.optPlan.reason,
          },
        });
      }
      ctx.log.push({ stage: "classify", intent: ctx.intent });
    },

    rules(ctx) {
      if (!canRun("rules") || !feat("rules", "needRules", true)) return;
      if (ctx.skipRules) {
        ctx.log.push({ stage: "rules", skipped: "short-circuit" });
        return;
      }
      const c = claim(ctx, "rules", ["need-match", "recommendation"]);
      if (c.denied.includes("need-match") || c.denied.includes("recommendation")) {
        ctx.log.push({ stage: "rules", skipped: "domain-owned", denied: c.denied });
        return;
      }
      const rule = rules().matchNeed(ctx.text);
      if (!rule) {
        ctx.log.push({ stage: "rules", hit: false });
        return;
      }
      ctx.rule = rule;

      // Biến nhúng nginx upstream — kết quả từ catalog vars
      if (
        rule.action === "upstream-vars" &&
        canRun("vars") &&
        feat("vars", "lookupOnQuery", true)
      ) {
        claim(ctx, "vars", ["embedded-vars", "upstream-var-lookup"]);
        const Vars = global.MaMoLogicModules.vars;
        const exact = Vars?.get?.(ctx.text);
        const list = exact
          ? [exact]
          : Vars?.search?.(ctx.text, { limit: 16 }) || Vars?.allEntries?.() || Vars?.all?.() || [];
        ctx.primary = list.map((v) => ({
          kind: v.kind === "directive" ? "upstream-directive" : "upstream-var",
          id: v.id,
          name: v.name,
          summary: v.summary,
          category: v.category,
          commercial: !!v.commercial,
          since: v.since,
          details: v.details,
          related: v.related,
          logUse: v.logUse,
          enum: v.enum,
          syntax: v.syntax || null,
          default: v.default || null,
          context: v.context || null,
          examples: v.examples || null,
          parameters: v.parameters || null,
          security: v.security || null,
          commercialHistory: v.commercialHistory || null,
          icon: v.icon,
          iconCall: v.iconCall,
          score: exact && v.id === exact.id ? 100 : 70,
          ref: v.kind === "directive" ? `directive:${v.id}` : `var:${v.id}`,
        }));
        ctx.primaryOwner = "vars";
        ctx.action = "upstream-vars";
        ctx.reason = rule.reason;
        ctx.ruleId = rule.id;
        ctx.pathFrom = "hub:crypto-libs";
        ctx.skipSearch = true;
        claim(ctx, "vars", ["ranked-search", "recommendation"]);
        if (feat("vars", "attachToEnrichment", true)) {
          ctx.enrichment.upstreamVars = {
            module: "ngx_http_upstream_module",
            count: ctx.primary.length,
            directives: Vars?.allDirectives?.()?.length || 0,
            logFormatExample: Vars?.logFormat?.() || null,
            separatorNote: global.NGINX_UPSTREAM_VARS?.meta?.separatorNote || null,
          };
        }
        // Thin path/icons for var docs
        if (ctx.optPlan) {
          ctx.optPlan.pathLimit = 3;
          ctx.optPlan.iconPathLimit = 2;
          ctx.optPlan.iconMaxCalled = 8;
        }
        ctx.log.push({ stage: "rules", hit: true, ruleId: rule.id, vars: ctx.primary.length });
        return;
      }

      let libs = enrichRefs(rule.libs, "lib");
      if (ctx.language && feat("recommend", "languageFilter", true) && canRun("recommend")) {
        const langN = S().normalize(ctx.language);
        const filtered = libs.filter((l) =>
          (l.languages || []).some((x) => S().normalize(x).includes(langN))
        );
        if (filtered.length) libs = filtered;
        if (global.MaMoCrypto?.listLibraries) {
          global.MaMoCrypto
            .listLibraries({ language: ctx.language, tier: "khuyến nghị" })
            .forEach((l) => {
              if (!libs.some((x) => x.id === l.id)) {
                libs.push({ ...l, kind: "lib", ref: `lib:${l.id}` });
              }
            });
        }
      }
      const concepts = enrichRefs(rule.concepts, "concept");
      ctx.primary = [...concepts, ...libs];
      ctx.primaryOwner = "rules";
      ctx.action = rule.action;
      ctx.reason = rule.reason;
      ctx.ruleId = rule.id;
      ctx.pathFrom =
        ctx.from ||
        (concepts[0] ? `concept:${concepts[0].id}` : "hub:crypto-libs");

      if (feat("rules", "shortCircuitOnMatch", true)) {
        ctx.skipSearch = true;
        claim(ctx, "rules", ["ranked-search"]); // chặn search chiếm kết quả chính
      }

      // A11y intent — tách surface, không trộn crypto results
      if (rule.action === "assist" && canRun("a11y") && feat("a11y", "isolatedSurface", true)) {
        claim(ctx, "a11y", ["switch-scan", "tts-assist"]);
        ctx.metaAssist = { surface: "a11y", href: "/#special-panel" };
      }

      ctx.log.push({ stage: "rules", hit: true, ruleId: rule.id });
    },

    search(ctx) {
      if (!canRun("search") || !feat("search", "rankedResults", true)) return;
      if (ctx.skipSearch && feat("search", "runIfNoRuleMatch", true)) {
        ctx.log.push({ stage: "search", skipped: "rule-short-circuit" });
        return;
      }
      if (ctx.claims["ranked-search"] && ctx.claims["ranked-search"] !== "search") {
        ctx.log.push({
          stage: "search",
          skipped: "domain-owned-by",
          owner: ctx.claims["ranked-search"],
        });
        return;
      }
      claim(ctx, "search", ["ranked-search"]);

      let results = [];
      if (global.MaMoCrypto?.search) {
        results = global.MaMoCrypto.search(ctx.text, { limit: 12 }).map((r) => ({
          ...r,
          ref: r.kind === "library" ? `lib:${r.id}` : `concept:${r.id}`,
          kind: r.kind === "library" ? "lib" : r.kind,
        }));
      } else {
        const q = S().normalize(ctx.text);
        results = index()
          .list()
          .filter((e) => S().normalize(`${e.name} ${e.summary || ""}`).includes(q))
          .slice(0, 12)
          .map((e) => ({
            ...e,
            ref: S().ref(e.kind === "lib" ? "lib" : e.kind, e.id),
            score: 10,
          }));
      }

      if (!ctx.primaryOwner) {
        ctx.primary = results;
        ctx.primaryOwner = "search";
        ctx.action = results.length ? "search" : "empty";
        ctx.reason = results.length
          ? "Kết quả tìm kiếm xếp hạng"
          : "Không khớp";
        ctx.ruleId = "SEARCH";
      } else if (cfg().get().enrichmentPolicy === "attach-only") {
        ctx.enrichment.searchAlt = results.slice(0, 5);
      }
      ctx.log.push({ stage: "search", count: results.length, owner: ctx.primaryOwner });
    },

    paths(ctx) {
      if (!canRun("paths") || !feat("paths", "allPathsToLibraries", true)) return;
      if (opt() && !opt().shouldPaths(ctx)) {
        ctx.log.push({ stage: "paths", skipped: "adaptive-plan" });
        return;
      }
      claim(ctx, "paths", ["graph-paths", "lib-routes"]);

      const pathIntent =
        ctx.intent === "path" ||
        ctx.from ||
        (feat("paths", "forceOnPathIntent", true) && /path|duong dan/i.test(ctx.text));

      const origin =
        ctx.from ||
        ctx.pathFrom ||
        ctx.resolvedRef ||
        (pathIntent ? "hub:crypto-libs" : null) ||
        (ctx.primary[0] && ctx.primary[0].kind !== "lib"
          ? ctx.primary[0].ref
          : null) ||
        "hub:crypto-libs";

      const list = paths().toLibraries(origin);
      const limit = pathIntent
        ? Math.max(opt()?.pathLimit(ctx) || 8, 20)
        : opt()?.pathLimit(ctx) || 8;
      const limited = list.slice(0, pathIntent ? Math.max(limit, 40) : limit);
      ctx.enrichment.paths = limited;
      ctx.pathOrigin = origin;

      if (pathIntent && !ctx.primaryOwner) {
        ctx.action = "paths";
        ctx.ruleId = "PATH";
        ctx.reason = `Mọi đường tới thư viện từ ${origin}`;
        ctx.primaryOwner = "paths";
      }

      if (ctx.action === "encode-info" && canRun("encode")) {
        claim(ctx, "encode", ["representation-encode"]);
        ctx.enrichment.encodeNote =
          global.MaMoCrypto?.encode?.explain?.("base64") ||
          "Encoding ≠ encryption";
      }

      ctx.log.push({
        stage: "paths",
        origin,
        count: limited.length,
        limit,
        asEnrichment: ctx.primaryOwner !== "paths",
      });
    },

    icons(ctx) {
      if (!canRun("icons") || !feat("icons", "callOnQuery", true)) return;
      if (opt() && !opt().shouldIcons(ctx)) {
        ctx.log.push({ stage: "icons", skipped: "adaptive-plan" });
        return;
      }
      const c = claim(ctx, "icons", ["icon-call", "icon-flow"]);
      if (c.denied.length) {
        ctx.log.push({ stage: "icons", skipped: "domain-denied", denied: c.denied });
        return;
      }
      const iconsMod = global.MaMoLogicModules.icons;
      if (!iconsMod) {
        ctx.log.push({ stage: "icons", skipped: "module-missing" });
        return;
      }
      const pathLim = ctx.optPlan?.iconPathLimit || 6;
      const maxCalled = ctx.optPlan?.iconMaxCalled || 24;
      const report = iconsMod.callFromContext(ctx, {
        pathLimit: pathLim,
        maxCalled,
      });
      ctx.enrichment.icons = report;
      ctx.iconFeedback = report.feedback;
      if (feat("icons", "attachFeedback", true) && report.feedback) {
        if (!ctx.reason) ctx.reason = report.feedback;
        else if (!String(ctx.reason).includes("Mapper gọi")) {
          ctx.reason = `${ctx.reason} · ${report.feedback}`;
        }
      }
      ctx.log.push({
        stage: "icons",
        origin: report.origin,
        unique: report.uniqueIcons?.length || 0,
        chant: report.chant,
      });
    },

    finalize(ctx) {
      if (canRun("optimize") && opt()) {
        opt().dedupeResults(ctx);
        opt().rankRefine(ctx);
        opt().softScreenResults?.(ctx);
      }
      if (!ctx.action || ctx.action === "none") {
        ctx.action = ctx.primary.length ? "search" : "empty";
        ctx.reason = ctx.reason || ctx.iconFeedback || "Hoàn tất pipeline";
      }
      ctx.log.push({
        stage: "finalize",
        action: ctx.action,
        primaryOwner: ctx.primaryOwner,
        claims: { ...ctx.claims },
        timing: ctx.stageTiming,
      });
    },
  };

  const Pipeline = {
    stages,

    run(input) {
      const conf = cfg().get();
      const O = opt();
      const session = O && canRun("optimize") ? O.beginQuery(input) : null;
      if (session?.cached) {
        return session.cached;
      }

      const ctx = createCtx(input);
      const conflict = cfg().detectConflicts();
      if (!conflict.ok) {
        ctx.log.push({ stage: "config", warning: "ownership-conflicts", conflicts: conflict.conflicts });
      }

      (conf.pipeline || []).forEach((name) => {
        if (ctx.stopped) return;
        const fn = stages[name];
        if (typeof fn === "function") {
          try {
            timed(ctx, name, () => fn(ctx));
          } catch (err) {
            ctx.log.push({ stage: name, error: String(err.message || err) });
          }
        }
      });

      const decision = S().decision({
        ok: true,
        action: ctx.action,
        reason: ctx.reason,
        ruleId: ctx.ruleId,
        intent: ctx.intent || "unknown",
        results: ctx.primary,
        paths: ctx.enrichment.paths || [],
        meta: {
          primaryOwner: ctx.primaryOwner,
          claims: ctx.claims,
          pathOrigin: ctx.pathOrigin,
          enrichment: {
            searchAlt: ctx.enrichment.searchAlt || [],
            encodeNote: ctx.enrichment.encodeNote || null,
            assist: ctx.metaAssist || null,
            format: ctx.enrichment.format || null,
            formatConcepts: ctx.enrichment.formatConcepts || [],
            icons: ctx.enrichment.icons || null,
            upstreamVars: ctx.enrichment.upstreamVars || null,
          },
          iconFeedback: ctx.iconFeedback || null,
          formatId: ctx.formatId || null,
          formatFamily: ctx.formatFamily || null,
          optPlan: ctx.optPlan
            ? {
                mode: ctx.optPlan.mode,
                runAnalyze: ctx.optPlan.runAnalyze,
                runPaths: ctx.optPlan.runPaths,
                runIcons: ctx.optPlan.runIcons,
                analyzeDepth: ctx.optPlan.analyzeDepth,
                pathLimit: ctx.optPlan.pathLimit,
                screen: ctx.optPlan.screen,
                reason: ctx.optPlan.reason,
              }
            : null,
          stageTiming: ctx.stageTiming,
          log: ctx.log,
          language: ctx.language || null,
        },
      });

      return session && O ? O.endQuery(session, decision) : decision;
    },

    start() {},
  };

  global.MaMoLogicModules.pipeline = Pipeline;
})(window);
