(() => {
  "use strict";

  const atlas = window.CRYPTO_ATLAS;
  const netMeta = window.NETWORK_MAP;
  if (!atlas || !netMeta || !window.buildCryptoNetwork) {
    console.error("Atlas / network map missing");
    return;
  }

  const graph = window.buildCryptoNetwork(atlas, netMeta);
  const nodeById = Object.fromEntries(graph.nodes.map((n) => [n.id, n]));

  const KIND_META = {
    hub: { label: "Hub thư viện", color: "#c45c3e" },
    group: { label: "Nhóm", color: "#d9c4a5" },
    concept: { label: "Khái niệm", color: "#2a9a8f" },
    library: { label: "Thư viện", color: "#9e3f28" },
    lang: { label: "Ngôn ngữ", color: "#7eb8b2" },
  };

  const state = {
    query: "",
    kinds: { hub: true, group: true, concept: true, library: true, lang: true },
    selectedId: null,
    showLibPaths: true,
    pathHighlight: null, // { nodeIds:Set, edgeIds:Set }
    transform: { x: 0, y: 0, k: 1 },
    sim: null,
    width: 800,
    height: 560,
  };

  const svg = document.getElementById("network-svg");
  const wrap = document.getElementById("canvas-wrap");
  const statsEl = document.getElementById("mapper-stats");
  const sideDetail = document.getElementById("side-detail");
  const neighborList = document.getElementById("neighbor-list");
  const pathList = document.getElementById("path-list");
  const searchInput = document.getElementById("mapper-search");
  const kindFilters = document.getElementById("kind-filters");
  const legendEl = document.getElementById("legend");

  const gRoot = document.createElementNS("http://www.w3.org/2000/svg", "g");
  gRoot.setAttribute("class", "viewport");
  const gEdges = document.createElementNS("http://www.w3.org/2000/svg", "g");
  gEdges.setAttribute("class", "edges");
  const gEdgeIcons = document.createElementNS("http://www.w3.org/2000/svg", "g");
  gEdgeIcons.setAttribute("class", "edge-icons");
  const gNodes = document.createElementNS("http://www.w3.org/2000/svg", "g");
  gNodes.setAttribute("class", "nodes");
  gRoot.appendChild(gEdges);
  gRoot.appendChild(gEdgeIcons);
  gRoot.appendChild(gNodes);
  svg.appendChild(gRoot);

  const ICONS = Object.assign(
    {},
    window.ICON_SVG || {},
    {
    layers: "M4 8l8-4 8 4-8 4-8-4zm0 4l8 4 8-4M4 16l8 4 8-4",
    key: "M14 8a4 4 0 11-4 4h-4v3H4v-3H2v-2h8a4 4 0 014-2zm2 2a1.5 1.5 0 100-3 1.5 1.5 0 000 3z",
    lock: "M7 10V7a5 5 0 0110 0v3h1a1 1 0 011 1v8a1 1 0 01-1 1H6a1 1 0 01-1-1v-8a1 1 0 011-1h1zm2 0h6V7a3 3 0 00-6 0v3z",
    keypair: "M8 10a3 3 0 110-6 3 3 0 010 6zm0-2a1 1 0 100-2 1 1 0 000 2zm8 8a3 3 0 110-6 3 3 0 010 6zm-1-3h-6v2h6v-2z",
    hash: "M9 4l-1 16M16 4l-1 16M5 9h14M4 15h14",
    network: "M5 12a2 2 0 110-4 2 2 0 010 4zm14 0a2 2 0 110-4 2 2 0 010 4zM12 19a2 2 0 110-4 2 2 0 010 4zM6.5 10.5l11-3M17.5 10.5l-4 5M6.7 11.5l4 5",
    atom: "M12 12m-2 0a2 2 0 104 0 2 2 0 10-4 0M12 4c4 3 4 13 0 16M12 4c-4 3-4 13 0 16M4 10c5-4 11-4 16 0M4 14c5 4 11 4 16 0",
    text: "M5 7h14M5 12h10M5 17h12",
    cube: "M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3zm0 2.2L6.5 8.4v7.2L12 18.8l5.5-3.2V8.4L12 5.2zM12 12l5.2-3M12 12v6.5M12 12L6.8 9",
    code: "M8 8l-4 4 4 4M16 8l4 4-4 4M13 6l-2 12",
    compass: "M12 4a8 8 0 100 16 8 8 0 000-16zm2.5 5.5l-5 2 2 5 5-2-2-5z",
    scroll: "M7 5h9a2 2 0 012 2v11H8a2 2 0 01-2-2V5zm1 2v10h1V7H8zm3 2h5v2h-5V9zm0 4h5v2h-5v-2z",
    cpu: "M8 8h8v8H8V8zm2 2v4h4v-4h-4zM11 4v3M13 4v3M11 17v3M13 17v3M4 11h3M4 13h3M17 11h3M17 13h3",
    spark: "M12 3l1.5 5.5L19 10l-5.5 1.5L12 17l-1.5-5.5L5 10l5.5-1.5L12 3z",
    monitor: "M4 6h16v10H4V6zm2 12h12v1H6v-1z",
    chip: "M8 8h8v8H8V8zm-2 2H4v1h2v-1zm0 3H4v1h2v-1zm12-3h2v1h-2v-1zm0 3h2v1h-2v-1zM10 4v2h1V4h-1zm3 0v2h1V4h-1zM10 18v2h1v-2h-1zm3 0v2h1v-2h-1z",
    wrench: "M14.5 5.5a3.5 3.5 0 00-4.6 4.6L4 16v4h4l5.9-5.9a3.5 3.5 0 004.6-4.6l-2.5 1.5-2-2 1.5-2.5z",
  }
  );

  function normalize(s) {
    return (s || "")
      .toString()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();
  }

  function nodeRadius(n) {
    if (n.kind === "hub") return 26;
    if (n.kind === "group") return 22;
    if (n.kind === "library") return 17;
    if (n.kind === "lang") return 12;
    return 14;
  }

  function visibleNodes() {
    return graph.nodes.filter((n) => state.kinds[n.kind] !== false);
  }

  function visibleSet() {
    return new Set(visibleNodes().map((n) => n.id));
  }

  function visibleEdges(vis) {
    return graph.edges.filter((e) => vis.has(e.source) && vis.has(e.target));
  }

  function matchIds() {
    const q = normalize(state.query).trim();
    if (!q) return null;
    const set = new Set();
    visibleNodes().forEach((n) => {
      if (normalize(n.searchText || n.label).includes(q)) set.add(n.id);
    });
    const expanded = new Set(set);
    visibleEdges(visibleSet()).forEach((e) => {
      if (set.has(e.source) || set.has(e.target)) {
        expanded.add(e.source);
        expanded.add(e.target);
      }
    });
    return { hit: set, context: expanded };
  }

  function neighborsOf(id) {
    const out = [];
    graph.edges.forEach((e) => {
      if (e.source === id) out.push({ edge: e, other: e.target, dir: "out" });
      if (e.target === id) out.push({ edge: e, other: e.source, dir: "in" });
    });
    return out;
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function iconPath(name) {
    return ICONS[name] || ICONS.cube;
  }

  function createIcon(iconName, r, stroke = "#f4faf9") {
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("class", "node-icon");
    const scale = (r * 0.7) / 12;
    g.setAttribute("transform", `translate(${-8 * scale}, ${-8 * scale}) scale(${scale})`);
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", iconPath(iconName));
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", stroke);
    path.setAttribute("stroke-width", "1.6");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    g.appendChild(path);
    return g;
  }

  function createEdgeIcon(e, ax, ay, bx, by, hot) {
    const mx = (ax + bx) / 2;
    const my = (ay + by) / 2;
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("class", "edge-icon" + (hot ? " is-hot" : ""));
    g.setAttribute("transform", `translate(${mx},${my})`);
    const bg = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    bg.setAttribute("r", 8);
    bg.setAttribute("class", "edge-icon-bg");
    g.appendChild(bg);
    const iconName =
      e.icon ||
      (nodeById[e.target]?.kind === "library"
        ? nodeById[e.target].icon
        : "cube");
    g.appendChild(createIcon(iconName, 11, hot ? "#0c2a2e" : "#f4faf9"));
    g.setAttribute("data-edge", e.id);
    return g;
  }

  /** Mọi cạnh nằm trên ít nhất một đường ngắn tới thư viện từ selected / hub */
  function computeLibPathHighlight(fromId) {
    if (!window.pathsToLibraries || !fromId) return null;
    const paths = window.pathsToLibraries(graph, fromId);
    const nodeIds = new Set([fromId]);
    const edgeIds = new Set();
    paths.forEach((p) => {
      p.nodes.forEach((id) => nodeIds.add(id));
      p.edges.forEach((e) => edgeIds.add(e.id));
    });
    return { nodeIds, edgeIds, paths };
  }

  function allToLibraryEdgeIds() {
    const set = new Set();
    graph.edges.forEach((e) => {
      if (
        e.toLibrary ||
        e.pathRole === "to-library" ||
        e.kind === "implemented-by" ||
        e.kind === "hub-link" ||
        e.kind === "provides-match" ||
        e.kind === "depends" ||
        nodeById[e.target]?.kind === "library" ||
        nodeById[e.source]?.kind === "library"
      ) {
        set.add(e.id);
      }
    });
    return set;
  }

  function measure() {
    const rect = wrap.getBoundingClientRect();
    state.width = Math.max(320, rect.width);
    state.height = Math.max(420, rect.height);
    svg.setAttribute("viewBox", `0 0 ${state.width} ${state.height}`);
  }

  function seedPositions(nodes) {
    const cx = state.width / 2;
    const cy = state.height / 2;
    nodes.forEach((n, i) => {
      if (n.x != null && n.y != null) return;
      if (n.id === "hub:crypto-libs") {
        n.x = cx;
        n.y = cy;
        n.vx = 0;
        n.vy = 0;
        return;
      }
      const ring = n.ring != null ? n.ring : 1;
      const baseR = 80 + ring * 100;
      const angle =
        n.angle != null
          ? n.angle
          : (i / Math.max(nodes.length, 1)) * Math.PI * 2 + ring * 0.35;
      n.x = cx + Math.cos(angle) * baseR + (Math.random() - 0.5) * 16;
      n.y = cy + Math.sin(angle) * baseR + (Math.random() - 0.5) * 16;
      n.vx = 0;
      n.vy = 0;
    });
  }

  function runSimulation(reset) {
    const nodes = visibleNodes();
    const vis = new Set(nodes.map((n) => n.id));
    const edges = visibleEdges(vis).map((e) => ({
      ...e,
      sourceNode: nodeById[e.source],
      targetNode: nodeById[e.target],
    }));

    if (reset) {
      nodes.forEach((n) => {
        n.x = undefined;
        n.y = undefined;
      });
    }
    seedPositions(nodes);

    let ticks = 0;
    const maxTicks = 300;

    function tick() {
      for (let i = 0; i < nodes.length; i += 1) {
        for (let j = i + 1; j < nodes.length; j += 1) {
          const a = nodes[i];
          const b = nodes[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let dist = Math.hypot(dx, dy) || 0.01;
          const minDist = nodeRadius(a) + nodeRadius(b) + 26;
          if (dist < minDist * 3) {
            const force = ((minDist - dist) / dist) * 0.08;
            dx *= force;
            dy *= force;
            a.vx += dx;
            a.vy += dy;
            b.vx -= dx;
            b.vy -= dy;
          }
        }
      }

      edges.forEach((e) => {
        const a = e.sourceNode;
        const b = e.targetNode;
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        const dist = Math.hypot(dx, dy) || 0.01;
        const ideal =
          e.kind === "hub-link"
            ? 120
            : e.kind === "contains"
              ? 90
              : e.kind === "depends"
                ? 100
                : 125;
        const f = ((dist - ideal) / dist) * 0.02;
        dx *= f;
        dy *= f;
        a.vx += dx;
        a.vy += dy;
        b.vx -= dx;
        b.vy -= dy;
      });

      const cx = state.width / 2;
      const cy = state.height / 2;
      nodes.forEach((n) => {
        if (n.id === "hub:crypto-libs") {
          n.vx += (cx - n.x) * 0.05;
          n.vy += (cy - n.y) * 0.05;
        } else {
          n.vx += (cx - n.x) * 0.003;
          n.vy += (cy - n.y) * 0.003;
        }
        n.vx *= 0.85;
        n.vy *= 0.85;
        n.x += n.vx;
        n.y += n.vy;
        n.x = Math.max(28, Math.min(state.width - 28, n.x));
        n.y = Math.max(28, Math.min(state.height - 28, n.y));
      });

      draw();
      ticks += 1;
      if (ticks < maxTicks) state.sim = requestAnimationFrame(tick);
      else state.sim = null;
    }

    if (state.sim) cancelAnimationFrame(state.sim);
    state.sim = requestAnimationFrame(tick);
  }

  function applyTransform() {
    const { x, y, k } = state.transform;
    gRoot.setAttribute("transform", `translate(${x},${y}) scale(${k})`);
  }

  function draw() {
    const vis = visibleSet();
    const edges = visibleEdges(vis);
    const matches = matchIds();
    const selected = state.selectedId;
    const libEdgeIds = state.showLibPaths ? allToLibraryEdgeIds() : null;
    const pathHL = state.pathHighlight;

    gEdges.innerHTML = "";
    gEdgeIcons.innerHTML = "";

    edges.forEach((e) => {
      const a = nodeById[e.source];
      const b = nodeById[e.target];
      if (!a || !b || a.x == null || b.x == null) return;

      const onLibPath = libEdgeIds?.has(e.id);
      const onSelectedPath = pathHL?.edgeIds?.has(e.id);
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", a.x);
      line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x);
      line.setAttribute("y2", b.y);
      line.setAttribute("class", "edge");
      if (onLibPath) line.classList.add("is-lib-path");
      if (onSelectedPath) line.classList.add("is-path-hot");
      if (
        matches &&
        (!matches.context.has(e.source) || !matches.context.has(e.target))
      ) {
        line.classList.add("is-dim");
      }
      if (matches && matches.hit.has(e.source) && matches.hit.has(e.target)) {
        line.classList.add("is-hot");
      }
      if (selected && (e.source === selected || e.target === selected)) {
        line.classList.add("is-selected");
      }
      gEdges.appendChild(line);

      // Icon giữa cạnh — mọi đường tới thư viện
      if (onLibPath || onSelectedPath || e.toLibrary || e.pathRole === "to-library") {
        const hot = !!(onSelectedPath || (selected && (e.source === selected || e.target === selected)));
        gEdgeIcons.appendChild(createEdgeIcon(e, a.x, a.y, b.x, b.y, hot));
      }
    });

    gNodes.innerHTML = "";
    visibleNodes().forEach((n) => {
      if (n.x == null) return;
      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.setAttribute("class", "node");
      g.setAttribute("transform", `translate(${n.x},${n.y})`);
      g.dataset.id = n.id;

      if (matches && !matches.context.has(n.id)) g.classList.add("is-dim");
      if (matches && matches.hit.has(n.id)) g.classList.add("is-match");
      if (selected === n.id) g.classList.add("is-selected");
      if (pathHL?.nodeIds?.has(n.id)) g.classList.add("is-on-path");
      if (n.kind === "library") g.classList.add("is-library");
      if (n.kind === "hub") g.classList.add("is-hub");

      const r = nodeRadius(n);
      const color = KIND_META[n.kind]?.color || "#2a9a8f";

      const halo = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      halo.setAttribute("class", "node-halo");
      halo.setAttribute("r", r + 8);
      g.appendChild(halo);

      const core = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      core.setAttribute("class", "node-core");
      core.setAttribute("r", r);
      core.setAttribute("fill", color);
      g.appendChild(core);
      g.appendChild(createIcon(n.icon || "cube", r));

      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("class", "node-label");
      label.setAttribute("y", r + 14);
      label.textContent =
        n.label.length > 18 ? `${n.label.slice(0, 16)}…` : n.label;
      g.appendChild(label);

      g.addEventListener("click", (ev) => {
        ev.stopPropagation();
        selectNode(n.id);
      });
      gNodes.appendChild(g);
    });

    const libCount = graph.nodes.filter((n) => n.kind === "library").length;
    const libEdges = libEdgeIds ? libEdgeIds.size : 0;
    const matchCount = matches ? matches.hit.size : visibleNodes().length;
    statsEl.textContent = `${visibleNodes().length} node · ${edges.length} cạnh · ${libCount} thư viện · ${libEdges} đường icon → lib · khớp: ${matchCount}${
      selected ? ` · chọn: ${nodeById[selected]?.label || ""}` : ""
    }`;
  }

  function renderPathsPanel(fromId) {
    if (!pathList) return;
    if (!window.pathsToLibraries || !fromId) {
      pathList.innerHTML = "";
      return;
    }
    const paths = window.pathsToLibraries(graph, fromId);
    const hl = computeLibPathHighlight(fromId);
    state.pathHighlight = hl;

    pathList.innerHTML = `<h3>Đường đến thư viện (${paths.length})</h3><ul></ul>`;
    const ul = pathList.querySelector("ul");
    paths.slice(0, 40).forEach((p) => {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "neighbor-btn path-btn";
      const via = p.nodes
        .map((id) => nodeById[id]?.label || id)
        .join(" → ");
      const army = window.NETWORK_MAP?.iconArmy || {};
      const iconCalls = (p.icons || p.nodes.map((id) => nodeById[id]?.icon).filter(Boolean) || [])
        .map((n) => army[n]?.call || n)
        .filter(Boolean);
      const uniqueCalls = [...new Set(iconCalls)];
      btn.innerHTML = `<strong>${escapeHtml(p.label)}</strong><small>${escapeHtml(
        via
      )} · ${p.length} bước</small><small class="icon-call-line">Mapper gọi: ${escapeHtml(
        uniqueCalls.join(" → ") || "—"
      )}</small>`;
      btn.addEventListener("click", () => {
        state.pathHighlight = {
          nodeIds: new Set(p.nodes),
          edgeIds: new Set(p.edges.map((e) => e.id)),
          paths: [p],
        };
        selectNode(p.to, true);
        focusNode(nodeById[p.to]);
        draw();
      });
      li.appendChild(btn);
      ul.appendChild(li);
    });
  }

  function selectNode(id, skipPathRecompute) {
    state.selectedId = id;
    const n = nodeById[id];
    if (!n) return;

    const kindLabel = KIND_META[n.kind]?.label || n.kind;
    sideDetail.innerHTML = `
      <div class="meta">
        <span class="tag">${escapeHtml(kindLabel)}</span>
        ${n.tier ? `<span class="tag">${escapeHtml(n.tier)}</span>` : ""}
        ${n.level ? `<span class="tag">${escapeHtml(n.level)}</span>` : ""}
        ${n.category ? `<span class="tag">${escapeHtml(n.category)}</span>` : ""}
        <span class="tag">icon:${escapeHtml(n.icon || "?")}</span>
        ${
          window.NETWORK_MAP?.iconArmy?.[n.icon]
            ? `<span class="tag">${escapeHtml(window.NETWORK_MAP.iconArmy[n.icon].call)}</span>`
            : ""
        }
      </div>
      <h2>${escapeHtml(n.label)}</h2>
      <p>${escapeHtml(n.summary || "")}</p>
      ${
        window.NETWORK_MAP?.iconArmy?.[n.icon]
          ? `<p class="icon-motto"><strong>Mapper gọi:</strong> ${escapeHtml(
              window.NETWORK_MAP.iconArmy[n.icon].call
            )} — ${escapeHtml(window.NETWORK_MAP.iconArmy[n.icon].motto || "")}</p>`
          : ""
      }
      ${
        n.provides?.length
          ? `<p><strong>Cung cấp:</strong> ${escapeHtml(n.provides.join(", "))}</p>`
          : ""
      }
      ${
        n.url
          ? `<p><a href="${escapeHtml(n.url)}" target="_blank" rel="noopener noreferrer">Tài liệu</a></p>`
          : ""
      }
    `;

    const neigh = neighborsOf(id).filter(
      (x) => state.kinds[nodeById[x.other]?.kind] !== false
    );
    neighborList.innerHTML = `<h3>Liên kết (${neigh.length})</h3><ul></ul>`;
    const ul = neighborList.querySelector("ul");
    neigh.forEach((x) => {
      const other = nodeById[x.other];
      if (!other) return;
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "neighbor-btn";
      btn.innerHTML = `<strong>${escapeHtml(other.label)}</strong><small>${escapeHtml(
        x.edge.label
      )} · ${escapeHtml(KIND_META[other.kind]?.label || other.kind)}</small>`;
      btn.addEventListener("click", () => {
        selectNode(other.id);
        focusNode(other);
      });
      li.appendChild(btn);
      ul.appendChild(li);
    });

    if (!skipPathRecompute) renderPathsPanel(id);
    draw();
  }

  function focusNode(n) {
    if (!n || n.x == null) return;
    const k = Math.max(state.transform.k, 1.1);
    state.transform.k = k;
    state.transform.x = state.width / 2 - n.x * k;
    state.transform.y = state.height / 2 - n.y * k;
    applyTransform();
  }

  function fitView() {
    const nodes = visibleNodes().filter((n) => n.x != null);
    if (!nodes.length) return;
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    nodes.forEach((n) => {
      minX = Math.min(minX, n.x);
      minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x);
      maxY = Math.max(maxY, n.y);
    });
    const pad = 60;
    const bw = maxX - minX || 1;
    const bh = maxY - minY || 1;
    const k = Math.min(
      (state.width - pad * 2) / bw,
      (state.height - pad * 2) / bh,
      1.8
    );
    state.transform.k = k;
    state.transform.x = state.width / 2 - ((minX + maxX) / 2) * k;
    state.transform.y = state.height / 2 - ((minY + maxY) / 2) * k;
    applyTransform();
  }

  function renderKindFilters() {
    kindFilters.innerHTML = "";
    Object.entries(KIND_META).forEach(([kind, meta]) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "kind-chip" + (state.kinds[kind] !== false ? " is-active" : "");
      btn.innerHTML = `<span class="dot" style="background:${meta.color}"></span>${meta.label}`;
      btn.addEventListener("click", () => {
        state.kinds[kind] = !(state.kinds[kind] !== false);
        if (!Object.values(state.kinds).some(Boolean)) state.kinds[kind] = true;
        renderKindFilters();
        runSimulation(false);
        draw();
      });
      kindFilters.appendChild(btn);
    });
  }

  function renderLegend() {
    legendEl.innerHTML =
      Object.entries(KIND_META)
        .map(
          ([, meta]) =>
            `<div class="legend-item"><span class="legend-swatch" style="background:${meta.color}"></span>${meta.label}</div>`
        )
        .join("") +
      `<div class="legend-item"><span class="legend-swatch legend-edge-icon"></span>Icon đường → thư viện</div>`;
  }

  // Pan / zoom
  let panning = false;
  let panStart = { x: 0, y: 0, tx: 0, ty: 0 };

  wrap.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".node")) return;
    panning = true;
    wrap.classList.add("is-panning");
    wrap.setPointerCapture(e.pointerId);
    panStart = {
      x: e.clientX,
      y: e.clientY,
      tx: state.transform.x,
      ty: state.transform.y,
    };
  });
  wrap.addEventListener("pointermove", (e) => {
    if (!panning) return;
    state.transform.x = panStart.tx + (e.clientX - panStart.x);
    state.transform.y = panStart.ty + (e.clientY - panStart.y);
    applyTransform();
  });
  wrap.addEventListener("pointerup", () => {
    panning = false;
    wrap.classList.remove("is-panning");
  });
  wrap.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      const rect = wrap.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const oldK = state.transform.k;
      const next = Math.min(3, Math.max(0.35, oldK * (e.deltaY < 0 ? 1.1 : 0.9)));
      const wx = (mx - state.transform.x) / oldK;
      const wy = (my - state.transform.y) / oldK;
      state.transform.k = next;
      state.transform.x = mx - wx * next;
      state.transform.y = my - wy * next;
      applyTransform();
    },
    { passive: false }
  );

  searchInput.addEventListener("input", () => {
    state.query = searchInput.value;
    draw();
    const matches = matchIds();
    if (matches && matches.hit.size === 1) {
      const id = [...matches.hit][0];
      selectNode(id);
      focusNode(nodeById[id]);
    }
  });

  document.getElementById("btn-fit")?.addEventListener("click", fitView);
  document.getElementById("btn-relayout")?.addEventListener("click", () => {
    state.transform = { x: 0, y: 0, k: 1 };
    applyTransform();
    runSimulation(true);
  });
  document.getElementById("btn-clear")?.addEventListener("click", () => {
    searchInput.value = "";
    state.query = "";
    state.selectedId = null;
    state.pathHighlight = null;
    sideDetail.innerHTML =
      "<h2>Chọn một node</h2><p>Icon trên mọi đường dẫn tới thư viện mật mã. Chạm hub hoặc khái niệm để xem đường đi.</p>";
    neighborList.innerHTML = "";
    if (pathList) pathList.innerHTML = "";
    draw();
  });

  document.getElementById("toggle-lib-paths")?.addEventListener("change", (e) => {
    state.showLibPaths = e.target.checked;
    draw();
  });

  document.getElementById("btn-focus-hub")?.addEventListener("click", () => {
    const hub = nodeById["hub:crypto-libs"];
    if (hub) {
      selectNode(hub.id);
      focusNode(hub);
    }
  });

  window.addEventListener("resize", () => {
    measure();
    draw();
  });

  function renderIconAtlasPanel() {
    const status = document.getElementById("icon-atlas-status");
    const list = document.getElementById("icon-atlas-list");
    if (!list || !window.buildIconLibraryAtlas) {
      if (status) status.textContent = "Thiếu icon-atlas.js";
      return;
    }
    const atlasMap = window.buildIconLibraryAtlas(atlas, netMeta, graph);
    window.__MAMO_ICON_ATLAS__ = atlasMap;
    if (status) {
      status.textContent = atlasMap.ok
        ? `${atlasMap.iconCount} icon · ${atlasMap.libraryCount} thư viện · docs đủ 100%`
        : `Thiếu: icons ${atlasMap.coverage.missingDocs.join(", ") || "—"} · libs ${atlasMap.coverage.incompleteLibs.join(", ") || "—"}`;
    }
    list.innerHTML = atlasMap.icons
      .map((e) => {
        const libs = e.libraries
          .slice(0, 8)
          .map(
            (l) =>
              `<a href="${escapeHtml(l.url || "#")}" target="_blank" rel="noopener noreferrer">${escapeHtml(
                l.name
              )}</a>`
          )
          .join(", ");
        const more =
          e.libraryCount > 8 ? ` +${e.libraryCount - 8}` : "";
        return `<details class="icon-doc-card" data-icon="${escapeHtml(e.icon)}">
          <summary>
            <span class="icon-doc-name">${escapeHtml(e.call)}</span>
            <code>${escapeHtml(e.icon)}</code>
            <span class="tag">${e.libraryCount} lib</span>
            ${e.docsComplete ? '<span class="tag ok">docs✓</span>' : '<span class="tag bad">docs✗</span>'}
          </summary>
          <p>${escapeHtml(e.documentation.body || e.motto || "")}</p>
          <p class="icon-doc-libs"><strong>Thư viện:</strong> ${libs}${more}</p>
          <p class="icon-doc-appear">Xuất hiện: ${e.appears.nodes} node · ${e.appears.edges} cạnh · kinds: ${escapeHtml(
            (e.appears.kinds || []).join(", ")
          )}</p>
        </details>`;
      })
      .join("");

    list.querySelectorAll(".icon-doc-card").forEach((card) => {
      card.addEventListener("toggle", () => {
        if (!card.open) return;
        const icon = card.getAttribute("data-icon");
        const hit = graph.nodes.find((n) => n.icon === icon);
        if (hit) {
          selectNode(hit.id);
          focusNode(hit);
          draw();
        }
      });
    });
  }

  renderKindFilters();
  renderLegend();
  renderIconAtlasPanel();
  measure();
  applyTransform();
  runSimulation(true);

  setTimeout(() => {
    fitView();
    const hub = nodeById["hub:crypto-libs"];
    if (hub) selectNode(hub.id);
    draw();
  }, 950);
})();
