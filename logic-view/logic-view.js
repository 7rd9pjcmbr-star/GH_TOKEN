(() => {
  "use strict";

  function runFormat() {
    const input = document.getElementById("format-input");
    const out = document.getElementById("format-output");
    if (!window.MaMoLogic?.analyze) {
      out.textContent = JSON.stringify({ error: "analyze module missing" }, null, 2);
      return;
    }
    const report = window.MaMoLogic.analyze(input.value || "");
    out.textContent = JSON.stringify(
      {
        primary: report.primary,
        feedback: report.feedback || report.icons?.feedback || null,
        icons: report.icons
          ? {
              lead: report.icons.lead,
              callChant: report.icons.callChant,
              uniqueIcons: report.icons.uniqueIcons,
            }
          : null,
        discriminated: report.discriminated,
        candidates: report.candidates,
      },
      null,
      2
    );
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
      optimized: result.meta?.optimized || null,
      optPlan: result.meta?.optPlan || null,
      stageTiming: result.meta?.stageTiming || null,
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
          }
        : null,
      log: result.meta?.log || [],
    };
    out.textContent = JSON.stringify(view, null, 2);
    renderIconStrip(icons);
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
