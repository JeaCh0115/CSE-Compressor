/* ================= CSE Compressor · 前端逻辑（资源管理器式布局） ================= */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  const state = {
    token: Math.random().toString(36).slice(2, 10),
    view: "disk",           // disk | archive
    cwd: "",                // 当前磁盘目录
    items: [],              // 当前目录项目
    parent: null,           // 上级目录
    sel: new Set(),         // 选中路径
    anchor: null,           // shift 选择锚点
    archive: null,          // {path, pwd, entries, summary, format, size}
    arcDir: "",             // 归档内当前目录（"sub/inner/"，空 = 根）
    formats: [],
    methods: ["auto", "store", "lzhuf", "zlib", "lzma"],
    settings: {},
    about: null,
    env: {},
    staging: "",
    polling: null,
    picking: null,
    browserPath: "",
    browserSel: [],
    detail: true,
    sort: { key: "name", dir: 1 },   // 列表排序（列头点击切换，记忆在 localStorage）
    compact: false,                  // 紧凑模式：右键压缩/解压起进程时只显示一个面板
    compactKind: "add",              // add=压缩对话框，extract=解压进度/结果
  };

  // 桌面外壳在「右键压缩/解压」起进程时给 URL 带上 ?compact=1&kind=...，
  // 并直接按小窗尺寸建窗口。这里只负责把主界面其余区块藏起来，
  // 让第一眼看到的就是压缩对话框（或解压进度）。
  const _qs = new URLSearchParams(location.search);
  state.compact = _qs.has("compact");
  state.compactKind = _qs.get("kind") === "extract" ? "extract" : "add";
  if (state.compact) document.body.classList.add("compact");

  const EXT = { cse: ".cse", zip: ".zip", targz: ".tar.gz", tarbz2: ".tar.bz2",
                tarxz: ".tar.xz", tar: ".tar", gz: ".gz", bz2: ".bz2", xz: ".xz" };
  const ARCHIVE_EXT = [".cse", ".zip", ".7z", ".rar", ".iso", ".cab", ".arj",
                       ".lzh", ".tar", ".tar.gz", ".tar.bz2", ".tar.xz",
                       ".tgz", ".tbz2", ".txz", ".gz", ".bz2", ".xz"];
  // 归档徽标：格式简写 + 配色类（与资源管理器图标同族设计）
  const ARC_BADGE = {
    ".cse": ["CSE", "k-cse"], ".zip": ["ZIP", "k-zip"], ".7z": ["7Z", "k-7z"],
    ".rar": ["RAR", "k-rar"], ".iso": ["ISO", "k-iso"], ".cab": ["CAB", "k-tar"],
    ".arj": ["ARJ", "k-tar"], ".lzh": ["LZH", "k-tar"], ".z": ["Z", "k-tar"],
    ".tar": ["TAR", "k-tar"], ".tar.gz": ["TGZ", "k-gz"], ".tgz": ["TGZ", "k-gz"],
    ".gz": ["GZ", "k-gz"], ".tar.bz2": ["TBZ", "k-bz2"], ".tbz2": ["TBZ", "k-bz2"],
    ".bz2": ["BZ2", "k-bz2"], ".tar.xz": ["TXZ", "k-xz"], ".txz": ["TXZ", "k-xz"],
    ".xz": ["XZ", "k-xz"],
  };
  const METHOD_LABEL = {
    auto: "auto · 逐块自动选优",
    store: "store · 仅存储（最快）",
    lzhuf: "lzhuf · 自研 LZ77+Huffman",
    zlib: "zlib · DEFLATE（均衡）",
    lzma: "lzma · LZMA（最高压缩比）",
  };
  const FORMAT_HINT = {
    cse: "独家格式：固实分块 + 内容去重 + ChaCha20 认证加密",
    zip: "Windows / macOS 原生支持，通用性最好",
    targz: "Linux 生态通用，保留目录结构与权限",
    tarbz2: "bzip2 压缩的 tar，适合文本",
    tarxz: "xz 压缩的 tar，压缩比高",
    tar: "仅打包不压缩，速度最快",
    gz: "GZIP 单文件压缩",
    bz2: "BZIP2 单文件压缩",
    xz: "XZ 单文件压缩，压缩比最高",
  };

  /* ---------------- 工具 ---------------- */
  const fmtSize = (n) => {
    if (n === null || n === undefined) return "—";
    if (n < 1024) return `${n} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let v = n / 1024, i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v >= 100 ? 0 : 2)} ${units[i]}`;
  };
  const pct = (a, b) => (b ? ((a / b) * 100).toFixed(1) + "%" : "—");
  const baseName = (p) => p.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || p;
  const dirName = (p) => {
    const clean = p.replace(/[\\/]+$/, "");
    const i = Math.max(clean.lastIndexOf("\\"), clean.lastIndexOf("/"));
    return i > 0 ? clean.slice(0, i) : clean;
  };
  const joinPath = (dir, name) => `${dir.replace(/[\\/]+$/, "")}\\${name}`;
  const extOf = (p) => {
    const n = baseName(p).toLowerCase();
    for (const ext of [".tar.gz", ".tar.bz2", ".tar.xz", ".cse", ".zip", ".7z", ".rar",
                       ".iso", ".cab", ".arj", ".lzh", ".tar", ".tgz", ".tbz2",
                       ".txz", ".gz", ".bz2", ".xz", ".z"]) {
      if (n.endsWith(ext)) return ext;
    }
    return "";
  };
  const isArchive = (p) => ARCHIVE_EXT.includes(extOf(p));
  const arcBadge = (p) => {
    const [label, cls] = ARC_BADGE[extOf(p)] || ["ARC", "k-tar"];
    return `<span class="arc-ico ${cls}">${label}</span>`;
  };
  const pad = (n) => String(n).padStart(2, "0");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmtTime = (ts) => {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    if (Number.isNaN(d.getTime())) return "—";
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
           `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };

  function toast(msg, kind = "") {
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = msg;
    $("#toasts").appendChild(el);
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 240); }, 3600);
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    let data = null;
    try { data = await res.json(); } catch (_) { /* 空响应 */ }
    if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
    return data;
  }

  /* ---------------- 图标 ---------------- */
  const ICON = {
    dir: '<svg viewBox="0 0 20 20" fill="none"><path d="M2.3 5.4h5.2l1.5 1.9h8.7v7.9a1.6 1.6 0 0 1-1.6 1.6H3.9a1.6 1.6 0 0 1-1.6-1.6V5.4Z" fill="currentColor" opacity=".92"/><path d="M2.3 5.4h5.2l1.5 1.9h8.7" stroke="currentColor" stroke-width="1.1" opacity=".55"/></svg>',
    archive: '<svg viewBox="0 0 20 20" fill="none"><rect x="2.4" y="3.4" width="15.2" height="13.2" rx="2" stroke="currentColor" stroke-width="1.4"/><path d="M8.2 3.4v13.2" stroke="currentColor" stroke-width="1.2" opacity=".6"/><path d="M11.2 6.4v1.6m0 1.6v1.6m0 1.6V15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
    file: '<svg viewBox="0 0 20 20" fill="none"><path d="M4.6 2.6h6.6l4.2 4.2v10.6a1.4 1.4 0 0 1-1.4 1.4H4.6a1.4 1.4 0 0 1-1.4-1.4V4a1.4 1.4 0 0 1 1.4-1.4Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M11 2.8V7h4.2" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
  };

  /* ---------------- 主题 ---------------- */
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
  const resolveTheme = (pref) => (pref === "system" ? (prefersDark.matches ? "dark" : "light") : pref);

  const applyTheme = (pref, save = false) => {
    const eff = resolveTheme(pref);
    document.documentElement.dataset.theme = eff;
    document.documentElement.dataset.themePref = pref;
    const btn = $("#themeBtn");
    if (btn) btn.textContent = eff === "dark" ? "☾" : "☀";
    try { localStorage.setItem("cse-theme", pref); } catch (_) {}
    if (save) saveSettings({ theme: pref });
    return eff;
  };
  applyTheme(localStorage.getItem("cse-theme") || "dark");
  prefersDark.addEventListener?.("change", () => {
    if (state.settings.theme === "system") applyTheme("system");
  });
  $("#themeBtn").onclick = () => toggleTheme();

  function toggleTheme() {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next, true);
    toast(next === "dark" ? "已切换到深色主题" : "已切换到浅色主题");
  }

  /* ---------------- 设置 ---------------- */
  async function saveSettings(patch) {
    Object.assign(state.settings, patch);
    try {
      state.settings = await api("/api/settings", { method: "POST", body: JSON.stringify(patch) });
    } catch (_) { /* 离线时仅内存态 */ }
    renderMenuChecks();
  }

  function applySettings() {
    const s = state.settings;
    applyTheme(s.theme || "dark");
    renderMenuChecks();
  }

  /* ---------------- 菜单 ---------------- */
  const menubar = $("#menubar");
  const MENU_CMD = {
    "add-files": () => openBrowser(null, "files"),
    "add-folder": () => openBrowser(null, "dir", (dir) => openCompress([dir])),
    "open-archive": () => {
      if (state.view === "archive") return backToDisk();
      openBrowser(null, "file", (p) => { if (p) openArchiveView(p); });
    },
    "extract-to": () => openExtract(),
    "test-archive": () => cmdTest(),
    "open-staging": async () => {
      if (!state.staging) return toast("当前模式没有缓存目录", "err");
      try {
        await api("/api/open_path", { method: "POST", body: JSON.stringify({ path: state.staging }) });
        toast("拖入的文件会先复制到这里，压缩时从这里读");
      } catch (e) { toast(e.message, "err"); }
    },
    "clear-staging": async () => {
      if (!confirm("确定清空拖入文件缓存？\n\n这里放的是「拖进窗口」的文件副本（拖放只能拿到文件内容、拿不到路径，所以先落一份到本地再压缩）。清空只删还没压过的副本，已生成的归档不受影响。")) return;
      try {
        const r = await api("/api/clear_staging", { method: "POST", body: "{}" });
        toast(`已清理 ${r.removed} 项暂存`, "ok");
      } catch (e) { toast(e.message, "err"); }
    },
    exit: async () => {
      const s = window.pywebview && window.pywebview.api;
      if (s && s.exit_app) await s.exit_app();
      else toast("浏览器模式：直接关闭标签页即可退出", "err");
    },
    "sel-all": () => { state.sel = new Set(state.items.map((i) => i.path)); renderList(); },
    "sel-invert": () => {
      const next = new Set();
      state.items.forEach((i) => { if (!state.sel.has(i.path)) next.add(i.path); });
      state.sel = next;
      renderList();
    },
    "sel-none": () => { state.sel.clear(); renderList(); },
    "copy-to": () => fsTo("copy"),
    "move-to": () => fsTo("move"),
    delete: () => cmdDelete(),
    info: () => showInfo(),
    refresh: () => (state.view === "archive" ? reopenArchive() : loadDir(state.cwd)),
    up: () => goUp(),
    "toggle-detail": () => {
      state.detail = !state.detail;
      $("#filetable").classList.toggle("no-detail", !state.detail);
      renderMenuChecks();
    },
    detect: () => detectFormat(),
    formats: () => showFormats(),
    "archive-icons": async () => {
      try {
        const r = await api("/api/archive_icons", { method: "POST", body: JSON.stringify({ op: "register" }) });
        const n = r.count || Object.keys(r.applied || {}).length;
        const own = (r.open && r.open.own) || 0;
        const left = (r.open && r.open.pending) || [];
        let msg = `已统一 ${n} 种压缩包图标`;
        if (own) msg += `，其中 ${own} 种双击已由 CSE Compressor 打开`;
        toast(msg + `；若仍显示旧图标，请按「刷新资源管理器图标缓存」`, "ok");
        if (left.length) {
          setTimeout(() => toast(
            `系统保护了 ${left.length} 种格式的默认打开方式（${left.join(" ")}），` +
            `如需要请用「工具 → 打开系统「默认应用」设置」确认一次`, "err"), 900);
        }
      } catch (e) { toast(e.message, "err"); }
      refreshArchiveIconStatus();
    },
    "default-apps": async () => {
      try {
        await api("/api/default_apps", { method: "POST", body: "{}" });
        toast("已打开系统设置：找到 CSE Compressor →「设置默认值」，把 .rar/.gz 等勾上即可", "ok");
      } catch (e) { toast(e.message, "err"); }
    },
    "archive-icons-remove": async () => {
      try {
        await api("/api/archive_icons", { method: "POST", body: JSON.stringify({ op: "remove" }) });
        toast("已还原压缩包图标与打开方式", "ok");
      } catch (e) { toast(e.message, "err"); }
      refreshArchiveIconStatus();
    },
    associate: async () => {
      try {
        const r = await api("/api/associate", { method: "POST", body: "{}" });
        toast(`已注册 .cse 文件关联（${r.progid}）`, "ok");
      } catch (e) { toast(e.message, "err"); }
      refreshAssociateStatus();
    },
    "refresh-icons": async () => {
      try {
        await api("/api/associate/refresh", { method: "POST", body: "{}" });
        toast("已通知资源管理器刷新图标缓存", "ok");
      } catch (e) { toast(e.message, "err"); }
    },
    "shellmenu-register": async () => {
      try {
        await api("/api/shellmenu", { method: "POST", body: JSON.stringify({ op: "register" }) });
        toast("已注册资源管理器右键菜单（文件 / 文件夹 → 压缩，归档 → 解压）", "ok");
      } catch (e) { toast(e.message, "err"); }
      refreshShellMenuStatus();
    },
    "shellmenu-remove": async () => {
      try {
        await api("/api/shellmenu", { method: "POST", body: JSON.stringify({ op: "remove" }) });
        toast("已移除资源管理器右键菜单", "ok");
      } catch (e) { toast(e.message, "err"); }
      refreshShellMenuStatus();
    },
    "toggle-solid": () => saveSettings({ solid: !state.settings.solid }),
    "toggle-dedup": () => saveSettings({ dedup: !state.settings.dedup }),
    "reset-settings": async () => {
      if (!confirm("恢复全部设置为默认值？")) return;
      try {
        state.settings = await api("/api/settings/reset", { method: "POST", body: "{}" });
        applySettings();
        toast("已恢复默认设置", "ok");
      } catch (e) { toast(e.message, "err"); }
    },
    about: () => showAbout(),
    licenses: () => showAbout("licenses"),
    homepage: async () => {
      try {
        await api("/api/open_url", {
          method: "POST",
          body: JSON.stringify({ url: state.about?.homepage || "https://example.com/cse-compressor" }),
        });
      } catch (e) { toast(e.message, "err"); }
    },
    updates: () => showUpdates(),
  };

  $$("[data-cmd]", menubar).forEach((el) => {
    el.onclick = (e) => {
      e.stopPropagation();
      const fn = MENU_CMD[el.dataset.cmd];
      closeAllMenus();
      if (fn) fn();
    };
  });

  function closeAllMenus(except = null) {
    $$(".menu", menubar).forEach((m) => {
      if (m !== except) {
        m.classList.remove("is-open");
        const pop = $(".menu-pop", m);
        if (pop) pop.hidden = true;
        $$(".menu-pop.side", m).forEach((p) => { p.hidden = true; });
      }
    });
  }

  function toggleMenu(menu) {
    const pop = $(".menu-pop", menu);
    const open = pop.hidden;
    closeAllMenus(menu);
    menu.classList.toggle("is-open", open);
    pop.hidden = !open;
    if (open) fillSubmenus(menu);
  }

  function fillSubmenus(menu) {
    $$(".submenu", menu).forEach((sub) => {
      let pop = $(".menu-pop.side", sub);
      if (!pop) {
        pop = document.createElement("div");
        pop.className = "menu-pop side";
        sub.appendChild(pop);
      }
      pop.hidden = true;
      const kind = sub.dataset.sub;
      if (kind === "theme") {
        pop.innerHTML = ["dark", "light", "system"].map((t) => `
          <button class="menu-item check${(state.settings.theme || "dark") === t ? " checked" : ""}"
                  data-set="theme" data-val="${t}">
            <span class="tick">✓</span>${{ dark: "深色", light: "浅色", system: "跟随系统" }[t]}
          </button>`).join("");
      } else if (kind === "format") {
        pop.innerHTML = state.formats.map((f) => `
          <button class="menu-item check${state.settings.format === f.id ? " checked" : ""}"
                  data-set="format" data-val="${f.id}">
            <span class="tick">✓</span>${f.label}
          </button>`).join("");
      } else if (kind === "method") {
        pop.innerHTML = state.methods.map((m) => `
          <button class="menu-item check${state.settings.method === m ? " checked" : ""}"
                  data-set="method" data-val="${m}">
            <span class="tick">✓</span>${METHOD_LABEL[m] || m}
          </button>`).join("");
      }
      $$("[data-set]", pop).forEach((btn) => {
        btn.onclick = (e) => {
          e.stopPropagation();
          const patch = { [btn.dataset.set]: btn.dataset.val };
          if (btn.dataset.set === "theme") applyTheme(btn.dataset.val, true);
          saveSettings(patch);
          closeAllMenus();
        };
      });
      sub.onmouseenter = () => {
        $$(".menu-pop.side", menu).forEach((p) => { if (p !== pop) p.hidden = true; });
        pop.hidden = false;
      };
      sub.onclick = (e) => {
        e.stopPropagation();
        $$(".menu-pop.side", menu).forEach((p) => { p.hidden = p !== pop; });
      };
    });
  }

  $$(".menu-btn", menubar).forEach((btn) => {
    btn.onclick = (e) => { e.stopPropagation(); toggleMenu(btn.parentElement); };
  });
  $$(".menu", menubar).forEach((menu) => {
    menu.addEventListener("mouseenter", () => {
      if ($(".menu.is-open", menubar) && !menu.classList.contains("is-open")) toggleMenu(menu);
    });
  });
  document.addEventListener("click", (e) => {
    // 点菜单项本身由各自的处理器收起；点菜单外部（含头部空白 / 品牌区 / 工具栏）一律收起
    if (e.target.closest(".menu-pop") || e.target.closest(".menu-btn")) return;
    closeAllMenus();
  });

  function renderMenuChecks() {
    const s = state.settings;
    [["toggle-solid", "solid"], ["toggle-dedup", "dedup"], ["toggle-detail", "detail"]].forEach(([cmd, key]) => {
      const el = $(`[data-cmd="${cmd}"]`);
      if (!el) return;
      if (!el.querySelector(".tick")) {
        const t = document.createElement("span");
        t.className = "tick";
        t.textContent = "✓";
        el.prepend(t);
      }
      const on = key === "detail" ? state.detail : !!s[key];
      el.classList.toggle("checked", on);
    });
  }

  async function refreshAssociateStatus() {
    try {
      const st = await api("/api/associate_status");
      const mi = $("#miAssociate");
      if (!mi || !st.supported) return;
      if (!mi.querySelector(".tick")) {
        const t = document.createElement("span");
        t.className = "tick";
        t.textContent = "✓";
        mi.prepend(t);
      }
      mi.classList.toggle("checked", !!st.registered);
    } catch (_) { /* 忽略 */ }
  }

  async function refreshShellMenuStatus() {
    try {
      const st = await api("/api/shellmenu_status");
      const mi = $("#miShellMenu");
      if (!mi || !st.supported) return;
      if (!mi.querySelector(".tick")) {
        const t = document.createElement("span");
        t.className = "tick";
        t.textContent = "✓";
        mi.prepend(t);
      }
      mi.classList.toggle("checked", !!st.registered);
    } catch (_) { /* 忽略 */ }
  }

  async function refreshArchiveIconStatus() {
    try {
      const st = await api("/api/archive_icons_status");
      const mi = $("#miArchiveIcons");
      if (!mi || !st.supported) return;
      if (!mi.querySelector(".tick")) {
        const t = document.createElement("span");
        t.className = "tick";
        t.textContent = "✓";
        mi.prepend(t);
      }
      mi.classList.toggle("checked", !!st.registered);
    } catch (_) { /* 忽略 */ }
  }

  /* ---------------- 桌面窗口控制 ---------------- */
  const shellApi = () => (window.pywebview && window.pywebview.api) || null;

  function setupWindowControls() {
    const s = shellApi();
    document.body.classList.toggle("in-shell", !!s);
    if (!s) return;
    $("#tbControls").hidden = false;
    const maximizeGlyph = (max) => {
      $("#winMaxIcon").innerHTML = max
        ? '<rect x="1.5" y="3.5" width="6" height="6" rx="1" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M3.8 3.5V2.2a.7.7 0 0 1 .7-.7h5.3a.7.7 0 0 1 .7.7v5.3a.7.7 0 0 1-.7.7H8.5" fill="none" stroke="currentColor" stroke-width="1.2"/>'
        : '<rect x="2.5" y="2.5" width="7" height="7" rx="1" fill="none" stroke="currentColor" stroke-width="1.2"/>';
    };
    const refreshGlyph = () => maximizeGlyph(window.innerWidth >= screen.availWidth - 8);
    $("#winMin").onclick = () => s.minimize().catch(() => {});
    $("#winMax").onclick = () => s.toggle_maximize().then(refreshGlyph).catch(() => {});
    $("#winClose").onclick = () => s.exit_app().catch(() => {});
    window.addEventListener("resize", refreshGlyph);
    refreshGlyph();
    $$(".tb-drag").forEach((el) =>
      el.addEventListener("dblclick", (e) => {
        if (e.target.closest(".tb-btn, .menu")) return;
        s.toggle_maximize().then(refreshGlyph).catch(() => {});
      }));
  }
  window.addEventListener("pywebviewready", setupWindowControls);
  setTimeout(() => { if (!document.body.classList.contains("in-shell")) setupWindowControls(); }, 1200);

  /* ================= 文件列表 ================= */
  const body = $("#fileBody");
  const head = $("#fileHead");
  const pathInput = $("#pathInput");

  function firstSel() {
    const it = state.items.find((i) => state.sel.has(i.path));
    return it ? it.path : (state.sel.size ? [...state.sel][0] : "");
  }
  function selItems() {
    return state.items.filter((i) => state.sel.has(i.path));
  }

  function renderHead() {
    // 列头即排序开关：单击排序，再点同一列切升降序（列表头显示箭头）
    const ind = (k) => (state.sort.key === k ? (state.sort.dir > 0 ? " asc" : " desc") : "");
    if (state.view === "archive") {
      head.innerHTML = `
        <th class="c-name" data-sort="name">名称</th>
        <th class="c-size" data-sort="size">大小</th>
        <th class="c-size" data-sort="comp">压缩后</th>
        <th class="c-size" data-sort="ratio">占比</th>
        <th class="c-note" data-sort="method">方法</th>
        <th class="c-flag">文件夹</th>
        <th class="c-flag">文件</th>`;
    } else {
      head.innerHTML = `
        <th class="c-name" data-sort="name">名称</th>
        <th class="c-size" data-sort="size">大小</th>
        <th class="c-time" data-sort="mtime">修改时间</th>
        <th class="c-time" data-sort="ctime">创建时间</th>
        <th class="c-note">注释</th>
        <th class="c-flag">文件夹</th>
        <th class="c-flag">文件</th>`;
    }
    $$("th[data-sort]", head).forEach((th) => {
      th.classList.add("is-sortable");
      const s = ind(th.dataset.sort);
      if (s) th.classList.add(s.trim());
      th.title = s ? (s.trim() === "asc" ? "升序排列 · 点击切换降序" : "降序排列 · 点击切换升序")
                   : "点击按此列排序";
    });
  }

  /* ---------------- 排序 ---------------- */
  const SORT_KEY = "cse.sort";
  function loadSort() {
    try {
      const raw = JSON.parse(localStorage.getItem(SORT_KEY) || "null");
      if (raw && typeof raw.key === "string" && (raw.dir === 1 || raw.dir === -1)) return raw;
    } catch (_) { /* 忽略损坏的值 */ }
    return { key: "name", dir: 1 };
  }
  function saveSort() {
    try { localStorage.setItem(SORT_KEY, JSON.stringify(state.sort)); } catch (_) { }
  }

  function sortVal(it, key) {
    switch (key) {
      case "size": return it.size || 0;
      case "comp": return it.comp_size || 0;
      case "ratio": return it.size ? (it.comp_size || 0) / it.size : 0;
      case "mtime": return it.mtime || 0;
      case "ctime": return it.ctime || 0;
      case "method": return String(it.method || "");
      case "count": return it.count || 0;
      default: return 0;
    }
  }

  function applySort() {
    const { key, dir } = state.sort;
    const nameCmp = (a, b) =>
      String(a.name || "").localeCompare(String(b.name || ""), "zh", { numeric: true });
    state.items.sort((a, b) => {
      // 文件夹恒在前（资源管理器习惯），同级再按所选列比较
      if (!!a.is_dir !== !!b.is_dir) return a.is_dir ? -1 : 1;
      let c;
      if (key === "name") c = nameCmp(a, b);
      else {
        const x = sortVal(a, key), y = sortVal(b, key);
        c = typeof x === "string" || typeof y === "string"
          ? String(x).localeCompare(String(y), "zh", { numeric: true })
          : (x - y);
        if (c === 0) c = nameCmp(a, b);       // 同值按名称，避免顺序抖动
      }
      return c * dir;
    });
  }

  function sortBy(key) {
    if (!key) return;
    if (state.sort.key === key) state.sort.dir = -state.sort.dir;
    else state.sort = { key, dir: 1 };
    saveSort();
    applySort();
    renderHead();
    renderList();
  }

  head.addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    // 双击的第二下不再翻一次方向：用户连着点两下只想"按这列排"，
    // 不该变成"按这列降序"
    if (!th || e.detail > 1) return;
    sortBy(th.dataset.sort);
  });

  function nameCell(ico, cls, name) {
    return `<td><div class="cell-name"><span class="ico ${cls}">${ico}</span><span class="nm" title="${name}">${name}</span></div></td>`;
  }

  function renderList() {
    document.body.classList.toggle("view-archive", state.view === "archive");
    const upRow = state.view === "archive" && state.arcDir
      ? `<tr class="row row-up" data-up="1">
           <td><div class="cell-name"><span class="ico dir up">↰</span><span class="nm">..</span></div></td>
           <td class="c-size">—</td><td class="c-size">—</td><td class="c-size">—</td>
           <td class="c-note">上一级</td><td class="c-flag"></td><td class="c-flag"></td>
         </tr>`
      : "";
    if (!state.items.length) {
      const tip = state.view === "archive"
        ? (state.arcDir ? "此文件夹为空" : "归档为空") : "此文件夹为空";
      body.innerHTML = upRow + `<tr class="empty-row"><td colspan="7">${tip}</td></tr>`;
      bindRows();
      updateStatus();
      return;
    }
    if (state.view === "archive") {
      body.innerHTML = upRow + state.items.map((e, i) => {
        const dir = !!e.is_dir;
        const sel = state.sel.has(e.path) ? " is-sel" : "";
        const ico = dir ? ICON.dir : "";
        const cell = dir
          ? `<span class="ico dir">${ico}</span><span class="nm" title="${esc(e.name)}">${esc(e.name)}</span>`
          : `<span class="ico arc-slot">${arcBadge(e.name)}</span><span class="nm" title="${esc(e.name)}">${esc(e.name)}</span>`;
        return `<tr class="row${sel}" data-path="${esc(e.path)}" data-idx="${i}">
          <td><div class="cell-name">${cell}</div></td>
          <td class="c-size">${dir ? fmtSize(e.size || 0) : fmtSize(e.size)}</td>
          <td class="c-size">${dir ? (e.comp_size ? fmtSize(e.comp_size) : "—") : fmtSize(e.comp_size)}</td>
          <td class="c-size">${dir ? pct(e.comp_size, e.size) : pct(e.comp_size, e.size)}</td>
          <td class="c-note">${dir ? (e.count ? e.count + " 项" : "") : (e.method || "—")}</td>
          <td class="c-flag">${dir ? '<span class="dot">●</span>' : ""}</td>
          <td class="c-flag">${dir ? "" : '<span class="dot">●</span>'}</td>
        </tr>`;
      }).join("");
    } else {
      body.innerHTML = state.items.map((it, i) => {
        const dir = !!it.is_dir;
        const arc = !dir && isArchive(it.name);
        const sel = state.sel.has(it.path) ? " is-sel" : "";
        const cell = dir
          ? `<span class="ico dir">${ICON.dir}</span><span class="nm" title="${esc(it.name)}">${esc(it.name)}</span>`
          : arc
            ? `<span class="ico arc-slot">${arcBadge(it.name)}</span><span class="nm" title="${esc(it.name)}">${esc(it.name)}</span>`
            : `<span class="ico file">${ICON.file}</span><span class="nm" title="${esc(it.name)}">${esc(it.name)}</span>`;
        return `<tr class="row${sel}" data-path="${esc(it.path)}" data-idx="${i}">
          <td><div class="cell-name">${cell}</div></td>
          <td class="c-size">${dir ? "—" : fmtSize(it.size)}</td>
          <td class="c-time">${fmtTime(it.mtime)}</td>
          <td class="c-time">${fmtTime(it.ctime)}</td>
          <td class="c-note"></td>
          <td class="c-flag">${dir ? '<span class="dot">●</span>' : ""}</td>
          <td class="c-flag">${dir ? "" : '<span class="dot">●</span>'}</td>
        </tr>`;
      }).join("");
    }
    bindRows();
    updateStatus();
  }

  function bindRows() {
    $$("#fileBody tr.row").forEach((tr) => {
      if (tr.dataset.up) {
        tr.onclick = (e) => e.stopPropagation();
        tr.ondblclick = () => goUp();
        return;
      }
      tr.onclick = (e) => selectRow(tr, e);
      tr.ondblclick = () => openRow(tr);
    });
  }

  function selectRow(tr, e) {
    const path = tr.dataset.path;
    const idx = Number(tr.dataset.idx);
    if (e.ctrlKey || e.metaKey) {
      if (state.sel.has(path)) state.sel.delete(path);
      else state.sel.add(path);
      state.anchor = idx;
    } else if (e.shiftKey && state.anchor !== null) {
      const [a, b] = [Math.min(state.anchor, idx), Math.max(state.anchor, idx)];
      state.sel = new Set(state.items.slice(a, b + 1).map((i) => i.path));
    } else {
      state.sel = new Set([path]);
      state.anchor = idx;
    }
    $$("#fileBody tr.row").forEach((r) => {
      r.classList.toggle("is-sel", state.sel.has(r.dataset.path));
    });
    updateStatus();
  }

  function openRow(tr) {
    if (state.view === "archive") {
      const ent = state.items[Number(tr.dataset.idx)];
      if (!ent) return;
      if (ent.is_dir) enterArcDir(ent.path);
      else showArchiveEntryInfo(ent);
      return;
    }
    const it = state.items[Number(tr.dataset.idx)];
    if (!it) return;
    if (it.is_dir) loadDir(it.path);
    else if (isArchive(it.name)) openArchiveView(it.path);
    else openCompress([it.path]);
  }

  function updateStatus() {
    const total = state.items.length;
    const chosen = selItems();
    const size = chosen.reduce((a, s) => a + (s.size || 0), 0);
    const isArc = state.view === "archive";
    $("#statSel").textContent = `选定 ${chosen.length} / ${total} 个项目`;
    const parts = [];
    if (state.view === "disk") {
      const dirs = state.items.filter((i) => i.is_dir).length;
      parts.push(`${dirs} 个文件夹`, `${total - dirs} 个文件`);
      const all = state.items.reduce((a, s) => a + (s.size || 0), 0);
      parts.push(`合计 ${fmtSize(all)}`);
      if (chosen.length > 1) parts.push(`已选 ${fmtSize(size)}`);
      $("#statInfo").textContent = parts.join(" · ");
    } else {
      const a = state.archive || {};
      const sum = a.summary || {};
      const dirs = state.items.filter((i) => i.is_dir).length;
      const files = total - dirs;
      const size = state.items.reduce((x, s) => x + (s.size || 0), 0);
      const chips = [a.format || "", `${dirs} 个文件夹 · ${files} 个文件`, `合计 ${fmtSize(size)}`];
      if (!state.arcDir) chips.push(`全部 ${(a.entries || []).length} 个条目`);
      if (sum.encrypted) chips.push("已加密");
      $("#statInfo").textContent = chips.filter(Boolean).join(" · ");
    }
  }

  function setStatResult(text, kind = "") {
    const el = $("#statResult");
    el.textContent = text || "";
    el.className = kind;
  }

  /* ---------------- 目录加载 ---------------- */
  async function loadDir(path) {
    try {
      const q = path ? `?path=${encodeURIComponent(path)}` : "";
      const data = await api("/api/browse" + q);
      state.view = "disk";
      state.archive = null;
      state.arcDir = "";
      state.cwd = data.path;
      state.parent = data.parent;
      state.items = data.items || [];
      state.sel.clear();
      state.anchor = null;
      pathInput.value = data.path;
      $("#navDisk").hidden = true;
      $("#pbIcon").classList.remove("is-archive");
      $("#navUp").disabled = !data.parent;
      applySort();
      renderHead();
      renderList();
    } catch (e) {
      toast(e.message, "err");
    }
  }

  function goUp() {
    if (state.view === "archive") {
      if (!state.arcDir) return backToDisk();
      const parts = state.arcDir.replace(/\/$/, "").split("/");
      parts.pop();
      state.arcDir = parts.length ? parts.join("/") + "/" : "";
      return refreshArchiveDir();
    }
    if (state.parent) loadDir(state.parent);
  }

  /* ---------------- 归档内目录层级 ---------------- */
  function arcChildren() {
    const a = state.archive;
    if (!a) return [];
    const prefix = state.arcDir;
    const dirs = new Map();
    const files = [];
    for (const e of a.entries) {
      let name = String(e.name || "").replace(/\\/g, "/");
      while (name.length > 1 && name.endsWith("/")) name = name.slice(0, -1);
      if (!name || !name.startsWith(prefix)) continue;
      const rest = name.slice(prefix.length);
      if (!rest) continue;
      const slash = rest.indexOf("/");
      const sz = e.size || 0;
      const cz = e.comp_size || 0;
      if (slash >= 0) {
        const dname = rest.slice(0, slash);
        const key = prefix + dname + "/";
        const cur = dirs.get(key) ||
          { name: dname, path: key, is_dir: true, size: 0, comp_size: 0, count: 0, method: "" };
        cur.size += sz;
        cur.comp_size += cz;
        cur.count += 1;
        dirs.set(key, cur);
      } else if (e.is_dir) {
        // 目录自身条目（"sub/"）：只进目录表，避免与文件名重复一行
        const key = prefix + rest + "/";
        if (!dirs.has(key)) {
          dirs.set(key, { name: rest, path: key, is_dir: true,
                          size: 0, comp_size: 0, count: 0, method: "" });
        }
      } else {
        files.push({ name: rest, path: prefix + rest, is_dir: false,
                     size: e.size, comp_size: e.comp_size, method: e.method });
      }
    }
    const cmp = (x, y) => x.name.localeCompare(y.name, "zh", { numeric: true });
    return [...dirs.values()].sort(cmp).concat(files.sort(cmp));
  }

  function enterArcDir(relPath) {
    state.arcDir = relPath.endsWith("/") ? relPath : relPath + "/";
    refreshArchiveDir();
  }

  function refreshArchiveDir() {
    if (state.view !== "archive" || !state.archive) return;
    state.items = arcChildren();
    state.sel.clear();
    state.anchor = null;
    pathInput.value = arcPathLabel();
    $("#navUp").disabled = false;
    applySort();
    renderHead();
    renderList();
  }

  function arcPathLabel() {
    const a = state.archive;
    if (!a) return "";
    const inner = state.arcDir.replace(/\/$/, "").replace(/\//g, "\\");
    return "归档：" + a.path + (inner ? "\\" + inner : "");
  }

  function resolveArchivePath(raw) {
    const segs = String(raw).replace(/^归档：\s*/, "").split(/[\\/]/).filter(Boolean);
    for (let i = segs.length; i > 0; i--) {
      const cand = segs.slice(0, i).join("\\");
      if (isArchive(cand)) return { archive: cand, inner: segs.slice(i).join("/") };
    }
    return null;
  }

  async function openArchiveView(path, pwd = "", inner = "") {
    if (!path) return;
    setStatResult("");
    try {
      const info = await api(`/api/preview?path=${encodeURIComponent(path)}` +
                             `&password=${encodeURIComponent(pwd || "")}`);
      if (!info.entries) {
        toast("该文件不是可识别的归档格式", "err");
        return;
      }
      const entries = info.entries;
      state.view = "archive";
      state.archive = {
        path, pwd,
        format: info.format,
        size: info.size,
        file_size: info.size,
        summary: info.summary || {},
        entries,
      };
      state.arcDir = inner ? (inner.replace(/\\/g, "/").replace(/^\/+/, "").replace(/\/?$/, "/")) : "";
      state.sel.clear();
      state.anchor = null;
      $("#navDisk").hidden = false;
      $("#pbIcon").classList.add("is-archive");
      refreshArchiveDir();
      toast(`已打开归档 · ${entries.length} 个条目`, "ok");
    } catch (e) {
      toast("无法读取归档：" + e.message, "err");
    }
  }

  function reopenArchive() {
    const a = state.archive;
    if (a) openArchiveView(a.path, a.pwd, state.arcDir);
  }

  function backToDisk() {
    $("#navDisk").hidden = true;
    $("#pbIcon").classList.remove("is-archive");
    state.arcDir = "";
    loadDir(state.cwd);
  }

  $("#navUp").onclick = goUp;
  $("#navGo").onclick = () => {
    const v = pathInput.value.trim();
    if (!v) return;
    if (state.view === "archive") {
      const hit = resolveArchivePath(v);
      if (!hit) {
        toast("无法定位该归档路径", "err");
        return;
      }
      openArchiveView(hit.archive, "", hit.inner);
      return;
    }
    openArchiveOrDir(v);
  };
  $("#navRefresh").onclick = () => (state.view === "archive" ? reopenArchive() : loadDir(state.cwd));
  $("#navDisk").onclick = backToDisk;
  pathInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("#navGo").click();
  });

  function openArchiveOrDir(p) {
    const hit = isArchive(p) ? { archive: p, inner: "" } : resolveArchivePath(p);
    if (hit) openArchiveView(hit.archive, "", hit.inner);
    else loadDir(p);
  }

  /* ================= 工具栏 ================= */
  $$("#toolbar .tool[data-act]").forEach((btn) => {
    btn.onclick = () => {
      const act = btn.dataset.act;
      if (act === "add") cmdAdd();
      else if (act === "extract") MENU_CMD["extract-to"]();
      else if (act === "test") cmdTest();
      else if (act === "copy") fsTo("copy");
      else if (act === "move") fsTo("move");
      else if (act === "delete") cmdDelete();
      else if (act === "info") showInfo();
      else if (act === "theme") toggleTheme();
    };
  });

  function cmdAdd() {
    if (state.view === "archive") {
      toast("归档视图下请使用「解压」按钮", "err");
      return;
    }
    const chosen = selItems().map((i) => i.path);
    openCompress(chosen);
  }

  function cmdTest() {
    const p = state.view === "archive" && state.archive ? state.archive.path : firstSel();
    if (!p || !isArchive(p)) {
      toast("请先选择一个归档文件（.cse / .zip / .tar 等）", "err");
      return;
    }
    const pwd = state.view === "archive" && state.archive ? state.archive.pwd : "";
    runJob("test", { archive: p, password: pwd }, null, {
      title: "完整性测试",
      doneLabel: "✓ 完整性校验通过（CRC / 认证全部正确）",
    });
  }

  /* ================= 拖入缓存（staging） ================= */
  /* 拖放只给文件内容、拿不到原始路径，所以拖入的文件先复制到 ~/.cse_upload/<token>/，
     压缩时从那里读。压完就把这批副本删掉——否则缓存目录会一直悄悄长大。 */
  function cleanupStaging(sources) {
    if (!state.staging || !sources || !sources.length) return;
    if (state.cwd && state.cwd.startsWith(state.staging)) return;  // 正看着缓存目录，别删
    if (!sources.some((p) => p && p.startsWith(state.staging))) return;
    api("/api/cleanup_staging", {
      method: "POST", body: JSON.stringify({ token: state.token }),
    }).catch(() => {});
  }

  /* ================= 压缩对话框 ================= */
  // 多选文件右键压缩时，资源管理器对每个文件各调起一次进程，请求会分几批
  // 陆续到达前端。这里记住当前对话框的源列表，后续批次往里追加，而不是重开。
  let compressCtx = null;
  let renderCompressSrc = null;

  function openCompress(paths) {
    const list = [];
    const seen = new Set();
    for (const p of (paths || [])) {
      if (p && !seen.has(p)) { seen.add(p); list.push(p); }
    }
    if (!list.length) {
      toast("请先在列表中选择要压缩的文件或文件夹", "err");
      return;
    }
    const fmt = state.settings.format && state.formats.some((f) => f.id === state.settings.format)
      ? state.settings.format : "cse";
    const first = list[0];
    const outDir = dirName(first);
    const outName = baseName(first).replace(/\.[^.]+$/, "");

    // 输出归档不能和任何一个源文件同名：源本身就是 x.zip、格式又选 ZIP 时，
    // 默认名会算成 x.zip 自己——引擎先截断目标再回读源，源文件当场就没了
    // （只读文件则是 [Errno 13] Permission denied）。这里换个名字避开。
    const stripExt = (p) => {
      const low = p.toLowerCase();
      for (const e of Object.values(EXT).sort((a, b) => b.length - a.length)) {
        if (low.endsWith(e)) return p.slice(0, p.length - e.length);
      }
      return p.replace(/\.[^.\\/]+$/, "");
    };
    const takenBySource = (p) => list.some((s) => s.toLowerCase() === p.toLowerCase());
    const avoidClash = (p) => {
      if (!takenBySource(p)) return p;
      const stem = stripExt(p);
      const ext = p.slice(stem.length);
      for (let i = 1; i < 100; i++) {
        const cand = stem + (i === 1 ? "_压缩" : `_压缩${i}`) + ext;
        if (!takenBySource(cand)) return cand;
      }
      return p;
    };
    const out = avoidClash(joinPath(outDir, outName + (EXT[fmt] || ".cse")));

    // 一页放完：左栏是源项目（自己带滚动条），右栏是各项设置。
    // 从前是单列堆叠，主界面 500 高的小窗里必然出滚动条，要滚才看得到"开始压缩"。
    openDlg("压缩设置", `
      <div class="cmp-src">
        <span class="cmp-label" id="cSrcCount">源项目 · ${list.length} 项</span>
        <div class="src-preview" id="cSrcList">
          ${list.map((p) => `<div class="src-row">
            <span class="ico">${isArchive(p) ? "▣" : "▪"}</span>
            <span class="nm" title="${esc(p)}">${esc(baseName(p))}</span>
            <span class="sz">${esc(dirName(p))}</span>
          </div>`).join("")}
        </div>
      </div>
      <div class="cmp-opts">
        <label class="field"><span>输出归档</span>
          <div class="input-row">
            <input class="input" id="cOut" value="${out}" spellcheck="false">
            <button class="btn ghost" id="cOutPick">选择位置</button>
          </div>
          <p class="hint over" id="cOver" hidden></p>
        </label>
        <div class="field">
          <div class="cmp-row-head"><span>归档格式</span><span class="hint" id="cHint"></span></div>
          <div class="pills" id="cFormats"></div>
        </div>
        <div class="grid2">
          <label class="field"><span>压缩方式</span>
            <select class="input" id="cMethod"></select>
          </label>
          <label class="field" id="cPwdField"><span>密码（仅 CSE-X）</span>
            <input class="input" id="cPwd" type="password" placeholder="留空则不加密">
          </label>
        </div>
        <label class="field"><span>备注</span>
          <input class="input" id="cNote" placeholder="可选，如：项目备份 2026-10">
        </label>
        <div class="switches">
          <label class="switch">
            <input type="checkbox" id="cSolid" ${state.settings.solid !== false ? "checked" : ""}>
            <span class="track"><span class="knob"></span></span>
            <span class="switch-text">固实压缩<em>整包建模，压缩比更高</em></span>
          </label>
          <label class="switch">
            <input type="checkbox" id="cDedup" ${state.settings.dedup !== false ? "checked" : ""}>
            <span class="track"><span class="knob"></span></span>
            <span class="switch-text">内容去重<em>相同内容只存一份</em></span>
          </label>
        </div>
        <div class="dlg-actions">
          <button class="btn ghost" id="cCancel">取消</button>
          <button class="btn primary" id="cStart">开始压缩</button>
        </div>
      </div>`, "cmp");

    compressCtx = { list, avoidClash };
    renderCompressSrc = () => {
      const cnt = $("#cSrcCount"), box = $("#cSrcList");
      if (!cnt || !box) return;
      cnt.textContent = `源项目 · ${list.length} 项`;
      box.innerHTML = list.map((p) => `<div class="src-row">
        <span class="ico">${isArchive(p) ? "▣" : "▪"}</span>
        <span class="nm" title="${esc(p)}">${esc(baseName(p))}</span>
        <span class="sz">${esc(dirName(p))}</span>
      </div>`).join("");
    };

    const pills = [["cse", "CSE-X ★"], ["zip", "ZIP"], ["targz", "TAR.GZ"], ["tar", "TAR"],
                   ["gz", "GZ"], ["xz", "XZ"], ["bz2", "BZ2"]]
      .filter(([id]) => state.formats.some((f) => f.id === id));
    $("#cFormats").innerHTML = pills.map(([id, label]) =>
      `<button class="pill${id === fmt ? " is-active" : ""}" data-fmt="${id}">${label}</button>`).join("");
    $("#cMethod").innerHTML = state.methods
      .map((m) => `<option value="${m}"${m === (state.settings.method || "auto") ? " selected" : ""}>${METHOD_LABEL[m] || m}</option>`).join("");

    /* ---- 输出路径撞上已有文件：先改名，改不了就让用户明确选一次 ----
       同名同目录是压缩最容易踩的雷：上次那份 a.zip 静静躺在原地，
       再点一次「开始压缩」就被整包替换，旧内容再也找不回来。 */
    let destDirty = false;        // 用户手改过输出路径后就不再自动改名
    let destTimer = null;
    const freeName = async (p, tag) => {
      try {
        const r = await api(`/api/freename?path=${encodeURIComponent(p)}&tag=${encodeURIComponent(tag)}`);
        return (r && r.free) || p;
      } catch (_) { return p; }
    };
    const syncDest = async (autoRename) => {
      const el = $("#cOut"), hint = $("#cOver");
      if (!el || !hint) return;
      const cur = el.value.trim();
      if (!cur) { hint.hidden = true; return; }
      let info = null;
      try { info = await api(`/api/pathinfo?path=${encodeURIComponent(cur)}`); } catch (_) { return; }
      if (!info || !info.exists || takenBySource(cur)) { hint.hidden = true; return; }
      if (autoRename && !destDirty) {
        const free = await freeName(avoidClash(cur), "_压缩");
        if (free && free !== cur) {
          el.value = free;
          hint.hidden = false;
          hint.textContent = `⚠ 同名文件已存在，已改为「${baseName(free)}」，不会覆盖原有文件`;
          return;
        }
      }
      hint.hidden = false;
      hint.textContent = `⚠ 该文件已存在${info.size ? `（${fmtSize(info.size)}，${fmtTime(info.mtime)}）` : ""}，继续会覆盖它`;
    };
    if (compressCtx) compressCtx.syncDest = syncDest;   // 后续批次的源并进来后还要再查一次

    let curFmt = fmt;
    const applyFmt = (f) => {
      curFmt = f;
      $$("#cFormats .pill").forEach((b) => b.classList.toggle("is-active", b.dataset.fmt === f));
      $("#cHint").textContent = FORMAT_HINT[f] || "";
      const exclusive = f === "cse";
      $("#cPwdField").style.visibility = exclusive ? "visible" : "hidden";
      $$(".switches .switch").forEach((s) => { s.style.display = exclusive ? "" : "none"; });
      const outEl = $("#cOut");
      const oldExt = extOf(outEl.value);
      // 换格式也可能撞上源文件（x.zip 换成 ZIP 格式 → 又叫 x.zip），一并避开
      if (oldExt) outEl.value = avoidClash(outEl.value.slice(0, outEl.value.length - oldExt.length) + (EXT[f] || ""));
      destDirty = false;          // 名字是跟着格式算出来的，允许再自动避让一次
      syncDest(true);
    };
    $$("#cFormats .pill").forEach((b) => (b.onclick = () => applyFmt(b.dataset.fmt)));
    applyFmt(fmt);

    const cOutEl = $("#cOut");
    if (cOutEl) cOutEl.addEventListener("input", () => {
      destDirty = true;
      clearTimeout(destTimer);
      destTimer = setTimeout(() => syncDest(false), 350);
    });

    const baseOutName = outName;
    $("#cOutPick").onclick = () => openBrowser("cOut", "dir", (dir) => {
      const el = $("#cOut");
      if (!el) return;
      el.value = avoidClash(joinPath(dir, baseOutName + (EXT[curFmt] || "")));
      destDirty = false;
      syncDest(true);
    });
    $("#cCancel").onclick = closeDlg;
    $("#cStart").onclick = async () => {
      const el = $("#cOut");
      let dest = el ? el.value.trim() : "";
      if (!dest) return toast("请填写输出归档路径", "err");
      // 扩展名跟着格式走：CSE 格式填成 a.zip 时后端会补成 a.zip.cse，
      // 那确认框里说的"覆盖 a.zip"和真正落盘的路径就不是一回事了
      const wantExt = EXT[curFmt] || "";
      const curExt = extOf(dest);
      if (wantExt && curExt.toLowerCase() !== wantExt) {
        dest = (curExt ? dest.slice(0, dest.length - curExt.length) : dest) + wantExt;
        if (el) el.value = dest;
      }
      // 手动改的名字也可能又撞上源文件：在发出去之前自己挡一次，
      // 否则引擎抛出来的是 [Errno 13] / 截断，用户看不懂也救不回源文件
      if (takenBySource(dest)) {
        const fixed = avoidClash(dest);
        if (el) el.value = fixed;
        toast(`输出归档不能和源文件同名，已改成「${baseName(fixed)}」`, "ok");
        return;
      }
      // 目标已存在 → 必须让用户明确选一次，绝不静默替换
      let overwrite = false;
      let info = null;
      try { info = await api(`/api/pathinfo?path=${encodeURIComponent(dest)}`); } catch (_) {}
      if ($("#dlg").hidden) return;                 // 等待期间对话框被关掉了
      if (info && info.exists) {
        const free = await freeName(dest, "_压缩");
        if ($("#dlg").hidden) return;
        const pick = await askOverwrite({
          title: "目标文件已存在",
          lines: [
            `<code>${esc(dest)}</code> 已经存在${info.size ? `（${fmtSize(info.size)}，${fmtTime(info.mtime)} 修改）` : ""}。`,
            "继续压缩会把它的内容整个换掉，旧文件无法恢复。",
          ],
          altLabel: free && free !== dest ? `另存为 ${baseName(free)}` : "另存一份",
        });
        if ($("#dlg").hidden) return;
        if (pick === "cancel") return;
        if (pick === "alt") {
          if (!free || free === dest) return toast("找不到可用的新文件名，请手动改一下", "err");
          if (el) el.value = free;
          dest = free;
          const hint = $("#cOver");
          if (hint) hint.hidden = true;
        } else {
          overwrite = true;
        }
      }
      const payload = {
        sources: list,
        dest,
        format: curFmt,
        method: $("#cMethod") ? $("#cMethod").value : "auto",
        solid: $("#cSolid") ? $("#cSolid").checked : true,
        dedup: $("#cDedup") ? $("#cDedup").checked : true,
        password: ($("#cPwd") && curFmt === "cse") ? $("#cPwd").value : "",
        comment: $("#cNote") ? $("#cNote").value : "",
        overwrite,                     // 引擎最后再挡一道：false 时目标存在就报错
      };
      closeDlg({ keep: state.compact });   // 紧凑窗口要留着显示进度
      runJob("compress", payload, () => {
        cleanupStaging(list);              // 拖入副本用完就删，别让缓存目录无声长大
        if (state.compact) return;
        if (state.view === "disk") loadDir(state.cwd);
      }, { title: "压缩", doneLabel: `✓ 已生成 ${baseName(dest)}` });
    };
    compactReady();   // 紧凑小窗：设置面板铺好了，可以移到屏幕中央
  }

  /* ================= 解压对话框 ================= */
  function openExtract(pathArg, pwd = "") {
    const p = pathArg || (state.view === "archive" && state.archive ? state.archive.path : firstSel());
    if (!p) {
      toast("请先选择一个归档文件", "err");
      return;
    }
    if (!isArchive(p)) {
      toast("所选文件不是可识别的归档格式", "err");
      return;
    }
    const compact = state.compact;
    const dest = dirName(p) + "\\" + baseName(p).replace(/\.[^.]+$/, "") + "_解压";
    // 紧凑小窗里归档文件就是刚右键的那一个，不用再给输入框；只留"解压到哪"
    const arcField = compact ? `
      <div class="arc-pill" title="${esc(p)}">
        <span class="ico">▣</span><span class="nm">${esc(baseName(p))}</span>
        <span class="sz">${esc(dirName(p))}</span>
      </div>` : `
      <div class="field">
        <span>归档文件</span>
        <input class="input" id="xArc" value="${esc(p)}" spellcheck="false">
      </div>`;
    openDlg("解压到", `
      ${arcField}
      <div class="field">
        <span>解压到</span>
        <div class="input-row">
          <input class="input" id="xDest" value="${esc(dest)}" spellcheck="false">
          <button class="btn ghost" id="xDestPick">选择位置</button>
        </div>
        <p class="hint">默认解压到归档同级目录「名称_解压」，可改到别处</p>
        <p class="hint over" id="xOver" hidden></p>
      </div>
      <div class="field" style="margin-top:14px">
        <span>密码（如归档已加密）</span>
        <input class="input" id="xPwd" type="password" value="${esc(pwd)}" placeholder="无密码留空">
      </div>
      <div class="dlg-actions">
        <button class="btn ghost" id="xTest">完整性测试</button>
        <button class="btn ghost" id="xCancel">取消</button>
        <button class="btn primary" id="xStart">开始解压</button>
      </div>`);
    const arcPath = () => (compact ? p : (($("#xArc") && $("#xArc").value.trim()) || p));

    /* 目标文件夹已经存在且有东西 → 挂条提醒，点「开始解压」时再确认一次。
       解压是逐条覆盖同名文件的：目录里那份被顶掉的可能是用户自己改过的版本。 */
    const syncXDest = async () => {
      const el = $("#xDest"), hint = $("#xOver");
      if (!el || !hint) return;
      const cur = el.value.trim();
      if (!cur) { hint.hidden = true; return; }
      let info = null;
      try { info = await api(`/api/pathinfo?path=${encodeURIComponent(cur)}`); } catch (_) { return; }
      if (!info || !info.exists || !info.nonempty) { hint.hidden = true; return; }
      hint.hidden = false;
      hint.textContent = `⚠ 该文件夹已有 ${info.count} 项，解压会自动覆盖其中同名文件`;
    };
    if ($("#xDest")) {
      $("#xDest").addEventListener("input", () => { clearTimeout(syncXDest._t); syncXDest._t = setTimeout(syncXDest, 350); });
    }

    $("#xDestPick").onclick = () => openBrowser("xDest", "dir", () => setTimeout(syncXDest, 60));
    $("#xCancel").onclick = closeDlg;
    $("#xTest").onclick = () => {
      const arc = arcPath();
      const pw = $("#xPwd") ? $("#xPwd").value : "";
      // keep：紧凑小窗里点「完整性测试」只是把面板换成进度/结果，
      // 不能当成"取消"把整个窗口关掉（原来就是这么退出的）。
      closeDlg({ keep: state.compact });
      runJob("test", { archive: arc, password: pw }, null,
        { title: "完整性测试", doneLabel: "✓ 完整性校验通过" });
    };
    $("#xStart").onclick = async () => {
      const arc = arcPath();
      let d = $("#xDest") ? $("#xDest").value.trim() : "";
      const pw = $("#xPwd") ? $("#xPwd").value : "";
      if (!d) d = dirName(arc) + "\\" + baseName(arc).replace(/\.[^.]+$/, "") + "_解压";
      const go = () => {
        closeDlg({ keep: state.compact });
        extractNow(arc, d, pw);
      };
      let info = null;
      try { info = await api(`/api/pathinfo?path=${encodeURIComponent(d)}`); } catch (_) {}
      if ($("#dlg").hidden) return;
      if (!info || !info.exists || !info.nonempty) return go();

      // 目标里已经有东西：先数一数有多少同名文件会被顶掉
      let c = null;
      try {
        c = await api("/api/extract_conflicts", {
          method: "POST",
          body: JSON.stringify({ archive: arc, dest: d, password: pw }),
        });
      } catch (_) {}
      if ($("#dlg").hidden) return;
      const known = !!(c && c.ok);
      const n = (known && c.conflicts) || 0;
      if (known && !n) return go();     // 一个都不撞，合并进去就好，不用打扰

      let free = d;
      try {
        const r = await api(`/api/freename?path=${encodeURIComponent(d)}&tag=`);
        free = (r && r.free) || d;
      } catch (_) {}
      if ($("#dlg").hidden) return;
      const pick = await askOverwrite({
        title: n ? `${n} 个文件将被覆盖` : "目标文件夹已有内容",
        lines: [
          `<code>${esc(d)}</code> 里已经有 ${info.count} 项。` + (n
            ? `其中 <b>${n} 个同名文件</b>会被解压出来的内容替换，原文件无法恢复。`
            : "无法确认有没有同名文件（可能是加密归档），建议先换个文件夹。"),
          ...(known && c.samples.length
            ? [`<code>${c.samples.map(esc).join("<br>")}${n > c.samples.length ? "<br>…" : ""}</code>`]
            : []),
        ],
        altLabel: free && free !== d ? `解压到 ${baseName(free)}` : "换个文件夹",
        overLabel: "仍然解压到这里",
      });
      if ($("#dlg").hidden) return;
      if (pick === "cancel") return;
      if (pick === "alt") {
        if (!free || free === d) return toast("找不到可用的新目录名，请手动改一下", "err");
        d = free;
      }
      go();
    };
    syncXDest();      // 打开就先看一眼默认目录是不是已经有东西
    compactReady();   // 紧凑小窗：解压设置面板铺好了，可以移到屏幕中央
  }

  function extractNow(arc, dest, pwd, onDone) {
    runJob("extract", { archive: arc, dest: dest || "", password: pwd || "" }, (r) => {
      if (state.view === "disk" && dest && dirName(dest) === state.cwd) loadDir(state.cwd);
      onDone && onDone(r);
    }, { title: "解压", doneLabel: "✓ 解压完成" });
  }

  /* ================= 文件操作（复制/移动/删除） ================= */
  function fsTo(op) {
    const items = selItems();
    if (!items.length) return toast("请先在列表中选择项目", "err");
    if (state.view === "archive") return toast("归档视图下不能执行该操作", "err");
    openBrowser(null, "dir", async (dest) => {
      try {
        const r = await api("/api/fs", {
          method: "POST",
          body: JSON.stringify({ op, paths: items.map((i) => i.path), dest }),
        });
        const verb = op === "copy" ? "复制" : "移动";
        if (r.failed && r.failed.length) {
          toast(`${verb}完成 ${r.count} 项，${r.failed.length} 项失败`, "err");
        } else {
          toast(`已${verb} ${r.count} 项到 ${r.dest}`, "ok");
        }
        setStatResult(`✓ 已${verb} ${r.count} 项`, "ok");
        loadDir(state.cwd);
      } catch (e) {
        toast(e.message, "err");
      }
    });
  }

  function cmdDelete() {
    const items = selItems();
    if (!items.length) return toast("请先在列表中选择要删除的项目", "err");
    if (state.view === "archive") return toast("归档视图下不能删除条目", "err");
    const names = items.slice(0, 20).map((i) => i.path);
    openDlg("确认删除", `
      <div class="warn-box">
        <span>⚠</span>
        <div>
          <b>即将把 ${items.length} 个项目移入回收站</b><br>
          删除后可在「回收站」中还原；本操作不会永久删除文件。
        </div>
      </div>
      <ul class="warn-list">${names.map((n) => `<li>${n}</li>`).join("")}
        ${items.length > 20 ? `<li>… 其余 ${items.length - 20} 项</li>` : ""}
      </ul>
      <div class="dlg-actions">
        <button class="btn ghost" id="delCancel">取消</button>
        <button class="btn danger" id="delOk">移入回收站</button>
      </div>`);
    $("#delCancel").onclick = closeDlg;
    $("#delOk").onclick = async () => {
      closeDlg();
      try {
        const r = await api("/api/fs", {
          method: "POST",
          body: JSON.stringify({ op: "delete", paths: items.map((i) => i.path) }),
        });
        if (r.failed && r.failed.length) toast(`已删除 ${r.count} 项，${r.failed.length} 项失败`, "err");
        else toast(`已移入回收站 ${r.count} 项`, "ok");
        setStatResult(`✓ 已删除 ${r.count} 项`, "ok");
        loadDir(state.cwd);
      } catch (e) {
        toast(e.message, "err");
      }
    };
  }

  function showInfo() {
    const path = firstSel();
    if (!path) { toast("请先选择一个项目", "err"); return; }
    if (state.view === "archive") {
      const ent = state.items.find((i) => i.path === path);
      if (ent) showArchiveEntryInfo(ent);
      return;
    }
    const it = state.items.find((i) => i.path === path);
    if (!it) return;
    api(`/api/detect?path=${encodeURIComponent(path)}`).then((info) => {
      openDlg("信息", `
        <div class="about-head" style="margin-bottom:12px">
          <div class="tool-ico ${it.is_dir ? "i-test" : "i-extract"}" style="width:34px;height:34px">
            ${it.is_dir ? ICON.dir : (isArchive(it.name) ? ICON.archive : ICON.file)}
          </div>
          <div>
            <h3 style="margin:0 0 2px;font-size:14.5px">${it.name}</h3>
            <div class="about-sub">${it.is_dir ? "文件夹" : (info.format || "文件")}</div>
          </div>
        </div>
        <dl class="about-rows">
          <dt>位置</dt><dd>${it.path}</dd>
          <dt>大小</dt><dd>${it.is_dir ? "—（文件夹）" : fmtSize(it.size)}</dd>
          <dt>修改时间</dt><dd>${fmtTime(it.mtime)}</dd>
          <dt>创建时间</dt><dd>${fmtTime(it.ctime)}</dd>
          <dt>识别格式</dt><dd class="plain">${info.format || "未知"}</dd>
        </dl>
        <div class="dlg-actions">
          <button class="btn ghost" id="infoCopy">复制路径</button>
          <button class="btn primary" id="infoOk">关闭</button>
        </div>`);
      $("#infoOk").onclick = closeDlg;
      $("#infoCopy").onclick = () => {
        navigator.clipboard?.writeText(it.path).then(() => toast("路径已复制", "ok")).catch(() => toast("复制失败", "err"));
      };
    }).catch((e) => toast(e.message, "err"));
  }

  function showArchiveEntryInfo(ent) {
    openDlg("归档条目信息", `
      <dl class="about-rows">
        <dt>名称</dt><dd>${esc(ent.name)}</dd>
        <dt>类型</dt><dd class="plain">${ent.is_dir ? "文件夹" : "文件"}</dd>
        <dt>大小</dt><dd>${ent.is_dir ? fmtSize(ent.size || 0) : fmtSize(ent.size)}</dd>
        <dt>压缩后</dt><dd>${ent.is_dir ? "—" : fmtSize(ent.comp_size)}</dd>
        <dt>压缩方式</dt><dd class="plain">${ent.method || "—"}</dd>
        <dt>归档内路径</dt><dd>${esc(String(ent.path || ent.name))}</dd>
        <dt>所在归档</dt><dd>${esc(state.archive ? state.archive.path : "")}</dd>
      </dl>
      <div class="dlg-actions"><button class="btn primary" id="eiOk">关闭</button></div>`);
    $("#eiOk").onclick = closeDlg;
  }

  /* ================= 通用对话框 ================= */
  const dlg = $("#dlg");
  function openDlg(title, html, mode) {
    $("#dlgTitle").textContent = title;
    const body = $("#dlgBody");
    body.innerHTML = html;
    // 两栏版式（压缩设置）：卡片加宽 + 主体换成左右两栏，一页放完所有选项
    body.classList.toggle("cmp-body", mode === "cmp");
    const card = $("#dlg .modal-card");
    if (card) card.classList.toggle("cmp-card", mode === "cmp");
    dlg.hidden = false;
    body.scrollTop = 0;      // 每次打开都从顶部看起（源项目在最上面）
    // 焦点放在对话框本体上（tabindex=-1），而不是第一个按钮：
    // 按钮被程序化聚焦时浏览器会画一圈"像被选中"的焦点框。
    if (card) {
      card.tabIndex = -1;
      setTimeout(() => {
        try { card.focus({ preventScroll: true }); } catch (_) {}
        body.scrollTop = 0;
      }, 30);
    }
  }
  const closeDlg = (opts = {}) => {
    dlg.hidden = true;
    compressCtx = null;
    const sheet = $("#dlg .sheet");
    if (sheet) {
      if (typeof sheet._cancel === "function") sheet._cancel();  // 别让 await 永远挂着
      sheet.remove();
    }
    // 紧凑模式下对话框就是整个窗口：取消 = 关窗退出，和系统对话框一致。
    // 但"开始压缩"会先关对话框再跑任务，那种情况要留着窗口显示进度。
    if (state.compact && !opts.keep) quitCompact();
  };

  /* 在当前对话框上盖一层确认，返回 "alt"（换个名字/目录）| "over"（覆盖）| "cancel"。
     覆盖是不可逆的：默认按钮永远是"另存一份"，真要点"覆盖"得再动一次手——
     同名时被顶掉的可能正是用户唯一的那份备份。 */
  function askOverwrite({ title, lines = [], altLabel, overLabel = "覆盖原文件" }) {
    return new Promise((resolve) => {
      const card = $("#dlg .modal-card");
      if (!card) return resolve("cancel");
      const old = card.querySelector(".sheet");
      if (old) old.remove();
      const box = document.createElement("div");
      box.className = "sheet";
      box.tabIndex = -1;
      box.innerHTML = `
        <h4>${esc(title)}</h4>
        ${lines.map((l) => `<p>${l}</p>`).join("")}
        <div class="sheet-actions">
          <button class="btn ghost" data-k="cancel">取消</button>
          <button class="btn danger" data-k="over">${esc(overLabel)}</button>
          <button class="btn primary" data-k="alt">${esc(altLabel)}</button>
        </div>`;
      card.appendChild(box);
      const done = (k) => {
        box.remove();
        document.removeEventListener("keydown", onKey, true);
        resolve(k);
      };
      // Esc = 取消（只吞这一层，不关掉下面的设置对话框）
      const onKey = (e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done("cancel"); }
      };
      document.addEventListener("keydown", onKey, true);
      box._cancel = () => done("cancel");
      box.querySelectorAll("button").forEach((b) => (b.onclick = () => done(b.dataset.k)));
      // 焦点给确认层本体而不是按钮：按钮被程序化聚焦会画成"已经选中覆盖"
      setTimeout(() => { try { box.focus({ preventScroll: true }); } catch (_) {} }, 20);
    });
  }
  $("#dlgClose").onclick = closeDlg;
  dlg.addEventListener("click", (e) => { if (e.target === dlg) closeDlg(); });

  async function showAbout(section = "about") {
    let a;
    try {
      a = await api("/api/about");
      state.about = a;
    } catch (e) {
      toast("无法读取版本信息：" + e.message, "err");
      return;
    }
    if (section === "licenses") {
      openDlg("开源许可与致谢", `
        <div class="about-sec" style="margin-top:0">
          <h4>使用的开源项目</h4>
          <div class="os-list">
            ${a.opensource.map((p) => `
              <div class="os-item"><b>${p.name}</b><span class="lic">${p.license}</span>
                <p>${p.use}</p></div>`).join("")}
          </div>
        </div>
        <div class="about-sec">
          <h4>特别致谢</h4>
          ${a.credits.map(([who, why]) => `<p class="credit"><b>${who}</b> — ${why}</p>`).join("")}
        </div>
        <p class="lic-note">${a.license}</p>`);
      return;
    }
    openDlg("关于 CSE Compressor", `
      <div class="about-head">
        <img class="about-logo" src="/static/app-logo.png" alt="CSE Compressor 图标">
        <div>
          <h3>${a.product}</h3>
          <div class="about-sub">
            <span class="ver">v${a.version}</span>
            <span class="chan">${(a.channel || "").toUpperCase()}</span>
            <span>${a.channel_label}</span>
          </div>
        </div>
      </div>
      <dl class="about-rows">
        <dt>打包时间</dt><dd>${a.build_time}</dd>
        <dt>构建号</dt><dd>${a.build_id}</dd>
        <dt>运行环境</dt><dd class="plain">${a.backend} · Python ${a.python}</dd>
        <dt>操作系统</dt><dd class="plain">${a.platform}</dd>
        <dt>压缩引擎</dt><dd class="plain">${a.engine_backends.join(" / ")}</dd>
        <dt>版权</dt><dd class="plain">${a.copyright}</dd>
      </dl>
      <div class="about-sec">
        <h4>开源项目与致谢</h4>
        <div class="os-list">
          ${a.opensource.slice(0, 4).map((p) => `
            <div class="os-item"><b>${p.name}</b><span class="lic">${p.license}</span>
              <p>${p.use}</p></div>`).join("")}
        </div>
      </div>
      <p class="lic-note">${a.license}</p>`);
  }

  function showUpdates() {
    const a = state.about || {};
    openDlg("检查更新", `
      <dl class="about-rows" style="margin-top:4px">
        <dt>当前版本</dt><dd>v${a.version || "?"}</dd>
        <dt>更新渠道</dt><dd class="plain">${a.channel_label || "Beta 公测版"}</dd>
        <dt>当前构建</dt><dd>${a.build_time || "开发版"}</dd>
      </dl>
      <p class="lic-note" style="border-top:0;padding-top:4px">
        Beta 渠道说明：新版本以 <b>CSE-Compressor-x.y.z-beta.n-Setup.exe</b> 形式发布，
        直接运行新版安装器即可覆盖升级（设置与文件关联自动保留）。
      </p>`);
  }

  function showFormats() {
    const rz = (state.env && state.env.read_formats) || [];
    const be = (state.env && state.env.backend) || {};
    const NAME = {
      cse: "CSE-X（独家 · 固实/去重/加密）", zip: "ZIP / JAR / APK", "7z": "7-Zip",
      rar: "RAR（含 RAR5）", tar: "TAR（不压缩）", targz: "TAR.GZ / TGZ",
      tarbz2: "TAR.BZ2 / TBZ2", tarxz: "TAR.XZ / TXZ", gz: "GZIP", bz2: "BZIP2",
      xz: "XZ", iso: "ISO 光盘映像", cab: "CAB 安装包", arj: "ARJ", lzh: "LZH",
      z: "Z（Unix compress）",
    };
    const creation = (state.formats || []).map((f) => f.id);
    const rows = rz.map((f) => `<div class="os-item"><b>${NAME[f] || f}</b>
        <span class="lic">${creation.includes(f) ? "压缩 + 解压" : "仅解压"}</span></div>`).join("");
    const engine = [
      "zip / tar / gzip / bzip2 / xz：Python 标准库内置",
      `7-Zip 命令行：${be["7zip"] ? "已检测到" : "未检测到"}`,
      `WinRAR / UnRAR：${be.unrar ? "已检测到" : "未检测到"}`,
      `py7zr：${be.py7zr ? "已内置" : "不可用"}`,
    ].map((t) => `<p class="credit">${t}</p>`).join("");
    openDlg("支持的压缩格式", `
      <p class="hint" style="margin:0 0 10px">共 ${rz.length} 种可解压格式；压缩可选 7 种常用格式。</p>
      <div class="os-list">${rows}</div>
      <div class="about-sec"><h4>解压后端</h4>${engine}</div>
      <div class="dlg-actions"><button class="btn primary" id="fmOk">关闭</button></div>`);
    $("#fmOk").onclick = closeDlg;
  }

  async function detectFormat() {
    const p = firstSel() || state.cwd;
    if (!p) return toast("请先选择要识别的文件", "err");
    try {
      const info = await api(`/api/detect?path=${encodeURIComponent(p)}`);
      openDlg("格式识别", `
        <dl class="about-rows" style="margin-top:4px">
          <dt>路径</dt><dd>${info.path}</dd>
          <dt>类型</dt><dd class="plain">${info.is_dir ? "文件夹" : "文件"}</dd>
          <dt>识别格式</dt><dd class="plain">${info.format || "未知"}</dd>
          <dt>大小</dt><dd>${info.size_human || fmtSize(info.size)}</dd>
        </dl>
        <div class="dlg-actions"><button class="btn primary" id="dtOk">关闭</button></div>`);
      $("#dtOk").onclick = closeDlg;
    } catch (e) {
      toast(e.message, "err");
    }
  }

  /* ================= 任务执行 ================= */
  const taskbar = $("#taskbar");
  let hideTimer = null;

  /* ================= 紧凑模式：只显示压缩进度/结果 ================= */
  const cmpBox = $("#cmpBox");

  // 紧凑小窗建在屏幕之外，面板铺好之后才让桌面外壳把它挪进屏幕中央。
  // 这样第一眼看到的就是完整面板，不会先出现一块"什么都没有"的底色。
  let compactReadySent = false;
  function compactReady() {
    if (!state.compact || compactReadySent) return;
    compactReadySent = true;
    const send = () => {
      const s = window.pywebview && window.pywebview.api;
      if (s && s.compact_ready) { try { s.compact_ready(); } catch (_) {} }
    };
    // 两帧后再发：保证这一帧的布局和样式都已应用，挪过来时就是画好的样子
    requestAnimationFrame(() => requestAnimationFrame(send));
    // 窗口挪到中央并激活时，WebView2 会把焦点丢给第一个可聚焦元素（关闭按钮），
    // 那时光标在对话框里按回车就等于"取消"。等它安顿好再把焦点收回对话框本体。
    setTimeout(quietInitialFocus, 220);
    setTimeout(quietInitialFocus, 700);
  }

  function quitCompact() {
    const s = window.pywebview && window.pywebview.api;
    if (s && s.exit_app) { try { s.exit_app(); } catch (_) {} return; }
    window.close();
  }

  function showCompactTask(title) {
    if (!state.compact) return;
    const verb = state.compactKind === "extract" ? "解压" : "压缩";
    cmpBox.hidden = false;
    cmpBox.classList.remove("is-done", "is-error");
    $("#cmpTitle").textContent = title || `正在${verb}…`;
    $("#cmpIco").textContent = "▣";
    $("#cmpFill").style.width = "0%";
    $("#cmpSub").textContent = "准备中…";
    $("#cmpOpen").hidden = true;
    $("#cmpMain").hidden = true;
    $("#cmpDone").hidden = true;
  }

  function updateCompactTask(pct, label) {
    if (!state.compact || cmpBox.hidden) return;
    $("#cmpFill").style.width = (pct || 0) + "%";
    if (label) $("#cmpSub").textContent = label;
  }

  function finishCompactTask(ok, detail, dest, name) {
    if (!state.compact) return;
    // 「完整性测试」不是压缩/解压，标题不能用默认动词
    const verb = name || (state.compactKind === "extract" ? "解压" : "压缩");
    cmpBox.hidden = false;
    cmpBox.classList.add(ok ? "is-done" : "is-error");
    if (ok) $("#cmpFill").style.width = "100%";
    $("#cmpIco").textContent = ok ? "✓" : "!";
    $("#cmpTitle").textContent = ok ? `${verb}完成` : `${verb}失败`;
    $("#cmpSub").textContent = detail || "";
    $("#cmpDone").hidden = false;
    $("#cmpMain").hidden = false;
    const openBtn = $("#cmpOpen");
    if (ok && dest) {
      openBtn.hidden = false;
      // 压缩：dest 是归档文件 → 打开它所在目录；解压：dest 是解出来的目录本身
      const where = state.compactKind === "extract" ? dest : dirName(dest);
      openBtn.onclick = () => api("/api/open_path", {
        method: "POST", body: JSON.stringify({ path: where }),
      }).catch(() => {});
    } else {
      openBtn.hidden = true;
    }
  }
  $("#cmpDone").onclick = () => quitCompact();
  $("#cmpMain").onclick = () => {
    // 压缩完想接着管理文件：把小窗还原成主界面，别逼用户重新启动一次
    const s = window.pywebview && window.pywebview.api;
    if (s && s.set_compact) { try { s.set_compact(false); } catch (_) {} }
    state.compact = false;
    document.body.classList.remove("compact");
    cmpBox.hidden = true;
    // 紧凑启动时没扫过目录（省那一下），回主界面时才补上
    const start = state.cwd || (state.env && (state.env.desktop || state.env.home)) || "";
    if (start) loadDir(start);
  };

  function showTask(label, p) {
    clearTimeout(hideTimer);
    taskbar.hidden = false;
    taskbar.classList.remove("is-done", "is-error");
    $("#tkLabel").textContent = label || "准备中…";
    $("#tkPct").textContent = (p || 0) + "%";
    $("#tkFill").style.width = (p || 0) + "%";
    $("#tkClose").onclick = () => { taskbar.hidden = true; };
  }

  function updateTask(info) {
    const total = info.total || 1;
    const p = info.status === "done" ? 100 : Math.min(100, Math.round((info.done / total) * 100));
    $("#tkFill").style.width = p + "%";
    $("#tkPct").textContent = p + "%";
    const label = info.status === "running"
      ? (info.total > 1 ? `处理中 · ${info.done}/${info.total} · ${info.message || ""}` : (info.message || "处理中…"))
      : (info.status === "error" ? "失败" : (info.message || "完成"));
    $("#tkLabel").textContent = label;
    updateCompactTask(p, info.status === "error" ? (info.error || label) : label);
    return p;
  }

  async function runJob(action, payload, onDone, opts = {}) {
    let jobId;
    try {
      ({ id: jobId } = await api("/api/job", {
        method: "POST", body: JSON.stringify({ action, ...payload }),
      }));
    } catch (e) {
      toast(e.message, "err");
      return;
    }
    showTask("准备中…", 0);
    showCompactTask((opts.title || "处理") + "中…");
    clearInterval(state.polling);
    state.polling = setInterval(async () => {
      let info;
      try { info = await api("/api/job?id=" + jobId); } catch (_) { return; }
      try {
        updateTask(info);
        if (info.status === "done") {
          clearInterval(state.polling);
          finishTask(true, info, opts, onDone);
        } else if (info.status === "error") {
          clearInterval(state.polling);
          finishTask(false, info, opts);
        }
      } catch (_) { /* UI 异常不得吞掉完成状态 */ }
    }, 350);
  }

  function finishTask(ok, info, opts, onDone) {
    taskbar.classList.add(ok ? "is-done" : "is-error");
    const r = info.result || {};
    let detail = "";
    if (ok) {
      if (info.action === "compress" && r.dest) {
        // 小文件的头部开销会让"压缩率"大于 100%，直接甩个 2233% 像出了 bug
        const ratio = r.ratio;
        const ratioTxt = (ratio === null || ratio === undefined) ? ""
          : (ratio < 100 ? `（原体积的 ${ratio}%）` : "（文件较小，压完略增）");
        detail = `${baseName(r.dest)} · ${fmtSize(r.orig_size)} → ${fmtSize(r.comp_size)}` + ratioTxt;
      } else if (info.action === "extract" && r.dest) {
        detail = `解出 ${r.files} 个文件 → ${r.dest}`;
      } else if (info.action === "test") {
        detail = opts.doneLabel || "完整性校验通过";
      }
      $("#tkLabel").textContent = `完成 · ${detail}`;
      $("#tkPct").textContent = "100%";
      setStatResult("✓ " + detail, "ok");
      if (!state.compact) toast(detail, "ok");   // 紧凑窗口的结果面板已经在显示这些
      hideTimer = setTimeout(() => { taskbar.hidden = true; }, 12000);
      finishCompactTask(true, detail, r.dest,
                         info.action === "test" ? "完整性测试" : "");
      onDone && onDone(r);
    } else {
      $("#tkLabel").textContent = "失败 · " + (info.error || "未知错误");
      setStatResult("✕ " + (info.error || "失败"), "err");
      if (!state.compact) toast(info.error || "任务失败", "err");
      finishCompactTask(false, info.error || "任务失败", "",
                         info.action === "test" ? "完整性测试" : "");
    }
  }

  /* ================= 文件浏览器（模态） ================= */
  const modal = $("#modal");
  let browserMode = "file";
  let browserOnPick = null;

  function openBrowser(inputId, mode, onPick) {
    browserOnPick = onPick || null;
    state.picking = inputId ? { inputId } : null;
    browserMode = mode;
    state.browserSel = [];
    $("#modalTitle").textContent = mode === "dir" ? "选择文件夹"
      : mode === "files" ? "选择文件（可多选）" : "选择文件";
    $("#pickDirBtn").style.display = mode === "dir" ? "" : "none";
    $("#modalOk").style.display = mode === "dir" ? "none" : "";
    modal.hidden = false;
    const input = inputId ? $("#" + inputId) : null;
    const start = input && input.value && !input.value.includes("归档：") ? input.value : state.cwd;
    loadDirModal(start && start.length && /\.[a-z0-9]{1,5}$/i.test(start) ? dirName(start) : start);
  }

  function closeBrowser() { modal.hidden = true; state.picking = null; browserOnPick = null; }
  $("#modalClose").onclick = closeBrowser;
  $("#modalCancel").onclick = closeBrowser;
  $("#upBtn").onclick = () => loadDirModal(dirName($("#modalPath").value));
  $("#goBtn").onclick = () => loadDirModal($("#modalPath").value.trim());
  $("#modalPath").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadDirModal($("#modalPath").value.trim());
  });

  async function loadDirModal(path) {
    try {
      const q = path ? `?path=${encodeURIComponent(path)}` : "";
      const data = await api("/api/browse" + q);
      state.browserPath = data.path;
      $("#modalPath").value = data.path;
      const rows = [];
      if (data.parent) rows.push(`<li data-nav="${data.parent}"><span class="ico">↩</span> 上一级</li>`);
      for (const d of data.dirs) rows.push(`<li data-nav="${d.path}"><span class="ico">📁</span> ${d.name}</li>`);
      for (const f of data.files) {
        rows.push(`<li data-file="${f.path}" data-size="${f.size}">
            <span class="ico">📄</span> ${f.name}
            <span class="sz">${fmtSize(f.size)}</span></li>`);
      }
      $("#browserList").innerHTML = rows.join("") ||
        `<li style="cursor:default;color:var(--text-3)">（空目录）</li>`;
      $$("#browserList li").forEach((li) => {
        if (li.dataset.nav) li.onclick = () => loadDirModal(li.dataset.nav);
        else if (li.dataset.file) {
          li.onclick = () => selectFileModal(li);
          li.ondblclick = () => {
            const input = state.picking && $("#" + state.picking.inputId);
            if (input) input.value = li.dataset.file;
            const cb = browserOnPick;
            closeBrowser();
            cb && cb(li.dataset.file);
          };
        }
      });
      updateSelText();
    } catch (e) {
      toast(e.message, "err");
    }
  }

  function selectFileModal(li) {
    const p = li.dataset.file;
    if (browserMode === "file") {
      $$("#browserList li").forEach((x) => x.classList.remove("is-sel"));
      li.classList.add("is-sel");
      state.browserSel = [{ path: p, size: Number(li.dataset.size) }];
    } else {
      const idx = state.browserSel.findIndex((s) => s.path === p);
      if (idx >= 0) { state.browserSel.splice(idx, 1); li.classList.remove("is-sel"); }
      else { state.browserSel.push({ path: p, size: Number(li.dataset.size) }); li.classList.add("is-sel"); }
    }
    updateSelText();
  }

  function updateSelText() {
    const n = state.browserSel.length;
    $("#modalSel").textContent = n
      ? `已选 ${n} 项 · ${fmtSize(state.browserSel.reduce((a, s) => a + (s.size || 0), 0))}` : "";
    $("#modalOk").disabled = !n;
  }

  $("#modalOk").onclick = () => {
    const picked = state.browserSel.map((s) => s.path);
    const cb = browserOnPick;
    if (state.picking && state.picking.inputId) {
      const input = $("#" + state.picking.inputId);
      if (input) input.value = picked[0] || "";
    }
    closeBrowser();
    if (cb) cb(picked[0] || "", picked);
    else if (picked.length) openCompress(picked);
  };

  $("#pickDirBtn").onclick = () => {
    const p = state.browserPath;
    if (!p) return;
    const input = state.picking && $("#" + state.picking.inputId);
    const cb = browserOnPick;
    if (input) input.value = p;
    closeBrowser();
    if (cb) cb(p);
  };

  /* ================= 拖拽 ================= */
  const lw = $("#listwrap");
  ["dragenter", "dragover"].forEach((ev) =>
    lw.addEventListener(ev, (e) => { e.preventDefault(); lw.classList.add("is-drag"); }));
  ["dragleave", "dragend"].forEach((ev) =>
    lw.addEventListener(ev, () => lw.classList.remove("is-drag")));
  lw.addEventListener("drop", async (e) => {
    e.preventDefault();
    lw.classList.remove("is-drag");
    const items = e.dataTransfer.items;
    if (!items || !items.length) return;
    const entries = [];
    for (const it of items) {
      const entry = it.webkitGetAsEntry && it.webkitGetAsEntry();
      if (entry) entries.push(entry);
      else if (it.getAsFile) entries.push({ isFile: true, _file: it.getAsFile() });
    }
    if (!entries.length) return toast("浏览器不支持拖拽该类型内容", "err");
    toast(`正在接收 ${entries.length} 项…`);
    const paths = [];
    try {
      for (const entry of entries) {
        const got = await ingestEntry(entry, "");
        paths.push(...got);
      }
    } catch (err) {
      toast("接收文件失败：" + err.message, "err");
    }
    if (paths.length) openCompress(paths);
  });

  async function ingestEntry(entry, prefix) {
    if (entry.isFile) {
      const file = entry._file || await new Promise((res, rej) => entry.file(res, rej));
      const info = await upload(file, prefix + file.name);
      return [info.path];
    }
    if (entry.isDirectory) {
      const reader = entry.createReader();
      let batch;
      do {
        batch = await new Promise((res, rej) => reader.readEntries(res, rej));
        for (const child of batch) {
          await ingestEntry(child, prefix + entry.name + "/");
        }
      } while (batch.length);
      // 目录整体作为一个源，保留层级结构
      const rel = (prefix + entry.name).replace(/\//g, "\\");
      return [joinPath(joinPath(state.staging, state.token), rel)];
    }
    return [];
  }

  async function upload(file, rel) {
    const url = `/api/upload?name=${encodeURIComponent(file.name)}` +
                `&rel=${encodeURIComponent(rel)}&token=${state.token}`;
    const res = await fetch(url, { method: "POST", body: file });
    if (!res.ok) throw new Error(`上传失败 HTTP ${res.status}`);
    return res.json();
  }

  /* ================= 右键 / 命令行带入 ================= */
  function handleLaunch(req) {
    if (!req || !req.paths || !req.paths.length) return;
    if (req.action === "add") {
      // 压缩对话框已开着 → 把新到的一批并进去（多选右键被拆成多批时的兜底）
      if (compressCtx && !dlg.hidden) {
        const seen = new Set(compressCtx.list);
        let added = 0;
        for (const p of req.paths) {
          if (!seen.has(p)) { seen.add(p); compressCtx.list.push(p); added++; }
        }
        if (added) {
          if (renderCompressSrc) renderCompressSrc();
          // 新并入的文件可能正好等于当前输出名（先选 a.txt 再选 a.zip，默认输出就是 a.zip）
          const outEl = $("#cOut");
          if (outEl) {
            const cur = outEl.value.trim();
            const fixed = compressCtx.avoidClash(cur);
            if (fixed !== cur) outEl.value = fixed;
            // 让开之后还要看一眼这个新名字在磁盘上是不是也已经有了
            if (compressCtx.syncDest) compressCtx.syncDest(false);
          }
          toast(`已并入压缩列表：新增 ${added} 项，共 ${compressCtx.list.length} 项`, "ok");
        }
        return;
      }
      openCompress(req.paths);
    } else if (req.action === "extract") {
      // 解压先问"解压到哪"：右键解压最容易踩的坑就是一不留神解压到了归档旁边，
      // 紧凑小窗里同样给设置面板（选位置/改路径/填密码），别默认闷头解压。
      const p = req.paths[0];
      if (!dlg.hidden) return;   // 已经有面板开着，不覆盖
      openExtract(p);
    } else {
      openArchiveView(req.paths[0]);
    }
  }

  let launchBusy = false;
  async function pollLaunch() {
    if (launchBusy) return;
    launchBusy = true;
    try {
      const data = await api("/api/launch");
      (data.requests || []).forEach(handleLaunch);
    } catch (_) { /* 静默 */ }
    launchBusy = false;
  }
  function startLaunchPolling() {
    // 刚启动时轮询密一点：右键「压缩」后窗口一出现就应立刻弹出对话框
    let n = 0;
    const tick = () => {
      pollLaunch();
      n += 1;
      setTimeout(tick, n < 24 ? 300 : 900);
    };
    tick();
  }

  /* ================= 快捷键 ================= */
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeAllMenus();
      if (!dlg.hidden) closeDlg();
      if (!modal.hidden) closeBrowser();
      return;
    }
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName);
    if (!dlg.hidden || !modal.hidden) return;   // 对话框打开时不响应全局快捷键
    if (e.altKey && !e.ctrlKey && !e.shiftKey) {
      const map = { f: "file", e: "edit", v: "view", t: "tools", s: "settings", a: "about" };
      const m = map[e.key.toLowerCase()];
      if (m) { e.preventDefault(); toggleMenu($(`.menu[data-menu="${m}"]`, menubar)); return; }
      if (e.key === "Enter") { e.preventDefault(); showInfo(); return; }
    }
    if (e.ctrlKey && !e.altKey) {
      const k = e.key.toLowerCase();
      if (k === "a" && !typing) { e.preventDefault(); MENU_CMD["sel-all"](); return; }
      if (k === "o") { e.preventDefault(); MENU_CMD["add-files"](); return; }
      if (k === "l") { e.preventDefault(); MENU_CMD["open-archive"](); return; }
      if (k === "n") { e.preventDefault(); cmdAdd(); return; }
      if (k === "e") { e.preventDefault(); openExtract(); return; }
      if (k === "t") { e.preventDefault(); cmdTest(); return; }
      if (k === "i") { e.preventDefault(); detectFormat(); return; }
    }
    if (typing) return;
    if (e.key === "F5") { e.preventDefault(); MENU_CMD.refresh(); }
    else if (e.key === "Backspace") { e.preventDefault(); goUp(); }
    else if (e.key === "Delete") { e.preventDefault(); cmdDelete(); }
    else if (e.key === "Enter") {
      const tr = $("#fileBody tr.row.is-sel");
      if (tr) { e.preventDefault(); openRow(tr); }
    }
  });

  /* ================= 焦点：别让按钮"看起来被选中" ================= */
  // WebView2 载入后会把焦点丢给第一个可聚焦元素（=「文件」菜单），浏览器又给它
  // 画一圈默认黑框。CSS 里已经默认不画环（body.kbd-nav 才画），这里再把焦点
  // 从菜单/工具按钮上摘掉，免得回车"不小心打开文件菜单"。
  function quietInitialFocus() {
    const ae = document.activeElement;
    if (!ae || ae === document.body) return;
    // 对话框开着时焦点落到按钮上很危险：回车会直接触发那个按钮
    // （紧凑小窗里第一个可聚焦元素是"关闭"，一按就退出）。
    if ((!dlg.hidden || !modal.hidden) && ae.tagName === "BUTTON"
        && ae.closest && ae.closest("#dlg, #modal")) {
      const card = (modal.hidden ? $("#dlg") : $("#modal")).querySelector(".modal-card");
      if (card) {
        card.tabIndex = -1;
        try { card.focus({ preventScroll: true }); } catch (_) {}
        return;
      }
    }
    const onChrome = (ae.closest && ae.closest("#menubar, #toolbar")) || ae.id === "themeBtn";
    if (!onChrome) return;
    try {
      ae.blur();
      document.body.tabIndex = -1;
      document.body.focus({ preventScroll: true });
    } catch (_) { /* 忽略 */ }
  }
  setTimeout(quietInitialFocus, 0);
  setTimeout(quietInitialFocus, 300);
  window.addEventListener("focus", () => setTimeout(quietInitialFocus, 0));

  // 只有真的按了 Tab 才显示焦点环（鼠标操作一律不显示）
  document.addEventListener("keydown", (e) => {
    if (e.key === "Tab") document.body.classList.add("kbd-nav");
  }, true);
  document.addEventListener("pointerdown", () => {
    document.body.classList.remove("kbd-nav");
  }, true);

  // 对话框里回车 = 点主按钮（焦点不再落在按钮上，这里显式补上）
  dlg.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.shiftKey) return;
    const t = e.target;
    if (t && (t.tagName === "TEXTAREA" || t.tagName === "BUTTON")) return;
    const primary = $("#dlgBody .btn.primary");
    if (primary) { e.preventDefault(); primary.click(); }
  });

  /* ================= 启动 ================= */
  async function init() {
    try {
      const env = await api("/api/env");
      state.env = env;
      state.settings = env.settings || {};
      state.staging = env.staging || "";
      state.methods = env.methods;
      state.formats = env.formats;
      $("#tbVer").textContent = "v" + env.version;
      if (env.channel) {
        $("#mbChannel").hidden = false;
        $("#mbChannel").textContent = env.channel.toUpperCase();
      }
      applySettings();
      state.sort = loadSort();
      renderHead();
      if (state.compact) {
        // 紧凑小窗此刻还在屏幕外等着：先把右键请求取回来，把面板铺好，
        // 铺好之后再上报"就绪"，窗口才被挪进屏幕中央——第一眼就是完整面板。
        await pollLaunch();
        if (dlg.hidden && cmpBox.hidden) showCompactTask("准备中…");
        compactReady();
        startLaunchPolling();
        refreshAssociateStatus();
        refreshShellMenuStatus();
        refreshArchiveIconStatus();
        return;   // 主界面被 CSS 隐藏着，没必要先扫一遍目录
      }
      // 起始目录：桌面优先，其次主目录（由后端一次性算好，省去额外往返）
      await loadDir(env.desktop || env.home || env.cwd || "");
      refreshAssociateStatus();
      refreshShellMenuStatus();
      refreshArchiveIconStatus();
      startLaunchPolling();
    } catch (e) {
      toast("无法连接本地引擎服务：" + e.message, "err");
    }
  }

  init();

  // 无边框窗口的边缘拉伸：WebView2 的渲染子窗口铺满客户区并自己返回 HTCLIENT，
  // 原生命中测试永远到不了父窗口，所以由前端判定边缘、把鼠标交给原生自控缩放。
  (() => {
    const D = 6;
    const CURSOR = {
      1: "ew-resize", 2: "ew-resize", 4: "ns-resize", 8: "ns-resize",
      5: "nwse-resize", 10: "nwse-resize", 6: "nesw-resize", 9: "nesw-resize",
    };
    const edgeOf = (e) => {
      const W = window.innerWidth, H = window.innerHeight;
      const l = e.clientX <= D, r = e.clientX >= W - D;
      const t = e.clientY <= D, b = e.clientY >= H - D;
      if (t && l) return 5;
      if (t && r) return 6;
      if (b && l) return 9;
      if (b && r) return 10;
      if (t) return 4;
      if (b) return 8;
      if (l) return 1;
      if (r) return 2;
      return 0;
    };
    document.addEventListener("pointermove", (e) => {
      const k = edgeOf(e);
      document.documentElement.style.cursor = CURSOR[k] || "";
    }, true);
    document.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      const k = edgeOf(e);
      if (!k) return;
      e.preventDefault();
      e.stopPropagation();
      const api = window.pywebview && window.pywebview.api;
      if (!api || !api.start_resize) return;
      const dpr = window.devicePixelRatio || 1;
      api.start_resize(k, Math.round(e.screenX * dpr), Math.round(e.screenY * dpr));
    }, true);
  })();
})();
