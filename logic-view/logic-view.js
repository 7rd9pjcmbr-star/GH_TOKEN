(() => {
  "use strict";

  function runFormat() {
    const input = document.getElementById("format-input");
    const out = document.getElementById("format-output");
    const panEl = document.getElementById("format-panorama");
    const anaEl = document.getElementById("format-analysis");
    const trEl = document.getElementById("format-translation");
    if (!window.MaMoLogic?.analyze) {
      const err = { error: "analyze module missing" };
      if (out) out.textContent = JSON.stringify(err, null, 2);
      return;
    }
    const report = window.MaMoLogic.analyze(input.value || "");
    const pan = report.panorama || {};
    const ana = report.analysis || {
      primary: report.primary,
      candidates: report.candidates,
      summary: report.primary?.label,
    };
    const tr = report.translation || {};

    if (panEl) {
      panEl.innerHTML = `
        <ul class="deep-kv">
          <li><span>Policy</span><code>${escapeHtml(pan.config?.conflictPolicy || "—")}</code></li>
          <li><span>Pipeline</span><code>${escapeHtml((pan.config?.pipeline || []).join(" → ") || "—")}</code></li>
          <li><span>Analyze</span><code>${escapeHtml(
            pan.analyzeModule
              ? `P${pan.analyzeModule.priority} · ${
                  pan.analyzeModule.enabled ? "on" : "off"
                }`
              : "—"
          )}</code></li>
          <li><span>Soft-screen</span><code>${escapeHtml(
            pan.optimize?.softScreen ? "on" : "off"
          )}</code></li>
          <li><span>Catalog</span><code>${escapeHtml(
            String(pan.catalog?.formatCount ?? "—")
          )} định dạng</code></li>
          <li><span>Kế hoạch</span><code>${escapeHtml(
            pan.plan
              ? `${pan.plan.mode || "?"} · depth ${pan.plan.analyzeDepth || "?"} · limit ${
                  pan.plan.analyzeLimit ?? "?"
                }`
              : "—"
          )}</code></li>
        </ul>
        <p class="hint">${escapeHtml(pan.note || "")}</p>
      `;
    }

    if (anaEl) {
      const primary = ana.primary || report.primary;
      const cands = (ana.candidates || report.candidates || [])
        .slice(0, 5)
        .map(
          (c) =>
            `<li><code>${escapeHtml(c.id)}</code> ${escapeHtml(
              String(c.confidence)
            )} — ${escapeHtml(c.uniqueness || "")}</li>`
        )
        .join("");
      anaEl.innerHTML = primary
        ? `
        <p class="deep-summary"><strong>${escapeHtml(
          primary.label
        )}</strong> · ${escapeHtml(primary.family)} · tin cậy ${escapeHtml(
          String(primary.confidence)
        )}</p>
        <p class="hint">${escapeHtml(primary.uniqueness || "")}</p>
        <pre class="deep-mini">${escapeHtml(
          JSON.stringify(primary.features || {}, null, 2)
        )}</pre>
        <ul class="deep-cands">${cands}</ul>
        ${
          report.feedback
            ? `<p class="icon-feedback">${escapeHtml(report.feedback)}</p>`
            : ""
        }
      `
        : `<p class="hint">${escapeHtml(ana.summary || "Không xác định")}</p>`;
    }

    if (trEl) {
      if (tr.skipped) {
        trEl.innerHTML = `<p class="hint">Thông dịch tắt: ${escapeHtml(
          String(tr.skipped)
        )}</p>`;
      } else {
        const steps = (tr.steps || [])
          .map((s) => `<li>${escapeHtml(s)}</li>`)
          .join("");
        trEl.innerHTML = `
          <p class="deep-summary"><strong>${escapeHtml(
            tr.method || "—"
          )}</strong> · ${tr.ok ? "OK" : "không giải được"}</p>
          ${
            tr.plaintext
              ? `<pre class="deep-plain">${escapeHtml(tr.plaintext)}</pre>`
              : `<p class="hint">Không có plaintext (hash/ciphertext/armor-only).</p>`
          }
          ${steps ? `<ol class="deep-steps">${steps}</ol>` : ""}
          <p class="hint">${escapeHtml(tr.disclaimer || tr.explain || "")}</p>
        `;
      }
    }

    if (out) {
      out.textContent = JSON.stringify(
        {
          panorama: {
            policy: pan.config?.conflictPolicy,
            pipeline: pan.config?.pipeline,
            capabilities: pan.capabilities,
            plan: pan.plan,
            catalogSize: pan.catalog?.formatCount,
          },
          analysis: {
            summary: ana.summary,
            primary: ana.primary || report.primary,
            candidates: ana.candidates || report.candidates,
            discriminated: ana.discriminated || report.discriminated,
          },
          translation: tr,
          feedback: report.feedback || report.icons?.feedback || null,
          icons: report.icons
            ? {
                lead: report.icons.lead,
                callChant: report.icons.callChant,
                uniqueIcons: report.icons.uniqueIcons,
              }
            : null,
        },
        null,
        2
      );
    }
    if (report.icons) renderIconStrip(report.icons);
  }

  function run() {
    const input = document.getElementById("logic-input");
    const out = document.getElementById("logic-run-output");
    if (!window.MaMoLogic) {
      out.textContent = JSON.stringify({ error: "MaMoLogic not ready" }, null, 2);
      return;
    }
    const result = window.MaMoLogic.query(input.value || "");
    const icons = result.meta?.enrichment?.icons;
    const view = {
      action: result.action,
      ruleId: result.ruleId,
      intent: result.intent,
      reason: result.reason,
      primaryOwner: result.meta?.primaryOwner,
      formatId: result.meta?.formatId || null,
      translation: result.meta?.translation
        ? {
            ok: result.meta.translation.ok,
            method: result.meta.translation.method,
            plaintext: result.meta.translation.plaintext,
          }
        : null,
      optimized: result.meta?.optimized || null,
      optPlan: result.meta?.optPlan || null,
      stageTiming: result.meta?.stageTiming || null,
      pipeSummary: result.meta?.pipeSummary || [],
      iconFeedback: result.meta?.iconFeedback || icons?.feedback || null,
      iconChant: icons?.callChant || null,
      iconsCalled: (icons?.uniqueIcons || []).map((name) => {
        const hit = (icons.called || []).find((c) => c.name === name);
        return { name, call: hit?.call || name };
      }),
      claims: result.meta?.claims,
      results: (result.results || []).map((r) => ({
        id: r.id,
        kind: r.kind,
        name: r.name,
        score: r.score,
        tier: r.tier,
        category: r.category,
        commercial: r.commercial,
        iconCall: r.iconCall,
        translation: r.translation,
        summary: r.summary,
      })),
      paths: (result.paths || []).slice(0, 6).map((p) => ({
        to: p.label,
        length: p.length,
        via: (p.nodes || []).map((id) => id.replace(/^[^:]+:/, "")),
        icons: p.icons || [],
        calls: (p.icons || []).map((n) => {
          const army = window.NETWORK_MAP?.iconArmy?.[n];
          return army?.call || n;
        }),
      })),
      enrichment: result.meta?.enrichment
        ? {
            format: result.meta.enrichment.format?.primary || null,
            formatCandidates: result.meta.enrichment.format?.candidateCount || 0,
            encodeNote: result.meta.enrichment.encodeNote,
            translationOk: result.meta.enrichment.translation?.ok || null,
          }
        : null,
      log: result.meta?.log || [],
    };
    out.textContent = JSON.stringify(view, null, 2);
    renderIconStrip(icons);
    renderPipe(result.meta?.pipe || [], result.meta?.pipeSummary || []);
  }

  function renderPipe(pipe, summary) {
    const flow = document.getElementById("pipe-flow");
    const pout = document.getElementById("pipe-output");
    if (flow) {
      const links = pipe?.length
        ? pipe
        : (summary || []).map((s) => {
            const m = String(s).match(/^([^→]+)→([^:]+):(.+)$/);
            return m
              ? { from: m[1], to: m[2], channel: m[3] }
              : { from: "?", to: "?", channel: s };
          });
      flow.innerHTML = links.length
        ? links
            .map(
              (p) =>
                `<li><code>${escapeHtml(p.from)}</code> → <code>${escapeHtml(
                  p.to
                )}</code> <span class="pipe-ch">${escapeHtml(p.channel)}</span></li>`
            )
            .join("")
        : `<li class="hint">Chưa có pipe — chạy query.</li>`;
    }
    if (pout && pipe) {
      pout.textContent = JSON.stringify(
        {
          links: pipe.length,
          summary: summary || pipe.map((p) => `${p.from}→${p.to}:${p.channel}`),
          sample: pipe.slice(0, 12),
        },
        null,
        2
      );
    }
  }

  function renderIconStrip(icons) {
    const el = document.getElementById("icon-army-strip");
    if (!el) return;
    if (!icons?.uniqueIcons?.length) {
      el.innerHTML = `<p class="hint">Chưa gọi icon — chạy query hoặc phân tích.</p>`;
      return;
    }
    el.innerHTML = `
      <p class="icon-feedback">${escapeHtml(icons.feedback || "")}</p>
      <ul class="icon-army-list">
        ${icons.uniqueIcons
          .map((name) => {
            const meta = window.NETWORK_MAP?.iconArmy?.[name] || {};
            return `<li><code>${escapeHtml(name)}</code> <strong>${escapeHtml(
              meta.call || name
            )}</strong> <span>${escapeHtml(meta.motto || "")}</span></li>`;
          })
          .join("")}
      </ul>
    `;
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderConfig() {
    const L = window.MaMoLogic;
    if (!L?.config) return;
    const conf = L.config.get();
    const conflicts = L.config.conflicts();
    const policyLabel = document.getElementById("conflict-policy-label");
    if (policyLabel) policyLabel.textContent = conf.conflictPolicy;

    const box = document.getElementById("config-conflicts");
    if (box) {
      box.innerHTML = conflicts.ok
        ? `<p class="ok-msg">Ownership OK — không domain trùng.</p>`
        : `<ul>${conflicts.conflicts
            .map(
              (c) =>
                `<li class="err-msg">${c.domain}: ${c.modules.join(" vs ")}</li>`
            )
            .join("")}</ul>`;
    }

    const host = document.getElementById("config-modules");
    if (!host) return;
    host.innerHTML = "";
    L.config.ordered().forEach((mod) => {
      const card = document.createElement("div");
      card.className = "mod-config" + (mod.enabled ? "" : " is-off");
      const feats = Object.entries(mod.features || {})
        .map(
          ([k, v]) =>
            `<label class="feat"><input type="checkbox" data-mod="${mod.id}" data-feat="${k}" ${
              v ? "checked" : ""
            }/> ${k}</label>`
        )
        .join("");
      card.innerHTML = `
        <div class="mod-head">
          <label class="mod-enable">
            <input type="checkbox" data-enable="${mod.id}" ${mod.enabled ? "checked" : ""}/>
            <strong>${mod.label || mod.id}</strong>
          </label>
          <span class="mod-pri">P${mod.priority}</span>
        </div>
        <p class="mod-owns">owns: ${(mod.owns || []).map((o) => `<code>${o}</code>`).join(" ")}</p>
        <div class="mod-feats">${feats}</div>
      `;
      host.appendChild(card);
    });

    host.querySelectorAll("[data-enable]").forEach((el) => {
      el.addEventListener("change", () => {
        L.config.setEnabled(el.getAttribute("data-enable"), el.checked);
        renderConfig();
        run();
      });
    });
    host.querySelectorAll("[data-feat]").forEach((el) => {
      el.addEventListener("change", () => {
        L.config.setFeature(
          el.getAttribute("data-mod"),
          el.getAttribute("data-feat"),
          el.checked
        );
        run();
      });
    });
  }

  window.__mamoRenderLogicConfig = renderConfig;

  document.getElementById("btn-vars-lookup")?.addEventListener("click", () => {
    const q = document.getElementById("vars-input")?.value || "";
    const out = document.getElementById("vars-output");
    if (!window.MaMoLogic?.vars) {
      out.textContent = JSON.stringify({ error: "vars module missing" }, null, 2);
      return;
    }
    const exact = window.MaMoLogic.vars.get(q);
    const hits = exact ? [exact] : window.MaMoLogic.vars.search(q, { limit: 12 });
    out.textContent = JSON.stringify(
      {
        describe: window.MaMoLogic.vars.describe(),
        query: q,
        results: hits.map((v) => ({
          kind: v.kind,
          name: v.name,
          category: v.category,
          summary: v.summary,
          commercial: v.commercial,
          since: v.since,
          syntax: v.syntax || null,
          default: v.default || null,
          context: v.context || null,
          examples: v.examples || null,
          parameters: v.parameters || null,
          security: v.security || null,
          icon: v.icon,
          iconCall: v.iconCall,
          details: v.details,
          logUse: v.logUse || null,
          enum: v.enum || null,
        })),
        logFormatExample: window.MaMoLogic.vars.logFormat(),
      },
      null,
      2
    );
  });
  document.getElementById("vars-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") document.getElementById("btn-vars-lookup")?.click();
  });

  document.getElementById("btn-ngx-desc")?.addEventListener("click", () => {
    const out = document.getElementById("ngx-embed-out");
    const mod = window.MaMoLogic?.nginxEmbed;
    if (!mod) {
      out.textContent = JSON.stringify({ error: "nginxEmbed module missing" }, null, 2);
      return;
    }
    out.textContent = JSON.stringify(mod.describe(), null, 2);
  });

  document.getElementById("btn-ngx-embed")?.addEventListener("click", async () => {
    const out = document.getElementById("ngx-embed-out");
    const mod = window.MaMoLogic?.nginxEmbed;
    if (!mod) {
      out.textContent = JSON.stringify({ error: "nginxEmbed module missing" }, null, 2);
      return;
    }
    out.textContent = "Đang gọi…";
    try {
      const result = await mod.runWhenNeeded();
      out.textContent = JSON.stringify(result, null, 2);
    } catch (err) {
      out.textContent = JSON.stringify({ ok: false, error: String(err?.message || err) }, null, 2);
    }
  });
  document.getElementById("btn-opt-stats")?.addEventListener("click", () => {
    const out = document.getElementById("opt-stats-output");
    out.textContent = JSON.stringify(
      {
        stats: window.MaMoLogic?.optimize?.stats?.(),
        planPreview: window.MaMoLogic?.optimize?.planPreview?.(
          document.getElementById("logic-input")?.value || ""
        ),
      },
      null,
      2
    );
  });
  document.getElementById("btn-opt-invalidate")?.addEventListener("click", () => {
    window.MaMoLogic?.optimize?.invalidate?.();
    const out = document.getElementById("opt-stats-output");
    out.textContent = JSON.stringify(
      { invalidated: true, stats: window.MaMoLogic?.optimize?.stats?.() },
      null,
      2
    );
  });
  document.getElementById("btn-logic-run")?.addEventListener("click", run);
  document.getElementById("btn-format-analyze")?.addEventListener("click", runFormat);
  document.getElementById("btn-pipe-map")?.addEventListener("click", () => {
    const map = window.MaMoLogic?.pipeMap?.() || {};
    const out = document.getElementById("pipe-output");
    if (out) out.textContent = JSON.stringify(map, null, 2);
    renderPipe(
      (map.links || []).map((l) => ({
        from: l.from,
        to: l.to,
        channel: l.channel,
      })),
      (map.links || []).map((l) => `${l.from}→${l.to}:${l.channel}`)
    );
  });
  document.getElementById("btn-pipe-trace")?.addEventListener("click", () => {
    const q = document.getElementById("logic-input")?.value || "";
    window.MaMoLogic?.optimize?.invalidate?.();
    const trace = window.MaMoLogic?.pipeTrace?.(q);
    const out = document.getElementById("pipe-output");
    if (out) out.textContent = JSON.stringify(trace, null, 2);
    renderPipe(trace?.pipe || [], trace?.pipeSummary || []);
  });
  document.getElementById("btn-icon-map")?.addEventListener("click", () => {
    const out = document.getElementById("icon-map-output");
    if (!window.MaMoLogic?.mapIconLibraries) {
      out.textContent = JSON.stringify({ error: "mapIconLibraries missing" }, null, 2);
      return;
    }
    const map = window.MaMoLogic.mapIconLibraries();
    out.textContent = JSON.stringify(
      {
        ok: map.ok,
        feedback: map.feedback,
        coverage: map.coverage,
        icons: (map.icons || []).map((e) => ({
          icon: e.icon,
          call: e.call,
          docsComplete: e.docsComplete,
          libraryCount: e.libraryCount,
          libraries: (e.libraries || []).slice(0, 6).map((l) => ({
            id: l.id,
            name: l.name,
            url: l.url,
          })),
        })),
      },
      null,
      2
    );
    if (map.icons) {
      renderIconStrip({
        feedback: map.feedback,
        uniqueIcons: map.icons.map((i) => i.icon),
        called: map.icons.map((i) => ({
          name: i.icon,
          call: i.call,
        })),
      });
    }
  });
  document.getElementById("format-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runFormat();
  });
  document.getElementById("logic-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") run();
  });
  document.getElementById("btn-config-reset")?.addEventListener("click", () => {
    window.MaMoLogic?.config.reset();
    renderConfig();
    run();
  });

  const boot = () => {
    if (window.MaMoLogic) {
      renderConfig();
      run();
      runFormat();
    } else setTimeout(boot, 120);
  };
  boot();
})();
