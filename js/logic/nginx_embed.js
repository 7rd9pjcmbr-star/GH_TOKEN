/**
 * nginx_embed — module client: mô tả + gọi đơn qua nginx khi stack đang up.
 * On-demand server: python3 scripts/nginx_order_embed.py once|start|orders|stop
 * Owns: nginx-order-embed, upstream-order-call
 */
(function (global) {
  "use strict";

  const DEFAULT_BASE = "http://127.0.0.1:18080";

  const EMBEDDED_VARS = [
    "$upstream_addr",
    "$upstream_status",
    "$upstream_response_time",
    "$upstream_connect_time",
    "$upstream_header_time",
    "$upstream_bytes_received",
    "$upstream_bytes_sent",
    "$upstream_response_length",
    "$upstream_cache_status",
  ];

  const HEADER_TO_VAR = {
    "x-upstream-addr": "$upstream_addr",
    "x-upstream-status": "$upstream_status",
    "x-upstream-response-time": "$upstream_response_time",
    "x-upstream-connect-time": "$upstream_connect_time",
    "x-upstream-header-time": "$upstream_header_time",
    "x-upstream-bytes-received": "$upstream_bytes_received",
    "x-upstream-bytes-sent": "$upstream_bytes_sent",
    "x-upstream-response-length": "$upstream_response_length",
    "x-upstream-cache-status": "$upstream_cache_status",
  };

  function describe() {
    return {
      module: "nginx_embed",
      title: "Nhúng gọi đơn qua nginx (on-demand)",
      whenNeeded: true,
      flow: "client → nginx:18080/orders → mock upstream:18081",
      embeddedVars: EMBEDDED_VARS,
      cli: {
        once: "python3 scripts/nginx_order_embed.py once",
        start: "python3 scripts/nginx_order_embed.py start",
        orders: "python3 scripts/nginx_order_embed.py orders",
        stop: "python3 scripts/nginx_order_embed.py stop",
        status: "python3 scripts/nginx_order_embed.py status",
        test: "python3 scripts/nginx_order_embed_test.py",
      },
      python: {
        once: "from nginx_order_embed import run_when_needed; run_when_needed()",
        module: "NginxOrderEmbed().once() / .ensure_up() / .call_orders() / .stop()",
      },
      note: "Browser chỉ gọi được khi stack đã start trên máy; bật bằng CLI/panel khi cần.",
    };
  }

  function extractEmbedded(headers) {
    const out = {};
    if (!headers) return out;
    const get = (name) => {
      if (typeof headers.get === "function") return headers.get(name);
      const key = Object.keys(headers).find((k) => k.toLowerCase() === name.toLowerCase());
      return key ? headers[key] : null;
    };
    Object.entries(HEADER_TO_VAR).forEach(([h, v]) => {
      out[v] = get(h);
    });
    return out;
  }

  async function status(base = DEFAULT_BASE) {
    try {
      const res = await fetch(`${base.replace(/\/$/, "")}/health`, { method: "GET" });
      const text = await res.text();
      return {
        ok: res.ok && text.includes("nginx-order-embed"),
        http: res.status,
        base,
        body: text.slice(0, 200),
      };
    } catch (err) {
      return {
        ok: false,
        base,
        error: String(err?.message || err),
        hint: "Chạy: python3 scripts/nginx_order_embed.py start",
      };
    }
  }

  async function callOrders(opts = {}) {
    const base = opts.base || DEFAULT_BASE;
    const st = await status(base);
    if (!st.ok) {
      return {
        ok: false,
        error: "embed stack chưa chạy",
        status: st,
        whenNeeded: describe().cli,
      };
    }
    const res = await fetch(`${base.replace(/\/$/, "")}/orders`);
    const embedded = extractEmbedded(res.headers);
    let payload = null;
    try {
      payload = await res.json();
    } catch (_) {
      payload = { raw: await res.text() };
    }
    return {
      ok: res.ok,
      http: res.status,
      via: res.headers.get("X-Order-Via"),
      embedded,
      payload,
      module: "nginx_embed",
    };
  }

  async function callOrder(id, opts = {}) {
    const base = opts.base || DEFAULT_BASE;
    const res = await fetch(`${base.replace(/\/$/, "")}/order/${encodeURIComponent(id)}`);
    return {
      ok: res.ok,
      http: res.status,
      embedded: extractEmbedded(res.headers),
      payload: await res.json().catch(() => null),
    };
  }

  async function runWhenNeeded(opts = {}) {
    const st = await status(opts.base);
    if (!st.ok) {
      return {
        ok: false,
        mode: "needs_cli_start",
        message: "Stack nginx embed chưa up — chạy CLI once/start khi cần.",
        cli: describe().cli,
        status: st,
      };
    }
    const orders = await callOrders(opts);
    return {
      ok: orders.ok,
      mode: "browser_call",
      orders,
      describe: describe(),
      verdict: orders.ok
        ? "✅ Gọi đơn qua nginx (stack sẵn có)"
        : "❌ Gọi đơn qua nginx thất bại",
    };
  }

  const NginxEmbed = {
    describe,
    status,
    callOrders,
    callOrder,
    runWhenNeeded,
    embeddedVars: EMBEDDED_VARS,
    owns: ["nginx-order-embed", "upstream-order-call"],
  };

  global.MaMoLogicModules = global.MaMoLogicModules || {};
  global.MaMoLogicModules.nginxEmbed = NginxEmbed;
})(window);
