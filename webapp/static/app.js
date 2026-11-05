// PASTA (Parallel Astrodynamic Solver for Trajectory Analysis) Web 前端逻辑 (原生 JS, 无框架)
"use strict";

/* ---------- 工具 ---------- */
const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (v == null ? "–" : Number(v).toFixed(d));
// 天数 -> "xxx d HH:MM:SS"
function fmtElapsed(days) {
  if (days == null || !isFinite(days)) return "–";
  const sign = days < 0 ? "-" : "";
  let total = Math.round(Math.abs(days) * 86400);
  const d = Math.floor(total / 86400);
  total -= d * 86400;
  const p = (n) => String(n).padStart(2, "0");
  const hh = Math.floor(total / 3600), mm = Math.floor((total % 3600) / 60), ss = total % 60;
  return `${sign}${d} d ${p(hh)}:${p(mm)}:${p(ss)}`;
}
async function jfetch(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}
function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function escAttr(s) {
  return escapeHtml(s).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function fmtTS(epochSec) {
  if (epochSec == null) return "";
  const d = new Date(epochSec * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/* ---------- 行星注册表 (与 orbcalc/planets.py 一致) ---------- */
const PLANETS = {
  MERCURY: { label: "Mercury 水星", note: "安全高度 200 km" },
  VENUS:   { label: "Venus 金星",   note: "≥ 200 km" },
  EARTH:   { label: "Earth 地球",   note: "≥ 200 km" },
  MARS:    { label: "Mars 火星",    note: "≥ 200 km" },
  JUPITER: { label: "Jupiter 木星", note: "≥ 2R<br>71492 km" },
  SATURN:  { label: "Saturn 土星",  note: "≥ 2R<br>58232 km" },
  URANUS:  { label: "Uranus 天王星", note: "到达节点 (无飞掠判定)" },
  NEPTUNE: { label: "Neptune 海王星", note: "≥ 2R<br>24622 km" },
};

/* ---------- 状态 ---------- */
const state = {
  jobs: [], presets: null,
  activeJobId: null, forkSourceId: null, presetLocked: false,
  pollTimer: null, listTimer: null,
  view: "history", detailTab: "data",
  resultJobId: null, plottedJobId: null,
  completedSeen: new Set(),
  failedImages: new Set(),
  logFollow: true,
  plotlyReady: false,
  seq: [], pendingTofBounds: null, pendingLambert: null, busy: false, showDsmDir: true,
  timeMode: "elapsed", dsmMode: "total", arriveMode: "elapsed", lastResult: null,
  bodies: null,
  jobSearch: "",
};

/* ============================================================
 * 预设与配置表单
 * ============================================================ */
const DEFAULT_PRESET = "EVVEJS 1997-01";

async function loadPresets() {
  try {
    const p = await jfetch("/api/presets");
    state.presets = p || {};
    const sel = $("presetSelect");
    const names = Object.keys(state.presets);
    sel.innerHTML = "";
    names.forEach(name => {
      const opt = document.createElement("option");
      opt.value = name; opt.textContent = name;
      sel.appendChild(opt);
    });
    const chosen = names.includes(DEFAULT_PRESET) ? DEFAULT_PRESET : names[0];
    if (chosen) {
      sel.value = chosen;
      fillTrajForm(state.presets[chosen]);
    }
  } catch (e) { console.error("presets", e); }
}

async function loadCompCfg() {
  try {
    const c = await jfetch("/api/compcfg");
    fillCompForm(c);
  } catch (e) { console.error("compcfg", e); }
}

function fillTrajForm(cfg) {
  $("cfgName").value = cfg.name || "";
  $("cfgObjective").value = cfg.objective === "min_dsm" ? "min_dsm"
    : cfg.objective === "custom" ? "custom" : "min_tof";
  const w = cfg.objective_weights || [1, 0];
  $("cfgWToF").value = w[0]; $("cfgWDsm").value = w[1];
  const pen = cfg.penalty || [10, 0.2];
  $("cfgPenL1").value = pen[0]; $("cfgPenL2").value = pen[1];
  const fpen = cfg.frontier_penalty || [30, 2];
  $("cfgFPenL1").value = fpen[0]; $("cfgFPenL2").value = fpen[1];
  onObjectiveChange();
  $("cfgDsmLimit").value = cfg.dsm_limit_ms;
  $("cfgVinfL").value = (cfg.vinf_bounds_kmps || [3.5, 6])[0];
  $("cfgVinfH").value = (cfg.vinf_bounds_kmps || [3.5, 6])[1];
  $("cfgEtaL").value = (cfg.eta_bounds || [0.01, 0.9])[0];
  $("cfgEtaH").value = (cfg.eta_bounds || [0.01, 0.9])[1];
  $("cfgRpUb").value = cfg.rp_ub;
  buildSeqEditor(cfg.seq || ["EARTH", "VENUS", "VENUS", "EARTH", "JUPITER", "URANUS"],
                 cfg.tof_bounds, cfg.lambert_types);
  buildEraTable(cfg.eras);
  updateConfigJson();
}

function fillCompForm(cfg) {
  $("cfgRunScan").checked = cfg.run_scan !== false;
  $("cfgRunSeed").checked = cfg.run_seed !== false;
  $("cfgRunCompress").checked = cfg.run_compress !== false;
  $("cfgRunFrontier").checked = cfg.run_frontier !== false;
  $("cfgJobs").value = cfg.jobs || navigator.hardwareConcurrency || 4;
  $("cfgScanKeepPct").value = cfg.scan_keep_pct || 80;
  $("cfgScanKeepPctOut").value = $("cfgScanKeepPct").value;
  $("cfgRefineKeepPct").value = cfg.refine_keep_pct || 80;
  $("cfgRefineKeepPctOut").value = $("cfgRefineKeepPct").value;
  $("cfgEraStep").value = cfg.era_step_d || 60;
  updateConfigJson();
}

/* ---------- 序列节点编辑器 ---------- */
const LAMBERT_TYPES = [
  { tag: "1/2", title: "单圈转移 (类型 1/2, 解 #0)" },
  { tag: "3",   title: "一圈低能转移 (类型 3, 解 #1)" },
  { tag: "4",   title: "一圈高能转移 (类型 4, 解 #2)" },
];

function setLegLambert(btn, lam) {
  const k = Number.isInteger(lam) && lam >= 0 && lam < LAMBERT_TYPES.length ? lam : 0;
  const t = LAMBERT_TYPES[k];
  btn.dataset.lam = String(k);
  btn.textContent = t.tag;
  btn.title = `${t.title}, 点击切换`;
}

function makeNodeRow(tag) {
  const row = document.createElement("div");
  row.className = "node-row flyby";
  row.innerHTML = `
    <span class="node-pick">
      <input class="node-tag" data-k="tag" data-tag="${escapeHtml(tag)}"
             value="${escapeHtml(bodyLabel(tag))}" autocomplete="off"
             spellcheck="false" title="输入星体名检索">
      <div class="node-menu hidden"></div>
    </span>
    <span class="node-settings" data-k="settings">
      <span class="node-safe">${bodyNote(tag)}</span>
    </span>
    <span class="node-moves">
      <button class="node-move up" title="上移">↑</button>
      <button class="node-move dn" title="下移">↓</button>
    </span>
    <button class="node-del" title="删除节点">✕</button>`;
  return row;
}

function buildSeqEditor(seq, tofBounds, lambertTypes) {
  state.seq = [...seq];
  state.pendingTofBounds = Array.isArray(tofBounds) ? tofBounds : null;
  state.pendingLambert = Array.isArray(lambertTypes) ? lambertTypes.slice() : null;
  const wrap = $("seqNodes");
  wrap.innerHTML = "";
  seq.forEach((tag) => {
    const row = makeNodeRow(tag);
    if (seq.length <= 2) row.querySelector(".node-del").style.visibility = "hidden";
    wrap.appendChild(row);
  });
  refreshSeqRoles();
  refreshPlanetOptions();
}

function readLegLambert() {
  const arr = [];
  document.querySelectorAll("#tofTable .leg-lam").forEach(b => {
    arr[+b.dataset.leg] = Number(b.dataset.lam) || 0;
  });
  return arr;
}

function bodyTags() {
  return (state.bodies && Object.keys(state.bodies).length)
    ? Object.keys(state.bodies) : Object.keys(PLANETS);
}
function bodyLabel(tag) {
  if (PLANETS[tag]) return PLANETS[tag].label;
  const b = state.bodies && state.bodies[tag];
  return (b && b.label) ? b.label : tag;
}
function bodyNote(tag) {
  if (PLANETS[tag]) return PLANETS[tag].note;
  const b = state.bodies && state.bodies[tag];
  return (b && b.note) ? b.note : "";
}
async function loadBodies() {
  try { state.bodies = await jfetch("/api/bodies"); } catch (e) { state.bodies = null; }
}
function refreshPlanetOptions() {
  document.querySelectorAll('#seqNodes .node-row [data-k="tag"]').forEach(inp => {
    if (inp.dataset.tag) inp.value = bodyLabel(inp.dataset.tag);
    closeMenu(inp);
  });
}

function menuFor(inp) {
  const pick = inp.closest(".node-pick");
  return pick ? pick.querySelector(".node-menu") : null;
}
function menuOpts(menu) {
  return menu ? [...menu.querySelectorAll(".opt[data-tag]")] : [];
}
function closeMenu(inp) {
  const menu = menuFor(inp);
  if (menu) menu.classList.add("hidden");
}
function activeIndex(menu) {
  return menuOpts(menu).findIndex(o => o.classList.contains("active"));
}
function setActive(menu, idx) {
  const opts = menuOpts(menu);
  opts.forEach((o, i) => o.classList.toggle("active", i === idx));
  if (idx >= 0 && opts[idx]) opts[idx].scrollIntoView({ block: "nearest" });
}

function openMenu(inp, q = "") {
  const menu = menuFor(inp);
  if (!menu) return;
  const s = String(q || "").trim().toLowerCase();
  const items = bodyTags().filter(tag => !s
    || bodyLabel(tag).toLowerCase().includes(s) || tag.toLowerCase().includes(s));
  menu.innerHTML = items.length
    ? items.map(tag =>
        `<div class="opt" data-tag="${escapeHtml(tag)}">${escapeHtml(bodyLabel(tag))}</div>`).join("")
    : `<div class="opt empty">无匹配</div>`;
  menu.classList.remove("hidden");
  setActive(menu, menuOpts(menu).findIndex(o => o.dataset.tag === inp.dataset.tag));
}

function chooseTag(inp, tag) {
  inp.dataset.tag = tag;
  inp.value = bodyLabel(tag);
  closeMenu(inp);
  refreshSeqRoles();
  updateConfigJson();
}

function resolveTag(text) {
  const s = String(text || "").trim();
  if (!s) return null;
  const tags = bodyTags();
  if (tags.includes(s) || PLANETS[s] || (state.bodies && state.bodies[s])) return s;
  const low = s.toLowerCase();
  const exact = tags.filter(t => bodyLabel(t).toLowerCase() === low);
  if (exact.length) return exact[0];
  return tags.find(t => t.toLowerCase() === low) || null;
}

function commitTagInput(inp) {
  const tag = resolveTag(inp.value);
  if (tag) {
    inp.dataset.tag = tag;
    inp.value = bodyLabel(tag);
    return tag;
  }
  inp.value = inp.dataset.tag ? bodyLabel(inp.dataset.tag) : "";
  return null;
}

function rowTag(row) {
  const inp = row.querySelector('[data-k="tag"]');
  return (inp && inp.dataset.tag) || "";
}

function refreshSeqRoles() {
  const rows = [...document.querySelectorAll("#seqNodes .node-row")];
  state.seq = rows.map(r => rowTag(r));
  rows.forEach((row, i) => {
    const role = i === 0 ? "depart" : (i === rows.length - 1 ? "arrive" : "flyby");
    row.className = "node-row " + role;
    row.dataset.i = i;
    const current = row.querySelector(".node-settings");
    current.innerHTML = "";
    const tag = rowTag(row);
    const safe = bodyNote(tag);
    current.insertAdjacentHTML("beforeend",
      `<span class="node-safe">${safe}</span>`);
    const del = row.querySelector(".node-del");
    del.style.visibility = rows.length <= 2 ? "hidden" : "visible";
    const up = row.querySelector(".node-move.up");
    const dn = row.querySelector(".node-move.dn");
    if (up) up.style.visibility = i > 0 ? "visible" : "hidden";
    if (dn) dn.style.visibility = i < rows.length - 1 ? "visible" : "hidden";
  });
  buildTofTable(rows.length - 1);
}

function moveNode(i, dir) {
  const wrap = $("seqNodes");
  const rows = [...wrap.children];
  const j = i + dir;
  if (j < 0 || j >= rows.length) return;
  const a = rows[i], b = rows[j];
  if (dir < 0) wrap.insertBefore(a, b);
  else wrap.insertBefore(b, a);
  refreshSeqRoles();
  updateConfigJson();
}

function buildTofTable(nLegs) {
  const tb = $("tofTable");
  tb.innerHTML = "";
  const tbl = document.createElement("table");
  tbl.innerHTML = "<tr><th>区间</th><th>类型</th><th>最小 (d)</th><th>最大 (d)</th></tr>";
  let lastCfg = {};
  try { lastCfg = JSON.parse($("cfgJsonBox").value || "{}") || {}; } catch (e) { lastCfg = {}; }
  const def = defaultTofBounds(nLegs);
  const fallback = (i) => [...(def[i] || [60, 5000])];
  const pend = state.pendingTofBounds;
  let arr;
  if (pend && pend.length === nLegs) {
    arr = pend.map((b, i) => (Array.isArray(b) ? [...b] : fallback(i)));
  } else if (Array.isArray(lastCfg.tof_bounds)) {
    arr = lastCfg.tof_bounds.slice(0, nLegs)
      .map((b, i) => (Array.isArray(b) ? [...b] : fallback(i)));
  } else {
    arr = [];
  }
  while (arr.length < nLegs) arr.push(fallback(arr.length));
  state.pendingTofBounds = null;

  const pLam = state.pendingLambert;
  let lamArr;
  if (Array.isArray(pLam) && pLam.length === nLegs) {
    lamArr = pLam.slice();
  } else if (Array.isArray(lastCfg.lambert_types)) {
    lamArr = lastCfg.lambert_types.slice(0, nLegs);
  } else {
    lamArr = [];
  }
  while (lamArr.length < nLegs) lamArr.push(0);
  state.pendingLambert = null;

  const seq = state.seq;
  for (let i = 0; i < nLegs; i++) {
    const [lo, hi] = arr[i] || [60, 5000];
    const from = seq[i], to = seq[i + 1];
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${from} → ${to}</td>
      <td><button class="leg-lam" type="button" data-leg="${i}"></button></td>
      <td><input data-leg="${i}" data-k="lo" type="number" value="${lo}" min="1"></td>
      <td><input data-leg="${i}" data-k="hi" type="number" value="${hi}" min="1"></td>`;
    setLegLambert(tr.querySelector(".leg-lam"), lamArr[i]);
    tbl.appendChild(tr);
  }
  tb.appendChild(tbl);
}

function defaultTofBounds(n) {
  const def = [[190, 200], [300, 500], [10, 90], [400, 1100], [1200, 4500], [800, 4000]];
  return def.slice(0, n).map(b => [...b]);
}

function buildEraTable(eras) {
  const arr = (eras && eras.length) ? eras : [["2029-01-01", "2033-06-30"]];
  const eb = $("eraTable");
  eb.innerHTML = "";
  arr.forEach(([a, b]) => {
    const row = document.createElement("div");
    row.className = "era-row";
    row.innerHTML = `<input data-k="a" type="date" value="${a}">
      <span>→</span>
      <input data-k="b" type="date" value="${b}">
      <button class="era-del" title="删除窗口" ${arr.length <= 1 ? "disabled" : ""}>✕</button>`;
    eb.appendChild(row);
  });
}

function collectConfig() {
  const rows = [...document.querySelectorAll("#seqNodes .node-row")];
  const seq = rows.map(r => rowTag(r));
  const tofB = [];
  document.querySelectorAll("#tofTable input").forEach(inp => {
    const leg = +inp.dataset.leg, k = inp.dataset.k;
    if (!tofB[leg]) tofB[leg] = [0, 0];
    tofB[leg][k === "lo" ? 0 : 1] = +inp.value;
  });
  const eraRows = [...document.querySelectorAll("#eraTable .era-row")];
  const eras = eraRows.map(r => [
    r.querySelector('[data-k="a"]').value, r.querySelector('[data-k="b"]').value]);
  const objective = $("cfgObjective").value;
  return {
    name: $("cfgName").value.trim(),
    seq,
    safe_radius: {},
    tof_bounds: tofB,
    lambert_types: readLegLambert(),
    vinf_bounds_kmps: [+$("cfgVinfL").value, +$("cfgVinfH").value],
    eta_bounds: [+$("cfgEtaL").value, +$("cfgEtaH").value],
    rp_ub: +$("cfgRpUb").value,
    objective,
    objective_weights: [+$("cfgWToF").value, +$("cfgWDsm").value],
    dsm_limit_ms: +$("cfgDsmLimit").value,
    penalty: [+$("cfgPenL1").value, +$("cfgPenL2").value],
    frontier_penalty: [+$("cfgFPenL1").value, +$("cfgFPenL2").value],
    wl: 2e-5, vinf_launch_limit_ms: 5000,
    wa: 2e-5, vinf_arrival_limit_ms: 9000,
    eras,
    era_step_d: +$("cfgEraStep").value,
    jobs: +$("cfgJobs").value,
    run_scan: $("cfgRunScan").checked,
    run_seed: $("cfgRunSeed").checked,
    run_compress: $("cfgRunCompress").checked,
    run_frontier: $("cfgRunFrontier").checked,
    scan_keep_pct: +$("cfgScanKeepPct").value,
    refine_keep_pct: +$("cfgRefineKeepPct").value,
  };
}

/* ---------- 事件绑定 (序列/era 编辑 / 预设 / 配置) ---------- */
$("nodeAdd").addEventListener("click", () => {
  const rows = [...document.querySelectorAll("#seqNodes .node-row")];
  if (rows.length >= 10) { alert("序列最多 10 个节点"); return; }
  const row = makeNodeRow("VENUS");
  $("seqNodes").appendChild(row);
  refreshSeqRoles();
  updateConfigJson();
});

$("seqNodes").addEventListener("click", (e) => {
  const opt = e.target.closest(".node-menu .opt[data-tag]");
  if (opt) {
    const inp = opt.closest(".node-pick").querySelector('[data-k="tag"]');
    if (inp) chooseTag(inp, opt.dataset.tag);
    return;
  }
  const pick = e.target.closest(".node-pick");
  if (pick && !e.target.closest('[data-k="tag"]')) {
    pick.querySelector('[data-k="tag"]').focus();
    return;
  }
  const del = e.target.closest(".node-del");
  if (del) {
    const rows = [...document.querySelectorAll("#seqNodes .node-row")];
    if (rows.length <= 2) return;
    del.closest(".node-row").remove();
    refreshSeqRoles();
    updateConfigJson();
    return;
  }
  const mv = e.target.closest(".node-move");
  if (mv) {
    const row = mv.closest(".node-row");
    const i = [...row.parentNode.children].indexOf(row);
    moveNode(i, mv.classList.contains("up") ? -1 : 1);
  }
});

$("seqNodes").addEventListener("change", (e) => {
  if (e.target.matches('[data-k="tag"]')) {
    commitTagInput(e.target);
    closeMenu(e.target);
    refreshSeqRoles();
  }
  updateConfigJson();
});
$("seqNodes").addEventListener("input", (e) => {
  if (e.target.matches('[data-k="tag"]')) {
    openMenu(e.target, e.target.value);
    return;
  }
  updateConfigJson();
});
$("seqNodes").addEventListener("keydown", (e) => {
  if (!e.target.matches('[data-k="tag"]')) return;
  const inp = e.target, menu = menuFor(inp);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    if (menu && menu.classList.contains("hidden")) openMenu(inp, inp.value);
    const opts = menuOpts(menu);
    if (!opts.length) return;
    e.preventDefault();
    let i = activeIndex(menu);
    i = e.key === "ArrowDown" ? (i + 1) % opts.length
                              : (i <= 0 ? opts.length - 1 : i - 1);
    setActive(menu, i);
  } else if (e.key === "Enter") {
    const opts = menuOpts(menu), i = activeIndex(menu);
    if (i >= 0 && opts[i]) {
      e.preventDefault();
      chooseTag(inp, opts[i].dataset.tag);
    } else {
      commitTagInput(inp);
      closeMenu(inp);
      inp.blur();
    }
  } else if (e.key === "Escape") {
    e.preventDefault();
    inp.value = inp.dataset.tag ? bodyLabel(inp.dataset.tag) : "";
    closeMenu(inp);
    inp.blur();
  } else if (e.key === "Tab") {
    closeMenu(inp);
  }
});
$("seqNodes").addEventListener("focusin", (e) => {
  if (e.target.matches('[data-k="tag"]')) {
    e.target.select();
    openMenu(e.target, "");
  }
});
$("seqNodes").addEventListener("focusout", (e) => {
  if (e.target.matches('[data-k="tag"]')) {
    const pick = e.target.closest(".node-pick");
    if (!pick || !pick.contains(e.relatedTarget)) closeMenu(e.target);
  }
});
$("seqNodes").addEventListener("mousedown", (e) => {
  if (e.target.closest(".node-menu .opt[data-tag]")) e.preventDefault();
});
$("tofTable").addEventListener("click", (e) => {
  const lb = e.target.closest(".leg-lam");
  if (!lb) return;
  setLegLambert(lb, (Number(lb.dataset.lam) + 1) % LAMBERT_TYPES.length);
  updateConfigJson();
});
$("eraTable").addEventListener("click", (e) => {
  const btn = e.target.closest(".era-del");
  if (!btn || btn.disabled) return;
  const rows = [...document.querySelectorAll("#eraTable .era-row")];
  if (rows.length <= 1) return;
  btn.closest(".era-row").remove();
  const delBtns = [...document.querySelectorAll("#eraTable .era-del")];
  if (delBtns.length === 1) delBtns[0].disabled = true;
  updateConfigJson();
});
$("eraAdd").addEventListener("click", () => {
  const rows = [...document.querySelectorAll("#eraTable .era-row")];
  const lastB = rows.length ? rows[rows.length - 1].querySelector('[data-k="b"]').value : "2029-01-01";
  const arr = rows.map(r => [r.querySelector('[data-k="a"]').value, r.querySelector('[data-k="b"]').value]);
  const start = lastB || "2029-01-01";
  const add = (d, y) => {
    const dt = new Date(d);
    dt.setFullYear(dt.getFullYear() + y);
    return dt.toISOString().slice(0, 10);
  };
  arr.push([start, add(start, 4)]);
  buildEraTable(arr);
  updateConfigJson();
});

function onObjectiveChange() {
  $("customWrap").classList.toggle("hidden", $("cfgObjective").value !== "custom");
  updateConfigJson();
}
$("cfgObjective").addEventListener("change", onObjectiveChange);

$("presetLoad").addEventListener("click", async () => {
  try {
    const p = await jfetch("/api/presets");
    const cfg = (p || {})[$("presetSelect").value];
    if (cfg) fillTrajForm(cfg);
  } catch (e) { console.error(e); }
});
$("presetSelect").addEventListener("change", async () => {
  try {
    const p = await jfetch("/api/presets");
    const cfg = (p || {})[$("presetSelect").value];
    if (cfg) fillTrajForm(cfg);
  } catch (e) { console.error(e); }
});
async function savePreset() {
  const name = $("cfgName").value.trim();
  if (!name) { alert("保存预设前请填写预设名"); return; }
  try {
    const r = await fetch("/api/presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, config: collectConfig() }),
    });
    const j = await r.json();
    if (!r.ok) { alert("保存失败: " + (j.error || r.statusText)); return; }
    alert("任务预设已保存: " + j.saved);
    loadPresets();
  } catch (e) { alert("保存失败: " + e.message); }
}
$("presetSave").addEventListener("click", savePreset);

function updateConfigJson() {
  try { $("cfgJsonBox").value = JSON.stringify(collectConfig(), null, 2); }
  catch (e) { $("cfgJsonBox").value = "配置错误: " + e.message; }
}
["cfgName", "cfgDsmLimit", "cfgVinfL", "cfgVinfH",
 "cfgEtaL", "cfgEtaH", "cfgRpUb", "cfgJobs",
 "cfgRunScan", "cfgRunSeed", "cfgRunCompress", "cfgRunFrontier",
 "cfgScanKeepPct", "cfgRefineKeepPct", "cfgEraStep", "cfgWToF", "cfgWDsm",
 "cfgPenL1", "cfgPenL2", "cfgFPenL1", "cfgFPenL2"]
  .forEach(id => $(id).addEventListener("input", updateConfigJson));
[["cfgScanKeepPct", "cfgScanKeepPctOut"], ["cfgRefineKeepPct", "cfgRefineKeepPctOut"]]
  .forEach(([slider, out]) => $(slider).addEventListener("input", () => {
    $(out).value = $(slider).value;
  }));
document.addEventListener("input", e => {
  if (e.target.closest("#tofTable") || e.target.closest("#eraTable")) updateConfigJson();
});
document.addEventListener("keydown", e => {
  if (e.target.matches('input[type="number"]') &&
      (e.key === "ArrowUp" || e.key === "ArrowDown")) {
    e.preventDefault();
  }
});
$("saveCfg").addEventListener("click", () => {
  const blob = new Blob([$("cfgJsonBox").value], { type: "application/json" });
  const a = document.createElement("a");
  const url = URL.createObjectURL(blob);
  a.href = url;
  a.download = "traj_config.json";
  a.click();
  URL.revokeObjectURL(url);
});

/* ============================================================
 * 页面导航与任务列表
 * ============================================================ */
function setView(name) {
  state.view = name;
  $("viewHistory").classList.toggle("active", name === "history");
  $("viewDetail").classList.toggle("active", name === "detail");
  syncListPolling();
}

const BASE_TITLE = document.title;
const DETAIL_TABS = new Set(["preset", "data", "plot"]);

function tabFromUrl() {
  const tab = new URLSearchParams(location.search).get("tab");
  return DETAIL_TABS.has(tab) ? tab : null;
}
function jobPath(jid, tab) {
  const path = `/jobs/${encodeURIComponent(jid)}`;
  return tab ? `${path}?tab=${tab}` : path;
}
function newPath(tab) {
  return tab ? `/new?tab=${tab}` : "/new";
}
function currentUrl() {
  return location.pathname + location.search;
}
function navigateTo(url, replace = false) {
  if (currentUrl() === url) return;
  if (replace) history.replaceState(null, "", url);
  else history.pushState(null, "", url);
}
function showRouteNotice(msg) {
  const el = $("routeNotice");
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("hidden");
}
function hideRouteNotice() {
  const el = $("routeNotice");
  if (el) el.classList.add("hidden");
}
function setDocTitle(name) {
  document.title = name ? `${name} · PASTA` : BASE_TITLE;
}

function isTerminal(j) {
  return j && ["done", "failed", "cancelled"].includes(j.status);
}
function isSuccess(j) {
  return j && j.status === "done" && j.has_result && j.result_status === "ok";
}

function seqShort(j) {
  if (j && j.seq_short) return j.seq_short;
  const seq = j && Array.isArray(j.seq) ? j.seq : [];
  return seq.map(x => String(x || "").slice(0, 1).toUpperCase()).join("") || "—";
}

function jobCardHtml(j) {
  const jid = String(j.job_id || "");
  const status = j.status || "unknown";
  const seq = seqShort(j);
  const name = escapeHtml(j.name || jid || "未命名任务");
  const c3 = j.c3 != null ? `${fmt(j.c3, 2)} km²/s²` : "–";
  const dsm = j.dsm_total_ms != null ? `${fmt(j.dsm_total_ms, 1)} m/s` : "–";
  const launch = j.launch_iso ? String(j.launch_iso).slice(0, 10) : "–";
  const meta = [];
  if (j.created) meta.push(fmtTS(j.created));
  if (j.elapsed_s != null) meta.push(`${j.elapsed_s} s`);
  const failed = state.failedImages.has(jid);
  const thumb = failed ? "" :
    `<img src="/api/jobs/${encodeURIComponent(jid)}/trajectory.png" alt="${name}" loading="lazy">`;
  return `<article class="job-card" data-jid="${escAttr(jid)}">
    <div class="job-thumb${failed ? " noimg" : ""}">
      ${thumb}
      <div class="job-thumb-fallback"><small>暂无轨迹图</small></div>
      <span class="job-seq">${escapeHtml(seq)}</span>
      <span class="badge ${status}">${escapeHtml(status)}</span>
    </div>
    <div class="job-body">
      <div class="job-title-row">
        <span class="job-name" title="${name}">${name}</span>
        <button class="job-del" title="删除任务 (含产物)">✕</button>
      </div>
      <div class="job-stats">
        <div><span class="k">C3</span><span class="v">${c3}</span></div>
        <div><span class="k">DSM</span><span class="v">${dsm}</span></div>
        <div><span class="k">Launch</span><span class="v">${launch}</span></div>
      </div>
      <div class="job-meta">${escapeHtml(meta.join(" · "))}</div>
    </div>
  </article>`;
}

function renderJobGrid() {
  const box = $("jobGrid");
  const all = state.jobs || [];
  const q = state.jobSearch.trim().toLowerCase();
  const jobs = q ? all.filter(j =>
    String(j.name || "").toLowerCase().includes(q) ||
    seqShort(j).toLowerCase().includes(q)) : all;
  if (!all.length) {
    box.innerHTML = `<div class="empty-state">
      <h3>暂无历史任务</h3>
      <p>点击“新建任务”开始配置一次轨道计算。</p>
      <button class="primary empty-new">＋ 新建任务</button>
    </div>`;
    return;
  }
  if (!jobs.length) {
    box.innerHTML = `<div class="empty-state">
      <h3>未找到匹配任务</h3>
      <p>尝试其他任务名或序列简写。</p>
    </div>`;
    return;
  }
  box.innerHTML = jobs.map(jobCardHtml).join("");
  box.querySelectorAll(".job-thumb img").forEach(img => {
    img.addEventListener("error", () => {
      const card = img.closest(".job-card");
      const jid = card ? card.dataset.jid : "";
      if (jid) state.failedImages.add(jid);   // 失败后不再自动重试, 等手动刷新
      const thumb = img.closest(".job-thumb");
      if (thumb) thumb.classList.add("noimg");
      img.remove();
    }, { once: true });
  });
}

async function loadJobList() {
  try {
    const jobs = await jfetch("/api/jobs");
    state.jobs = jobs || [];
    renderJobGrid();
  } catch (e) { console.error("jobs", e); }
  syncListPolling();
}

function hasActiveJobs() {
  return (state.jobs || []).some(j =>
    ["queued", "starting", "running"].includes(j.status));
}
function syncListPolling() {
  if (state.view === "history" && hasActiveJobs()) startListPolling();
  else stopListPolling();
}
function startListPolling() {
  if (state.listTimer) return;
  state.listTimer = setInterval(() => {
    if (state.view === "history") loadJobList();
    else stopListPolling();
  }, 5000);
}
function stopListPolling() {
  if (state.listTimer) { clearInterval(state.listTimer); state.listTimer = null; }
}
function manualRefreshJobs() {
  state.failedImages.clear();   // 只有手动刷新才重新尝试失败过的缩略图
  loadJobList();
}

async function showHistory({ updateUrl = true } = {}) {
  stopPolling();
  state.activeJobId = null;
  state.forkSourceId = null;
  $("detailName").textContent = "";
  setDocTitle("");
  hideRouteNotice();
  setView("history");
  if (updateUrl) navigateTo("/");
  await loadJobList();
}

function setPresetLocked(locked) {
  state.presetLocked = !!locked;
  const body = $("presetBody");
  if (body) body.toggleAttribute("inert", !!locked);
  const hint = $("presetLockHint");
  if (hint) {
    hint.textContent = "只读 · Fork 后可修改";
    hint.classList.toggle("hidden", !locked);
  }
  $("runBtn").disabled = !!locked;
}

async function openNewJob({ updateUrl = true, tab = null, replaceUrl = false } = {}) {
  stopPolling();
  state.activeJobId = null;
  state.forkSourceId = null;
  state.resultJobId = null;
  state.plottedJobId = null;
  resetDetailPanes();
  setPresetLocked(false);
  hideRouteNotice();
  $("detailName").textContent = "新建任务";
  setDocTitle("新建任务");
  $("forkBtn").classList.add("hidden");
  setView("detail");
  await loadPresets();
  const chosen = DETAIL_TABS.has(tab) ? tab : "preset";
  switchDetailTab(chosen, { syncUrl: false });
  if (updateUrl) navigateTo(newPath(chosen), replaceUrl);
}

async function loadJobConfig(jid) {
  try {
    return await jfetch(`/api/jobs/${encodeURIComponent(jid)}/config.json`);
  } catch (e) {
    console.error("job config", e);
    return null;
  }
}

async function openJob(jid, { updateUrl = true, tab = null } = {}) {
  jid = String(jid || "");
  if (!jid) return false;
  stopPolling();
  state.activeJobId = jid;
  state.forkSourceId = jid;
  state.resultJobId = null;
  state.plottedJobId = null;
  resetDetailPanes();
  setPresetLocked(true);
  hideRouteNotice();
  setView("detail");
  $("detailName").textContent = jid;
  $("forkBtn").classList.add("hidden");
  let j, cfg;
  try {
    [j, cfg] = await Promise.all([
      jfetch(`/api/jobs/${encodeURIComponent(jid)}`),
      loadJobConfig(jid),
    ]);
  } catch (e) {
    await showHistory({ updateUrl: false });
    navigateTo("/", true);
    showRouteNotice("任务不存在或已删除");
    return false;
  }
  if (state.activeJobId !== jid) return false;
  if (cfg) {
    fillTrajForm(cfg);
    $("forkBtn").classList.remove("hidden");
  } else {
    $("presetLockHint").textContent = "只读";
  }
  $("detailName").textContent = j.name || jid;
  setDocTitle(j.name || jid);
  updateJobDetail(j);
  const success = isSuccess(j);
  if (success) state.completedSeen.add(jid);
  const chosen = DETAIL_TABS.has(tab) ? tab : (success ? "plot" : "data");
  switchDetailTab(chosen, { syncUrl: false });
  if (updateUrl) navigateTo(jobPath(jid, chosen));
  else if (!tab) navigateTo(jobPath(jid, chosen), true);
  if (!isTerminal(j)) startPolling();
  return true;
}
window.openJob = openJob;

function forkCurrent() {
  if (!state.forkSourceId) return;
  $("cfgName").value = "";   // 留空, 显示 Auto-generated, 提交时由后端自动命名
  state.activeJobId = null;
  state.forkSourceId = null;
  state.resultJobId = null;
  state.plottedJobId = null;
  resetDetailPanes();
  setPresetLocked(false);
  hideRouteNotice();
  $("detailName").textContent = "新建任务（分支）";
  setDocTitle("新建任务");
  $("forkBtn").classList.add("hidden");
  updateConfigJson();
  switchDetailTab("preset", { syncUrl: false });
  navigateTo(newPath("preset"));
}

async function deleteJob(jid) {
  if (!confirm(`确定删除任务 ${jid}？\n产物 (log/result/plot) 会被一并删除。`)) return;
  try {
    const r = await fetch(`/api/jobs/${encodeURIComponent(jid)}`, { method: "DELETE" });
    if (!r.ok) { alert("删除失败: " + r.status); return; }
    state.failedImages.delete(jid);
    if (state.activeJobId === jid) await showHistory();
    else await loadJobList();
  } catch (e) { alert("删除失败: " + e.message); }
}

$("jobGrid").addEventListener("click", (e) => {
  const del = e.target.closest(".job-del");
  if (del) {
    e.stopPropagation();
    const card = del.closest(".job-card");
    if (card) deleteJob(card.dataset.jid);
    return;
  }
  if (e.target.closest(".empty-new")) { openNewJob(); return; }
  const card = e.target.closest(".job-card");
  if (card) openJob(card.dataset.jid);
});
$("jobSearch").addEventListener("input", (e) => {
  state.jobSearch = e.target.value;
  renderJobGrid();
});
$("newJobBtn").addEventListener("click", () => openNewJob());
$("refreshJobs").addEventListener("click", manualRefreshJobs);
$("backBtn").addEventListener("click", () => showHistory());
$("forkBtn").addEventListener("click", forkCurrent);

/* ---------- 详情 Tab ---------- */
function switchDetailTab(tab, { syncUrl = true } = {}) {
  if (!DETAIL_TABS.has(tab)) tab = "preset";
  state.detailTab = tab;
  const map = [
    ["preset", "tabPreset", "panePreset"],
    ["data", "tabData", "paneData"],
    ["plot", "tabPlot", "panePlot"],
  ];
  map.forEach(([key, tabId, paneId]) => {
    $(tabId).classList.toggle("active", key === tab);
    $(paneId).classList.toggle("active", key === tab);
  });
  if (tab === "data") renderTables();
  if (tab === "plot") {
    setTimeout(() => {
      if (!state.activeJobId) {
        $("chart").innerHTML = "<p>提交任务后显示轨迹图</p>";
        return;
      }
      if (state.plottedJobId !== state.activeJobId) renderPlot(state.activeJobId);
      if (typeof Plotly !== "undefined") Plotly.Plots.resize($("chart"));
    }, 30);
  }
  if (syncUrl && state.view === "detail") {
    const url = state.activeJobId ? jobPath(state.activeJobId, tab) : newPath(tab);
    navigateTo(url, true);
  }
}
$("tabPreset").addEventListener("click", () => switchDetailTab("preset"));
$("tabData").addEventListener("click", () => switchDetailTab("data"));
$("tabPlot").addEventListener("click", () => switchDetailTab("plot"));

async function applyRoute() {
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const tab = tabFromUrl();
  hideRouteNotice();
  if (path === "/new") {
    await openNewJob({ updateUrl: false, tab: tab || "preset" });
    return;
  }
  const match = path.match(/^\/jobs\/([^/]+)$/);
  if (match) {
    let jid = match[1];
    try { jid = decodeURIComponent(jid); } catch (e) { /* keep raw */ }
    await openJob(jid, { updateUrl: false, tab });
    return;
  }
  if (path === "/") {
    if (!state.jobs.length) {
      await openNewJob({ updateUrl: true, replaceUrl: true, tab: tab || "preset" });
    } else {
      await showHistory({ updateUrl: false });
    }
    return;
  }
  await showHistory({ updateUrl: false });
  navigateTo("/", true);
  showRouteNotice("页面不存在，已返回历史任务");
}
window.addEventListener("popstate", () => { applyRoute(); });

/* ---------- 结果表列切换 ---------- */
const LS_KEY = { timeMode: "pasta.timeMode", dsmMode: "pasta.dsmMode", arriveMode: "pasta.arriveMode" };
Object.entries(LS_KEY).forEach(([k, store]) => {
  const v = localStorage.getItem(store);
  if (v) state[k] = v;
});
$("paneData").addEventListener("click", e => {
  const b = e.target.closest(".seg button[data-v]");
  if (!b) return;
  const key = b.closest(".seg").dataset.k;
  state[key] = b.dataset.v;
  if (LS_KEY[key]) localStorage.setItem(LS_KEY[key], state[key]);
  renderTables();
});

/* ============================================================
 * 任务生命周期
 * ============================================================ */
async function submitJob() {
  if (state.busy) return;
  let cfg;
  try { cfg = collectConfig(); }
  catch (e) { alert("配置无效: " + e.message); return; }
  state.busy = true;
  $("runBtn").disabled = true;
  try {
    const r = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config: cfg, jobs: cfg.jobs }),
    });
    const j = await r.json();
    if (!r.ok) { alert("提交失败: " + (j.error || r.statusText)); return; }
    await loadJobList();
    await openJob(j.job_id);
  } catch (e) { alert("提交失败: " + e.message); }
  finally { state.busy = false; $("runBtn").disabled = state.presetLocked; }
}
$("runBtn").addEventListener("click", submitJob);

function resetDetailPanes() {
  state.lastResult = null;
  state.resultJobId = null;
  $("summaryCards").innerHTML = "";
  $("legTable").innerHTML = "";
  $("flybyTable").innerHTML = "";
  $("jobStateBar").innerHTML = "";
  $("chart").innerHTML = "";
  $("logBox").textContent = "（提交任务后显示）";
  $("logMeta").textContent = "";
  state.logFollow = true;
  const sec = $("logSection");
  sec.open = false;
  if (sec.parentElement === $("paneData")) $("paneData").appendChild(sec);
  $("jobSearch").value = state.jobSearch;
}

function renderStateBar(j) {
  const bits = [`<span class="badge ${j.status || "unknown"}">${escapeHtml(j.status || "unknown")}</span>`];
  if (j.name) bits.push(escapeHtml(j.name));
  if (j.seq_short) bits.push(escapeHtml(j.seq_short));
  if (j.elapsed_s != null) bits.push(`耗时 ${j.elapsed_s} s`);
  if (j.jobs != null) bits.push(`${j.jobs} workers`);
  $("jobStateBar").innerHTML = bits.join('<span class="dot">·</span>');
}

function setLogText(j) {
  const box = $("logBox");
  box.textContent = (j.log_tail || []).join("\n");
  const n = j.log_len != null ? j.log_len : (j.log_tail || []).length;
  $("logMeta").textContent = n ? `${n} 行` : "";
  if ($("logSection").open && state.logFollow) {
    requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; });
  }
}

function setLogLayout(success) {
  const sec = $("logSection");
  const pane = $("paneData");
  if (success) {
    sec.open = false;
    pane.appendChild(sec);
  } else {
    sec.open = true;
    pane.prepend(sec);
  }
}
$("logBox").addEventListener("scroll", () => {
  const box = $("logBox");
  state.logFollow = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
});
$("logSection").addEventListener("toggle", () => {
  const sec = $("logSection");
  if (sec.open && state.logFollow) {
    const box = $("logBox");
    requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; });
  }
});

function updateJobDetail(j) {
  if (!j || state.activeJobId !== j.job_id) return;
  state.jobs = (state.jobs || []).map(x =>
    x.job_id === j.job_id ? { ...x, ...j } : x);
  $("detailName").textContent = j.name || j.job_id;
  renderStateBar(j);
  const success = isSuccess(j);
  const terminal = isTerminal(j);
  if (j.status === "done" && j.has_result) {
    if (state.resultJobId !== j.job_id) renderResult(j.job_id);
  } else {
    state.lastResult = null;
    state.resultJobId = null;
    $("summaryCards").innerHTML = "";
    $("legTable").innerHTML = "";
    $("flybyTable").innerHTML = "";
    $("chart").innerHTML = "";
  }
  setLogLayout(success);
  setLogText(j);
  if (terminal) stopPolling();
}

async function refreshJobDetail() {
  const jid = state.activeJobId;
  if (!jid) return;
  let j;
  try { j = await jfetch("/api/jobs/" + encodeURIComponent(jid)); }
  catch (e) { return; }
  if (state.activeJobId !== jid) return;
  updateJobDetail(j);
  if (isTerminal(j)) {
    stopPolling();
    if (isSuccess(j) && !state.completedSeen.has(jid)) {
      state.completedSeen.add(jid);
      if (state.detailTab !== "plot") switchDetailTab("plot");
    }
  }
}

function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(refreshJobDetail, 1500);
}
function stopPolling() {
  if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
}

/* 取消当前任务 */
const cancelBtn = document.createElement("button");
cancelBtn.className = "ghost danger";
cancelBtn.textContent = "取消当前任务";
cancelBtn.onclick = async () => {
  if (!state.activeJobId) return;
  try {
    await fetch(`/api/jobs/${encodeURIComponent(state.activeJobId)}/cancel`, { method: "POST" });
  } catch (e) { console.error(e); }
};
$("logActions").appendChild(cancelBtn);

/* ============================================================
 * 结果渲染
 * ============================================================ */
async function renderResult(jid) {
  let r;
  try { r = await jfetch(`/api/jobs/${encodeURIComponent(jid)}/result.json`); }
  catch (e) { return; }
  if (state.activeJobId !== jid) return;
  const cards = $("summaryCards");
  if (r.status && r.status !== "ok") {
    cards.innerHTML = `<div class="card bad"><div class="k">状态</div>
      <div class="v">${escapeHtml(r.status)}</div></div>
      <div class="card wide"><div class="k">说明</div>
      <div class="v">${escapeHtml(r.error || "无可行解，未生成轨迹")}</div></div>`;
    state.lastResult = null;
    state.resultJobId = jid;
    $("legTable").innerHTML = "";
    $("flybyTable").innerHTML = "";
    $("chart").innerHTML = "<p>无可行解，无轨迹图</p>";
    return;
  }
  const c3 = r.c3 != null ? r.c3
    : (r.vinf_launch_kmps != null ? Math.pow(r.vinf_launch_kmps, 2) : null);
  cards.innerHTML = `
    <div class="card ${r.dsm_ok ? "ok" : "bad"}"><div class="k">总飞行时间</div>
      <div class="v">${fmt(r.tof_yr, 3)} yr (${fmt(r.tof_d, 1)} d)</div></div>
    <div class="card ${r.dsm_ok ? "ok" : "bad"}"><div class="k">总 DSM</div>
      <div class="v">${fmt(r.dsm_total_ms, 1)} m/s</div></div>
    <div class="card ${r.dsm_ok ? "ok" : "bad"}"><div class="k">发射 C3</div>
      <div class="v">${fmt(c3, 2)} km²/s²</div></div>
    <div class="card wide"><div class="k">发射</div>
      <div class="v">${r.launch_iso ? r.launch_iso.slice(0, 10) : "–"}<br>v∞ ${fmt(r.vinf_launch_kmps, 4)} km/s</div></div>
    <div class="card wide"><div class="k">到达</div>
      <div class="v">${r.arrival_iso ? r.arrival_iso.slice(0, 10) : "–"}<br>v∞ ${fmt(r.vinf_arrival_kmps, 4)} km/s</div></div>`;
  state.lastResult = r;
  state.resultJobId = jid;
  renderTables();
  if (state.detailTab === "plot") {
    state.plottedJobId = null;
    renderPlot(jid);
  }
}

function renderTables() {
  const r = state.lastResult;
  if (!r || (r.status && r.status !== "ok")) return;
  const timeMode = state.timeMode;
  const dsmMode = state.dsmMode;

  const legs = r.legs || [];
  const legHeads = [
    "从", "到",
    `<span class="tool-label">DSM</span>` +
      segHtml("timeMode", [["elapsed", "T+"], ["datetime", "Datetime"]]),
    `<span class="tool-label">Delta-v</span>` +
      segHtml("dsmMode", [["total", "Total"], ["rtn", "RTN"], ["rpn", "RPN"]]),
  ];
  const legRows = legs.map(l => {
    const out = [l.from, l.to];
    out.push(timeMode === "elapsed"
      ? fmtElapsed(l.dsm_elapsed_d)
      : (l.dsm_iso ? l.dsm_iso.slice(0, 19) : "–"));
    if (dsmMode === "total") {
      out.push(fmt(l.dsm_ms, 2));
    } else if (dsmMode === "rpn") {
      const rpn = l.dsm_rpn_ms || [];
      out.push(rpn.length === 3
        ? `R ${fmt(rpn[0], 2)} · P ${fmt(rpn[1], 2)} · N ${fmt(rpn[2], 2)}`
        : "–");
    } else {
      const rtn = l.dsm_rtn_ms || [];
      out.push(rtn.length === 3
        ? `R ${fmt(rtn[0], 2)} · T ${fmt(rtn[1], 2)} · N ${fmt(rtn[2], 2)}`
        : "–");
    }
    return out;
  });
  $("legTable").innerHTML = "<h3>转移信息</h3>" + tableHtml(legHeads, legRows);

  const arriveMode = state.arriveMode;
  const fly = r.flybys || [];
  const flyHeads = [
    "天体",
    `<span class="tool-label">到达时间</span>` +
      segHtml("arriveMode", [["elapsed", "T+"], ["datetime", "Datetime"]]),
    "rp (R)", "低点高度 (km)",
  ];
  const flyWhen = (iso, elapsed) => arriveMode === "elapsed"
    ? fmtElapsed(elapsed)
    : (iso ? iso.slice(0, 19) : "–");
  const flyRows = fly.map(f => {
    const cls = f.alt_ok ? "ok" : "bad";
    return [`<span class="${cls}">${escapeHtml(f.name)}</span>`,
      flyWhen(f.arrive_iso, f.arrive_elapsed_d),
      fmt(f.rp_R, 5), f.alt_km != null ? fmt(f.alt_km, 1) : "–"];
  });
  const seq = r.sequence || [];
  if (seq.length) {
    flyRows.push([`<span class="ok">${escapeHtml(seq[seq.length - 1])}</span>`,
      flyWhen(r.arrival_iso, r.arrival_elapsed_d), "–", "–"]);
  }
  $("flybyTable").innerHTML = "<h3>飞掠信息</h3>" + (flyRows.length ? tableHtml(
    flyHeads, flyRows) : "<p>无飞掠数据</p>");
}

function tableHtml(heads, rows) {
  return `<table><tr>${heads.map(h => `<th>${h}</th>`).join("")}</tr>` +
    rows.map(row => `<tr>${row.map(c => `<td>${c}</td>`).join("")}</tr>`).join("") + "</table>";
}

function segHtml(key, opts) {
  return `<span class="seg" data-k="${key}">` + opts.map(([v, label]) =>
    `<button data-v="${v}"${state[key] === v ? ' class="active"' : ""}>${label}</button>`
  ).join("") + `</span>`;
}

function dsmArrowTrace(tail, unit, L, name) {
  const [x, y, z] = tail;
  const [ux, uy, uz] = unit;
  const tx = x + L * ux, ty = y + L * uy, tz = z + L * uz;
  let rx = 0, ry = 0, rz = 1;
  if (Math.abs(uz) > 0.9) { rx = 1; ry = 0; rz = 0; }
  let px = uy * rz - uz * ry, py = uz * rx - ux * rz, pz = ux * ry - uy * rx;
  const pn = Math.hypot(px, py, pz) || 1;
  px /= pn; py /= pn; pz /= pn;
  const hl = 0.3 * L, hw = 0.15 * L;
  const bx = tx - hl * ux, by = ty - hl * uy, bz = tz - hl * uz;
  return {
    type: "scatter3d", mode: "lines", name,
    x: [x, tx, null, bx + hw * px, tx, bx - hw * px],
    y: [y, ty, null, by + hw * py, ty, by - hw * py],
    z: [z, tz, null, bz + hw * pz, tz, bz - hw * pz],
    line: { color: "#ff5c5c", width: 4 }, hoverinfo: "name",
  };
}

function orbitLabel(label, e) {
  if (!e) return "";
  const a = e.a_au == null ? "–" : fmt(e.a_au, 4);
  const rev = e.rev != null ? ` · rev=${e.rev}` : "";
  return `${label}<br>a=${a} AU · e=${fmt(e.e, 4)}` +
    `<br>i=${fmt(e.i_deg, 2)}° · Ω=${fmt(e.raan_deg, 2)}°` +
    ` · ω=${fmt(e.argp_deg, 2)}° · ν=${fmt(e.nu_deg, 2)}°${rev}`;
}

function vecLabel(v) {
  return Array.isArray(v) && v.length === 3
    ? v.map(x => fmt(x, 3)).join(", ") : "–";
}

async function renderPlot(jid) {
  if (!state.plotlyReady || typeof Plotly === "undefined") {
    $("chart").innerHTML = "<p>Plotly 未加载</p>";
    return;
  }
  let p;
  try { p = await jfetch(`/api/jobs/${encodeURIComponent(jid)}/plot.json`); }
  catch (e) { $("chart").innerHTML = "<p>plot.json 不可用</p>"; return; }
  if (state.activeJobId !== jid) return;

  const traces = [];
  traces.push({ type: "scatter3d", mode: "markers",
    x: p.sun.x, y: p.sun.y, z: p.sun.z, name: "Sun",
    marker: { size: 5, color: "#ffd75e" } });
  const depByKey = {};
  (p.legs || []).forEach(leg => {
    const d = leg.departure;
    if (d && d.epoch_iso) depByKey[`${leg.from}|${d.epoch_iso}`] = d;
  });
  (p.bodies || []).forEach((b, bi) => {
    traces.push({ type: "scatter3d", mode: "lines", name: b.tag,
      x: b.orbit.x, y: b.orbit.y, z: b.orbit.z,
      line: { color: b.color, width: 2 }, opacity: 0.6 });
    (b.encounters || []).forEach((en, ei) => {
      const when = (en.iso || "").slice(0, 10);
      const isDeparture = bi === 0 && ei === 0;
      const c3txt = (isDeparture && p.c3 != null)
        ? `\nC3=${fmt(p.c3, 2)} km²/s²` : "";
      const dep = depByKey[`${b.tag}|${en.iso}`];
      let hover = `${b.tag}${when ? "  " + when : ""}` +
        (isDeparture && p.c3 != null ? `  C3=${fmt(p.c3, 2)} km²/s²` : "");
      if (dep) {
        hover += `<br>v_out=(${vecLabel(dep.v_out_kmps)}) km/s` +
          `<br>v∞=(${vecLabel(dep.vinf_out_kmps)}) km/s`;
      }
      if (p.arrival && en.iso === p.arrival.epoch_iso) {
        hover += `<br>到达 v∞=(${vecLabel(p.arrival.vinf_in_kmps)}) km/s`;
      }
      traces.push({ type: "scatter3d", mode: "markers+text", name: b.tag + " 交会",
        x: [en.x], y: [en.y], z: [en.z],
        text: [`${b.tag}\n${when}${c3txt}`],
        hovertext: [hover], hoverinfo: "text",
        textfont: { size: 9, color: "#f0f0f0" },
        marker: { size: 3.5, color: b.color } });
    });
  });
  (p.legs || []).forEach((leg, i) => {
    const bl = leg.ballistic || {};
    const orb = leg.orbits || {};
    const blHover = orbitLabel(`leg #${i + 1}`, orb.ballistic);
    if ((bl.x || []).length) {
      const tr = { type: "scatter3d", mode: "lines",
        name: `leg${i + 1} 弹道`, x: bl.x, y: bl.y, z: bl.z,
        line: { color: "#f9a8d4", width: 3 } };
      if (blHover) { tr.hovertext = bl.x.map(() => blHover); tr.hoverinfo = "text"; }
      traces.push(tr);
    }
    const lm = leg.lambert || {};
    const lmHover = orbitLabel(`leg #${i + 1}`, orb.lambert);
    if ((lm.x || []).length) {
      const tr = { type: "scatter3d", mode: "lines",
        name: `leg${i + 1} Lambert`, x: lm.x, y: lm.y, z: lm.z,
        line: { color: "#f78fbe", width: 3, dash: "dot" } };
      if (lmHover) { tr.hovertext = lm.x.map(() => lmHover); tr.hoverinfo = "text"; }
      traces.push(tr);
    }
    const dsm = leg.dsm || {};
    if (dsm.x != null) {
      const when = dsm.iso ? dsm.iso.slice(0, 19) : "";
      let hover = `DSM${i + 1}${when ? "<br>" + when : ""}`;
      if (dsm.eta != null) hover += `<br>eta ${fmt(dsm.eta, 4)}`;
      hover += `<br>总 ${fmt(dsm.dsm_ms, 2)} m/s`;
      const rtn = dsm.rtn;
      if (Array.isArray(rtn) && rtn.length === 3) {
        hover += `<br>R ${fmt(rtn[0], 2)} · T ${fmt(rtn[1], 2)} · N ${fmt(rtn[2], 2)} m/s`;
      }
      const rpn = dsm.rpn;
      if (Array.isArray(rpn) && rpn.length === 3) {
        hover += `<br>R ${fmt(rpn[0], 2)} · P ${fmt(rpn[1], 2)} · N ${fmt(rpn[2], 2)} m/s`;
      }
      traces.push({ type: "scatter3d", mode: "markers+text",
        name: `DSM${i + 1}`, x: [dsm.x], y: [dsm.y], z: [dsm.z],
        text: [fmt(dsm.dsm_ms, 0) + " m/s"], hovertext: [hover],
        hoverinfo: "text",
        textfont: { size: 9, color: "#f0f0f0" },
        marker: { size: 2.5, color: "#b56cff", symbol: "diamond" } });
    }
    if (state.showDsmDir && Array.isArray(dsm.unit) && dsm.unit.length === 3) {
      traces.push(dsmArrowTrace([dsm.x, dsm.y, dsm.z], dsm.unit,
        p.dsm_arrow_au || 0.3, `DSM${i + 1} 方向`));
    }
  });

  const axis = {
    gridcolor: "#3f3f3f",
    zerolinecolor: "#3f3f3f",
    color: "#b0b0b0",
    backgroundcolor: "#1f1f1f",
    showbackground: true,
    title: "AU",
  };
  Plotly.newPlot("chart", traces, {
    paper_bgcolor: "#1f1f1f", plot_bgcolor: "#1f1f1f",
    font: { color: "#f0f0f0", size: 10, family: "Segoe UI, Microsoft YaHei, sans-serif" },
    scene: { aspectmode: "data",
      bgcolor: "#1f1f1f",
      camera: { eye: { x: -0.1, y: -1.6, z: 0.35 } },
      xaxis: { ...axis }, yaxis: { ...axis }, zaxis: { ...axis } },
    margin: { l: 0, r: 0, t: 36, b: 0 },
    title: { text: `${p.name || "EVVEJU"} · TOF=${fmt(p.tof_yr, 2)} yr · DSM=${fmt(p.dsm_total_ms, 0)} m/s`,
      font: { size: 13, color: "#f0f0f0" } },
    showlegend: true,
    legend: { bgcolor: "rgba(43,43,43,.9)", bordercolor: "#3f3f3f", borderwidth: 1,
      font: { color: "#f0f0f0", size: 10 } },
  }, { responsive: true });
  state.plottedJobId = jid;
}

/* 下载按钮 */
$("dsmDirToggle").addEventListener("change", (e) => {
  state.showDsmDir = e.target.checked;
  if (state.activeJobId) {
    state.plottedJobId = null;
    renderPlot(state.activeJobId);
  }
});
$("pngDown").addEventListener("click", () => {
  if (state.activeJobId)
    window.open(`/api/jobs/${encodeURIComponent(state.activeJobId)}/trajectory.png`);
});
$("jsonDown").addEventListener("click", () => {
  if (state.activeJobId)
    window.open(`/api/jobs/${encodeURIComponent(state.activeJobId)}/result.json`);
});
$("logDown").addEventListener("click", () => {
  if (state.activeJobId)
    window.open(`/api/jobs/${encodeURIComponent(state.activeJobId)}/log.txt`);
});

/* ============================================================
 * 终止程序 / 关闭确认 / 系统设置
 * ============================================================ */
$("shutdownBtn").addEventListener("click", async () => {
  if (!confirm("确定终止后端程序?\n- 运行中的任务会被中断\n- 服务将退出, 之后无法再访问本页面\n- 本标签页将尝试自动关闭\n(以后再次运行程序即可恢复)")) return;
  try {
    await fetch("/api/shutdown", { method: "POST" });
  } catch (e) { /* 连接断开 = 后端已退出 */ }
  try { window.close(); } catch (err) { /* 忽略 */ }
  setTimeout(() => {
    if (!document.hidden) {
      alert("后端已退出。若标签页未自动关闭，请手动关闭。");
    }
  }, 600);
});

window.addEventListener("beforeunload", (e) => {
  const running = (state.jobs || []).some(j => j.status === "running" || j.status === "queued");
  if (running) {
    e.preventDefault();
    e.returnValue = "";
  }
});

function showModalPane(pane) {
  document.querySelectorAll("#sysModal .side-item").forEach(b =>
    b.classList.toggle("active", b.dataset.pane === pane));
  $("mpaneSettings").classList.toggle("active", pane === "settings");
  $("mpaneEphemeris").classList.toggle("active", pane === "ephemeris");
  $("mpaneAbout").classList.toggle("active", pane === "about");
}
function closeModal() { $("sysModal").classList.add("hidden"); }

async function loadSysConfig() {
  try {
    const s = await jfetch("/api/sysconfig");
    $("sysLan").checked = (s.host === "0.0.0.0" || s.host === "::");
    $("sysPort").value = s.port;
    $("sysOpenBrowser").checked = !!s.open_browser;
    renderEphemerisList(s.ephemeris_options || [], s.ephemerides || []);
    showEphWarnings(s.ephemeris_options || [], s.ephemerides || []);
    $("sysHorizonsResults").innerHTML = "";
    $("sysHorizonsMsg").textContent = "";
    $("sysHorizonsDownload").disabled = true;
    const bw = $("sysLanWarnBanner");
    if (bw) bw.classList.toggle("hidden", !$("sysLan").checked);
  } catch (e) { console.error(e); }
}

function renderEphemerisList(opts, chosen) {
  const box = $("sysEphemList");
  if (!box) return;
  const sel = new Set(chosen || []);
  box.innerHTML = "";
  if (!opts.length) {
    box.innerHTML = '<div class="hint">(未发现 *.bsp — 放入 ephemeris/ 后点"重新扫描")</div>';
    return;
  }
  opts.forEach(o => {
    const mb = (o.size / 1048576).toFixed(1);
    const ok = !o.warning;
    const lab = document.createElement("label");
    lab.className = "ephem-item";
    lab.innerHTML = `<input type="checkbox" value="${escapeHtml(o.file)}" ` +
      `${sel.has(o.file) ? "checked" : ""} ${ok ? "" : "disabled"}> ` +
      `<span class="eph-name">${escapeHtml(o.file)}</span> ` +
      `<span class="eph-meta">${o.n_bodies} 天体 · ${mb} MB</span>` +
      `<button class="eph-del" title="删除该星历" data-file="${escapeHtml(o.file)}">✕</button>` +
      (o.partial ? `<div class="eph-warn">⚠ 覆盖期不足 1849–2150（超出部分任务可能失败）</div>` : "") +
      (o.warning ? `<div class="eph-warn">⚠ ${escapeHtml(o.warning)}</div>` : "");
    box.appendChild(lab);
  });
}
function showEphWarnings(opts, chosen) {
  const b = $("ephWarnBanner");
  if (!b) return;
  const sel = new Set(chosen || []);
  const bad = (opts || []).filter(o => o.warning && sel.has(o.file));
  if (!bad.length) {
    b.textContent = "";
    b.classList.add("hidden");
    return;
  }
  b.textContent = "⚠ 星历文件异常：" +
    bad.map(o => `${o.file}（${o.warning}）`).join("；") +
    "。请取消勾选、删除或重新下载该文件。";
  b.classList.remove("hidden");
}
function selectedEphemerides() {
  return [...document.querySelectorAll("#sysEphemList input[type=checkbox]:checked")].map(c => c.value);
}

let aboutLoaded = false;
function aboutCard(k, v, cls) {
  const val = (v == null || v === "") ? "–" : escapeHtml(String(v));
  return `<div class="about-item"><div class="k">${escapeHtml(k)}</div>` +
         `<div class="v ${cls || ""}">${val}</div></div>`;
}
async function loadAbout() {
  if (aboutLoaded) return;
  try {
    const a = await jfetch("/api/about");
    $("aboutMeta").innerHTML = [
      aboutCard("版本", a.describe),
      aboutCard("构建时间", a.build_time),
      aboutCard("提交", a.commit + (a.dirty ? " (dirty)" : ""), a.dirty ? "bad" : "ok"),
      aboutCard("提交日期", a.commit_date),
      aboutCard("提交说明", a.commit_subject),
      aboutCard("完整哈希", a.commit_full),
    ].join("");
    const rt = a.runtime || {};
    $("aboutRuntime").innerHTML = [
      aboutCard("Python", rt.python),
      aboutCard("平台", rt.platform),
      aboutCard("运行方式", rt.frozen ? "打包程序 (frozen)" : "源码"),
      aboutCard("pykep", rt.pykep),
      aboutCard("pygmo", rt.pygmo),
      aboutCard("numpy", rt.numpy),
    ].join("");
    $("aboutLicense").textContent = a.license_text || "(未找到 LICENSE 文件)";
    $("aboutThirdParty").textContent = a.thirdparty_text || "(未找到 ThirdPartyNotice.md)";
    if (a.repo) $("aboutRepo").onclick = () => window.open(a.repo, "_blank", "noopener");
    if (a.releases) $("aboutReleases").onclick = () => window.open(a.releases, "_blank", "noopener");
    aboutLoaded = true;
  } catch (e) { console.error("about", e); }
}
async function openAbout() {
  await loadAbout();
  showModalPane("about");
  $("sysModal").classList.remove("hidden");
}

document.querySelectorAll("#sysModal .side-item").forEach(btn => {
  btn.addEventListener("click", () => {
    if (btn.dataset.pane === "about") loadAbout();
    showModalPane(btn.dataset.pane);
  });
});
$("sysOpen").addEventListener("click", async () => {
  await loadSysConfig();
  showModalPane("settings");
  $("sysModal").classList.remove("hidden");
});
$("sysEphemRescan").addEventListener("click", async () => {
  try {
    const r = await fetch("/api/ephemeris/rescan", { method: "POST" });
    const j = await r.json();
    if (!r.ok) { alert("扫描失败: " + (j.error || r.statusText)); return; }
    renderEphemerisList(j.options || [], selectedEphemerides());
    showEphWarnings(j.options || [], selectedEphemerides());
    await loadBodies(); refreshPlanetOptions();
  } catch (e) { alert("扫描失败: " + e.message); }
});
$("sysEphemList").addEventListener("click", async (e) => {
  const b = e.target.closest(".eph-del");
  if (!b) return;
  const file = b.dataset.file;
  if (!confirm("删除星历文件 " + file + " ?")) return;
  try {
    const r = await fetch("/api/ephemeris/" + encodeURIComponent(file), { method: "DELETE" });
    const j = await r.json();
    if (!r.ok) { alert("删除失败: " + (j.error || r.statusText)); return; }
    const s = await jfetch("/api/sysconfig");
    renderEphemerisList(s.ephemeris_options || [], s.ephemerides || []);
    showEphWarnings(s.ephemeris_options || [], s.ephemerides || []);
    await loadBodies(); refreshPlanetOptions();
  } catch (e) { alert("删除失败: " + e.message); }
});

function renderHorizonsResults(results) {
  const box = $("sysHorizonsResults");
  box.innerHTML = "";
  if (!results.length) { box.innerHTML = '<div class="hint">未找到天体</div>'; return; }
  results.forEach((c, i) => {
    const disabled = c.naif == null || !c.horizons;
    const row = document.createElement("label");
    row.className = "eph-result" + (disabled ? " disabled" : "");
    row.dataset.json = JSON.stringify(c);
    row.innerHTML = `<input type="radio" name="hz" value="${i}" ${disabled ? "disabled" : (i === 0 ? "checked" : "")}> ` +
      `<span class="eph-name">${escapeHtml(c.label || "")}</span> ` +
      `<span class="eph-meta">${escapeHtml(c.kind || "")}${c.orbit_class ? " · " + escapeHtml(c.orbit_class) : ""}${disabled ? " · 暂不支持" : ""}</span>`;
    box.appendChild(row);
  });
  $("sysHorizonsDownload").disabled = !results.some(c => c.naif != null && c.horizons);
}
$("sysHorizonsSearch").addEventListener("click", async () => {
  const q = $("sysHorizonsQuery").value.trim();
  if (!q) return;
  $("sysHorizonsResults").innerHTML = '<div class="hint">搜索中…</div>';
  $("sysHorizonsDownload").disabled = true;
  try {
    const r = await jfetch("/api/horizons/search?q=" + encodeURIComponent(q));
    renderHorizonsResults(r.results || []);
  } catch (e) { $("sysHorizonsResults").innerHTML = `<div class="hint">搜索失败: ${escapeHtml(e.message)}</div>`; }
});
async function pollAddTask(tid, onTick) {
  for (;;) {
    const t = await jfetch("/api/ephemeris/task/" + encodeURIComponent(tid));
    if (onTick) onTick(t);
    if (t.status === "done" || t.status === "error") return t;
    await new Promise(r => setTimeout(r, 1000));
  }
}
$("sysHorizonsDownload").addEventListener("click", async () => {
  const sel = document.querySelector('#sysHorizonsResults input[name="hz"]:checked');
  if (!sel) return;
  const cand = JSON.parse(sel.closest(".eph-result").dataset.json);
  const msg = $("sysHorizonsMsg");
  const btn = $("sysHorizonsDownload");
  msg.textContent = "连接中…";
  btn.disabled = true;
  try {
    const payload = {
      naif: cand.naif, horizons: cand.horizons, name: cand.label,
    };
    const post = (overwrite) => fetch("/api/ephemeris/add", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, overwrite: !!overwrite }),
    });
    let r = await post(false);
    let j = await r.json();
    if (r.status === 409 && j.can_overwrite) {
      if (!confirm(`${j.error}\n\n覆盖会重新下载完整时间窗口，是否继续？`)) {
        msg.textContent = "已取消";
        return;
      }
      r = await post(true);
      j = await r.json();
    }
    if (!r.ok) { msg.textContent = "失败: " + (j.error || r.statusText); return; }
    const t = await pollAddTask(j.task_id, (s) => {
      if (s.status === "running") {
        msg.textContent = s.total ? `下载中 (${s.done}/${s.total})…` : "连接中…";
      }
    });
    if (t.status === "error") { msg.textContent = "失败: " + t.error; return; }
    const res = t.result || {};
    const chosen = selectedEphemerides();
    if (res.file && !chosen.includes(res.file)) chosen.push(res.file);
    const s = await jfetch("/api/sysconfig");
    renderEphemerisList(s.ephemeris_options || [], chosen);
    showEphWarnings(s.ephemeris_options || [], chosen);
    await loadBodies(); refreshPlanetOptions();
    msg.textContent = `已添加 ${res.file}` + (res.radius_m ? ` (R=${Math.round(res.radius_m)} m)` : "")
      + (res.warning ? " ⚠ " + res.warning : "");
  } catch (e) { msg.textContent = "失败: " + e.message; }
  finally { btn.disabled = false; }
});
$("modalClose").addEventListener("click", closeModal);
$("sysSave").addEventListener("click", async () => {
  const lan = $("sysLan").checked;
  if (lan && !confirm(
      "⚠ 将监听 0.0.0.0 (开放到局域网):\n- 局域网内任何设备都能访问本页面\n- 本工具无任何鉴权\n- 仅限安全内网使用!\n\n确定保存并重启生效?")) return;
  try {
    const r = await fetch("/api/sysconfig", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        host: lan ? "0.0.0.0" : "127.0.0.1",
        port: +$("sysPort").value,
        open_browser: $("sysOpenBrowser").checked,
      }),
    });
    const j = await r.json();
    if (!r.ok) { alert("保存失败: " + (j.error || r.statusText)); return; }
    alert("已保存。需重启程序后生效 (host/port)。");
    closeModal();
  } catch (e) { alert("保存失败: " + e.message); }
});
$("sysEphSave").addEventListener("click", async () => {
  try {
    const r = await fetch("/api/sysconfig", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ephemerides: selectedEphemerides() }),
    });
    const j = await r.json();
    if (!r.ok) { alert("保存失败: " + (j.error || r.statusText)); return; }
    await loadBodies(); refreshPlanetOptions();
    alert("已保存");
    closeModal();
  } catch (e) { alert("保存失败: " + e.message); }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("sysModal").classList.contains("hidden")) closeModal();
});

/* Fluent Reveal: 让高光与边框跟随鼠标, 只在悬停元素附近出现 */
const REVEAL_SELECTOR = [
  ".job-card", ".detail-tabs .tab", ".side-item", ".eph-result", ".ephem-item",
  ".node-row", "details.main-panel > summary", "button.primary", "button.ghost",
  ".search-box",
].join(",");
let revealFrame = 0;
let revealTarget = null;
let revealX = 0;
let revealY = 0;
document.addEventListener("pointermove", (e) => {
  const target = e.target;
  if (!(target instanceof Element)) return;
  const el = target.closest(REVEAL_SELECTOR);
  if (!el) return;
  revealTarget = el;
  revealX = e.clientX;
  revealY = e.clientY;
  if (revealFrame) return;
  revealFrame = requestAnimationFrame(() => {
    revealFrame = 0;
    if (!revealTarget || !revealTarget.isConnected) return;
    const rect = revealTarget.getBoundingClientRect();
    revealTarget.style.setProperty("--mx", `${revealX - rect.left}px`);
    revealTarget.style.setProperty("--my", `${revealY - rect.top}px`);
  });
}, { passive: true });

/* ============================================================
 * 初始化
 * ============================================================ */
(async function init() {
  if (typeof Plotly !== "undefined") state.plotlyReady = true;
  else {
    const t = setInterval(() => {
      if (typeof Plotly !== "undefined") {
        state.plotlyReady = true;
        clearInterval(t);
        if (state.view === "detail" && state.detailTab === "plot" && state.activeJobId) {
          state.plottedJobId = null;
          renderPlot(state.activeJobId);
        }
      }
    }, 200);
  }
  try {
    const h = await jfetch("/api/health");
    const ver = h.describe || h.version || "";
    const el = $("appVersion");
    if (el) {
      el.textContent = ver;
      el.title = `${h.host || "127.0.0.1"}:${h.port || 8765} · 点击查看关于`;
      el.addEventListener("click", openAbout);
      if (!ver) el.classList.add("hidden");
    }
    if (h.lan) { const b = $("sysLanWarnBanner"); if (b) b.classList.remove("hidden"); }
  } catch (e) { console.error(e); }
  await loadBodies();
  await loadSysConfig();
  await loadPresets();
  await loadCompCfg();
  await loadJobList();
  await applyRoute();
})();
