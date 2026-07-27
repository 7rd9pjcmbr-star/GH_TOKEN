/**
 * icons — Mapper gọi tên quân đội icon trên dòng chảy dữ liệu
 * Khi analyze / query: triệu tập icon theo path → phản hồi có tên gọi.
 * Owns: icon-call, icon-flow (enrichment — không đè recommend).
 */
(function (global) {
  "use strict";

  function net() {
    return global.NETWORK_MAP || {};
  }

  function army() {
    return net().iconArmy || {};
  }

  function pathsMod() {
    return global.MaMoLogicModules?.paths;
  }

  function indexMod() {
    return global.MaMoLogicModules?.index;
  }

  function describeIcon(name) {
    const meta = army()[name] || {};
    const atlas = global.__MAMO_ICON_ATLAS__?.byIcon?.[name];
    return {
      name,
      call: meta.call || atlas?.call || name,
      role: meta.role || atlas?.role || "unit",
      motto: meta.motto || "",
      documentation: atlas?.documentation || null,
      libraryCount: atlas?.libraryCount || 0,
    };
  }

  function getAtlas(force) {
    if (!force && global.__MAMO_ICON_ATLAS__) return global.__MAMO_ICON_ATLAS__;
    const atlasData = global.CRYPTO_ATLAS;
    const netMap = net();
    const g =
      pathsMod()?.graph?.() ||
      (global.buildCryptoNetwork && atlasData && netMap
        ? global.buildCryptoNetwork(atlasData, netMap)
        : { nodes: [], edges: [] });
    // Graph rỗng → build trực tiếp
    const graph =
      g.nodes && g.nodes.length
        ? g
        : global.buildCryptoNetwork && atlasData && netMap
          ? global.buildCryptoNetwork(atlasData, netMap)
          : g;
    if (typeof global.buildIconLibraryAtlas === "function" && atlasData) {
      global.__MAMO_ICON_ATLAS__ = global.buildIconLibraryAtlas(
        atlasData,
        netMap,
        graph
      );
      return global.__MAMO_ICON_ATLAS__;
    }
    return null;
  }

  /**
   * Mapping đầy đủ mọi icon trên mạng → thư viện có tài liệu
   * Không bỏ sót icon nào xuất hiện trong không gian mạng.
   */
  function mapAllLibraries() {
    const atlas = getAtlas(true);
    if (!atlas) {
      return { ok: false, error: "icon-atlas missing" };
    }
    return {
      ok: atlas.ok,
      iconCount: atlas.iconCount,
      libraryCount: atlas.libraryCount,
      coverage: atlas.coverage,
      icons: atlas.icons.map((e) => ({
        icon: e.icon,
        call: e.call,
        docsComplete: e.docsComplete,
        libraryCount: e.libraryCount,
        appears: e.appears,
        documentation: e.documentation,
        libraries: e.libraries.map((l) => ({
          id: l.id,
          name: l.name,
          url: l.url,
          tier: l.tier,
          docsComplete: l.docsComplete,
          summary: l.summary,
        })),
      })),
      feedback: atlas.ok
        ? `Mapper: ${atlas.iconCount}/${atlas.iconCount} icon có tài liệu đầy đủ · ${atlas.libraryCount} thư viện`
        : `Mapper: thiếu docs — icons ${atlas.coverage.missingDocs.join(", ") || "—"} · libs ${atlas.coverage.incompleteLibs.join(", ") || "—"}`,
    };
  }

  function docsFor(iconName) {
    const atlas = getAtlas(true);
    const entry = atlas?.byIcon?.[iconName];
    if (!entry) return { ok: false, error: `icon không có trên mạng: ${iconName}` };
    return { ok: true, ...entry };
  }

  function coverageReport() {
    const atlas = getAtlas(true);
    if (!atlas) return { ok: false, error: "icon-atlas missing" };
    return {
      ok: atlas.ok,
      ...atlas.coverage,
      iconCount: atlas.iconCount,
      libraryCount: atlas.libraryCount,
      icons: atlas.icons.map((e) => ({
        icon: e.icon,
        call: e.call,
        docsComplete: e.docsComplete,
        libraryCount: e.libraryCount,
        hasSvg: e.documentation.svg,
      })),
    };
  }

  function resolveOrigin(ctx) {
    if (!ctx) return "hub:crypto-libs";
    if (ctx.pathOrigin) return ctx.pathOrigin;
    if (ctx.from) return ctx.from;
    if (ctx.resolvedRef) return ctx.resolvedRef;
    const primary = ctx.primary && ctx.primary[0];
    if (primary?.ref) return primary.ref;
    if (primary?.kind && primary?.id) {
      const kind = primary.kind === "library" ? "lib" : primary.kind;
      return `${kind}:${primary.id}`;
    }
    // format → concept gợi ý
    const fmtId = ctx.formatId;
    const related = ctx.enrichment?.format?.primary?.relatedConcepts;
    if (related && related[0]) {
      const hit = indexMod()?.resolve?.(related[0]);
      if (hit) return hit.ref;
      return `concept:${related[0]}`;
    }
    if (fmtId && net().iconByFormat?.[fmtId]) {
      // vẫn xuất phát hub, icon format sẽ lead
      return "hub:crypto-libs";
    }
    return "hub:crypto-libs";
  }

  /**
   * Gọi quân đội icon trên các path tới thư viện
   */
  function callOnPaths(from, opts = {}) {
    const limit = opts.limit || 8;
    const list = pathsMod()?.toLibraries?.(from) || [];
    const limited = list.slice(0, limit);
    const called = [];
    const seen = new Set();

    limited.forEach((p) => {
      const flow = p.iconFlow || [];
      const edgeIcons = p.icons || [];
      // node icons trên dòng chảy
      flow.forEach((step, i) => {
        const key = `${step.icon}@${step.id}`;
        if (seen.has(key)) return;
        seen.add(key);
        const d = describeIcon(step.icon);
        called.push({
          ...d,
          nodeId: step.id,
          nodeLabel: step.label,
          kind: step.kind,
          hop: i,
          pathTo: p.to,
          pathLabel: p.label,
          onFlow: true,
        });
      });
      // edge icons (quân đội trên cạnh)
      edgeIcons.forEach((iconName, i) => {
        const edge = p.edges?.[i];
        const key = `edge:${iconName}:${edge?.id || i}:${p.to}`;
        if (seen.has(key)) return;
        seen.add(key);
        const d = describeIcon(iconName);
        called.push({
          ...d,
          edgeId: edge?.id || null,
          edgeKind: edge?.kind || null,
          hop: i,
          pathTo: p.to,
          pathLabel: p.label,
          onFlow: true,
          asEdge: true,
        });
      });
    });

    const chant = [...new Set(called.map((c) => c.name))].slice(0, 12);
    const callChant = chant.map((n) => describeIcon(n).call);

    return {
      ok: true,
      origin: from,
      armySize: Object.keys(army()).length,
      pathCount: limited.length,
      called,
      uniqueIcons: chant,
      chant: chant.join(" → "),
      callChant: callChant.join(" → "),
      feedback: callChant.length
        ? `Mapper gọi: ${callChant.join(" → ")}`
        : "Mapper: chưa có icon trên dòng chảy",
      pathsPreview: limited.map((p) => ({
        to: p.to,
        label: p.label,
        length: p.length,
        icons: p.icons || [],
        calls: (p.icons || []).map((n) => describeIcon(n).call),
      })),
    };
  }

  /**
   * Gọi icon khi phân tích định dạng
   */
  function callForFormat(formatReport) {
    const primary = formatReport?.primary || formatReport;
    if (!primary?.id) {
      return {
        ok: true,
        called: [describeIcon("spark")],
        feedback: "Mapper gọi: Tia Lửa Hub (chưa nhận dạng định dạng)",
        chant: "spark",
      };
    }
    const iconName = net().iconByFormat?.[primary.id] || "text";
    const lead = describeIcon(iconName);
    const related = primary.relatedConcepts || [];
    const extras = [];
    related.slice(0, 4).forEach((cid) => {
      const ref = `concept:${cid}`;
      const g = pathsMod()?.graph?.() || { nodes: [] };
      const node = g.nodes.find((n) => n.id === ref);
      const icon = node?.icon || "key";
      extras.push({
        ...describeIcon(icon),
        nodeId: ref,
        nodeLabel: node?.label || cid,
        kind: "concept",
        viaFormat: primary.id,
      });
    });
    // luôn kèm khối thư viện nếu có path
    const flow = callOnPaths(
      related[0] ? `concept:${related[0]}` : "hub:crypto-libs",
      { limit: 3 }
    );
    const called = [lead, ...extras, ...flow.called.slice(0, 10)];
    const unique = [...new Set(called.map((c) => c.name))];
    const calls = unique.map((n) => describeIcon(n).call);
    return {
      ok: true,
      formatId: primary.id,
      lead,
      called,
      uniqueIcons: unique,
      chant: unique.join(" → "),
      callChant: calls.join(" → "),
      feedback: `Phân tích ${primary.label || primary.id}: Mapper gọi ${lead.call}` +
        (calls.length > 1 ? ` → ${calls.slice(1).join(" → ")}` : ""),
      pathsPreview: flow.pathsPreview,
    };
  }

  /**
   * Gọi icon từ ngữ cảnh pipeline (query)
   */
  function callFromContext(ctx, opts = {}) {
    const format = ctx?.enrichment?.format;
    const formatCalls =
      format?.primary && format.primary.confidence >= 0.55
        ? callForFormat(format)
        : null;

    const origin = resolveOrigin(ctx);
    const pathCalls = callOnPaths(origin, { limit: opts.pathLimit || 8 });

    // Hợp nhất: format lead trước, rồi quân đội trên flow
    const merged = [];
    const seen = new Set();
    const pushAll = (list) => {
      (list || []).forEach((c) => {
        const key = `${c.name}:${c.nodeId || c.edgeId || c.role}`;
        if (seen.has(key)) return;
        seen.add(key);
        merged.push(c);
      });
    };
    if (formatCalls) pushAll(formatCalls.called);
    pushAll(pathCalls.called);

    const unique = [...new Set(merged.map((c) => c.name))];
    const callNames = unique.map((n) => describeIcon(n).call);

    return {
      ok: true,
      origin,
      armySize: Object.keys(army()).length,
      format: formatCalls
        ? { lead: formatCalls.lead, feedback: formatCalls.feedback }
        : null,
      called: merged.slice(0, opts.maxCalled || 40),
      uniqueIcons: unique,
      chant: unique.join(" → "),
      callChant: callNames.join(" → "),
      feedback: callNames.length
        ? `Mapper gọi quân đội icon: ${callNames.join(" → ")}`
        : pathCalls.feedback,
      pathsPreview: pathCalls.pathsPreview,
    };
  }

  /**
   * Mapper icon nhận phản hồi truy vấn thời gian thực (OMS / pipe / sync)
   * input: { channels:[{id,status,backend,detail}], backends?:[], links?:[] }
   */
  function callForRealtime(report, opts = {}) {
    const byStatus = net().iconByRealtimeStatus || {
      connected: "monitor",
      alive: "monitor",
      ok: "monitor",
      missing_cred: "key",
      auth_fail: "lock",
      error: "wrench",
      stale: "wrench",
      blocked: "lock",
    };
    const byChannel = net().iconByRealtimeChannel || {
      telegram: "spark",
      pancake: "layers",
      ghn: "network",
      viettelpost: "network",
      tracking: "compass",
      tpos: "cpu",
      direct_api: "code",
      spx_local: "cube",
      vnpost_local: "code",
      oms_bus: "spark",
    };

    const channels = report?.channels || report?.backends || [];
    const paths = [];
    const mergedIcons = ["spark", "monitor"];

    channels.forEach((ch) => {
      const id = ch.id || ch.backend || "channel";
      const status = String(ch.status || "error").toLowerCase();
      const lead = byChannel[id] || byChannel[String(ch.backend || "").toLowerCase()] || "chip";
      const stIcon = byStatus[status] || "wrench";
      let icons = [lead, stIcon];
      if (status === "missing_cred") icons = [lead, "key", "lock"];
      if (status === "connected" || status === "alive" || status === "ok") icons = [lead, "monitor"];
      icons.forEach((n) => {
        if (!mergedIcons.includes(n)) mergedIcons.push(n);
      });
      const calls = icons.map((n) => describeIcon(n).call);
      const detail = `${ch.backend || id}: ${status}${ch.detail ? " · " + String(ch.detail).slice(0, 80) : ""}`;
      paths.push({
        channel: id,
        status,
        icons,
        icon_chant: calls.join(" → "),
        feedback: `Mapper gọi: ${calls.join(" → ")} — ${detail}`,
        called: icons.map((n) => describeIcon(n)),
      });
    });

    (report?.links || []).forEach((link) => {
      if (!link?.live) return;
      ["spark", byChannel[link.to] || "network", "monitor"].forEach((n) => {
        if (!mergedIcons.includes(n)) mergedIcons.push(n);
      });
    });

    const callNames = mergedIcons.map((n) => describeIcon(n).call);
    const connected = paths.filter((p) =>
      ["connected", "alive", "ok"].includes(p.status)
    ).length;
    const feedback =
      `Mapper gọi: ${callNames.join(" → ")} — realtime ${connected}/${paths.length} channel sống` +
      (opts.detail ? ` · ${opts.detail}` : "");

    return {
      ok: true,
      armySize: Object.keys(army()).length,
      called: mergedIcons.map((n) => describeIcon(n)),
      uniqueIcons: mergedIcons,
      chant: mergedIcons.join(" → "),
      callChant: callNames.join(" → "),
      feedback,
      paths,
      channels: paths,
    };
  }

  const Icons = {
    army() {
      return Object.entries(army()).map(([name, meta]) => ({
        name,
        ...meta,
      }));
    },

    describe: describeIcon,

    callOnPaths,
    callForFormat,
    callFromContext,
    callForRealtime,

    mapAllLibraries,
    docsFor,
    coverage: coverageReport,
    atlas: getAtlas,

    /** API ngắn: gọi theo query text / format id / node ref */
    call(input, opts = {}) {
      if (input && typeof input === "object" && (input.primary || input.enrichment)) {
        if (input.primary?.id && input.candidates) {
          return callForFormat(input);
        }
        return callFromContext(input, opts);
      }
      if (input && typeof input === "object" && (input.channels || input.backends)) {
        return callForRealtime(input, opts);
      }
      const text = String(input ?? "");
      // thử format trước
      const analyzer = global.MaMoLogicModules?.analyze;
      if (analyzer && opts.asFormat !== false && text.length >= 3) {
        const report = analyzer.analyze(text, { limit: 3 });
        if (report.primary && report.primary.confidence >= 0.72) {
          return callForFormat(report);
        }
      }
      // resolve node rồi gọi path
      const hit = indexMod()?.resolve?.(text);
      const from = hit?.ref || (text.includes(":") ? text : "hub:crypto-libs");
      return callOnPaths(from, opts);
    },

    start() {
      getAtlas();
    },
  };

  global.MaMoLogicModules = global.MaMoLogicModules || {};
  global.MaMoLogicModules.icons = Icons;
})(typeof window !== "undefined" ? window : globalThis);
