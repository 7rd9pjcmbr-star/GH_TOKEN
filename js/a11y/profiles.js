/**
 * Profiles — hồ sơ người dùng khuyết tật / hỗ trợ đặc biệt.
 */
(function (global) {
  "use strict";

  const PROFILES = {
    "locked-in": {
      id: "locked-in",
      name: "Không vận động (locked-in)",
      summary: "Một công tắc + quét tự động + TTS. Không cần chuột.",
      prefs: {
        autoScan: true,
        scanAnnounce: true,
        scanSpeedMs: 2500,
        largeTargets: true,
        highContrast: false,
        longPressMs: 400,
      },
      defaultMode: "special",
    },
    "low-motor": {
      id: "low-motor",
      name: "Vận động hạn chế",
      summary: "Ô lớn, quét chậm hơn, Morse một công tắc.",
      prefs: {
        autoScan: true,
        scanAnnounce: false,
        scanSpeedMs: 3200,
        largeTargets: true,
        highContrast: false,
        longPressMs: 500,
      },
      defaultMode: "special",
    },
    "low-vision": {
      id: "low-vision",
      name: "Thị lực kém",
      summary: "Tương phản cao, đọc to khi quét, mục tiêu lớn.",
      prefs: {
        autoScan: true,
        scanAnnounce: true,
        scanSpeedMs: 2800,
        largeTargets: true,
        highContrast: true,
        voiceRate: 0.9,
      },
      defaultMode: "special",
    },
    "speech": {
      id: "speech",
      name: "Khó nói",
      summary: "Ưu tiên câu nhanh / bảng nhu cầu, TTS mạnh.",
      prefs: {
        autoScan: false,
        scanAnnounce: false,
        largeTargets: true,
      },
      defaultMode: "phrases",
    },
    "blind-braille": {
      id: "blind-braille",
      name: "Khiếm thị (Braille)",
      summary: "Braille + TTS; công bố live region.",
      prefs: {
        autoScan: false,
        scanAnnounce: true,
        highContrast: true,
      },
      defaultMode: "braille",
    },
  };

  const Profiles = {
    all: PROFILES,

    list() {
      return Object.values(PROFILES);
    },

    get(id) {
      return PROFILES[id] || PROFILES["locked-in"];
    },

    apply(id) {
      const profile = Profiles.get(id);
      const store = global.MaMoA11y.core.get("store");
      store?.set("profile", profile.id);
      store?.patch(profile.prefs);
      document.body.dataset.profile = profile.id;
      document.body.classList.toggle("high-contrast", !!profile.prefs.highContrast);
      document.body.classList.toggle(
        "large-targets",
        profile.prefs.largeTargets !== false
      );
      global.MaMoA11y.core.emit("profile:applied", { profile });
      global.MaMoA11y.core.get("shell")?.setMode(profile.defaultMode);
      global.MaMoA11y.core.get("speech")?.speak(
        `Đã chọn hồ sơ ${profile.name}`,
        { quietStatus: true }
      );
      Profiles.syncUi();
      return profile;
    },

    syncUi() {
      const store = global.MaMoA11y.core.get("store");
      const prefs = store?.getAll() || {};
      const sel = document.getElementById("profile-select");
      if (sel) sel.value = prefs.profile || "locked-in";

      const speed = document.getElementById("scan-speed");
      if (speed) speed.value = String(prefs.scanSpeedMs || 2500);

      const map = [
        ["scan-announce", "scanAnnounce"],
        ["high-contrast", "highContrast"],
        ["auto-scan", "autoScan"],
      ];
      map.forEach(([id, key]) => {
        const el = document.getElementById(id);
        if (el) el.checked = !!prefs[key];
      });

      const label = document.getElementById("profile-summary");
      if (label) {
        const p = Profiles.get(prefs.profile);
        label.textContent = p.summary;
      }
    },

    start() {
      const sel = document.getElementById("profile-select");
      if (sel) {
        sel.innerHTML = Profiles.list()
          .map(
            (p) =>
              `<option value="${p.id}">${p.name}</option>`
          )
          .join("");
        sel.addEventListener("change", () => Profiles.apply(sel.value));
      }

      // Pref controls → store
      document.getElementById("scan-speed")?.addEventListener("change", (e) => {
        storeSet("scanSpeedMs", Number(e.target.value));
        if (global.MaMoA11y.core.get("store")?.get("autoScan")) {
          global.MaMoA11y.core.get("scan")?.startScan();
        }
      });
      document.getElementById("scan-announce")?.addEventListener("change", (e) => {
        storeSet("scanAnnounce", e.target.checked);
      });
      document.getElementById("high-contrast")?.addEventListener("change", (e) => {
        storeSet("highContrast", e.target.checked);
        document.body.classList.toggle("high-contrast", e.target.checked);
      });
      document.getElementById("auto-scan")?.addEventListener("change", (e) => {
        storeSet("autoScan", e.target.checked);
        global.MaMoA11y.core.get("scan")?.toggle(e.target.checked);
      });

      const current = global.MaMoA11y.core.get("store")?.get("profile") || "locked-in";
      const profile = Profiles.get(current);
      document.body.dataset.profile = profile.id;
      document.body.classList.toggle("high-contrast", !!store()?.get("highContrast"));
      document.body.classList.toggle(
        "large-targets",
        store()?.get("largeTargets") !== false
      );
      Profiles.syncUi();
    },
  };

  function store() {
    return global.MaMoA11y.core.get("store");
  }

  function storeSet(key, value) {
    store()?.set(key, value);
  }

  global.MaMoA11y.core.register("profiles", Profiles);
})(window);
