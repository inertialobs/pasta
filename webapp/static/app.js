// PASTA (Parallel Astrodynamic Solver for Trajectory Analysis) Web 前端逻辑 (原生 JS, 无框架)
"use strict";

/* ---------- 工具 ---------- */
const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (v == null ? "–" : Number(v).toFixed(d));
async function jfetch(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}
function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
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
  VENUS:   { label: "Venus 金星",   note: "≥ 200 km 地表以上" },
  EARTH:   { label: "Earth 地球",   note: "≥ 200 km 地表以上" },
  MARS:    { label: "Mars 火星",    note: "≥ 200 km 地表以上" },
  JUPITER: { label: "Jupiter 木星", note: "≥ 2×R_planet (71492 km)" },
  SATURN:  { label: "Saturn 土星",  note: "≥ 2×R_planet (58232 km)" },
  URANUS:  { label: "Uranus 天王星", note: "到达节点 (无飞掠判定)" },
  NEPTUNE: { label: "Neptune 海王星", note: "≥ 2×R_planet (24622 km)" },
};

/* ---------- 状态 ---------- */
const state = {
  jobs: [], activeJobId: null, pollTimer: null, plotlyReady: false,
  seq: [], pendingTofBounds: null, busy: false,
};

/* ============================================================
 * 预设与配置表单
 * ============================================================ */
async function loadPresets() {
  try {
    const p = await jfetch("/api/presets");
    const sel = $("presetSelect");
    sel.innerHTML = "";
    Object.keys(p || {}).forEach(name => {
      const opt = document.createElement("option");
      opt.value = name; opt.textContent = name;
      sel.appendChild(opt);
    });
    const first = Object.values(p || {})[0];
    if (first) fillTrajForm(first);
  } catch (e) { console.error("presets", e); }
}

/* 全局计算配置: 唯一一份, 表单初始值来源 (每次提交任务后由后端自动更新) */
async function loadCompCfg() {
  try {
    const c = await jfetch("/api/compcfg");
    fillCompForm(c);
  } catch (e) { console.error("compcfg", e); }
}

function fillTrajForm(cfg) {
  $("cfgName").value = cfg.name || "EVVEJU";
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
                 cfg.tof_bounds);
  buildEraTable(cfg.eras);
  updateConfigJson();
  // 载入反馈
  $("trajPresetMsg").textContent =
    `已载入任务预设「${cfg.name || "?"}」: ` +
    `${(cfg.seq || []).join("→")} · 目标 ${cfg.objective || "min_tof"}`;
}

function fillCompForm(cfg) {
  $("cfgRunScan").checked = cfg.run_scan !== false;
  $("cfgRunSeed").checked = cfg.run_seed !== false;
  $("cfgRunCompress").checked = cfg.run_compress !== false;
  $("cfgRunFrontier").checked = cfg.run_frontier !== false;
  $("cfgSmoke").checked = !!cfg.smoke;
  $("cfgJobs").value = cfg.jobs || 4;
  $("cfgScanKeep").value = cfg.scan_keep || 8;
  $("cfgRefineKeep").value = cfg.refine_keep || 6;
  $("cfgEraStep").value = cfg.era_step_d || 60;   // 搜索步进 (天)
  updateConfigJson();
}

/* ---------- 序列节点编辑器 ---------- */
function buildSeqEditor(seq, tofBounds) {
  state.seq = [...seq];
  state.pendingTofBounds = Array.isArray(tofBounds) ? tofBounds : null;
  const wrap = $("seqNodes");
  wrap.innerHTML = "";
  seq.forEach((tag, i) => {
    const role = i === 0 ? "depart"
      : (i === seq.length - 1 ? "arrive" : "flyby");
    const row = document.createElement("div");
    row.className = "node-row " + role;
    row.dataset.i = i;
    row.innerHTML = `
      <span class="node-idx">${i + 1}</span>
      <select data-k="tag">${planetOptions(tag)}</select>
      <span class="node-role ${role}">${role === "depart" ? "出发" : role === "arrive" ? "到达" : "飞掠"}</span>
      <span class="node-settings" data-k="settings">
        <span class="node-safe">${PLANETS[tag] ? PLANETS[tag].note : ""}</span>
      </span>
      <span class="node-moves">
        <button class="node-move up" title="上移">↑</button>
        <button class="node-move dn" title="下移">↓</button>
      </span>
      <button class="node-del" title="删除节点">✕</button>`;
    if (seq.length <= 2) row.querySelector(".node-del").style.visibility = "hidden";
    wrap.appendChild(row);
  });
  refreshSeqRoles();
}

function planetOptions(sel) {
  return Object.entries(PLANETS).map(([tag, p]) =>
    `<option value="${tag}" ${tag === sel ? "selected" : ""}>${p.label}</option>`).join("");
}

/* 重新计算角色/设置 (行星变化或增删移后) */
function refreshSeqRoles() {
  const rows = [...document.querySelectorAll("#seqNodes .node-row")];
  rows.forEach((row, i) => {
    const role = i === 0 ? "depart" : (i === rows.length - 1 ? "arrive" : "flyby");
    row.className = "node-row " + role;
    row.dataset.i = i;
    const roleSpan = row.querySelector(".node-role");
    roleSpan.className = "node-role " + role;
    roleSpan.textContent = role === "depart" ? "出发" : role === "arrive" ? "到达" : "飞掠";
    const current = row.querySelector(".node-settings");
    current.innerHTML = "";
    const tag = row.querySelector('select[data-k="tag"]').value;
    const safe = PLANETS[tag] ? PLANETS[tag].note : "";
    current.insertAdjacentHTML("beforeend",
      `<span class="node-safe">${safe}</span>`);
    const del = row.querySelector(".node-del");
    del.style.visibility = rows.length <= 2 ? "hidden" : "visible";
    const up = row.querySelector(".node-move.up");
    const dn = row.querySelector(".node-move.dn");
    if (up) up.style.visibility = (role === "flyby" && i > 1) ? "visible" : "hidden";
    if (dn) dn.style.visibility = (role === "flyby" && i < rows.length - 2) ? "visible" : "hidden";
  });
  buildTofTable(rows.length - 1);
}

/* 交换飞掠节点位置 (dir: -1 上移, +1 下移) */
function moveNode(i, dir) {
  const wrap = $("seqNodes");
  const rows = [...wrap.children];
  if (i < 1 || i > rows.length - 2) return;
  const j = i + dir;
  if (j < 1 || j > rows.length - 2) return;
  const a = rows[i], b = rows[j];
  if (dir < 0) wrap.insertBefore(a, b);
  else wrap.insertBefore(b, a);
  refreshSeqRoles();
  updateConfigJson();
}

/* 每腿 TOF 表 (与 seq 联动) */
function buildTofTable(nLegs) {
  const tb = $("tofTable");
  tb.innerHTML = "";
  const tbl = document.createElement("table");
  tbl.innerHTML = "<tr><th>腿</th><th>区间</th><th>最小 (d)</th><th>最大 (d)</th></tr>";
  let lastCfg = {};
  try { lastCfg = JSON.parse($("cfgJsonBox").value || "{}") || {}; } catch (e) { lastCfg = {}; }
  // 优先级: 预设载入带来的 tof_bounds > 上次表单值 (节点增删时保留编辑) > 默认值
  const pend = state.pendingTofBounds;
  const arr = (pend && pend.length === nLegs)
    ? pend
    : (lastCfg.tof_bounds && lastCfg.tof_bounds.length === nLegs)
      ? lastCfg.tof_bounds : defaultTofBounds(nLegs);
  state.pendingTofBounds = null;   // 只消费一次, 后续节点增删走 lastCfg 回退
  const seq = state.seq;
  for (let i = 0; i < nLegs; i++) {
    const [lo, hi] = arr[i] || [60, 5000];
    const from = seq[i], to = seq[i + 1];
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${i + 1}</td>
      <td>${from} → ${to}</td>
      <td><input data-leg="${i}" data-k="lo" type="number" value="${lo}" min="1"></td>
      <td><input data-leg="${i}" data-k="hi" type="number" value="${hi}" min="1"></td>`;
    tbl.appendChild(tr);
  }
  tb.appendChild(tbl);
}

function defaultTofBounds(n) {
  const def = [[190, 200], [300, 500], [10, 90], [400, 1100], [1200, 4500], [800, 4000]];
  return def.slice(0, n).map(b => [...b]);
}

/* 发射窗口表 (可增可删, 至少保留 1 个) */
function buildEraTable(eras) {
  const arr = (eras && eras.length) ? eras : [["2029-01-01", "2033-06-30"]];
  const eb = $("eraTable");
  eb.innerHTML = "";
  arr.forEach(([a, b], i) => {
    const row = document.createElement("div");
    row.className = "era-row";
    row.innerHTML = `<label>窗口 ${i + 1}</label>
      <input data-k="a" type="date" value="${a}">
      <span>→</span>
      <input data-k="b" type="date" value="${b}">
      <button class="era-del" title="删除窗口" ${arr.length <= 1 ? "disabled" : ""}>✕</button>`;
    eb.appendChild(row);
  });
}

/* 汇总为配置 JSON */
function collectConfig() {
  const rows = [...document.querySelectorAll("#seqNodes .node-row")];
  const seq = rows.map(r => r.querySelector('select[data-k="tag"]').value);
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
  const obj = {
    name: $("cfgName").value || "EVVEJU",
    seq,
    safe_radius: {},
    tof_bounds: tofB,
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
    era_step_d: +$("cfgEraStep").value,          // 搜索步进 (天)
    jobs: +$("cfgJobs").value,
    smoke: $("cfgSmoke").checked,
    run_scan: $("cfgRunScan").checked,
    run_seed: $("cfgRunSeed").checked,
    run_compress: $("cfgRunCompress").checked,
    run_frontier: $("cfgRunFrontier").checked,
    scan_keep: +$("cfgScanKeep").value,
    refine_keep: +$("cfgRefineKeep").value,
  };
  return obj;
}

/* ---------- 事件绑定 (序列/era 编辑 / 预设 / 配置) ---------- */
$("nodeAdd").addEventListener("click", () => {
  const rows = [...document.querySelectorAll("#seqNodes .node-row")];
  if (rows.length >= 10) { alert("序列最多 10 个节点"); return; }
  const arrive = rows[rows.length - 1];
  const row = document.createElement("div");
  row.className = "node-row flyby";
  row.innerHTML = `
    <span class="node-idx">${rows.length}</span>
    <select data-k="tag">${planetOptions("VENUS")}</select>
    <span class="node-role flyby">飞掠</span>
    <span class="node-settings">
      <span class="node-safe">${PLANETS.VENUS.note}</span>
    </span>
    <span class="node-moves">
      <button class="node-move up" title="上移">↑</button>
      <button class="node-move dn" title="下移">↓</button>
    </span>
    <button class="node-del" title="删除节点">✕</button>`;
  arrive.parentNode.insertBefore(row, arrive);
  refreshSeqRoles();
  updateConfigJson();
});

$("seqNodes").addEventListener("click", (e) => {
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
  if (e.target.matches('select[data-k="tag"]')) {
    const row = e.target.closest(".node-row");
    const safe = PLANETS[e.target.value] ? PLANETS[e.target.value].note : "";
    const s = row.querySelector(".node-safe");
    if (s) s.textContent = safe;
  }
  updateConfigJson();
});
$("seqNodes").addEventListener("input", () => updateConfigJson());
$("eraTable").addEventListener("click", (e) => {
  const btn = e.target.closest(".era-del");
  if (!btn || btn.disabled) return;
  const rows = [...document.querySelectorAll("#eraTable .era-row")];
  if (rows.length <= 1) return;
  btn.closest(".era-row").remove();
  [...document.querySelectorAll("#eraTable .era-row")].forEach((r, i) => {
    r.querySelector("label").textContent = `窗口 ${i + 1}`;
  });
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
  const name = ($("cfgName").value || "EVVEJU").trim();
  if (!name) { alert("先填写任务名"); return; }
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
 "cfgEtaL", "cfgEtaH", "cfgRpUb", "cfgJobs", "cfgSmoke",
 "cfgRunScan", "cfgRunSeed", "cfgRunCompress", "cfgRunFrontier",
 "cfgScanKeep", "cfgRefineKeep", "cfgEraStep", "cfgWToF", "cfgWDsm",
 "cfgPenL1", "cfgPenL2", "cfgFPenL1", "cfgFPenL2"]
  .forEach(id => $(id).addEventListener("input", updateConfigJson));
document.addEventListener("input", e => {
  if (e.target.closest("#tofTable") || e.target.closest("#eraTable")) updateConfigJson();
});
$("saveCfg").addEventListener("click", () => {
  const blob = new Blob([$("cfgJsonBox").value], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "traj_config.json";
  a.click();
});

/* ============================================================
 * 左侧边栏折叠
 * ============================================================ */
$("sidebarToggle").addEventListener("click", () => {
  document.querySelector(".layout").classList.toggle("side-collapsed");
});

/* ============================================================
 * 日志 / 结果 双 tab
 * ============================================================ */
function switchTab(tab) {
  const isLog = tab === "log";
  $("tabLog").classList.toggle("active", isLog);
  $("tabResult").classList.toggle("active", !isLog);
  $("paneLog").classList.toggle("active", isLog);
  $("paneResult").classList.toggle("active", !isLog);
  if (!isLog && typeof Plotly !== "undefined") setTimeout(() => Plotly.Plots.resize($("chart")), 60);
}
$("tabLog").addEventListener("click", () => switchTab("log"));
$("tabResult").addEventListener("click", () => switchTab("result"));

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
  switchTab("log");
  try {
    const r = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config: cfg, jobs: cfg.jobs }),
    });
    const j = await r.json();
    if (!r.ok) { alert("提交失败: " + (j.error || r.statusText)); return; }
    state.activeJobId = j.job_id;
    loadJobList();
    startPolling();
  } catch (e) { alert("提交失败: " + e.message); }
  finally { state.busy = false; $("runBtn").disabled = false; }
}
$("runBtn").addEventListener("click", submitJob);

function jobItemHtml(j, active) {
  const meta = [];
  if (j.created) meta.push(fmtTS(j.created));
  if (j.elapsed_s != null) meta.push(j.elapsed_s + " s");
  return `<div class="job-item ${active ? "active" : ""}" id="jobitem-${escapeHtml(j.job_id)}"
      onclick="selectJob('${escapeHtml(j.job_id)}')">
    <div class="job-line1"><span class="badge ${j.status}">${j.status}</span><span class="name">${escapeHtml(j.name || "无任务名")}</span>
      <button class="job-del" title="删除任务 (含产物)" onclick="deleteJob('${escapeHtml(j.job_id)}', event)">🗑</button></div>
    <div class="job-meta">${meta.length ? meta.join(" · ") : ""}</div>
  </div>`;
}

/* 删除任务: 终止(若在跑) + 删 runs/<jid> 产物 + 移出列表 */
async function deleteJob(jid, ev) {
  if (ev) ev.stopPropagation();   // 不触发选中
  if (!confirm(`确定删除任务 ${jid}？\n产物 (log/result/plot) 会被一并删除。`)) return;
  try {
    const r = await fetch(`/api/jobs/${encodeURIComponent(jid)}`, { method: "DELETE" });
    if (!r.ok) { alert("删除失败: " + r.status); return; }
    if (state.activeJobId === jid) { state.activeJobId = null; stopPolling(); }
    await loadJobList();
  } catch (e) { alert("删除失败: " + e.message); }
}
window.deleteJob = deleteJob;

function selectJob(jid) {
  state.activeJobId = jid;
  [...document.querySelectorAll("#jobList .job-item")].forEach(el =>
    el.classList.toggle("active", el.id === "jobitem-" + jid));
  refreshJobDetail();
}
window.selectJob = selectJob;

async function refreshJobDetail() {
  if (!state.activeJobId) return;
  let j;
  try { j = await jfetch("/api/jobs/" + state.activeJobId); }
  catch (e) { return; }
  const item = $("jobitem-" + state.activeJobId);
  if (item) item.outerHTML = jobItemHtml(j, true);
  state.jobs = (state.jobs || []).map(x => x.job_id === j.job_id ? j : x);
  $("logBox").textContent = (j.log_tail || []).join("\n");
  $("logBox").scrollTop = $("logBox").scrollHeight;
  if (j.status === "done" && j.has_result) {
    renderResult(j.job_id);
    stopPolling();
    switchTab("result");   // 计算完成自动切到结果
  } else if (["failed", "cancelled"].includes(j.status)) {
    stopPolling();
    if (j.status === "failed") {
      switchTab("log");
      $("logBox").textContent += "\n\n[状态: failed — 见上方日志末尾错误]";
    }
    $("summaryCards").innerHTML = `<div class="card bad"><div class="k">状态</div>
      <div class="v">${j.status}</div></div>`;
  }
}

async function startPolling() {
  stopPolling();
  state.pollTimer = setInterval(refreshJobDetail, 1500);
}
function stopPolling() {
  if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
}
$("refreshJobs").addEventListener("click", loadJobList);

async function loadJobList() {
  try {
    const jobs = await jfetch("/api/jobs");
    state.jobs = jobs;
    const box = $("jobList");
    box.innerHTML = "";
    jobs.forEach(j => {
      box.insertAdjacentHTML("beforeend",
        jobItemHtml(j, j.job_id === state.activeJobId));
    });
  } catch (e) { console.error(e); }
}

/* 取消当前任务 */
const cancelBtn = document.createElement("button");
cancelBtn.className = "ghost danger";
cancelBtn.textContent = "取消当前任务";
cancelBtn.onclick = async () => {
  if (!state.activeJobId) return;
  try {
    await fetch(`/api/jobs/${state.activeJobId}/cancel`, { method: "POST" });
  } catch (e) { console.error(e); }
};
$("paneLog").querySelector(".actions").appendChild(cancelBtn);

/* ============================================================
 * 结果渲染 (两行紧凑布局)
 * ============================================================ */
async function renderResult(jid) {
  let r;
  try { r = await jfetch(`/api/jobs/${jid}/result.json`); }
  catch (e) { return; }
  const cards = $("summaryCards");
  // 行1: 总飞行时间 (年+天合并) | 总 DSM | C3   (无独立 DSM 限制框)
  // 行2: 左 = 发射时间·出射v∞, 右 = 到达时间·入射v∞
  cards.innerHTML = `
    <div class="card ${r.dsm_ok ? "ok" : "bad"}"><div class="k">总飞行时间</div>
      <div class="v">${fmt(r.tof_yr, 2)} yr (${fmt(r.tof_d, 0)} d)</div></div>
    <div class="card ${r.dsm_ok ? "ok" : "bad"}"><div class="k">总 DSM</div>
      <div class="v">${fmt(r.dsm_total_ms, 0)} m/s</div></div>
    <div class="card"><div class="k">C3</div>
      <div class="v">${fmt(r.c3, 2)} km²/s²</div></div>
    <div class="card wide"><div class="k">发射</div>
      <div class="v">${r.launch_iso ? r.launch_iso.slice(0, 10) : "–"} · v∞ ${fmt(r.vinf_launch_kmps, 3)} km/s</div></div>
    <div class="card wide"><div class="k">到达</div>
      <div class="v">${r.arrival_iso ? r.arrival_iso.slice(0, 10) : "–"} · v∞ ${fmt(r.vinf_arrival_kmps, 3)} km/s</div></div>`;

  const legs = r.legs || [];
  $("legTable").innerHTML = "<h3>每腿</h3>" + tableHtml(
    ["从", "到", "TOF (d)", "DSM (m/s)", "eta"],
    legs.map(l => [l.from, l.to, fmt(l.tof_d, 1), fmt(l.dsm_ms, 0), fmt(l.eta, 3)]));

  // 飞掠信息: 天体 / rp / 低点高度 (无判定列; 颜色保留 ok/bad 语义)
  const fly = r.flybys || [];
  $("flybyTable").innerHTML = "<h3>飞掠信息</h3>" + (fly.length ? tableHtml(
    ["天体", "rp (R)", "低点高度 (km)"],
    fly.map(f => {
      const cls = f.alt_ok ? "ok" : "bad";
      return [`<span class="${cls}">${escapeHtml(f.name)}</span>`,
        fmt(f.rp_R, 3),
        f.alt_km != null ? fmt(f.alt_km, 0) : "–"];
    })) : "<p>无飞掠数据</p>");

  renderPlot(jid);
}

function tableHtml(heads, rows) {
  return `<table><tr>${heads.map(h => `<th>${h}</th>`).join("")}</tr>` +
    rows.map(row => `<tr>${row.map(c => `<td>${c}</td>`).join("")}</tr>`).join("") + "</table>";
}

async function renderPlot(jid) {
  if (!state.plotlyReady || typeof Plotly === "undefined") {
    $("chart").innerHTML = "<p>Plotly 未加载</p>";
    return;
  }
  let p;
  try { p = await jfetch(`/api/jobs/${jid}/plot.json`); }
  catch (e) { $("chart").innerHTML = "<p>plot.json 不可用</p>"; return; }

  const traces = [];
  traces.push({ type: "scatter3d", mode: "markers",
    x: p.sun.x, y: p.sun.y, z: p.sun.z, name: "Sun",
    marker: { size: 10, color: "#ffd75e" } });
  (p.bodies || []).forEach(b => {
    traces.push({ type: "scatter3d", mode: "lines", name: b.tag,
      x: b.orbit.x, y: b.orbit.y, z: b.orbit.z,
      line: { color: b.color, width: 2 }, opacity: 0.6 });
    (b.encounters || []).forEach(en => {
      traces.push({ type: "scatter3d", mode: "markers+text", name: b.tag + " 交会",
        x: [en.x], y: [en.y], z: [en.z],
        text: [b.tag + "\n" + (en.iso || "").slice(0, 10)],
        textfont: { size: 9 }, marker: { size: 7, color: b.color } });
    });
  });
  (p.legs || []).forEach((leg, i) => {
    const bl = leg.ballistic || {};
    if ((bl.x || []).length) traces.push({ type: "scatter3d", mode: "lines",
      name: `leg${i + 1} 弹道`, x: bl.x, y: bl.y, z: bl.z,
      line: { color: "#f9a8d4", width: 3 } });
    const lm = leg.lambert || {};
    if ((lm.x || []).length) traces.push({ type: "scatter3d", mode: "lines",
      name: `leg${i + 1} Lambert`, x: lm.x, y: lm.y, z: lm.z,
      line: { color: "#f78fbe", width: 3, dash: "dot" } });
    const dsm = leg.dsm || {};
    if (dsm.x != null) traces.push({ type: "scatter3d", mode: "markers+text",
      name: `DSM${i + 1}`, x: [dsm.x], y: [dsm.y], z: [dsm.z],
      text: [fmt(dsm.dsm_ms, 0) + " m/s"],
      textfont: { size: 9 }, marker: { size: 5, color: "#b56cff", symbol: "diamond" } });
  });

  Plotly.newPlot("chart", traces, {
    paper_bgcolor: "#0f1420", plot_bgcolor: "#0f1420",
    font: { color: "#d7e0f0", size: 10 },
    scene: { aspectmode: "data",
      camera: { eye: { x: -0.1, y: -1.6, z: 0.35 } },
      xaxis: { title: "AU" }, yaxis: { title: "AU" }, zaxis: { title: "AU" } },
    margin: { l: 0, r: 0, t: 30, b: 0 },
    title: { text: `${p.name || "EVVEJU"} · TOF=${fmt(p.tof_yr, 2)} yr · DSM=${fmt(p.dsm_total_ms, 0)} m/s`,
      font: { size: 12 } },
    showlegend: true,
  }, { responsive: true });
}

/* 下载按钮 */
$("pngDown").addEventListener("click", () => {
  if (state.activeJobId) window.open(`/api/jobs/${state.activeJobId}/trajectory.png`);
});
$("jsonDown").addEventListener("click", () => {
  if (state.activeJobId) window.open(`/api/jobs/${state.activeJobId}/result.json`);
});
$("logDown").addEventListener("click", () => {
  if (state.activeJobId) window.open(`/api/jobs/${state.activeJobId}/log.txt`);
});

/* ============================================================
 * 终止程序 / 关闭确认 / 0.0.0.0 警告 / 系统设置
 * ============================================================ */
$("shutdownBtn").addEventListener("click", async () => {
  if (!confirm("确定终止后端程序?\n- 运行中的任务会被中断\n- 服务将退出, 之后无法再访问本页面\n- 本标签页将尝试自动关闭\n(以后再次运行程序即可恢复)")) return;
  try {
    await fetch("/api/shutdown", { method: "POST" });
  } catch (e) { /* 连接断开 = 后端已退出 */ }
  // 顺便关闭标签页: 浏览器只允许脚本自开的窗口被 window.close(),
  // webbrowser 开的标签可能被忽略, 失败则提示手动关闭。
  try { window.close(); } catch (err) { /* 忽略 */ }
  setTimeout(() => {
    if (!document.hidden) {
      alert("后端已退出。若标签页未自动关闭，请手动关闭。");
    }
  }, 600);
});

// 关闭标签页确认: 有任务在跑时防止误关 (关闭 = 后台继续运行)
window.addEventListener("beforeunload", (e) => {
  const running = (state.jobs || []).some(j => j.status === "running" || j.status === "queued");
  if (running) {
    e.preventDefault();
    e.returnValue = "";
  }
});

/* 系统设置 */
async function loadSysConfig() {
  try {
    const s = await jfetch("/api/sysconfig");
    $("sysLan").checked = (s.host === "0.0.0.0" || s.host === "::");
    $("sysPort").value = s.port;
    $("sysSingle").checked = !!s.single_instance;
    $("sysOpenBrowser").checked = !!s.open_browser;
    const bw = $("sysLanWarnBanner");
    if (bw) bw.classList.toggle("hidden", !$("sysLan").checked);
  } catch (e) { console.error(e); }
}
$("sysOpen").addEventListener("click", async () => {
  await loadSysConfig();
  $("sysModal").classList.remove("hidden");
});
$("sysClose").addEventListener("click", () => $("sysModal").classList.add("hidden"));
$("sysSave").addEventListener("click", async () => {
  const lan = $("sysLan").checked;
  // 开放到局域网 = 高风险的显式选择: 保存前二次确认
  if (lan && !confirm(
      "⚠ 将监听 0.0.0.0 (开放到局域网):\n- 局域网内任何设备都能访问本页面\n- 本工具无任何鉴权\n- 仅限安全内网使用!\n\n确定保存并重启生效?")) return;
  try {
    const r = await fetch("/api/sysconfig", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        host: lan ? "0.0.0.0" : "127.0.0.1",
        port: +$("sysPort").value,
        single_instance: $("sysSingle").checked,
        open_browser: $("sysOpenBrowser").checked,
        show_lan_warning: true,
      }),
    });
    const j = await r.json();
    if (!r.ok) { alert("保存失败: " + (j.error || r.statusText)); return; }
    alert("已保存。需重启程序后生效 (host/port)。");
    $("sysModal").classList.add("hidden");
  } catch (e) { alert("保存失败: " + e.message); }
});

/* ============================================================
 * 初始化
 * ============================================================ */
(async function init() {
  if (typeof Plotly !== "undefined") state.plotlyReady = true;
  else {
    const t = setInterval(() => {
      if (typeof Plotly !== "undefined") { state.plotlyReady = true; clearInterval(t); }
    }, 200);
  }
  // 系统信息: host/port/单实例/0.0.0.0 警告
  try {
    const h = await jfetch("/api/health");
    $("sysInfo").textContent = `${h.host || "127.0.0.1"}:${h.port || 8765}` +
      (h.single_instance ? " · 单实例" : " · 多实例");
    if (h.lan) { const b = $("sysLanWarnBanner"); if (b) b.classList.remove("hidden"); }
  } catch (e) { console.error(e); }
  // 一次性提示: 关标签=后台继续 (可删, 不影响脚本)
  { const ch = $("closeHint"); if (ch) ch.classList.remove("hidden"); }
  await loadPresets();
  await loadCompCfg();
  await loadJobList();
})();