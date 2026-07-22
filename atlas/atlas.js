(() => {
  "use strict";

  const atlas = window.CRYPTO_ATLAS;
  if (!atlas) {
    console.error("CRYPTO_ATLAS missing");
    return;
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
  };

  const categoryName = Object.fromEntries(
    atlas.taxonomy.map((t) => [t.id, t.name])
  );

  els.disclaimer.textContent = atlas.meta.disclaimer;

  function normalize(s) {
    return (s || "")
      .toString()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();
  }

  function matchesQuery(parts) {
    const q = normalize(state.query).trim();
    if (!q) return true;
    return parts.some((p) => normalize(p).includes(q));
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
    const items = [
      { id: "all", name: "Tất cả", summary: "Toàn bộ mục trong atlas" },
      ...atlas.taxonomy,
    ];
    els.taxonomy.innerHTML = "";
    items.forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "filter-btn" + (state.category === item.id ? " is-active" : "");
      btn.innerHTML = `<strong>${item.name}</strong><small>${item.summary}</small>`;
      btn.addEventListener("click", () => {
        state.category = item.id;
        renderTaxonomy();
        render();
      });
      els.taxonomy.appendChild(btn);
    });
  }

  function allLanguages() {
    const set = new Set();
    atlas.libraries.forEach((lib) => {
      lib.languages.forEach((l) => set.add(l));
    });
    return ["all", ...[...set].sort((a, b) => a.localeCompare(b))];
  }

  function renderLangChips() {
    els.langChips.innerHTML = "";
    allLanguages().forEach((lang) => {
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
    return atlas.concepts.filter((c) => {
      if (state.category !== "all" && c.category !== state.category) return false;
      return matchesQuery([
        c.name,
        c.summary,
        c.level,
        categoryName[c.category],
        ...(c.details || []),
        ...(c.related || []),
      ]);
    });
  }

  function filteredLibraries() {
    return atlas.libraries.filter((lib) => {
      if (state.lang !== "all" && !lib.languages.includes(state.lang)) return false;
      // category filter for libs: map taxonomy loosely via provides/category keywords
      if (state.category !== "all") {
        const blob = normalize(
          [lib.category, lib.summary, ...(lib.provides || []), lib.tier].join(" ")
        );
        const cat = normalize(state.category + " " + (categoryName[state.category] || ""));
        const map = {
          foundations: /platform|engine|modern|nguyen|nen/,
          classical: /classical|co dien/,
          symmetric: /aes|chacha|aead|secretbox|symmetric|doi xung/,
          asymmetric: /rsa|ec|ed25519|x25519|sign|bat doi|asymmetric|hybrid/,
          "hash-mac-kdf": /hash|hmac|argon|blake|sha|kdf|mac|bam/,
          protocols: /tls|pgp|jose|signal|protocol|ssh|age/,
          "post-quantum": /pqc|quantum|ml-kem|ml-dsa|oqs|post/,
          encoding: /encode|base64|hex|morse/,
        };
        const re = map[state.category];
        if (re && !re.test(blob) && !blob.includes(normalize(state.category))) {
          // still allow if query empty and category is encoding-only miss — skip
          if (!matchesQuery([lib.name]) || state.query) {
            /* fall through to query check but require category hit */
          }
          if (!re.test(blob)) return false;
        }
      }
      return matchesQuery([
        lib.name,
        lib.summary,
        lib.tier,
        lib.category,
        lib.notes || "",
        ...(lib.languages || []),
        ...(lib.bindings || []),
        ...(lib.provides || []),
      ]);
    });
  }

  function openConcept(c) {
    els.detailTitle.textContent = c.name;
    els.detailBody.innerHTML = `
      <div class="meta-row">
        <span class="tag">${categoryName[c.category] || c.category}</span>
        <span class="tag">${c.level}</span>
      </div>
      <p>${escapeHtml(c.summary)}</p>
      <h3>Chi tiết</h3>
      <ul>${(c.details || []).map((d) => `<li>${escapeHtml(d)}</li>`).join("")}</ul>
      ${
        c.related?.length
          ? `<h3>Liên quan</h3><div class="meta-row">${c.related
              .map((id) => `<span class="tag">${escapeHtml(id)}</span>`)
              .join("")}</div>`
          : ""
      }
    `;
    els.dialog.showModal();
  }

  function openLibrary(lib) {
    els.detailTitle.textContent = lib.name;
    els.detailBody.innerHTML = `
      <div class="meta-row">
        <span class="tag">${escapeHtml(lib.tier)}</span>
        <span class="tag">${escapeHtml(lib.category)}</span>
        ${(lib.languages || []).map((l) => `<span class="tag">${escapeHtml(l)}</span>`).join("")}
      </div>
      <p>${escapeHtml(lib.summary)}</p>
      <h3>Cung cấp</h3>
      <ul>${(lib.provides || []).map((p) => `<li>${escapeHtml(p)}</li>`).join("")}</ul>
      ${
        lib.bindings?.length
          ? `<h3>Bindings / hệ sinh thái</h3><ul>${lib.bindings
              .map((b) => `<li>${escapeHtml(b)}</li>`)
              .join("")}</ul>`
          : ""
      }
      ${lib.notes ? `<h3>Ghi chú</h3><p>${escapeHtml(lib.notes)}</p>` : ""}
      ${
        lib.url
          ? `<p><a href="${escapeAttr(lib.url)}" target="_blank" rel="noopener noreferrer">Tài liệu chính thức</a></p>`
          : ""
      }
    `;
    els.dialog.showModal();
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(str) {
    return escapeHtml(str).replace(/'/g, "&#39;");
  }

  function renderConcepts() {
    const list = filteredConcepts();
    els.conceptsGrid.innerHTML = "";
    list.forEach((c) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "atlas-card";
      btn.innerHTML = `
        <span class="card-kicker">${escapeHtml(categoryName[c.category] || "")} · ${escapeHtml(c.level)}</span>
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
        <span class="card-kicker">${escapeHtml(lib.category)} · ${escapeHtml(lib.tier)}</span>
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
    els.guideList.innerHTML = "";
    atlas.decisionGuide.forEach((g) => {
      if (
        !matchesQuery([g.need, g.pick]) &&
        state.query.trim()
      ) {
        return;
      }
      const div = document.createElement("div");
      div.className = "guide-item";
      div.innerHTML = `<strong>${escapeHtml(g.need)}</strong><span>${escapeHtml(g.pick)}</span>`;
      els.guideList.appendChild(div);
    });

    els.cheatDo.innerHTML = atlas.cheatSheet.do
      .map((x) => `<li>${escapeHtml(x)}</li>`)
      .join("");
    els.cheatDont.innerHTML = atlas.cheatSheet.dont
      .map((x) => `<li>${escapeHtml(x)}</li>`)
      .join("");

    return els.guideList.children.length;
  }

  function render() {
    let count = 0;
    if (state.view === "concepts") count = renderConcepts();
    if (state.view === "libraries") count = renderLibraries();
    if (state.view === "guide") count = renderGuide();
    els.empty.hidden = count > 0 || state.view === "guide";
    if (state.view === "guide" && count === 0 && state.query.trim()) {
      els.empty.hidden = false;
    }
  }

  els.navBtns.forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });

  els.search.addEventListener("input", () => {
    state.query = els.search.value;
    render();
  });

  renderTaxonomy();
  renderLangChips();
  render();
})();
