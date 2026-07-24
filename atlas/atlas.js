(() => {
  "use strict";

  function api() {
    return window.MaMoCrypto;
  }

  function atlasRaw() {
    return window.CRYPTO_ATLAS;
  }

  const state = {
    view: "concepts",
    category: "all",
    lang: "all",
    query: "",
  };

  const els = {
    navBtns: [...document.querySelectorAll(".nav-btn[data-view]")],
    views: [...document.querySelectorAll("[data-view-panel]")],
    taxonomy: document.getElementById("taxonomy-list"),
    conceptsGrid: document.getElementById("concepts-grid"),
    librariesGrid: document.getElementById("libraries-grid"),
    langChips: document.getElementById("lang-chips"),
    guideList: document.getElementById("guide-list"),
    cheatDo: document.getElementById("cheat-do"),
    cheatDont: document.getElementById("cheat-dont"),
    search: document.getElementById("atlas-search"),
    empty: document.getElementById("empty-state"),
    disclaimer: document.getElementById("atlas-disclaimer"),
    dialog: document.getElementById("detail-dialog"),
    detailTitle: document.getElementById("detail-title"),
    detailBody: document.getElementById("detail-body"),
    recommendInput: document.getElementById("recommend-input"),
    recommendOutput: document.getElementById("recommend-output"),
    apiMethod: document.getElementById("api-method"),
    apiArg1: document.getElementById("api-arg1"),
    apiArg2: document.getElementById("api-arg2"),
    apiOutput: document.getElementById("api-output"),
    apiEndpoints: document.getElementById("api-endpoints"),
  };

  function categoryName(id) {
    const tax = api()?.taxonomy?.() || atlasRaw()?.taxonomy || [];
    return tax.find((t) => t.id === id)?.name || id;
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function switchView(view) {
    state.view = view;
    els.navBtns.forEach((btn) => {
      const on = btn.dataset.view === view;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-pressed", String(on));
    });
    els.views.forEach((panel) => {
      const on = panel.dataset.viewPanel === view;
      panel.hidden = !on;
      panel.classList.toggle("is-visible", on);
    });
    render();
  }

  function renderTaxonomy() {
    const tax = api()?.taxonomy?.() || atlasRaw()?.taxonomy || [];
    const items = [
      { id: "all", name: "Tất cả", summary: "Toàn bộ mục trong atlas" },
      ...tax,
    ];
    els.taxonomy.innerHTML = "";
    items.forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "filter-btn" + (state.category === item.id ? " is-active" : "");
      btn.innerHTML = `<strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(
        item.summary || ""
      )}</small>`;
      btn.addEventListener("click", () => {
        state.category = item.id;
        renderTaxonomy();
        render();
      });
      els.taxonomy.appendChild(btn);
    });
  }

  function renderLangChips() {
    const langs = ["all", ...(api()?.languages?.() || [])];
    els.langChips.innerHTML = "";
    langs.forEach((lang) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chip" + (state.lang === lang ? " is-active" : "");
      btn.textContent = lang === "all" ? "Mọi ngôn ngữ" : lang;
      btn.addEventListener("click", () => {
        state.lang = lang;
        renderLangChips();
        render();
      });
      els.langChips.appendChild(btn);
    });
  }

  function filteredConcepts() {
    const crypto = api();
    if (crypto?.search && state.query.trim()) {
      return crypto
        .search(state.query, {
          kind: "concept",
          category: state.category === "all" ? undefined : state.category,
          limit: 50,
        })
        .map((r) => crypto.getConcept(r.id))
        .filter(Boolean);
    }
    let list = crypto?.listConcepts?.(
      state.category === "all" ? {} : { category: state.category }
    ) || atlasRaw()?.concepts || [];
    if (state.query.trim() && !crypto?.search) {
      const q = state.query.toLowerCase();
      list = list.filter((c) =>
        `${c.name} ${c.summary}`.toLowerCase().includes(q)
      );
    }
    return list;
  }

  function filteredLibraries() {
    const crypto = api();
    if (crypto?.search && state.query.trim()) {
      return crypto
        .search(state.query, {
          kind: "library",
          language: state.lang === "all" ? undefined : state.lang,
          category: state.category === "all" ? undefined : state.category,
          limit: 50,
        })
        .map((r) => crypto.getLibrary(r.id))
        .filter(Boolean);
    }
    const filter = {};
    if (state.lang !== "all") filter.language = state.lang;
    if (state.category !== "all") filter.category = state.category;
    let list = crypto?.listLibraries?.(filter) || atlasRaw()?.libraries || [];
    if (state.query.trim() && !crypto?.search) {
      const q = state.query.toLowerCase();
      list = list.filter((l) =>
        `${l.name} ${l.summary}`.toLowerCase().includes(q)
      );
    }
    return list;
  }

  function openConcept(c) {
    const related = api()?.related?.(c.id);
    els.detailTitle.textContent = c.name;
    els.detailBody.innerHTML = `
      <div class="meta-row">
        <span class="tag">${escapeHtml(categoryName(c.category))}</span>
        <span class="tag">${escapeHtml(c.level || "")}</span>
      </div>
      <p>${escapeHtml(c.summary)}</p>
      <h3>Chi tiết</h3>
      <ul>${(c.details || []).map((d) => `<li>${escapeHtml(d)}</li>`).join("")}</ul>
      ${
        related?.graph?.length
          ? `<h3>Graph neighbors</h3><div class="meta-row">${related.graph
              .slice(0, 12)
              .map(
                (n) =>
                  `<span class="tag">${escapeHtml(n.label)} · ${escapeHtml(
                    n.relation
                  )}</span>`
              )
              .join("")}</div>`
          : ""
      }
    `;
    els.dialog.showModal();
  }

  function openLibrary(lib) {
    const related = api()?.related?.(lib.id);
    els.detailTitle.textContent = lib.name;
    els.detailBody.innerHTML = `
      <div class="meta-row">
        <span class="tag">${escapeHtml(lib.tier || "")}</span>
        <span class="tag">${escapeHtml(lib.category || "")}</span>
        ${(lib.languages || [])
          .map((l) => `<span class="tag">${escapeHtml(l)}</span>`)
          .join("")}
      </div>
      <p>${escapeHtml(lib.summary)}</p>
      <h3>Cung cấp</h3>
      <ul>${(lib.provides || []).map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul>
      ${lib.notes ? `<h3>Ghi chú</h3><p>${escapeHtml(lib.notes)}</p>` : ""}
      ${
        lib.url
          ? `<p><a href="${escapeHtml(lib.url)}" target="_blank" rel="noopener noreferrer">Tài liệu</a></p>`
          : ""
      }
      ${
        related?.graph?.length
          ? `<h3>Liên kết mạng</h3><div class="meta-row">${related.graph
              .slice(0, 12)
              .map((n) => `<span class="tag">${escapeHtml(n.label)}</span>`)
              .join("")}</div>`
          : ""
      }
    `;
    els.dialog.showModal();
  }

  function renderConcepts() {
    const list = filteredConcepts();
    els.conceptsGrid.innerHTML = "";
    list.forEach((c) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "atlas-card";
      btn.innerHTML = `
        <span class="card-kicker">${escapeHtml(categoryName(c.category))} · ${escapeHtml(
        c.level || ""
      )}</span>
        <h3>${escapeHtml(c.name)}</h3>
        <p>${escapeHtml(c.summary)}</p>
      `;
      btn.addEventListener("click", () => openConcept(c));
      els.conceptsGrid.appendChild(btn);
    });
    return list.length;
  }

  function renderLibraries() {
    const list = filteredLibraries();
    els.librariesGrid.innerHTML = "";
    list.forEach((lib) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "atlas-card";
      btn.innerHTML = `
        <span class="card-kicker">${escapeHtml(lib.category || "")} · ${escapeHtml(
        lib.tier || ""
      )}</span>
        <h3>${escapeHtml(lib.name)}</h3>
        <p>${escapeHtml(lib.summary)}</p>
        <div class="tags">${(lib.languages || [])
          .slice(0, 4)
          .map((l) => `<span class="tag">${escapeHtml(l)}</span>`)
          .join("")}</div>
      `;
      btn.addEventListener("click", () => openLibrary(lib));
      els.librariesGrid.appendChild(btn);
    });
    return list.length;
  }

  function renderGuide() {
    const guide = atlasRaw()?.decisionGuide || [];
    els.guideList.innerHTML = "";
    guide.forEach((g) => {
      if (state.query.trim()) {
        const hit = api()?.search?.(state.query, { limit: 30 }) || [];
        const blob = `${g.need} ${g.pick}`.toLowerCase();
        if (
          !blob.includes(state.query.toLowerCase()) &&
          !hit.some((h) => blob.includes(h.name.toLowerCase()))
        ) {
          return;
        }
      }
      const div = document.createElement("div");
      div.className = "guide-item";
      div.innerHTML = `<strong>${escapeHtml(g.need)}</strong><span>${escapeHtml(
        g.pick
      )}</span>`;
      els.guideList.appendChild(div);
    });
    const sheet = api()?.cheatSheet?.() || atlasRaw()?.cheatSheet || { do: [], dont: [] };
    els.cheatDo.innerHTML = sheet.do.map((x) => `<li>${escapeHtml(x)}</li>`).join("");
    els.cheatDont.innerHTML = sheet.dont
      .map((x) => `<li>${escapeHtml(x)}</li>`)
      .join("");
    return els.guideList.children.length;
  }

  function renderApiPanel() {
    const desc = api()?.describe?.();
    if (els.apiEndpoints && desc) {
      els.apiEndpoints.innerHTML = `
        <h3 class="api-ep-title">Endpoints</h3>
        <ul class="api-ep-list">
          ${desc.endpoints
            .map(
              (e) =>
                `<li><code>${escapeHtml(e.name)}</code> — ${escapeHtml(e.desc)}</li>`
            )
            .join("")}
        </ul>
      `;
    }
    return 1;
  }

  function render() {
    let count = 0;
    if (state.view === "concepts") count = renderConcepts();
    if (state.view === "libraries") count = renderLibraries();
    if (state.view === "guide") count = renderGuide();
    if (state.view === "api") count = renderApiPanel();
    els.empty.hidden = count > 0 || state.view === "api" || state.view === "guide";
  }

  function runApi() {
    const crypto = api();
    if (!crypto) {
      els.apiOutput.textContent = '{"error":"MaMoCrypto not ready"}';
      return;
    }
    const method = els.apiMethod.value;
    const a1 = els.apiArg1.value.trim();
    const a2 = els.apiArg2.value.trim();
    let result;
    try {
      switch (method) {
        case "lookup":
          result = crypto.lookup(a1);
          break;
        case "search":
          result = crypto.search(a1, { limit: 12 });
          break;
        case "suggest":
          result = crypto.suggest(a1);
          break;
        case "recommend":
          result = crypto.recommend(a1);
          break;
        case "getLibrary":
          result = crypto.getLibrary(a1);
          break;
        case "getConcept":
          result = crypto.getConcept(a1);
          break;
        case "related":
          result = crypto.related(a1);
          break;
        case "path":
          result = crypto.path(a1, a2);
          break;
        case "stats":
          result = crypto.stats();
          break;
        case "describe":
          result = crypto.describe();
          break;
        default:
          result = { error: "unknown method" };
      }
    } catch (err) {
      result = { error: String(err.message || err) };
    }
    els.apiOutput.textContent = JSON.stringify(result, null, 2);
  }

  function runRecommend() {
    const need = els.recommendInput?.value || "";
    const result = api()?.recommend(need);
    if (els.recommendOutput) {
      els.recommendOutput.textContent = JSON.stringify(result, null, 2);
    }
  }

  function bootUi() {
    const raw = atlasRaw();
    if (els.disclaimer) {
      els.disclaimer.textContent =
        raw?.meta?.disclaimer ||
        "Thư viện giáo dục MaMoCrypto — ưu tiên thư viện đã kiểm chứng.";
    }
    renderTaxonomy();
    renderLangChips();
    render();
    if (els.apiOutput && api()) {
      els.apiOutput.textContent = JSON.stringify(api().stats(), null, 2);
    }
  }

  els.navBtns.forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });
  els.search?.addEventListener("input", () => {
    state.query = els.search.value;
    render();
  });
  document.getElementById("btn-api-run")?.addEventListener("click", runApi);
  document.getElementById("btn-recommend")?.addEventListener("click", runRecommend);

  // Wait for crypto bootstrap if needed
  if (api()?.lookup) {
    bootUi();
  } else if (window.MaMoCryptoCore) {
    window.MaMoCryptoCore.on("ready", bootUi);
    // fallback timer
    setTimeout(bootUi, 200);
  } else {
    bootUi();
  }
})();
