let state = {
  profiles: [], currentProfile: null, currentView: "files",
  currentFile: null, currentSkill: null, currentSkillFile: null,
  fileContents: {}, originalContents: {}, fileMeta: {},
  skills: {}, skillContents: {}, skillOriginals: {},
  wrapMode: true,
  // structured config
  configData: {}, configOriginal: {}, configSchema: [],
  configMode: "form", // "form" or "raw"
  configRawContent: "", configRawOriginal: "",
  // structured env
  envData: [], envOriginal: [],
  envMode: "form", // "form" or "raw"
  envRawContent: "", envRawOriginal: "",
  // 技能中心：源列表缓存 + 当前选中的源（"all"=全部启用源）
  hubSources: [], hubSelectedSource: "all",
};

const FILE_ORDER = ["config.yaml", ".env", "SOUL.md", "MEMORY.md", "USER.md"];
const FILE_LANG = {"config.yaml":"yaml",".env":"ini","SOUL.md":"md","MEMORY.md":"md","USER.md":"md"};
const PROFILE_COLORS = ["#58a6ff","#3fb950","#d29922","#f85149","#a371f7","#79c0ff"];

async function api(url, method="GET", body=null) {
  const opts = {method, headers:{"Content-Type":"application/json"}};
  if (body) opts.body = JSON.stringify(body);
  showLoader(true);
  try {
    const res = await fetch(url, opts);
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); }
    catch(e) {
      // 服务器返回非 JSON（如 Flask 500 HTML 错误页）
      const err = new Error(`服务器返回非 JSON 响应 (HTTP ${res.status})`);
      err.status = res.status;
      err.data = {error: `HTTP ${res.status}: ${text.substring(0, 120)}`};
      throw err;
    }
    if (!res.ok) {
      const err = new Error(data.error || data.warning || `request failed (${res.status})`);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  } finally {
    showLoader(false);
  }
}

// 全局加载条
let _loaderTimer = null;
function showLoader(active) {
  const el = document.getElementById("global-loader"); if (!el) return;
  if (active) {
    // 延迟 100ms 显示，避免快速请求闪烁
    if (_loaderTimer) return;
    _loaderTimer = setTimeout(() => el.classList.add("active"), 100);
  } else {
    if (_loaderTimer) { clearTimeout(_loaderTimer); _loaderTimer = null; }
    el.classList.remove("active");
  }
}

// 按钮加载态封装：禁用按钮、显示 spinner，完成或失败后恢复
async function withLoading(btn, fn) {
  if (!btn || btn.disabled) return;
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.classList.add("loading");
  try { return await fn(); }
  finally { btn.classList.remove("loading"); btn.innerHTML = original; btn.disabled = false; }
}

// 通用 modal 助手：支持 Esc 关闭、Enter 确认、自动聚焦
function showModal({title, bodyHtml, onConfirm, confirmText="确认", cancelText="取消", danger=false, autoFocus=true}) {
  const o = document.createElement("div"); o.className = "modal-overlay";
  const confirmCls = danger ? "btn confirm danger" : "btn confirm";
  o.innerHTML = `<div class="modal"><h3>${title}</h3>${bodyHtml||""}<div class="modal-actions"><button class="btn cancel">${cancelText}</button><button class="btn ${confirmCls.split(" ").slice(1).join(" ")}" data-confirm>${confirmText}</button></div></div>`;
  document.body.appendChild(o);
  const close = () => o.remove();
  o.querySelector(".cancel").onclick = close;
  o.querySelector("[data-confirm]").onclick = async () => {
    const btn = o.querySelector("[data-confirm]");
    btn.classList.add("loading");
    try { await onConfirm(o); close(); }
    catch(e) { toast(e.message||"操作失败","error"); btn.classList.remove("loading"); }
  };
  // 点击遮罩关闭
  o.onclick = e => { if (e.target === o) close(); };
  // Esc 关闭
  const onKey = e => {
    if (e.key === "Escape") { close(); document.removeEventListener("keydown", onKey); }
    else if (e.key === "Enter" && e.target.tagName !== "TEXTAREA") {
      e.preventDefault(); o.querySelector("[data-confirm]").click();
    }
  };
  document.addEventListener("keydown", onKey);
  // 自动聚焦
  if (autoFocus) {
    const focusEl = o.querySelector("input, select, textarea");
    if (focusEl) setTimeout(() => focusEl.focus(), 50);
  }
  return o;
}

// 侧栏折叠
function toggleSidebar() {
  document.getElementById("sidebar").classList.toggle("collapsed");
}
document.getElementById("sidebar-toggle").onclick = toggleSidebar;

// 全局 modal 行为委托：Esc 关闭、点击遮罩关闭、Enter 触发 confirm
(function setupModalDelegation() {
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    const modals = document.querySelectorAll(".modal-overlay");
    if (!modals.length) return;
    // 关闭最顶层的 modal
    const top = modals[modals.length - 1];
    const cancel = top.querySelector(".cancel");
    if (cancel) cancel.click();
    else top.remove();
  });
  document.addEventListener("click", e => {
    if (e.target.classList?.contains("modal-overlay")) {
      const cancel = e.target.querySelector(".cancel");
      if (cancel) cancel.click();
      else e.target.remove();
    }
  }, true);
})();

// ── 主题切换 ─────────────────────────────────
function applyTheme(theme){
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem('hermes-theme', theme); } catch(_) {}
  const btn = document.getElementById('theme-toggle');
  if(btn){
    btn.textContent = theme === 'light' ? '🌙' : '☀️';
    btn.title = theme === 'light' ? '切换到深色主题' : '切换到浅色主题';
  }
}
function toggleTheme(){
  const cur = document.documentElement.getAttribute('data-theme') || 'dark';
  const next = cur === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  // 持久化到后端文件（.hermes_theme），跨重启保活（localStorage 在 webview 里不稳定）
  api('/api/theme', 'PUT', {theme: next}).catch(()=>{});
}

async function init() {
  // 主题：以后端持久化为准（head 内联脚本已用 localStorage 瞬间防闪）
  try { const d = await api('/api/theme'); applyTheme(d.theme || 'dark'); }
  catch(_) { applyTheme(document.documentElement.getAttribute('data-theme') || 'dark'); }
  await loadInfo();
  await loadProfiles();
  await loadConfigSchema();
  loadBackups();  // 异步加载备份列表，不阻塞启动
  startPolling();
  // 全局键盘快捷键
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      // 根据当前上下文触发对应保存
      if (state.currentView === "skills" && state.currentSkill) {
        saveSkillFile();
      } else if (state.configMode === "raw" && state.currentFile === "config.yaml") {
        saveConfigRaw();
      } else if (state.envMode === "raw" && state.currentFile === ".env") {
        saveEnvRaw();
      } else {
        saveAllCurrent();
      }
    }
  });
}

async function loadInfo() {
  try {
    const info = await api("/api/info");
    const el = document.getElementById("hermes-home");
    el.textContent = info.hermes_home;
    if (!info.detected) { el.classList.add("warn"); el.title = "HERMES_HOME 未自动检测到，点击修改"; }
    else el.title = `来源: ${info.source}\n点击修改`;
    el.onclick = () => showSetHermesHome(info.hermes_home);
  } catch(e) { console.error(e); }
}

async function loadConfigSchema() {
  try {
    const d = await api(`/api/profile/default/config-schema`);
    state.configSchema = d.schema;
  } catch(e) { console.error("schema load failed", e); }
}

function showSetHermesHome(current) {
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal"><h3>设置 HERMES_HOME</h3><input id="hh-input" value="${current}"><p style="font-size:12px;color:var(--fg2);margin-top:8px">修改后需要重启服务器生效</p><div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm">保存</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    try { await api("/api/hermes-home","PUT",{path:o.querySelector("#hh-input").value.trim()}); o.remove(); toast("已保存，请重启服务器生效~","info"); }
    catch(e) { toast("设置失败: "+e.message,"error"); }
  };
}

async function loadProfiles() {
  const data = await api("/api/profiles");
  state.profiles = data.profiles;
  for (const p of state.profiles) state.fileMeta[p.name] = p.files;
  renderSidebar();
  if (state.currentProfile) selectProfile(state.currentProfile, true);
  else if (state.profiles.length > 0) selectProfile(state.profiles[0].name);
}

function renderSidebar() {
  const el = document.getElementById("profile-list"); el.innerHTML = "";
  state.profiles.forEach((p, i) => {
    const color = PROFILE_COLORS[i % PROFILE_COLORS.length];
    const item = document.createElement("div");
    item.className = `profile-item ${p.name===state.currentProfile?"active":""}`;
    item.innerHTML = `<div class="icon" style="background:${color}22;color:${color}">${p.name[0].toUpperCase()}</div><div class="name">${p.name}<div class="meta">技能 ${p.skill_count} · 工具集 ${p.toolset_enabled}/${p.toolset_total} · MCP ${p.mcp_count}</div></div>${p.name==="default"?'<span class="tag root">root</span>':''}`;
    item.onclick = () => selectProfile(p.name);
    item.oncontextmenu = (e) => { e.preventDefault(); showProfileContextMenu(p.name, e.clientX, e.clientY); };
    el.appendChild(item);
  });
}

function showProfileContextMenu(name, x, y) {
  // 移除已有菜单
  document.querySelectorAll(".ctx-menu").forEach(m => m.remove());
  const menu = document.createElement("div");
  menu.className = "ctx-menu";
  menu.style.left = x + "px";
  menu.style.top = y + "px";
  menu.innerHTML = `
    <div class="ctx-menu-item" data-action="open-dir">📂 打开目录</div>
    <div class="ctx-menu-sep"></div>
    <div class="ctx-menu-item" data-action="select">切换到此 Profile</div>`;
  document.body.appendChild(menu);
  // 边界检测
  const rect = menu.getBoundingClientRect();
  if (rect.right > window.innerWidth) menu.style.left = (window.innerWidth - rect.width - 4) + "px";
  if (rect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - rect.height - 4) + "px";
  menu.querySelectorAll(".ctx-menu-item").forEach(item => {
    item.onclick = async () => {
      menu.remove();
      const action = item.dataset.action;
      if (action === "open-dir") {
        try {
          await api(`/api/profile/${name}/open-dir`, "POST");
          toast(`已打开 ${name} 目录`, "success");
        } catch(e) { toast("打开失败: " + e.message, "error"); }
      } else if (action === "select") {
        selectProfile(name);
      }
    };
  });
  // 点击其他地方关闭
  setTimeout(() => {
    const close = (e) => { menu.remove(); document.removeEventListener("click", close); document.removeEventListener("contextmenu", close); };
    document.addEventListener("click", close);
    document.addEventListener("contextmenu", close);
  }, 0);
}

async function selectProfile(name, skipRender) {
  if (name !== state.currentProfile && state.currentProfile) {
    const doSwitch = async () => {
      state.currentProfile = name; state.currentView = "files"; state.currentFile = "config.yaml";
      state.configMode = "form"; state.envMode = "form";
      delete state.fileContents[name]; delete state.originalContents[name];
      delete state.configData[name]; delete state.configOriginal[name];
      delete state.envData[name]; delete state.envOriginal[name];
      renderSidebar();
      await renderEditor();
    };
    if (guardSwitch(doSwitch, `切换到 Profile "${name}" 前，是否保存当前修改？`)) return;
    await doSwitch();
    return;
  }
  state.currentProfile = name; state.currentView = "files"; state.currentFile = "config.yaml";
  state.configMode = "form"; state.envMode = "form";
  // reset caches
  delete state.fileContents[name]; delete state.originalContents[name];
  delete state.configData[name]; delete state.configOriginal[name];
  delete state.envData[name]; delete state.envOriginal[name];
  renderSidebar();
  await renderEditor();
}

// ── 主面板 ──
async function renderEditor() {
  const area = document.getElementById("editor-area");
  if (!state.currentProfile) { area.innerHTML = `<div class="empty-state"><div class="big">(◠‿◠)</div><div>选择一个 Profile 开始编辑~</div></div>`; return; }
  const profile = state.profiles.find(p => p.name === state.currentProfile);
  if (!profile) return;

  // load md files cache (SOUL/MEMORY/USER)
  if (!state.fileContents[state.currentProfile]) {
    state.fileContents[state.currentProfile] = {};
    state.originalContents[state.currentProfile] = {};
    await Promise.all(["SOUL.md","MEMORY.md","USER.md"].map(async fk => {
      try { const d = await api(`/api/profile/${state.currentProfile}/${fk}`); state.fileContents[state.currentProfile][fk]=d.content; state.originalContents[state.currentProfile][fk]=d.content; }
      catch(e) { state.fileContents[state.currentProfile][fk]=""; state.originalContents[state.currentProfile][fk]=""; }
    }));
  }

  area.innerHTML = `
    <div class="view-tabs">
      <div class="view-tab ${state.currentView==='files'?'active':''}" onclick="switchView('files')">配置文件</div>
      <div class="view-tab ${state.currentView==='skills'?'active':''}" onclick="switchView('skills')">Skills ${profile.skill_count?`<span class="count">${profile.skill_count}</span>`:''}</div>
    </div>
    <div id="view-content" style="flex:1;display:flex;flex-direction:column;overflow:hidden"></div>
  `;
  if (state.currentView === "files") renderFilesView();
  else renderSkillsView();
}

// ── 未保存守卫 ──
function hasUnsavedChanges() {
  if (!state.currentProfile) return false;
  const _dbg = (tag, val) => { if (val) console.warn("[hasUnsavedChanges] triggered:", tag); return val; };
  // config.yaml
  if (state.currentFile === "config.yaml" || state.currentView !== "files") {
    if (_dbg("config.raw", state.configMode === "raw" && state.configRawContent !== state.configRawOriginal)) return true;
    if (_dbg("config.form", isConfigModified())) return true;
  }
  // .env
  if (state.currentFile === ".env" || state.currentView !== "files") {
    if (_dbg("env.raw", state.envMode === "raw" && state.envRawContent !== state.envRawOriginal)) return true;
    if (_dbg("env.form", isEnvModified())) return true;
  }
  // md files
  for (const fk of ["SOUL.md","MEMORY.md","USER.md"]) {
    if (_dbg("md:"+fk, (state.fileContents[state.currentProfile]?.[fk]??"") !== (state.originalContents[state.currentProfile]?.[fk]??""))) return true;
  }
  // skills
  if (state.skillContents[state.currentProfile] && state.skillOriginals[state.currentProfile]) {
    for (const sn of Object.keys(state.skillContents[state.currentProfile])) {
      const c = state.skillContents[state.currentProfile][sn];
      const o = state.skillOriginals[state.currentProfile][sn];
      for (const fp of Object.keys(c)) {
        if (_dbg("skill:"+sn+"/"+fp, c[fp] !== o[fp])) return true;
      }
    }
  }
  return false;
}

function guardSwitch(action, label) {
  if (hasUnsavedChanges()) {
    const o = document.createElement("div"); o.className = "modal-overlay";
    o.innerHTML = `<div class="modal"><h3>未保存的修改</h3><p style="font-size:13px;color:var(--fg2);margin-bottom:8px">${label||"当前有未保存的修改，是否保存后再切换？"}</p><div class="modal-actions"><button class="btn cancel">不保存</button><button class="btn confirm">保存并切换</button></div></div>`;
    document.body.appendChild(o);
    o.querySelector(".cancel").onclick = () => { o.remove(); action(); };
    o.querySelector(".confirm").onclick = async () => {
      o.remove();
      await saveAllCurrent();
      action();
    };
    return true; // blocked
  }
  return false; // not blocked
}

async function saveAllCurrent() {
  // save everything that's modified
  if (isConfigModified()) await saveConfig();
  if (state.configMode === "raw" && state.configRawContent !== state.configRawOriginal) await saveConfigRaw();
  if (isEnvModified()) await saveEnv();
  if (state.envMode === "raw" && state.envRawContent !== state.envRawOriginal) await saveEnvRaw();
  for (const fk of ["SOUL.md","MEMORY.md","USER.md"]) {
    if ((state.fileContents[state.currentProfile]?.[fk]??"") !== (state.originalContents[state.currentProfile]?.[fk]??"")) {
      state.currentFile = fk; await saveMdFile();
    }
  }
}

function switchView(view) {
  const doSwitch = () => {
    state.currentView = view;
    document.querySelectorAll(".view-tab").forEach((t,i) => t.classList.toggle("active", (i===0&&view==="files")||(i===1&&view==="skills")));
    const vc = document.getElementById("view-content"); if (!vc) return;
    vc.innerHTML = "";
    if (view === "files") renderFilesView(); else renderSkillsView();
  };
  if (guardSwitch(doSwitch, "切换视图前是否保存？")) return;
  doSwitch();
}

// ── 配置文件视图 ──
function renderFilesView() {
  const vc = document.getElementById("view-content");
  vc.innerHTML = `
    <div class="file-tabs" id="file-tabs"></div>
    <div id="file-content" style="flex:1;display:flex;flex-direction:column;overflow:hidden"></div>
  `;
  renderFileTabs();
  loadFileContent(state.currentFile);
}

function renderFileTabs() {
  const el = document.getElementById("file-tabs"); if (!el) return; el.innerHTML = "";
  FILE_ORDER.forEach(fk => {
    let modified = false;
    if (fk === "config.yaml") modified = isConfigModified();
    else if (fk === ".env") modified = isEnvModified();
    else modified = (state.fileContents[state.currentProfile]?.[fk] ?? "") !== (state.originalContents[state.currentProfile]?.[fk] ?? "");
    const tab = document.createElement("div");
    tab.className = `file-tab ${fk===state.currentFile?"active":""} ${modified?"modified":""}`;
    tab.innerHTML = `<span class="dot"></span><span>${fk}</span><span class="lang-tag">${FILE_LANG[fk]}</span>`;
    tab.onclick = () => {
      if (fk === state.currentFile) return;
      const doSwitch = () => { state.currentFile = fk; renderFileTabs(); loadFileContent(fk); };
      if (guardSwitch(doSwitch, `切换到 ${fk} 前，是否保存当前修改？`)) return;
      doSwitch();
    };
    el.appendChild(tab);
  });
}

function isConfigModified() {
  if (state.configMode === "raw") return state.configRawContent !== state.configRawOriginal;
  const d = state.configData[state.currentProfile]; const o = state.configOriginal[state.currentProfile];
  if (!d || !o) return false;
  return JSON.stringify(d.values) !== JSON.stringify(o.values)
    || JSON.stringify(d.custom_providers) !== JSON.stringify(o.custom_providers)
    || JSON.stringify(d.mcp_servers || []) !== JSON.stringify(o.mcp_servers || []);
}

function isEnvModified() {
  if (state.envMode === "raw") return state.envRawContent !== state.envRawOriginal;
  return JSON.stringify(state.envData[state.currentProfile] || []) !== JSON.stringify(state.envOriginal[state.currentProfile] || []);
}

async function loadFileContent(fk) {
  const fc = document.getElementById("file-content"); if (!fc) return;
  if (fk === "config.yaml") { await loadConfigView(fc); return; }
  if (fk === ".env") { await loadEnvView(fc); return; }
  // md files: raw textarea
  fc.innerHTML = `
    <div class="raw-toolbar">
      <span class="path" id="file-path"></span>
      <button class="btn save" onclick="saveMdFile()" id="btn-save-md" disabled>保存</button>
    </div>
    <div class="raw-wrap">
      <div class="line-numbers" id="line-numbers"></div>
      <div class="textarea-wrap"><textarea id="editor" spellcheck="false"></textarea></div>
    </div>
  `;
  const content = state.fileContents[state.currentProfile]?.[fk] ?? "";
  const ta = document.getElementById("editor");
  ta.value = content; ta.oninput = onMdInput;
  updateLineNumbers(content);
  updateMdSaveButton();
  const meta = state.fileMeta[state.currentProfile]?.[fk];
  const sub = meta?.sub ? `${meta.sub}/` : "";
  document.getElementById("file-path").textContent = `${state.currentProfile}/${sub}${fk}`;
}

function onMdInput() {
  const ta = document.getElementById("editor"); if (!ta) return;
  state.fileContents[state.currentProfile][state.currentFile] = ta.value;
  updateLineNumbers(ta.value); updateMdSaveButton(); renderFileTabs();
}
function updateMdSaveButton() {
  const fk = state.currentFile;
  const c = state.fileContents[state.currentProfile]?.[fk] ?? "";
  const o = state.originalContents[state.currentProfile]?.[fk] ?? "";
  const btn = document.getElementById("btn-save-md"); if (btn) btn.disabled = c === o;
}
async function saveMdFile() {
  const fk = state.currentFile;
  const content = state.fileContents[state.currentProfile][fk];
  const btn = document.getElementById("btn-save-md");
  await withLoading(btn, async () => {
    await api(`/api/profile/${state.currentProfile}/${fk}`, "PUT", {content});
    state.originalContents[state.currentProfile][fk] = content;
    updateMdSaveButton(); renderFileTabs();
    toast("保存成功~","success");
  }).catch(e => toast("保存失败: "+e.message,"error"));
}

// ── config.yaml 结构化视图 ──
async function loadConfigView(container) {
  // load structured config
  if (!state.configData[state.currentProfile]) {
    try {
      const d = await api(`/api/profile/${state.currentProfile}/config`);
      state.configData[state.currentProfile] = {values: d.values, custom_providers: d.custom_providers, mcp_servers: d.mcp_servers || []};
      state.configOriginal[state.currentProfile] = JSON.parse(JSON.stringify({values: d.values, custom_providers: d.custom_providers, mcp_servers: d.mcp_servers || []}));
    } catch(e) { toast("加载 config 失败: "+e.message,"error"); return; }
  }
  if (state.configMode === "form") renderConfigForm(container);
  else renderConfigRaw(container);
}

function renderConfigForm(container) {
  const cd = state.configData[state.currentProfile];
  const orig = state.configOriginal[state.currentProfile];
  const schema = state.configSchema;
  let formHtml = "";
  // 表单模式只保留常用配置（模型设置）；其他配置（Agent/终端/显示/语音/记忆/审批/会话）
  // 请切换到"原始"模式编辑。saveConfig 仍提交完整 values，未显示的字段不会丢失。
  schema.forEach((g, gi) => {
    if (g.group !== "模型设置") return;
    // 应用记忆的折叠状态
    let collapsed = false;
    try { collapsed = localStorage.getItem(`pm:collapse:${gi}`) === "1"; } catch(_) {}
    formHtml += `<div class="config-group${collapsed?" hidden":""}" id="cg-${gi}">
      <div class="config-group-header${collapsed?" collapsed":""}" onclick="toggleGroup(${gi})">
        <span class="gicon">${g.icon}</span><span>${g.group}</span><span class="arrow">▼</span>
      </div>
      <div class="config-group-body">`;
    g.fields.forEach(f => {
      const val = cd.values[f.key];
      const origVal = orig.values[f.key];
      const modified = JSON.stringify(val) !== JSON.stringify(origVal);
      formHtml += renderField(f, val, modified);
    });
    formHtml += `</div></div>`;
  });

  // custom providers section
  let cpHtml = `<div class="config-group">
    <div class="config-group-header" onclick="this.parentElement.classList.toggle('hidden')">
      <span class="gicon" style="background:var(--purple)">P</span><span>模型源 (custom_providers)</span><span class="arrow">▼</span>
    </div>
    <div class="config-group-body">
      <div class="cp-section">`;
  cd.custom_providers.forEach((cp, i) => {
    const origCp = orig.custom_providers[i];
    const modified = origCp ? JSON.stringify(cp) !== JSON.stringify(origCp) : true;
    cpHtml += renderCpCard(cp, i, modified);
  });
  cpHtml += `<div style="display:flex;gap:8px"><button class="cp-add-btn" onclick="addCp()" style="flex:1">+ 新增模型源</button><button class="cp-add-btn" onclick="copyProviderFrom()" style="flex:1">从其他 Profile 复制</button></div>
      </div></div></div>`;

  // MCP servers section（与 custom_providers 同级，可视化增删改 mcp_servers）
  const mcpList = cd.mcp_servers || [];
  let mcpHtml = `<div class="config-group">
    <div class="config-group-header" onclick="this.parentElement.classList.toggle('hidden')">
      <span class="gicon" style="background:var(--purple-dim);color:var(--purple)">M</span><span>MCP Servers (mcp_servers) · ${mcpList.length}</span><span class="arrow">▼</span>
    </div>
    <div class="config-group-body">
      <div class="cp-section">`;
  mcpList.forEach((m, i) => {
    const origM = (orig.mcp_servers || [])[i];
    const modified = origM ? JSON.stringify(m) !== JSON.stringify(origM) : true;
    mcpHtml += renderMcpCard(m, i, modified);
  });
  mcpHtml += `<div style="display:flex;gap:8px"><button class="cp-add-btn" onclick="addMcpServer()" style="flex:1">+ 新增 MCP Server</button><button class="cp-add-btn" onclick="copyMcpFrom()" style="flex:1">从其他 Profile 复制</button></div>
      </div></div></div>`;

  container.innerHTML = `
    <div class="raw-toolbar">
      <span class="path">config.yaml (结构化编辑)</span>
      <div class="mode-switch"><button class="active" onclick="setConfigMode('form')">表单</button><button onclick="setConfigMode('raw')">原始</button></div>
    </div>
    <div class="config-form" id="config-form">${formHtml}${cpHtml}${mcpHtml}</div>
    <div class="save-bar">
      <span class="info" id="config-info">${isConfigModified()?"有未保存的修改":"无修改"}</span>
      <button class="btn save" onclick="saveConfig()" id="btn-save-config" ${isConfigModified()?"":"disabled"}>保存 config.yaml</button>
    </div>
  `;
  // attach change listeners
  attachConfigListeners();
}
function renderMcpCard(m, i, modified) {
  const modCls = modified ? " modified" : "";
  const body = { command: m.command || "", args: m.args || [], enabled: m.enabled !== false };
  const jsonStr = JSON.stringify(body, null, 2);
  return `<div class="cp-card${modCls}" data-mcp-i="${i}">
    <div class="cp-card-header">
      <input class="cp-name-input" value="${(m.name||"").replace(/"/g,"&quot;")}" placeholder="server 名称（如 blindkey）" oninput="updateMcpField(${i},'name',this.value)" style="color:var(--purple)">
      <button class="cp-btn del" onclick="removeMcpServer(${i})" title="删除">✕</button>
    </div>
    <div class="mcp-json-wrap">
      <textarea class="mcp-json-edit" oninput="updateMcpJson(${i},this.value)" spellcheck="false" rows="8">${jsonStr.replace(/</g,"&lt;")}</textarea>
      <div class="hint">JSON 格式：command(字符串) / args(数组) / enabled(布尔)</div>
    </div>
  </div>`;
}
function addMcpServer() {
  const cd = state.configData[state.currentProfile];
  if (!cd.mcp_servers) cd.mcp_servers = [];
  cd.mcp_servers.push({name: "", command: "", args: [], enabled: true});
  renderConfigForm(document.getElementById("file-content"));
  updateConfigSaveState();
  const cards = document.querySelectorAll("[data-mcp-i]");
  const last = cards[cards.length - 1];
  if (last) { last.scrollIntoView({behavior:"smooth", block:"center"}); last.querySelector(".cp-name-input")?.focus(); }
}
function removeMcpServer(i) {
  const cd = state.configData[state.currentProfile];
  if (!cd.mcp_servers) return;
  cd.mcp_servers.splice(i, 1);
  renderConfigForm(document.getElementById("file-content"));
  updateConfigSaveState();
}
function updateMcpField(i, key, val) {
  const cd = state.configData[state.currentProfile];
  if (!cd.mcp_servers || !cd.mcp_servers[i]) return;
  cd.mcp_servers[i][key] = val;
  // name 变化时同步卡片标题颜色无需重渲染；enabled 同步 span 文案
  if (key === "enabled") {
    const card = document.querySelector(`[data-mcp-i="${i}"]`);
    if (card) { const span = card.querySelector('.bool-row span'); if (span) span.textContent = val ? "启用" : "禁用"; }
  }
  updateConfigSaveState();
}
function updateMcpJson(i, val) {
  const cd = state.configData[state.currentProfile];
  if (!cd.mcp_servers || !cd.mcp_servers[i]) return;
  const ta = document.querySelector(`[data-mcp-i="${i}"] .mcp-json-edit`);
  try {
    const parsed = JSON.parse(val);
    const name = cd.mcp_servers[i].name; // name 是 server key，保留不动
    cd.mcp_servers[i] = Object.assign({ name: name }, parsed);
    updateConfigSaveState();
    if (ta) ta.classList.remove("json-error");
  } catch(e) {
    // JSON 无效：标记错误，但不崩溃，保留旧值等待用户修正
    if (ta) ta.classList.add("json-error");
  }
}

function renderField(f, val, modified) {
  const v = val === null || val === undefined ? "" : val;
  const modCls = modified ? " modified" : "";
  if (f.type === "bool") {
    return `<div class="config-field${modCls}" data-field-key="${f.key}">
      <label>${f.label}</label>
      <div class="bool-row"><input type="checkbox" data-key="${f.key}" data-type="bool" ${v?"checked":""}><span>${v?"启用":"禁用"}</span></div>
      ${f.help?`<span class="help">${f.help}</span>`:""}
    </div>`;
  }
  if (f.type === "provider_select") {
    // 从 custom_providers 构建选项；始终带"(未选择)"空选项，避免新 profile 新增 provider 后被自动选为默认
    const cps = state.configData[state.currentProfile]?.custom_providers || [];
    const emptySel = (v===""||v===null||v===undefined) ? "selected" : "";
    const opts = `<option value="" ${emptySel}>(未选择)</option>` + cps.map(cp => {
      const pval = cp.name ? `custom:${cp.name}` : "";
      return `<option value="${pval}" ${String(v)===String(pval)?"selected":""}>${cp.name||"(unnamed)"}</option>`;
    }).join("");
    return `<div class="config-field${modCls}" data-field-key="${f.key}">
      <label>${f.label}</label>
      <select data-key="${f.key}" data-type="provider_select">${opts}</select>
      ${f.help?`<span class="help">${f.help}</span>`:""}
    </div>`;
  }
  if (f.type === "model_select") {
    // 根据 model.provider 的值找到对应 provider 的模型列表
    const cd = state.configData[state.currentProfile];
    const providerVal = cd?.values?.["model.provider"] || "";
    let models = [];
    if (providerVal.startsWith("custom:")) {
      const cpName = providerVal.slice(7);
      const cp = (cd?.custom_providers||[]).find(c => c.name === cpName);
      if (cp && Array.isArray(cp.models)) models = cp.models;
    }
    const opts = models.length > 0
      ? models.map(m => `<option value="${m}" ${String(v)===String(m)?"selected":""}>${m}</option>`).join("")
      : `<option value="${v}" selected>${v||"(请先选择 provider 或添加模型)"}</option>`;
    return `<div class="config-field${modCls}" data-field-key="${f.key}">
      <label>${f.label}</label>
      <select data-key="${f.key}" data-type="model_select">${opts}</select>
      ${f.help?`<span class="help">${f.help}</span>`:""}
    </div>`;
  }
  if (f.type === "select") {
    const opts = (f.options||[]).map(o => `<option value="${o}" ${String(v)===String(o)?"selected":""}>${o||"(空)"}</option>`).join("");
    return `<div class="config-field${modCls}" data-field-key="${f.key}">
      <label>${f.label}</label>
      <select data-key="${f.key}" data-type="select">${opts}</select>
      ${f.help?`<span class="help">${f.help}</span>`:""}
    </div>`;
  }
  if (f.type === "number") {
    return `<div class="config-field${modCls}" data-field-key="${f.key}">
      <label>${f.label}</label>
      <input type="number" value="${v}" data-key="${f.key}" data-type="number">
      ${f.help?`<span class="help">${f.help}</span>`:""}
    </div>`;
  }
  return `<div class="config-field${modCls}" data-field-key="${f.key}">
    <label>${f.label}</label>
    <input type="text" value="${v}" data-key="${f.key}" data-type="text" placeholder="${f.help||""}">
  </div>`;
}

function renderCpCard(cp, i, modified) {
  const modelsStr = Array.isArray(cp.models) ? cp.models.join("\n") : "";
  // textarea 默认高度按模型行数自适应，无需手动拉大即可看全模型
  const modelLines = modelsStr ? modelsStr.split("\n").filter(s=>s.trim()).length : 0;
  const rows = Math.min(24, Math.max(3, modelLines || 3));
  const modCls = modified ? " modified" : "";
  return `<div class="cp-card${modCls}" data-cp-index="${i}">
    <div class="cp-card-header">
      <input class="cp-name-input" value="${cp.name||""}" data-cp-field="name" data-cp-index="${i}" placeholder="provider 名称">
      <button class="cp-discover-btn" onclick="discoverModels(${i})" id="cp-discover-${i}" title="请求 API 获取模型列表">探测模型</button>
      <button class="cp-btn del" onclick="delCp(${i})">删除</button>
    </div>
    <div class="cp-card-body">
      <div class="cp-field"><label>Base URL</label><input type="text" value="${cp.base_url||""}" data-cp-field="base_url" data-cp-index="${i}" placeholder="https://..."></div>
      <div class="cp-field"><label>API Key</label><div class="cp-key-row"><input type="password" value="${cp.api_key||""}" data-cp-field="api_key" data-cp-index="${i}" placeholder="sk-..." id="cp-key-${i}"><button type="button" class="cp-eye-btn" onclick="toggleKeyVisible(${i})" title="显示/隐藏 API Key"><span id="cp-eye-icon-${i}">[*]</span></button></div></div>
      <div class="cp-field"><div class="bool-row"><input type="checkbox" data-cp-field="discover_models" data-cp-index="${i}" ${cp.discover_models?"checked":""}><span>自动发现模型 (Hermes 启动时从 API 拉取)</span></div></div>
      <div class="cp-models"><label>模型列表${modelLines?` (${modelLines})`:''}</label><textarea data-cp-field="models" data-cp-index="${i}" rows="${rows}" placeholder="model-name-1&#10;model-name-2">${modelsStr}</textarea><div class="hint">每行一个模型名；点上方"探测模型"可从 API 自动获取</div></div>
    </div>
  </div>`;
}

function attachConfigListeners() {
  const form = document.getElementById("config-form"); if (!form) return;
  form.querySelectorAll("[data-key]").forEach(el => {
    const eventType = el.type === "checkbox" ? "change" : "input";
    el.addEventListener(eventType, () => {
      const key = el.dataset.key; const type = el.dataset.type;
      let v;
      if (type === "bool") v = el.checked;
      else if (type === "number") v = el.value === "" ? null : Number(el.value);
      else v = el.value;
      state.configData[state.currentProfile].values[key] = v;
      updateFieldHighlight(key);
      if (type === "bool") {
        const span = el.parentElement.querySelector("span");
        if (span) span.textContent = v ? "启用" : "禁用";
      }
      // provider 变化时刷新 model_select 下拉
      if (type === "provider_select") {
        // 重新渲染 model_select 字段
        const modelField = document.querySelector('[data-field-key="model.default"]');
        if (modelField) {
          const curModel = state.configData[state.currentProfile].values["model.default"];
          const newModelHtml = renderModelSelect(curModel);
          modelField.outerHTML = newModelHtml;
          // 重新绑定事件
          const newEl = document.querySelector('[data-key="model.default"]');
          if (newEl) {
            newEl.addEventListener("change", () => {
              state.configData[state.currentProfile].values["model.default"] = newEl.value;
              updateFieldHighlight("model.default");
              updateConfigSaveState();
            });
          }
        }
      }
      updateConfigSaveState();
    });
  });
  form.querySelectorAll("[data-cp-field]").forEach(el => {
    const eventType = el.type === "checkbox" ? "change" : "input";
    el.addEventListener(eventType, () => {
      const idx = parseInt(el.dataset.cpIndex); const field = el.dataset.cpField;
      const cp = state.configData[state.currentProfile].custom_providers[idx]; if (!cp) return;
      if (field === "discover_models") cp[field] = el.checked;
      else if (field === "models") cp[field] = el.value.split("\n").map(s=>s.trim()).filter(s=>s);
      else cp[field] = el.value;
      updateCpHighlight(idx);
      updateConfigSaveState();
    });
  });
}

function updateFieldHighlight(key) {
  const field = document.querySelector(`[data-field-key="${key}"]`);
  if (!field) return;
  const cur = state.configData[state.currentProfile].values[key];
  const orig = state.configOriginal[state.currentProfile].values[key];
  const modified = JSON.stringify(cur) !== JSON.stringify(orig);
  field.classList.toggle("modified", modified);
}

function updateCpHighlight(idx) {
  const card = document.querySelector(`[data-cp-index="${idx}"]`);
  if (!card) return;
  const cp = state.configData[state.currentProfile].custom_providers[idx];
  const origCp = state.configOriginal[state.currentProfile].custom_providers[idx];
  const modified = origCp ? JSON.stringify(cp) !== JSON.stringify(origCp) : true;
  card.classList.toggle("modified", modified);
}

function toggleGroup(gi) {
  const el = document.getElementById(`cg-${gi}`);
  el.classList.toggle("hidden");
  // 记忆折叠状态到 localStorage
  try {
    const key = `pm:collapse:${gi}`;
    if (el.classList.contains("hidden")) localStorage.setItem(key, "1");
    else localStorage.removeItem(key);
  } catch(_) {}
  // 同步箭头方向
  const header = el.querySelector(".config-group-header");
  if (header) header.classList.toggle("collapsed", el.classList.contains("hidden"));
}

function renderModelSelect(val) {
  const cd = state.configData[state.currentProfile];
  const providerVal = cd?.values?.["model.provider"] || "";
  let models = [];
  if (providerVal.startsWith("custom:")) {
    const cpName = providerVal.slice(7);
    const cp = (cd?.custom_providers||[]).find(c => c.name === cpName);
    if (cp && Array.isArray(cp.models)) models = cp.models;
  }
  const v = val === null || val === undefined ? "" : val;
  const origVal = state.configOriginal[state.currentProfile]?.values?.["model.default"];
  const modified = JSON.stringify(v) !== JSON.stringify(origVal);
  const modCls = modified ? " modified" : "";
  const opts = models.length > 0
    ? models.map(m => `<option value="${m}" ${String(v)===String(m)?"selected":""}>${m}</option>`).join("")
    : `<option value="${v}" selected>${v||"(请先选择 provider 或添加模型)"}</option>`;
  return `<div class="config-field${modCls}" data-field-key="model.default">
    <label>默认模型</label>
    <select data-key="model.default" data-type="model_select">${opts}</select>
    <span class="help">根据 provider 选择模型</span>
  </div>`;
}

function addCp() {
  state.configData[state.currentProfile].custom_providers.push({
    name: "new-provider", base_url: "", api_key: "", discover_models: false, models: [], models_format: "list"
  });
  renderConfigForm(document.getElementById("file-content"));
  // 滚动到底部并聚焦新增卡片的 name 输入框
  const cards = document.querySelectorAll(".cp-card");
  const lastCard = cards[cards.length - 1];
  if (lastCard) {
    lastCard.scrollIntoView({ behavior: "smooth", block: "center" });
    const nameInput = lastCard.querySelector(".cp-name-input");
    if (nameInput) { nameInput.focus(); nameInput.select(); }
  }
}
function delCp(i) {
  state.configData[state.currentProfile].custom_providers.splice(i, 1);
  renderConfigForm(document.getElementById("file-content"));
}
async function copyProviderFrom() {
  if (!state.currentProfile) return;
  const others = state.profiles.filter(p => p.name !== state.currentProfile);
  if (!others.length) { toast("没有其他 profile 可复制","info"); return; }
  const m = document.createElement("div"); m.className = "modal-overlay";
  m.innerHTML = `<div class="modal"><h3>从其他 Profile 复制模型源</h3>
    <div style="margin-bottom:10px"><label style="font-size:11px;color:var(--fg2)">源 Profile</label>
    <select id="cp-src" style="width:100%;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg)">${others.map(p=>`<option value="${p.name}">${p.name}</option>`).join("")}</select></div>
    <div id="cp-src-list" style="max-height:240px;overflow:auto;border:1px solid var(--border);border-radius:4px;padding:6px"></div>
    <div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm">复制选中</button></div></div>`;
  document.body.appendChild(m);
  const listEl = m.querySelector("#cp-src-list");
  let srcCps = [];
  const loadSrc = async () => {
    const src = m.querySelector("#cp-src").value;
    listEl.innerHTML = '<div style="color:var(--fg3);padding:8px">加载中...</div>';
    try {
      const d = await api(`/api/profile/${src}/config`);
      srcCps = d.custom_providers || [];
      if (!srcCps.length) { listEl.innerHTML = '<div style="color:var(--fg3);padding:8px">该 Profile 无模型源</div>'; return; }
      listEl.innerHTML = srcCps.map((cp,i)=>`<label style="display:flex;align-items:center;gap:6px;padding:4px;cursor:pointer"><input type="checkbox" value="${i}" style="accent-color:var(--accent)"><span><b>${cp.name||"(unnamed)"}</b> <span style="color:var(--fg3);font-size:11px">${(cp.models||[]).length} 模型</span></span></label>`).join("");
    } catch(e) { listEl.innerHTML = `<div style="color:var(--red);padding:8px">加载失败: ${e.message}</div>`; }
  };
  m.querySelector("#cp-src").onchange = loadSrc;
  m.querySelector(".cancel").onclick = () => m.remove();
  m.querySelector(".confirm").onclick = () => {
    const checked = [...listEl.querySelectorAll("input[type=checkbox]:checked")].map(c=>parseInt(c.value));
    if (!checked.length) { toast("请至少选择一个模型源","error"); return; }
    const cd = state.configData[state.currentProfile];
    checked.forEach(i => srcCps[i] && cd.custom_providers.push(JSON.parse(JSON.stringify(srcCps[i]))));
    m.remove();
    renderConfigForm(document.getElementById("file-content"));
    updateConfigSaveState();
    toast(`已复制 ${checked.length} 个模型源，记得保存~`,"success");
  };
  loadSrc();
}
async function copyMcpFrom() {
  if (!state.currentProfile) return;
  const others = state.profiles.filter(p => p.name !== state.currentProfile);
  if (!others.length) { toast("没有其他 profile 可复制","info"); return; }
  const m = document.createElement("div"); m.className = "modal-overlay";
  m.innerHTML = `<div class="modal"><h3>从其他 Profile 复制 MCP Server</h3>
    <div style="margin-bottom:10px"><label style="font-size:11px;color:var(--fg2)">源 Profile</label>
    <select id="mcp-src" style="width:100%;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg)">${others.map(p=>`<option value="${p.name}">${p.name}</option>`).join("")}</select></div>
    <div id="mcp-src-list" style="max-height:240px;overflow:auto;border:1px solid var(--border);border-radius:4px;padding:6px"></div>
    <div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm">复制选中</button></div></div>`;
  document.body.appendChild(m);
  const listEl = m.querySelector("#mcp-src-list");
  let srcMcps = [];
  const loadSrc = async () => {
    const src = m.querySelector("#mcp-src").value;
    listEl.innerHTML = '<div style="color:var(--fg3);padding:8px">加载中...</div>';
    try {
      const d = await api(`/api/profile/${src}/config`);
      srcMcps = d.mcp_servers || [];
      if (!srcMcps.length) { listEl.innerHTML = '<div style="color:var(--fg3);padding:8px">该 Profile 无 MCP Server</div>'; return; }
      listEl.innerHTML = srcMcps.map((mc,i)=>`<label style="display:flex;align-items:center;gap:6px;padding:4px;cursor:pointer"><input type="checkbox" value="${i}" style="accent-color:var(--accent)"><span><b style="color:var(--purple)">${mc.name||"(unnamed)"}</b> <span style="color:var(--fg3);font-size:11px">${mc.command||""} · ${(mc.args||[]).length} args</span></span></label>`).join("");
    } catch(e) { listEl.innerHTML = `<div style="color:var(--red);padding:8px">加载失败: ${e.message}</div>`; }
  };
  m.querySelector("#mcp-src").onchange = loadSrc;
  m.querySelector(".cancel").onclick = () => m.remove();
  m.querySelector(".confirm").onclick = () => {
    const checked = [...listEl.querySelectorAll("input[type=checkbox]:checked")].map(c=>parseInt(c.value));
    if (!checked.length) { toast("请至少选择一个 MCP Server","error"); return; }
    const cd = state.configData[state.currentProfile];
    if (!cd.mcp_servers) cd.mcp_servers = [];
    const existing = new Set(cd.mcp_servers.map(x=>x.name));
    let added = 0, skipped = 0;
    checked.forEach(i => {
      const mc = srcMcps[i];
      if (!mc) return;
      if (existing.has(mc.name)) { skipped++; return; }
      cd.mcp_servers.push(JSON.parse(JSON.stringify(mc))); added++; existing.add(mc.name);
    });
    m.remove();
    renderConfigForm(document.getElementById("file-content"));
    updateConfigSaveState();
    toast(added>0 ? `已复制 ${added} 个 MCP Server${skipped?`，${skipped} 个同名跳过`:''}，记得保存~` : "所选 MCP 均与现有同名，未复制","success");
  };
  loadSrc();
}
function toggleKeyVisible(i) {
  const input = document.getElementById(`cp-key-${i}`);
  const icon = document.getElementById(`cp-eye-icon-${i}`);
  if (!input) return;
  if (input.type === "password") {
    input.type = "text";
    input.style.letterSpacing = "normal";
    if (icon) icon.textContent = "[.]";
  } else {
    input.type = "password";
    input.style.letterSpacing = "2px";
    if (icon) icon.textContent = "[*]";
  }
}
async function discoverModels(i) {
  const btn = document.getElementById(`cp-discover-${i}`);
  if (!btn) return;
  btn.disabled = true; btn.classList.add("loading"); btn.textContent = "探测中...";
  try {
    // 直接传当前内存中的 provider 配置，无需先保存
    const cp = state.configData[state.currentProfile].custom_providers[i] || {};
    const d = await api(`/api/profile/${state.currentProfile}/discover-models/${i}`, "POST", {provider: cp});
    if (d.models && d.models.length > 0) {
      // 填入 models textarea
      const ta = document.querySelector(`textarea[data-cp-field="models"][data-cp-index="${i}"]`);
      if (ta) {
        ta.value = d.models.join("\n");
        ta.dispatchEvent(new Event("input"));
      }
      toast(`发现 ${d.count} 个模型~`, "success");
      // 如果当前选的 provider 就是这个，刷新 model_select 下拉
      const cp = state.configData[state.currentProfile].custom_providers[i];
      const providerVal = state.configData[state.currentProfile].values["model.provider"];
      if (cp && providerVal === `custom:${cp.name}`) {
        const curModel = state.configData[state.currentProfile].values["model.default"];
        const modelField = document.querySelector('[data-field-key="model.default"]');
        if (modelField) {
          modelField.outerHTML = renderModelSelect(curModel);
          const newEl = document.querySelector('[data-key="model.default"]');
          if (newEl) newEl.addEventListener("change", () => {
            state.configData[state.currentProfile].values["model.default"] = newEl.value;
            updateFieldHighlight("model.default"); updateConfigSaveState();
          });
        }
      }
    } else {
      toast("API 返回了空模型列表", "info");
    }
  } catch(e) {
    toast("探测失败: " + e.message, "error");
  } finally {
    btn.disabled = false; btn.classList.remove("loading"); btn.textContent = "探测模型";
  }
}

function updateConfigSaveState() {
  const modified = isConfigModified();
  const btn = document.getElementById("btn-save-config"); if (btn) btn.disabled = !modified;
  const info = document.getElementById("config-info"); if (info) info.textContent = modified ? "有未保存的修改" : "无修改";
  renderFileTabs();
}

async function saveConfig() {
  const cd = state.configData[state.currentProfile];
  const btn = document.getElementById("btn-save-config");
  await withLoading(btn, async () => {
    await api(`/api/profile/${state.currentProfile}/config`, "PUT", {values: cd.values, custom_providers: cd.custom_providers, mcp_servers: cd.mcp_servers || []});
    state.configOriginal[state.currentProfile] = JSON.parse(JSON.stringify(cd));
    // 重新渲染表单以清除所有字段的"已修改"黄色边框
    const fc = document.getElementById("file-content");
    if (fc && state.configMode === "form") renderConfigForm(fc);
    else updateConfigSaveState();
    toast("config.yaml 保存成功~","success");
  }).catch(e => toast("保存失败: "+e.message,"error"));
}

// ── config raw mode ──
function setConfigMode(mode) {
  state.configMode = mode;
  const fc = document.getElementById("file-content"); if (!fc) return;
  if (mode === "raw") {
    // load raw content
    api(`/api/profile/${state.currentProfile}/config/raw`).then(d => {
      state.configRawContent = d.content; state.configRawOriginal = d.content;
      renderConfigRaw(fc);
    }).catch(e => toast("加载原始内容失败: "+e.message,"error"));
  } else {
    loadConfigView(fc);
  }
}

function renderConfigRaw(container) {
  container.innerHTML = `
    <div class="raw-toolbar">
      <span class="path">config.yaml (原始编辑 - 高级模式)</span>
      <div class="mode-switch"><button onclick="setConfigMode('form')">表单</button><button class="active" onclick="setConfigMode('raw')">原始</button></div>
    </div>
    <div class="raw-wrap">
      <div class="line-numbers" id="line-numbers"></div>
      <div class="textarea-wrap"><textarea id="editor" spellcheck="false"></textarea></div>
    </div>
    <div class="save-bar">
      <span class="info">高级模式: 直接编辑原始 YAML，注意不要破坏格式</span>
      <button class="btn save" onclick="saveConfigRaw()" id="btn-save-raw" ${state.configRawContent===state.configRawOriginal?"disabled":""}>保存</button>
    </div>
  `;
  const ta = document.getElementById("editor");
  ta.value = state.configRawContent;
  ta.oninput = () => { state.configRawContent = ta.value; updateLineNumbers(ta.value); const b=document.getElementById("btn-save-raw"); if(b) b.disabled = ta.value === state.configRawOriginal; renderFileTabs(); };
  updateLineNumbers(state.configRawContent);
}

async function saveConfigRaw() {
  const btn = document.getElementById("btn-save-raw");
  await withLoading(btn, async () => {
    await confirmDiffSave("config.yaml", state.configRawContent, async () => {
      await api(`/api/profile/${state.currentProfile}/config/raw`, "PUT", {content: state.configRawContent});
      state.configRawOriginal = state.configRawContent;
      const b = document.getElementById("btn-save-raw"); if (b) b.disabled = true;
      // reload structured data
      delete state.configData[state.currentProfile]; delete state.configOriginal[state.currentProfile];
      toast("config.yaml (原始) 保存成功~","success");
      renderFileTabs();
    });
  }).catch(e => toast("保存失败: "+e.message,"error"));
}

// ── config raw mode ──
function setConfigMode(mode) {
  state.configMode = mode;
  const fc = document.getElementById("file-content"); if (!fc) return;
  if (mode === "raw") {
    // load raw content
    api(`/api/profile/${state.currentProfile}/config/raw`).then(d => {
      state.configRawContent = d.content; state.configRawOriginal = d.content;
      renderConfigRaw(fc);
    }).catch(e => toast("加载原始内容失败: "+e.message,"error"));
  } else {
    loadConfigView(fc);
  }
}

// ── .env 结构化视图 ──
async function loadEnvView(container) {
  if (!state.envData[state.currentProfile]) {
    try {
      const d = await api(`/api/profile/${state.currentProfile}/env`);
      state.envData[state.currentProfile] = d.entries;
      state.envOriginal[state.currentProfile] = JSON.parse(JSON.stringify(d.entries));
    } catch(e) { toast("加载 .env 失败: "+e.message,"error"); return; }
  }
  if (state.envMode === "form") renderEnvForm(container);
  else renderEnvRaw(container);
}

function renderEnvForm(container) {
  const entries = state.envData[state.currentProfile] || [];
  const orig = state.envOriginal[state.currentProfile] || [];
  let rowsHtml = "";
  entries.forEach((e, i) => {
    const origE = orig[i];
    const modified = origE ? JSON.stringify(e) !== JSON.stringify(origE) : true;
    const classes = [e.active ? "" : "env-row-inactive", modified ? "env-row-modified" : ""].filter(Boolean).join(" ");
    rowsHtml += `<tr class="${classes}" data-env-row="${i}">
      <td class="col-active"><input type="checkbox" data-env-field="active" data-env-index="${i}" ${e.active?"checked":""}></td>
      <td class="col-key"><input type="text" value="${escHtml(e.key)}" data-env-field="key" data-env-index="${i}"></td>
      <td><input type="text" value="${escHtml(e.value)}" data-env-field="value" data-env-index="${i}"></td>
      <td><input type="text" value="${escHtml(e.comment||"")}" data-env-field="comment" data-env-index="${i}" placeholder="注释"></td>
      <td class="col-del"><button class="del-btn" onclick="delEnvRow(${i})">×</button></td>
    </tr>`;
  });
  container.innerHTML = `
    <div class="raw-toolbar">
      <span class="path">.env (条目编辑 - ${entries.length} 条)</span>
      <div class="mode-switch"><button class="active" onclick="setEnvMode('form')">条目</button><button onclick="setEnvMode('raw')">原始</button></div>
    </div>
    <div class="env-editor">
      <div class="env-toolbar">
        <button class="btn" onclick="addEnvRow()">+ 新增条目</button>
      </div>
      <table class="env-table">
        <thead><tr><th class="col-active">启</th><th class="col-key">Key</th><th>Value</th><th>注释</th><th class="col-del"></th></tr></thead>
        <tbody id="env-tbody">${rowsHtml}</tbody>
      </table>
    </div>
    <div class="save-bar">
      <span class="info" id="env-info">${isEnvModified()?"有未保存的修改":"无修改"}</span>
      <button class="btn save" onclick="saveEnv()" id="btn-save-env" ${isEnvModified()?"":"disabled"}>保存 .env</button>
    </div>
  `;
  attachEnvListeners();
}

function escHtml(s) { return String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }

function attachEnvListeners() {
  document.querySelectorAll("[data-env-field]").forEach(el => {
    const eventType = el.type === "checkbox" ? "change" : "input";
    el.addEventListener(eventType, () => {
      const idx = parseInt(el.dataset.envIndex); const field = el.dataset.envField;
      const entries = state.envData[state.currentProfile]; if (!entries[idx]) return;
      if (field === "active") {
        entries[idx].active = el.checked;
        el.closest("tr").classList.toggle("env-row-inactive", !el.checked);
      } else {
        entries[idx][field] = el.value;
      }
      updateEnvSaveState();
    });
  });
}

function addEnvRow() {
  state.envData[state.currentProfile].push({key:"NEW_KEY", value:"", comment:"", active:true});
  renderEnvForm(document.getElementById("file-content"));
}
function delEnvRow(i) {
  state.envData[state.currentProfile].splice(i, 1);
  renderEnvForm(document.getElementById("file-content"));
}
function updateEnvSaveState() {
  const modified = isEnvModified();
  const btn = document.getElementById("btn-save-env"); if (btn) btn.disabled = !modified;
  const info = document.getElementById("env-info"); if (info) info.textContent = modified ? "有未保存的修改" : "无修改";
  renderFileTabs();
}
async function saveEnv() {
  const btn = document.getElementById("btn-save-env");
  await withLoading(btn, async () => {
    await api(`/api/profile/${state.currentProfile}/env`, "PUT", {entries: state.envData[state.currentProfile]});
    state.envOriginal[state.currentProfile] = JSON.parse(JSON.stringify(state.envData[state.currentProfile]));
    // 重新渲染表单以清除所有行的"已修改"黄色边框
    const fc = document.getElementById("file-content");
    if (fc && state.envMode === "form") loadEnvView(fc);
    else updateEnvSaveState();
    toast(".env 保存成功~","success");
  }).catch(e => toast("保存失败: "+e.message,"error"));
}

function setEnvMode(mode) {
  state.envMode = mode;
  const fc = document.getElementById("file-content"); if (!fc) return;
  if (mode === "raw") {
    api(`/api/profile/${state.currentProfile}/.env`).then(d => {
      state.envRawContent = d.content; state.envRawOriginal = d.content;
      renderEnvRaw(fc);
    }).catch(e => toast("加载失败: "+e.message,"error"));
  } else {
    loadEnvView(fc);
  }
}
function renderEnvRaw(container) {
  container.innerHTML = `
    <div class="raw-toolbar">
      <span class="path">.env (原始编辑)</span>
      <div class="mode-switch"><button onclick="setEnvMode('form')">条目</button><button class="active" onclick="setEnvMode('raw')">原始</button></div>
    </div>
    <div class="raw-wrap"><div class="line-numbers" id="line-numbers"></div><div class="textarea-wrap"><textarea id="editor" spellcheck="false"></textarea></div></div>
    <div class="save-bar"><span class="info">直接编辑原始内容</span><button class="btn save" onclick="saveEnvRaw()" id="btn-save-env-raw" ${state.envRawContent===state.envRawOriginal?"disabled":""}>保存</button></div>
  `;
  const ta = document.getElementById("editor"); ta.value = state.envRawContent;
  ta.oninput = () => { state.envRawContent = ta.value; updateLineNumbers(ta.value); const b=document.getElementById("btn-save-env-raw"); if(b)b.disabled=ta.value===state.envRawOriginal; renderFileTabs(); };
  updateLineNumbers(state.envRawContent);
}
async function saveEnvRaw() {
  const btn = document.getElementById("btn-save-env-raw");
  await withLoading(btn, async () => {
    await confirmDiffSave(".env", state.envRawContent, async () => {
      await api(`/api/profile/${state.currentProfile}/.env`, "PUT", {content: state.envRawContent});
      state.envRawOriginal = state.envRawContent;
      const b = document.getElementById("btn-save-env-raw"); if(b) b.disabled = true;
      delete state.envData[state.currentProfile]; delete state.envOriginal[state.currentProfile];
      toast(".env (原始) 保存成功~","success"); renderFileTabs();
    });
  }).catch(e => toast("保存失败: "+e.message,"error"));
}

// ── line numbers ──
function updateLineNumbers(content) {
  const el = document.getElementById("line-numbers"); if (!el) return;
  const lines = content.split("\n").length; let html = "";
  for (let i = 1; i <= lines; i++) html += `<div>${i}</div>`;
  el.innerHTML = html;
}

// ── Skills ──
async function renderSkillsView() {
  if (!state.skillSubview) state.skillSubview = "skills";
  if (!state.skills[state.currentProfile]) {
    try { const d = await api(`/api/profile/${state.currentProfile}/skills`); state.skills[state.currentProfile] = d.skills; }
    catch(e) { state.skills[state.currentProfile] = []; }
  }
  const skills = state.skills[state.currentProfile] || [];
  const builtinCnt = skills.filter(s=>s.source==="builtin").length;
  const userCnt = skills.filter(s=>s.source==="user").length;
  const customCnt = skills.filter(s=>s.source==="custom").length;
  const sharedCnt = skills.filter(s=>s.source==="shared").length;
  const enabledCnt = skills.filter(s=>s.enabled!==false).length;
  const p = state.profiles.find(x=>x.name===state.currentProfile) || {};
  const vc = document.getElementById("view-content");
  vc.innerHTML = `
    <div class="skill-subtabs">
      <div class="skill-subtab ${state.skillSubview==="skills"?"active":""}" data-view="skills" onclick="switchSkillSubview('skills')" title="启用 ${enabledCnt} / 总计 ${skills.length}（内置 ${builtinCnt} + 用户 ${userCnt} + 自定义 ${customCnt} + 共享 ${sharedCnt}）">技能 <b>${enabledCnt}</b><span style="color:var(--fg3);font-size:11px;font-weight:400">/${skills.length}</span></div>
      <div class="skill-subtab ${state.skillSubview==="toolsets"?"active":""}" data-view="toolsets" onclick="switchSkillSubview('toolsets')" title="config.yaml platform_toolsets 启用数 / 注册总数">工具集 <b>${p.toolset_enabled||0}</b><span style="color:var(--fg3);font-size:11px;font-weight:400">/${p.toolset_total||0}</span></div>
      <div class="skill-subtab ${state.skillSubview==="mcp"?"active":""}" data-view="mcp" onclick="switchSkillSubview('mcp')" title="config.yaml mcp_servers 数">MCP <b>${p.mcp_count||0}</b></div>
      <button class="btn" onclick="batchExtractAll()" style="margin-left:auto;height:26px;padding:0 12px;font-size:12px" title="将当前 profile 所有用户技能一键抽取到共享库（原位置替换为 junction）">📦 一键抽取</button>
      <button class="btn" onclick="showSharedLibraryModal()" style="height:26px;padding:0 12px;font-size:12px" title="查看共享技能库并引用到当前 profile">🔗 共享库</button>
      <button class="btn" onclick="fixSkillJunctions()" style="height:26px;padding:0 12px;font-size:12px" title="修复失效的共享技能链接（移动 AAAHermesHub 目录后使用）">🔧 修复链接</button>
      <button class="btn" onclick="showHubModal()" style="height:26px;padding:0 12px;font-size:12px" title="浏览在线技能市场">🌐 技能中心</button>
    </div>
    <div id="skill-subview-container" style="flex:1;overflow:hidden;display:flex;flex-direction:column"></div>`;
  if (state.skillSubview === "toolsets") renderToolsetsSubview();
  else if (state.skillSubview === "mcp") renderMcpSubview();
  else renderSkillsSubview();
}
function switchSkillSubview(view) {
  state.skillSubview = view;
  // 切换子视图时无需重新拉取 skills，直接重渲染
  const vc = document.getElementById("view-content");
  if (!vc) return;
  vc.querySelectorAll(".skill-subtab").forEach(el => {
    el.classList.toggle("active", el.dataset.view === view);
  });
  const container = document.getElementById("skill-subview-container");
  if (!container) { renderSkillsView(); return; }
  if (view === "toolsets") renderToolsetsSubview();
  else if (view === "mcp") renderMcpSubview();
  else renderSkillsSubview();
}
function renderSkillsSubview() {
  const skills = state.skills[state.currentProfile] || [];
  const builtinCnt = skills.filter(s=>s.source==="builtin").length;
  const userCnt = skills.filter(s=>s.source==="user").length;
  const customCnt = skills.filter(s=>s.source==="custom").length;
  const sharedCnt = skills.filter(s=>s.source==="shared").length;
  const enabledCnt = skills.filter(s=>s.enabled!==false).length;
  const disabledCnt = skills.length - enabledCnt;
  const container = document.getElementById("skill-subview-container");
  if (!container) return;
  container.innerHTML = `
    <div class="skills-panel">
      <div class="skills-sidebar" id="skills-sidebar">
        <div class="skills-sidebar-header"><span>${enabledCnt}/${skills.length} Skills</span><span style="font-size:10px;color:var(--fg3);text-transform:none;letter-spacing:0;font-weight:400">内置 ${builtinCnt} + 用户 ${userCnt} + 自定义 ${customCnt} + 共享 ${sharedCnt}${disabledCnt?` + 禁用 ${disabledCnt}`:''}</span></div>
        <div class="skills-search"><input id="skill-search" placeholder="搜索 skill..." oninput="filterSkills()"></div>
        <div id="skills-filter-bar" style="display:flex;gap:4px;padding:4px 8px;flex-wrap:wrap">
          <button class="source-filter-btn active" data-filter="all" onclick="setSourceFilter('all',this)" style="font-size:10px;padding:2px 8px;border-radius:10px;border:1px solid var(--border);background:var(--bg);color:var(--fg2);cursor:pointer">全部</button>
          <button class="source-filter-btn" data-filter="shared" onclick="setSourceFilter('shared',this)" style="font-size:10px;padding:2px 8px;border-radius:10px;border:1px solid var(--border);background:var(--bg);color:var(--fg2);cursor:pointer">共享</button>
          <button class="source-filter-btn" data-filter="custom" onclick="setSourceFilter('custom',this)" style="font-size:10px;padding:2px 8px;border-radius:10px;border:1px solid var(--border);background:var(--bg);color:var(--fg2);cursor:pointer">自定义</button>
          <button class="source-filter-btn" data-filter="user" onclick="setSourceFilter('user',this)" style="font-size:10px;padding:2px 8px;border-radius:10px;border:1px solid var(--border);background:var(--bg);color:var(--fg2);cursor:pointer">内置→用户</button>
          <button class="source-filter-btn" data-filter="builtin" onclick="setSourceFilter('builtin',this)" style="font-size:10px;padding:2px 8px;border-radius:10px;border:1px solid var(--border);background:var(--bg);color:var(--fg2);cursor:pointer">内置</button>
          <button class="source-filter-btn" data-filter="disabled" onclick="setSourceFilter('disabled',this)" style="font-size:10px;padding:2px 8px;border-radius:10px;border:1px solid var(--border);background:var(--bg);color:var(--fg2);cursor:pointer">已禁用</button>
        </div>
        <div class="skills-list" id="skills-list"></div>
      </div>
      <div class="sidebar-resizer" id="sidebar-resizer" title="拖拽调整面板宽度"></div>
      <div class="skill-editor" id="skill-editor"><div class="empty-state"><div class="big">(◕‿◕)</div><div>选择一个 Skill 开始编辑~</div></div></div>
    </div>`;
  // 恢复上次宽度
  const savedW = localStorage.getItem("skills-sidebar-width");
  if (savedW) document.getElementById("skills-sidebar").style.width = savedW + "px";
  // 拖拽调整宽度
  const resizer = document.getElementById("sidebar-resizer");
  const sidebar = document.getElementById("skills-sidebar");
  let dragging = false, startX = 0, startW = 0;
  resizer.addEventListener("mousedown", (e) => {
    dragging = true; startX = e.clientX; startW = sidebar.offsetWidth;
    resizer.classList.add("dragging"); document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    e.preventDefault();
  });
  document.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const w = Math.max(200, Math.min(600, startW + e.clientX - startX));
    sidebar.style.width = w + "px";
  });
  document.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false; resizer.classList.remove("dragging");
    document.body.style.cursor = ""; document.body.style.userSelect = "";
    localStorage.setItem("skills-sidebar-width", sidebar.offsetWidth);
  });
  renderSkillsList();
}
async function renderToolsetsSubview() {
  const container = document.getElementById("skill-subview-container");
  if (!container) return;
  container.innerHTML = '<div class="empty-state" style="flex:1"><div class="big">⏳</div><div>加载工具集...</div></div>';
  let d;
  try { d = await api(`/api/profile/${state.currentProfile}/toolsets`); }
  catch(e) { container.innerHTML = `<div class="empty-state" style="flex:1"><div class="big">⚠️</div><div>${e.message||"加载失败"}</div></div>`; return; }
  const items = d.toolsets || [];
  if (items.length === 0) {
    container.innerHTML = '<div class="empty-state" style="flex:1"><div class="big">(Empty)</div><div>未找到工具集注册信息（hermes-agent/toolsets.py 不存在）</div></div>';
    return;
  }
  container.innerHTML = `
    <div style="padding:12px 16px;overflow-y:auto;flex:1">
      <div style="font-size:12px;color:var(--fg3);margin-bottom:10px">共 <b>${d.total}</b> 个工具集，当前 Profile 启用 <b style="color:var(--green)">${d.enabled_count}</b> 个。启用状态由 config.yaml 的 <code style="font-family:var(--font-mono);color:var(--accent2)">platform_toolsets</code> 控制，可在"配置"页修改。</div>
      ${items.map(t => `
        <div style="background:var(--bg2);border:1px solid ${t.enabled?'var(--green)':'var(--border)'};border-radius:6px;padding:10px;margin-bottom:8px;${t.enabled?'box-shadow:0 0 0 1px var(--green-dim)':''}">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
            <span style="font-weight:600;color:var(--fg);font-size:13px">${t.name}</span>
            ${t.enabled?'<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:var(--green-dim);color:var(--green);font-weight:600">启用</span>':'<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:var(--bg4);color:var(--fg3)">未启用</span>'}
          </div>
          <div style="font-size:12px;color:var(--fg2);margin-bottom:6px">${t.description||'无描述'}</div>
          ${t.tools && t.tools.length ? `<div style="display:flex;flex-wrap:wrap;gap:4px">${t.tools.map(tool=>`<span style="font-size:10px;background:var(--bg3);color:var(--fg2);padding:1px 5px;border-radius:3px;font-family:var(--font-mono)">${tool}</span>`).join('')}</div>`:''}
        </div>
      `).join('')}
    </div>`;
}
function highlightJson(obj) {
  const json = JSON.stringify(obj, null, 2);
  return json.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false)\b|\bnull\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, (m) => {
      let color = "var(--accent2)"; // number
      if (/^"/.test(m)) { color = /:$/.test(m) ? "var(--purple)" : "var(--green)"; } // key : string
      else if (/true|false/.test(m)) color = "var(--yellow)"; // bool
      else if (/null/.test(m)) color = "var(--fg3)"; // null
      return `<span style="color:${color}">${m}</span>`;
    });
}
async function renderMcpSubview() {
  const container = document.getElementById("skill-subview-container");
  if (!container) return;
  container.innerHTML = '<div class="empty-state" style="flex:1"><div class="big">⏳</div><div>加载 MCP...</div></div>';
  let d;
  try { d = await api(`/api/profile/${state.currentProfile}/mcp`); }
  catch(e) { container.innerHTML = `<div class="empty-state" style="flex:1"><div class="big">⚠️</div><div>${e.message||"加载失败"}</div></div>`; return; }
  const servers = d.mcp_servers || [];
  const header = `
    <div style="display:flex;align-items:center;gap:10px;padding:10px 16px;border-bottom:1px solid var(--border);background:var(--bg2);flex-shrink:0">
      <div style="font-size:12px;color:var(--fg3);flex:1">共 <b style="color:var(--fg)">${d.count}</b> 个 MCP server，配置在 config.yaml 的 <code style="font-family:var(--font-mono);color:var(--accent2)">mcp_servers</code> 段</div>
      <button class="btn" onclick="editMcpInConfig()" title="跳转到 config.yaml 编辑 MCP 配置">在 config.yaml 编辑</button>
    </div>`;
  if (servers.length === 0) {
    container.innerHTML = header + '<div class="empty-state" style="flex:1"><div class="big">(Empty)</div><div>该 Profile 未配置 MCP server<br>点击右上角按钮在 config.yaml 添加</div></div>';
    return;
  }
  container.innerHTML = header + `
    <div style="padding:12px 16px;overflow-y:auto;flex:1">
      ${servers.map(s => {
        const disabled = s.enabled === false;
        const obj = {};
        obj[s.name] = { command: s.command || "", args: s.args || [], enabled: s.enabled !== false };
        const jsonHtml = highlightJson(obj);
        return `
        <div style="background:var(--bg2);border:1px solid ${disabled?'var(--border)':'var(--purple)'};border-radius:6px;padding:0;margin-bottom:10px;${disabled?'opacity:.55':''}${disabled?'':'box-shadow:0 0 0 1px var(--purple-dim)'};overflow:hidden">
          <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;border-bottom:1px solid var(--border)">
            <span style="font-weight:600;color:var(--purple);font-size:13px;font-family:var(--font-mono)">${s.name}</span>
            ${disabled
              ? '<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:var(--bg4);color:var(--fg3);font-weight:600">已禁用</span>'
              : '<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:var(--green-dim);color:var(--green);font-weight:600">启用</span>'}
          </div>
          <pre style="margin:0;padding:10px 12px;font-family:var(--font-mono);font-size:12px;line-height:1.6;background:var(--bg);overflow-x:auto;white-space:pre;word-break:break-all">${jsonHtml}</pre>
        </div>`;
      }).join('')}
    </div>`;
}
function editMcpInConfig() {
  // 跳转到配置文件视图并选中 config.yaml，便于用户编辑 mcp_servers 段
  state.currentFile = "config.yaml";
  switchView('files');
  setTimeout(() => toast("已跳转到 config.yaml，搜索 mcp_servers 编辑 MCP 配置", "info"), 200);
}
// 一键修复失效的共享技能 junction（移动 AAAHermesHub 目录后使用）
async function fixSkillJunctions() {
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal"><h3>🔧 修复技能链接</h3>
    <p style="font-size:13px;color:var(--fg2);line-height:1.6">检测并修复所有 profile 中失效的共享技能链接（junction）。<br>
    适用场景：将 <code>AAAHermesHub</code> 或 exe 移到新目录后，原有的技能链接会指向旧路径导致失效。</p>
    <div class="modal-actions"><button class="btn cancel">取消</button><button class="btn primary confirm">开始修复</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    const btn = o.querySelector(".confirm");
    btn.classList.add("loading"); btn.textContent = "修复中...";
    try {
      const d = await api("/api/skills/fix-junctions", "POST");
      o.remove();
      toast(d.message, "success");
      // 如果有修复详情，弹窗展示
      if (d.details && d.details.length > 0) {
        const detailModal = document.createElement("div"); detailModal.className = "modal-overlay";
        detailModal.innerHTML = `<div class="modal" style="max-width:560px"><h3>修复详情（${d.fixed} 个）</h3>
          <div style="max-height:360px;overflow-y:auto;font-size:12px;color:var(--fg2);line-height:1.8;font-family:var(--mono);background:var(--bg2);padding:10px;border-radius:4px;border:1px solid var(--border)">
            ${d.details.map(l => `<div>${l}</div>`).join("")}
          </div>
          <div class="modal-actions"><button class="btn primary" onclick="this.closest('.modal-overlay').remove();location.reload()">刷新页面</button></div></div>`;
        document.body.appendChild(detailModal);
      } else {
        // 刷新技能列表
        delete state.skills[state.currentProfile];
        if (state.skillSubview === "skills") renderSkillsSubview();
      }
    } catch(e) {
      o.remove();
      toast("修复失败: " + e.message, "error");
    }
  };
}

function showHubModal() {
  // 内置技能中心：多源搜索（ClawHub / Skills.sh / 自定义源）
  // 后端访问各源 API/HTML，前端展示结果并一键安装到共享技能库。
  // 冲突检测：后端比对 SHARED_SKILLS_DIR 已有内容，前端用 conflict 标记展示。
  showModal({
    title: "🌐 技能中心",
    bodyHtml: `
      <div style="display:flex;flex-direction:column;gap:12px">
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <div style="display:flex;flex:1;min-width:240px">
            <input id="hub-search-input" placeholder="搜索技能（如 weather / github / pdf）" style="flex:1;min-width:160px;padding:6px 10px;border:1px solid var(--border);border-radius:4px 0 0 4px;background:var(--bg);color:var(--fg);font-size:13px;outline:none" onkeydown="if(event.key==='Enter'){event.preventDefault();searchHubSkills()}">
            <button class="btn" onclick="searchHubSkills()" id="hub-search-btn" style="border-radius:0 4px 4px 0;margin-left:-1px">搜索</button>
          </div>
          <select id="hub-source-select" style="padding:6px 10px;border:1px solid var(--border);border-radius:4px;background:var(--bg2);color:var(--fg);font-size:12px;outline:none;cursor:pointer">
            <option value="all">全部源</option>
          </select>
          <button class="btn" onclick="showSourceManageModal()" title="管理技能源" style="font-size:12px">⚙ 管理源</button>
        </div>
        <div style="font-size:11px;color:var(--fg3);line-height:1.5">
          安装到 <b style="color:var(--accent)">共享技能库</b>（AAAHermesHub/shared-skills/），跨 profile 复用。社区技能（community trust）安装前请自行确认来源可信。
        </div>
        <div id="hub-search-results" style="max-height:420px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;padding:8px;display:flex;flex-direction:column;gap:8px;background:var(--bg)">
          <div style="color:var(--fg3);text-align:center;padding:24px;font-size:12px">输入关键词并点击搜索</div>
        </div>
      </div>`,
    confirmText: "关闭",
    onConfirm: () => {}
  });
  // 异步加载源列表到下拉
  loadHubSources();
  setTimeout(() => document.getElementById("hub-search-input")?.focus(), 50);
}

// 加载技能源列表到下拉框（保留 "全部" 选项，重建其余）
async function loadHubSources() {
  const select = document.getElementById("hub-source-select");
  if (!select) return;
  try {
    const d = await api("/api/skills-hub/sources");
    state.hubSources = d.sources || [];
    select.innerHTML = '<option value="all">全部源</option>' +
      state.hubSources.map(s => `<option value="${s.id}" ${s.enabled?'':'disabled'}>${s.name}${s.enabled?'':' (禁用)'}</option>`).join('');
    // 恢复上次选择（若仍有效）
    if (state.hubSelectedSource && state.hubSelectedSource !== "all" &&
        state.hubSources.some(s => s.id === state.hubSelectedSource && s.enabled)) {
      select.value = state.hubSelectedSource;
    } else {
      select.value = "all";
      state.hubSelectedSource = "all";
    }
  } catch(e) {
    // 静默失败，保留 "全部" 选项
  }
}

async function searchHubSkills() {
  const input = document.getElementById("hub-search-input");
  const resultsEl = document.getElementById("hub-search-results");
  const btn = document.getElementById("hub-search-btn");
  const select = document.getElementById("hub-source-select");
  if (!input || !input.value.trim()) return;
  const source = select ? select.value : "all";
  state.hubSelectedSource = source;
  withLoading(btn, async () => {
    resultsEl.innerHTML = '<div style="color:var(--fg3);text-align:center;padding:24px">搜索中...</div>';
    let d;
    try { d = await api(`/api/skills-hub/search?q=${encodeURIComponent(input.value)}&source=${encodeURIComponent(source)}`); }
    catch(e) { resultsEl.innerHTML = `<div style="color:var(--red);padding:16px;text-align:center">${e.message||"搜索失败"}</div>`; return; }
    if (!d.items || d.items.length === 0) {
      resultsEl.innerHTML = '<div style="color:var(--fg3);text-align:center;padding:24px">未找到匹配技能</div>';
      return;
    }
    resultsEl.innerHTML = d.items.map(item => `
      <div style="border:1px solid ${item.conflict?'var(--red)':'var(--border)'};border-radius:6px;padding:10px;position:relative;background:var(--bg2)">
        ${item.conflict?'<div style="position:absolute;top:8px;right:8px;background:var(--red-dim);color:var(--red);font-size:10px;padding:2px 6px;border-radius:4px;font-weight:600">已存在</div>':''}
        <div style="font-weight:600;color:var(--fg);font-size:13px;padding-right:70px">${item.name}</div>
        <div style="font-size:12px;color:var(--fg2);margin:4px 0;max-height:60px;overflow:hidden">${item.description||'无描述'}</div>
        <div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px">
          <span style="font-size:10px;background:var(--accent-dim);color:var(--accent);padding:1px 5px;border-radius:3px;font-weight:600">${item.source_name||item.source||''}</span>
          ${(item.tags||[]).slice(0,6).map(tag=>`<span style="font-size:10px;background:var(--bg3);color:var(--fg3);padding:1px 5px;border-radius:3px">${tag}</span>`).join('')}
          <span style="font-size:10px;background:var(--purple-dim);color:var(--purple);padding:1px 5px;border-radius:3px">${item.trust||'community'}</span>
        </div>
        <button class="btn ${item.conflict?'danger':'primary'}" style="font-size:12px;padding:4px 12px" onclick="installHubSkill('${item.slug}', '${item.source}', ${item.conflict})">
          ${item.conflict?'强制安装':'安装'}
        </button>
      </div>
    `).join('');
  });
}

async function installHubSkill(slug, source, conflict) {
  // 冲突时需二次确认（旧文件会备份到 .trash/）
  // 安装目标：共享技能库（SHARED_SKILLS_DIR），跨 profile 复用
  const confirmed = await new Promise(resolve => {
    const o = document.createElement("div"); o.className = "modal-overlay";
    o.innerHTML = `<div class="modal"><h3>${conflict?'强制安装':'安装'}「${slug}」？</h3><p style="font-size:13px;color:var(--fg2);margin-bottom:8px">${conflict?'已存在同名技能，旧文件会备份到 .trash/ 目录。':'将从源 '+source+' 下载并安装到共享技能库 '+slug+'/。'}</p><p style="font-size:11px;color:var(--yellow);background:var(--yellow-dim);padding:6px 8px;border-radius:4px">⚠️ 社区技能（community trust）安装前请自行确认来源可信。</p><div class="modal-actions"><button class="btn cancel">取消</button><button class="btn ${conflict?'confirm danger':'confirm'}">${conflict?'强制安装':'确认安装'}</button></div></div>`;
    document.body.appendChild(o);
    o.querySelector(".cancel").onclick = () => { o.remove(); resolve(false); };
    o.querySelector(".confirm").onclick = () => { o.remove(); resolve(true); };
    o.onclick = e => { if (e.target === o) { o.remove(); resolve(false); } };
  });
  if (!confirmed) return;
  try {
    const d = await api(`/api/skills-hub/install`, "POST", {slug, source, force: conflict});
    toast(d.message || `已安装 ${slug} 到共享库`, "success");
    // 刷新技能列表（清除缓存重新拉取）- 共享技能可能被引用到当前 profile
    delete state.skills[state.currentProfile];
    delete state.skillContents[state.currentProfile];
    delete state.skillOriginals[state.currentProfile];
    if (state.skillSubview === "skills") renderSkillsSubview();
    else { state.skillSubview = "skills"; renderSkillsView(); }
    // 刷新搜索结果的冲突状态
    searchHubSkills();
  } catch(e) {
    toast(e.message || "安装失败", "error");
  }
}

// 源管理子模态：列出当前源（含启用/禁用、删除），并支持添加自定义源
function showSourceManageModal() {
  showModal({
    title: "⚙ 管理技能源",
    bodyHtml: `
      <div style="display:flex;flex-direction:column;gap:12px">
        <div id="source-list" style="display:flex;flex-direction:column;gap:6px;max-height:240px;overflow-y:auto">
          <div style="color:var(--fg3);text-align:center;padding:12px;font-size:12px">加载中...</div>
        </div>
        <div style="border-top:1px solid var(--border);padding-top:12px">
          <div style="font-size:12px;font-weight:600;color:var(--fg2);margin-bottom:6px">添加自定义源（JSON 索引）</div>
          <div style="display:flex;flex-direction:column;gap:6px">
            <input id="new-src-name" placeholder="名称（如 我的技能源）" style="padding:6px 10px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--fg);font-size:12px;outline:none">
            <input id="new-src-url" placeholder="JSON 索引 URL（如 https://example.com/skills.json）" style="padding:6px 10px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--fg);font-size:12px;outline:none">
            <button class="btn primary" style="font-size:12px;padding:6px 12px" onclick="addCustomSource()">添加</button>
          </div>
          <div style="font-size:10px;color:var(--fg3);margin-top:6px;line-height:1.5">
            JSON 索引格式：<code style="color:var(--accent2)">[{"name":"...","description":"...","download_url":"..."}]</code>
          </div>
        </div>
      </div>`,
    confirmText: "关闭",
    onConfirm: () => {}
  });
  loadSourceList();
}

// 加载源列表到管理模态
async function loadSourceList() {
  const el = document.getElementById("source-list");
  if (!el) return;
  try {
    const d = await api("/api/skills-hub/sources");
    const sources = d.sources || [];
    state.hubSources = sources;
    if (sources.length === 0) {
      el.innerHTML = '<div style="color:var(--fg3);text-align:center;padding:12px;font-size:12px">暂无源</div>';
      return;
    }
    el.innerHTML = sources.map(s => `
      <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 10px;border:1px solid var(--border);border-radius:4px;background:var(--bg2)">
        <div style="flex:1;min-width:0">
          <div style="font-size:12px;font-weight:600;color:var(--fg)">${s.name}</div>
          <div style="font-size:10px;color:var(--fg3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${s.url||''}</div>
        </div>
        <div style="display:flex;gap:6px;align-items:center">
          <span style="font-size:10px;color:var(--fg3);background:var(--bg3);padding:1px 5px;border-radius:3px">${s.type}</span>
          <button class="btn" style="font-size:10px;padding:2px 8px" onclick="toggleSource('${s.id}')">${s.enabled?'禁用':'启用'}</button>
          <button class="btn danger" style="font-size:10px;padding:2px 8px" onclick="deleteSource('${s.id}')">删除</button>
        </div>
      </div>
    `).join('');
  } catch(e) {
    el.innerHTML = `<div style="color:var(--red);padding:12px;text-align:center;font-size:12px">${e.message||'加载失败'}</div>`;
  }
}

// 添加自定义源
async function addCustomSource() {
  const name = document.getElementById("new-src-name")?.value.trim();
  const url = document.getElementById("new-src-url")?.value.trim();
  if (!name || !url) { toast("请填写名称和 URL", "error"); return; }
  try {
    await api("/api/skills-hub/sources", "POST", {name, url, type: "custom"});
    toast("已添加自定义源", "success");
    document.getElementById("new-src-name").value = "";
    document.getElementById("new-src-url").value = "";
    loadSourceList();
    // 同步刷新搜索模态的下拉
    loadHubSources();
  } catch(e) {
    toast(e.message || "添加失败", "error");
  }
}

// 切换源启用/禁用
async function toggleSource(sid) {
  try {
    await api(`/api/skills-hub/sources/${sid}/toggle`, "POST");
    loadSourceList();
    loadHubSources();
  } catch(e) {
    toast(e.message || "切换失败", "error");
  }
}

// 删除源
async function deleteSource(sid) {
  if (!confirm("确认删除该技能源？")) return;
  try {
    await api(`/api/skills-hub/sources/${sid}`, "DELETE");
    toast("已删除", "success");
    loadSourceList();
    loadHubSources();
  } catch(e) {
    toast(e.message || "删除失败", "error");
  }
}
function renderSkillsList() {
  const el = document.getElementById("skills-list"); if (!el) return;
  const search = (document.getElementById("skill-search")?.value || "").toLowerCase();
  const srcFilter = state.sourceFilter || "all";
  const skills = (state.skills[state.currentProfile]||[]).filter(s => {
    if (search && !s.name.toLowerCase().includes(search) && !(s.description||"").toLowerCase().includes(search) && !(s.category||"").toLowerCase().includes(search)) return false;
    if (srcFilter === "all") return true;
    if (srcFilter === "disabled") return s.enabled === false;
    return s.source === srcFilter;
  });
  el.innerHTML = "";
  // 共享技能批量操作栏（勾选共享技能后显示）
  const selBar = document.createElement("div");
  selBar.id = "shared-selection-bar";
  selBar.style.cssText = "display:none;align-items:center;gap:8px;padding:6px 8px;margin-bottom:6px;background:var(--yellow-dim);border:1px solid var(--yellow);border-radius:6px;font-size:12px";
  el.appendChild(selBar);
  if (skills.length === 0) {
    el.innerHTML = '<div style="color:var(--fg3);text-align:center;padding:24px;font-size:12px">无匹配技能</div>';
    return;
  }
  // 按 category 分组（无分类的归到 ""，显示为顶层）
  const groups = {};
  skills.forEach(s => {
    const cat = s.category || "";
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(s);
  });
  // 排序：无分类("")在前，其余按字母序
  const cats = Object.keys(groups).sort((a,b) => {
    if (a === "" && b !== "") return -1;
    if (a !== "" && b === "") return 1;
    return a.localeCompare(b);
  });
  const renderSkillItem = s => {
    const item = document.createElement("div");
    const nested = s.category ? " nested" : "";
    const disabledCls = s.enabled === false ? " disabled" : "";
    item.className = `skill-item${nested}${disabledCls} ${s.name===state.currentSkill?"active":""}`;
    // 来源标签：builtin=内置(紫)，user=内置→用户(绿)，custom=自定义(蓝)，shared=共享(橙)
    const srcTag = s.source === "builtin"
      ? '<span style="font-size:9px;padding:1px 5px;border-radius:6px;background:var(--purple-dim);color:var(--purple);margin-left:6px;font-weight:600">内置</span>'
      : s.source === "user"
        ? '<span style="font-size:9px;padding:1px 5px;border-radius:6px;background:var(--green-dim);color:var(--green);margin-left:6px;font-weight:600">内置→用户</span>'
        : s.source === "shared"
          ? '<span style="font-size:9px;padding:1px 5px;border-radius:6px;background:var(--yellow-dim);color:var(--yellow);margin-left:6px;font-weight:600" title="共享技能（junction，修改同步所有引用的 profile）">共享</span>'
          : '<span style="font-size:9px;padding:1px 5px;border-radius:6px;background:var(--accent-dim);color:var(--accent);margin-left:6px;font-weight:600">自定义</span>';
    // 启用/禁用开关
    const enabled = s.enabled !== false;
    const toggle = `<span class="skill-toggle ${enabled?"on":"off"}" data-skill="${s.name}" title="${enabled?"点击禁用":"点击启用"}">${enabled?"●":"○"}</span>`;
    // 共享技能加 checkbox（用于跨分类批量解除共享）
    const sharedChk = s.source === "shared"
      ? `<input type="checkbox" class="shared-select" data-skill="${s.name}" title="勾选后可批量解除共享" style="margin-right:6px;accent-color:var(--yellow);cursor:pointer">`
      : '';
    item.innerHTML = `<div class="sname">${sharedChk}${toggle}${s.name}${srcTag}</div><div class="sdesc">${s.description||"(no description)"}</div><div class="smeta">${s.version?`<span>v${s.version}</span>`:''}<span>${s.sub_files.length} files</span><span>${s.modified}</span></div>`;
    item.onclick = (e) => {
      if (e.target.classList.contains("shared-select")) {
        e.stopPropagation();
        updateSharedSelectionBar();
        return;
      }
      if (e.target.classList.contains("skill-toggle")) {
        e.stopPropagation();
        toggleSkillState(s.name, !enabled);
        return;
      }
      selectSkill(s.name);
    };
    return item;
  };
  cats.forEach(cat => {
    if (cat) {
      // 分类标题 + 批量操作按钮
      const header = document.createElement("div");
      header.className = "skill-cat-header";
      const items = groups[cat];
      // 统计可批量操作的 skill
      const extractable = items.filter(s => s.location === "user" && s.source !== "shared");
      const shared = items.filter(s => s.source === "shared");
      let batchBtns = '';
      if (extractable.length >= 1) {
        batchBtns += `<button class="cat-batch-btn" onclick="batchExtractCategory('${cat}')" title="将该分类下 ${extractable.length} 个自定义/用户技能全部抽取到共享库">全部抽取(${extractable.length})</button>`;
      }
      if (shared.length >= 1) {
        batchBtns += `<button class="cat-batch-btn" onclick="batchUnlinkCategory('${cat}')" title="将该分类下 ${shared.length} 个共享技能全部解除共享">全部解除(${shared.length})</button>`;
      }
      // 全部删除（排除内置 skill）
      const deletable = items.filter(s => s.source !== "builtin");
      if (deletable.length >= 1) {
        batchBtns += `<button class="cat-batch-btn" style="color:var(--red)" onclick="batchDeleteCategory('${cat}')" title="将该分类下 ${deletable.length} 个技能全部删除（共享 skill 仅删 junction，非共享移到 .trash）">全部删除(${deletable.length})</button>`;
      }
      header.innerHTML = `<span>📁 ${cat}</span><span class="cat-count">${items.length}</span>${batchBtns}`;
      el.appendChild(header);
    }
    groups[cat].forEach(s => el.appendChild(renderSkillItem(s)));
  });
}
function filterSkills() { renderSkillsList(); }

// ── 来源过滤 ──
state.sourceFilter = "all";
function setSourceFilter(filter, btn) {
  state.sourceFilter = filter;
  document.querySelectorAll(".source-filter-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  renderSkillsList();
}
async function toggleSkillState(skillName, enabled) {
  try {
    await api(`/api/profile/${state.currentProfile}/skills/${skillName}/state`, "PUT", {enabled});
    // 更新本地缓存
    const skills = state.skills[state.currentProfile] || [];
    const s = skills.find(x => x.name === skillName);
    if (s) s.enabled = enabled;
    renderSkillsList();
    if (state.currentSkill === skillName) renderSkillEditor();
    toast(`${skillName} 已${enabled?"启用":"禁用"}`, enabled?"success":"info");
  } catch(e) {
    toast(`切换失败: ${e.message}`, "error");
  }
}
async function selectSkill(name) {
  state.currentSkill = name; state.currentSkillFile = "SKILL.md";
  if (!state.skillContents[state.currentProfile]) state.skillContents[state.currentProfile] = {};
  if (!state.skillOriginals[state.currentProfile]) state.skillOriginals[state.currentProfile] = {};
  if (!state.skillContents[state.currentProfile][name]) { state.skillContents[state.currentProfile][name] = {}; state.skillOriginals[state.currentProfile][name] = {}; }
  renderSkillsList(); await renderSkillEditor();
}
async function renderSkillEditor() {
  const el = document.getElementById("skill-editor"); if (!el || !state.currentSkill) return;
  const skill = (state.skills[state.currentProfile]||[]).find(s => s.name === state.currentSkill); if (!skill) return;
  const cache = state.skillContents[state.currentProfile][state.currentSkill];
  if (!cache["SKILL.md"]) {
    try { const d = await api(`/api/profile/${state.currentProfile}/skills/${state.currentSkill}/SKILL.md`); cache["SKILL.md"]=d.content; state.skillOriginals[state.currentProfile][state.currentSkill]["SKILL.md"]=d.content; }
    catch(e) { cache["SKILL.md"]=""; state.skillOriginals[state.currentProfile][state.currentSkill]["SKILL.md"]=""; }
  }
  const allFiles = ["SKILL.md", ...skill.sub_files];
  if (!state.currentSkillFile || !allFiles.includes(state.currentSkillFile)) state.currentSkillFile = "SKILL.md";
  const isBuiltin = skill.location === "builtin";
  const isShared = skill.source === "shared";
  // builtin(物理在hermes-agent/skills/)只读：仅"复制到..."；shared 可解除共享；location=user 且非 shared 可抽取到共享库
  const sharedBtn = isShared
    ? `<button class="btn" onclick="unlinkSharedSkill()" title="解除共享：删除 junction，复制独立副本（断开共同进化）">解除共享</button>`
    : (isBuiltin ? '' : `<button class="btn" onclick="extractToShared()" title="抽取到共享库：原位置替换为 junction，所有引用的 profile 共同进化">抽取到共享库</button>`);
  const skillActions = isBuiltin
    ? `<button class="btn" onclick="copySkillTo(false)" title="复制此内置 skill 到其他 profile">复制到...</button>`
    : `<button class="btn" onclick="copySkillTo(false)" title="复制到其他 profile">复制到...</button><button class="btn" onclick="copySkillTo(true)" title="移动到其他 profile">移动到...</button><button class="btn" onclick="copySkillFrom()">复制自...</button>${sharedBtn}<button class="btn danger" onclick="deleteSkill()">删除</button>`;
  el.innerHTML = `
    <div class="raw-toolbar">
      <span class="path" id="skill-path">${isBuiltin?'(内置) ':''}${state.currentProfile}/skills/${state.currentSkill}/${state.currentSkillFile}</span>
      ${skillActions}
      <button class="btn" onclick="toggleSkillState('${state.currentSkill}', ${skill.enabled===false})" title="${skill.enabled===false?'启用此技能':'禁用此技能'}" style="${skill.enabled===false?'color:var(--red)':'color:var(--green)'}">${skill.enabled===false?'已禁用':'已启用'}</button>
      ${isBuiltin?'':`<button class="btn save" onclick="saveSkillFile()" id="btn-save-skill" disabled>保存</button>`}
    </div>
    <div class="skill-files-bar" id="skill-files-bar"></div>
    <div class="raw-wrap"><div class="line-numbers" id="line-numbers"></div><div class="textarea-wrap"><textarea id="editor" spellcheck="false" ${isBuiltin?'readonly style="opacity:.7"':''}></textarea></div></div>
  `;
  renderSkillFileBar(allFiles); loadSkillFileToEditor(state.currentSkillFile);
}
function renderSkillFileBar(allFiles) {
  const el = document.getElementById("skill-files-bar"); if (!el) return; el.innerHTML = "";
  allFiles.forEach(fp => {
    const cache = state.skillContents[state.currentProfile]?.[state.currentSkill]||{};
    const orig = state.skillOriginals[state.currentProfile]?.[state.currentSkill]||{};
    const modified = (cache[fp]??"") !== (orig[fp]??"");
    const tab = document.createElement("div");
    tab.className = `skill-file-tab ${fp===state.currentSkillFile?"active":""}`;
    tab.style.cssText = modified ? "color:var(--yellow);" : "";
    tab.textContent = fp;
    tab.onclick = async () => {
      state.currentSkillFile = fp;
      if (!cache[fp]) { try { const d = await api(`/api/profile/${state.currentProfile}/skills/${state.currentSkill}/${fp}`); cache[fp]=d.content; state.skillOriginals[state.currentProfile][state.currentSkill][fp]=d.content; } catch(e) { cache[fp]=""; state.skillOriginals[state.currentProfile][state.currentSkill][fp]=""; } }
      renderSkillFileBar(allFiles); loadSkillFileToEditor(fp);
    };
    el.appendChild(tab);
  });
}
function loadSkillFileToEditor(fp) {
  const content = state.skillContents[state.currentProfile]?.[state.currentSkill]?.[fp]??"";
  const ta = document.getElementById("editor"); if (ta) { ta.value = content; ta.oninput = onSkillEditorInput; updateLineNumbers(content); updateSkillSaveButton(); }
  const pathEl = document.getElementById("skill-path"); if (pathEl) pathEl.textContent = `${state.currentProfile}/skills/${state.currentSkill}/${fp}`;
}
function onSkillEditorInput() {
  const ta = document.getElementById("editor"); if (!ta) return;
  state.skillContents[state.currentProfile][state.currentSkill][state.currentSkillFile] = ta.value;
  updateLineNumbers(ta.value); updateSkillSaveButton();
  const skill = (state.skills[state.currentProfile]||[]).find(s => s.name === state.currentSkill);
  if (skill) renderSkillFileBar(["SKILL.md", ...skill.sub_files]);
}
function updateSkillSaveButton() {
  const content = state.skillContents[state.currentProfile]?.[state.currentSkill]?.[state.currentSkillFile]??"";
  const original = state.skillOriginals[state.currentProfile]?.[state.currentSkill]?.[state.currentSkillFile]??"";
  const btn = document.getElementById("btn-save-skill"); if (btn) btn.disabled = content === original;
}
async function saveSkillFile() {
  if (!state.currentProfile||!state.currentSkill||!state.currentSkillFile) return;
  const content = state.skillContents[state.currentProfile][state.currentSkill][state.currentSkillFile];
  const btn = document.getElementById("btn-save-skill");
  await withLoading(btn, async () => {
    const fileKey = `skills/${state.currentSkill}/${state.currentSkillFile}`;
    await confirmDiffSave(fileKey, content, async () => {
      await api(`/api/profile/${state.currentProfile}/skills/${state.currentSkill}/${state.currentSkillFile}`, "PUT", {content});
      state.skillOriginals[state.currentProfile][state.currentSkill][state.currentSkillFile] = content;
      updateSkillSaveButton();
      const skill = (state.skills[state.currentProfile]||[]).find(s => s.name === state.currentSkill);
      if (skill) renderSkillFileBar(["SKILL.md", ...skill.sub_files]);
      toast("Skill 保存成功~","success");
    });
  }).catch(e => toast("保存失败: "+e.message,"error"));
}
async function copySkillFrom() {
  if (!state.currentProfile||!state.currentSkill) return;
  const others = state.profiles.filter(p => p.name !== state.currentProfile);
  if (!others.length) { toast("没有其他 profile 可复制","info"); return; }
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal"><h3>从其他 Profile 复制 Skill「${state.currentSkill}」</h3><select id="copy-src">${others.map(p=>`<option value="${p.name}">${p.name}</option>`).join("")}</select><div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm">复制</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    const src = o.querySelector("#copy-src").value; o.remove();
    try { await api(`/api/profile/${state.currentProfile}/skills/${state.currentSkill}/copy`,"POST",{source_profile:src}); delete state.skills[state.currentProfile]; delete state.skillContents[state.currentProfile]; await renderSkillsView(); toast(`已从 ${src} 复制 skill~`,"success"); }
    catch(e) { toast("复制失败: "+e.message,"error"); }
  };
}
async function copySkillTo(move) {
  if (!state.currentProfile||!state.currentSkill) return;
  const others = state.profiles.filter(p => p.name !== state.currentProfile);
  if (!others.length) { toast("没有其他 profile 可用","info"); return; }
  const verb = move ? "移动" : "复制";
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal"><h3>${verb} Skill「${state.currentSkill}」到</h3><select id="cp-target" style="width:100%;padding:6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--fg)">${others.map(p=>`<option value="${p.name}">${p.name}</option>`).join("")}</select><p style="font-size:12px;color:var(--fg3);margin-top:8px">${move?'移动后源 skill 会移入 .trash/，可恢复。':'目标已存在同名 skill 会先备份到 .trash/。'}</p><div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm">${verb}</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    const target = o.querySelector("#cp-target").value; o.remove();
    try {
      const d = await api(`/api/profile/${state.currentProfile}/skills/${state.currentSkill}/copy-to`,"POST",{target_profile:target, move});
      delete state.skills[state.currentProfile]; delete state.skillContents[state.currentProfile];
      if (move) { state.currentSkill = null; await renderEditor(); }
      else { await renderSkillsView(); }
      toast(`${verb}到 ${target} 成功${d.moved?'（源已移入 .trash）':''}`,"success");
    } catch(e) { toast(`${verb}失败: `+e.message,"error"); }
  };
}
async function deleteSkill() {
  if (!state.currentProfile||!state.currentSkill) return;
  // 判断当前 skill 是否为共享（junction）
  const skill = (state.skills[state.currentProfile]||[]).find(s => s.name === state.currentSkill);
  const isShared = skill && skill.source === "shared";
  const o = document.createElement("div"); o.className = "modal-overlay";
  if (isShared) {
    o.innerHTML = `<div class="modal"><h3>删除共享引用「${state.currentSkill}」？</h3>
      <p style="font-size:13px;color:var(--fg2);line-height:1.6">此技能是<b style="color:var(--accent)">共享 junction</b>，删除仅移除当前 profile 的引用链接。<br>
      <b style="color:var(--green)">共享库内容不受影响</b>，其他 profile 的引用也不受影响。</p>
      <div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm danger">删除引用</button></div></div>`;
  } else {
    o.innerHTML = `<div class="modal"><h3>删除 Skill「${state.currentSkill}」？</h3><p style="font-size:13px;color:var(--fg2)">此操作不可恢复，技能将被永久删除</p><div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm danger">删除</button></div></div>`;
  }
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    o.remove();
    try {
      const d = await api(`/api/profile/${state.currentProfile}/skills/${state.currentSkill}`,"DELETE");
      state.currentSkill=null; delete state.skills[state.currentProfile]; delete state.skillContents[state.currentProfile];
      await renderEditor();
      toast(d.message || "Skill 已删除~","success");
    }
    catch(e) { toast("删除失败: "+e.message,"error"); }
  };
}

// ── 共享技能（junction，共同进化）──
async function extractToShared() {
  if (!state.currentProfile || !state.currentSkill) return;
  const skillName = state.currentSkill;
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal"><h3>抽取到共享技能库？</h3>
    <p style="font-size:13px;color:var(--fg2);line-height:1.6">将 <b style="color:var(--accent)">${skillName}</b> 移动到共享库（AAAHermesHub/shared-skills/），原位置替换为 junction。<br><br>
    <b style="color:var(--yellow)">共同进化</b>：修改共享库中的此 skill 时，所有通过 junction 引用的 profile 会同步生效。<br>
    其他 profile 可从共享库引用此 skill，无需各自复制。</p>
    <div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm">抽取到共享库</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    o.querySelector(".confirm").classList.add("loading");
    try {
      const d = await api("/api/skills/shared/extract","POST",{profile:state.currentProfile,skill_name:skillName});
      o.remove();
      delete state.skills[state.currentProfile]; delete state.skillContents[state.currentProfile];
      await renderEditor();
      toast(d.message||"已抽取到共享库","success");
    } catch(e) {
      o.remove();
      // 内容不同的冲突：提示用户选择 跳过/覆盖
      if (e.data && e.data.differs === true) {
        promptOverwriteExtract(skillName);
      } else {
        toast("抽取失败: "+(e.message||""),"error");
      }
    }
  };
}

// 内容不同时的确认弹窗（单技能）
// force=true 表示用户确认放弃本地版本（移到 .trash），用共享库版本建 junction
function promptOverwriteExtract(skillName) {
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal"><h3>⚠ 共享库已存在 '${skillName}' 且内容不同</h3>
    <p style="font-size:13px;color:var(--fg2);line-height:1.6">共享库中已有同名同分类的 skill，但内容与本地不同。<br><br>
    <b style="color:var(--yellow)">替换为共享库版本</b>：将本地原 skill 移动到 <code>.trash/</code>（可恢复），原位置建立 junction 指向共享库版本。<br>
    <b style="color:var(--fg3)">跳过</b>：不抽取此 skill，保留本地现状。</p>
    <div class="modal-actions"><button class="btn cancel">跳过</button><button class="btn confirm">替换为共享库版本</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    o.querySelector(".confirm").classList.add("loading");
    try {
      const d = await api("/api/skills/shared/extract","POST",{profile:state.currentProfile,skill_name:skillName,force:true});
      o.remove();
      delete state.skills[state.currentProfile]; delete state.skillContents[state.currentProfile];
      await renderEditor();
      toast(d.message||"已替换为共享库版本（本地原版本在 .trash/）","success");
    } catch(e2) { o.remove(); toast("抽取失败: "+e2.message,"error"); }
  };
}

async function unlinkSharedSkill() {
  if (!state.currentProfile || !state.currentSkill) return;
  const skillName = state.currentSkill;
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal"><h3>解除共享？</h3>
    <p style="font-size:13px;color:var(--fg2);line-height:1.6">将 <b style="color:var(--yellow)">${skillName}</b> 的 junction 删除，复制一份独立副本到当前 profile。<br>
    解除后修改此 skill <b>不再影响</b>其他 profile。</p>
    <div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm">解除共享</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    o.querySelector(".confirm").classList.add("loading");
    try {
      const d = await api("/api/skills/shared/unlink","POST",{profile:state.currentProfile,skill_name:skillName});
      o.remove();
      delete state.skills[state.currentProfile]; delete state.skillContents[state.currentProfile];
      await renderEditor();
      toast(d.message||"已解除共享","success");
    } catch(e) { o.remove(); toast("解除失败: "+e.message,"error"); }
  };
}

async function showSharedLibraryModal() {
  if (!state.currentProfile) return;
  const m = document.createElement("div"); m.className = "modal-overlay";
  m.innerHTML = `<div class="modal" style="max-width:560px"><h3>🔗 共享技能库</h3>
    <p style="font-size:12px;color:var(--fg3);margin-bottom:12px">共享库位于 <code style="color:var(--accent2)">AAAHermesHub/shared-skills/</code>。引用到当前 profile 后，修改共享库内容会同步所有引用的 profile（共同进化）。</p>
    <input id="shared-search" type="text" class="input" placeholder="🔍 搜索技能名称或描述..." style="width:100%;margin-bottom:8px;box-sizing:border-box">
    <div id="shared-list" style="max-height:400px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:8px"></div>
    <div class="modal-actions"><span id="shared-count" style="font-size:11px;color:var(--fg3);margin-right:auto"></span><button class="btn cancel">关闭</button></div></div>`;
  document.body.appendChild(m);
  m.querySelector(".cancel").onclick = () => m.remove();
  const listEl = m.querySelector("#shared-list");
  const searchEl = m.querySelector("#shared-search");
  const countEl = m.querySelector("#shared-count");
  listEl.innerHTML = '<div style="color:var(--fg3);text-align:center;padding:16px">加载中...</div>';
  let allSkills = [];
  let allCategories = [];
  function render(filter) {
    const q = (filter||"").toLowerCase().trim();
    const skills = q ? allSkills.filter(s =>
      s.name.toLowerCase().includes(q) || (s.description||"").toLowerCase().includes(q) || (s.category||"").toLowerCase().includes(q)
    ) : allSkills;
    const categories = q ? allCategories.filter(c =>
      c.name.toLowerCase().includes(q) || c.skills.some(sn => sn.toLowerCase().includes(q))
    ) : allCategories;
    const topSkills = skills.filter(s => !s.category);
    const catSkillNames = new Set();
    categories.forEach(c => c.skills.forEach(s => catSkillNames.add(s)));
    countEl.textContent = `共 ${skills.length}${q ? ` / ${allSkills.length}` : ""} 个`;
    if (!skills.length && !categories.length) {
      listEl.innerHTML = q
        ? '<div style="color:var(--fg3);text-align:center;padding:24px">没有匹配的技能</div>'
        : '<div style="color:var(--fg3);text-align:center;padding:24px">共享库为空<br><span style="font-size:11px">在技能详情页点击"抽取到共享库"可将技能加入共享库</span></div>';
      return;
    }
    const currentSkillNames = new Set((state.skills[state.currentProfile]||[]).map(s=>s.name.toLowerCase()));
    const currentCats = new Set((state.skills[state.currentProfile]||[]).filter(s=>s.category).map(s=>s.category.toLowerCase()));
    let html = '';
    // 顶层 skill（无分类）
    topSkills.forEach(s => {
      const conflict = currentSkillNames.has(s.name.toLowerCase());
      const refBadge = s.ref_count > 0 ? `<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:var(--accent-dim);color:var(--accent);font-weight:600;white-space:nowrap">${s.ref_count} 引用</span>` : "";
      html += `<div style="border:1px solid ${conflict?'var(--yellow-dim)':'var(--border)'};border-radius:6px;padding:10px;margin-bottom:8px;display:flex;align-items:center;gap:10px">
        <div style="flex:1">
          <div style="font-weight:600;color:var(--fg);font-size:13px">${s.name}</div>
          <div style="font-size:11px;color:var(--fg3);margin-top:2px">${s.description||'无描述'} · ${s.modified}</div>
        </div>
        ${refBadge}
        ${conflict ? '<span style="font-size:10px;padding:2px 8px;border-radius:4px;background:var(--yellow-dim);color:var(--yellow);font-weight:600;white-space:nowrap">已存在</span>'
          : `<button class="btn" style="font-size:12px;padding:4px 10px" onclick="linkSharedSkill('${s.name}', this)">引用</button>`}
        <button class="btn danger" style="font-size:11px;padding:3px 8px" onclick="deleteSharedSkill('${s.name}', this)" title="从共享库删除（移到 .trash，各 profile 获得独立副本）">删除</button>
      </div>`;
    });
    // 分类分组
    categories.forEach(cat => {
      const catSkillsFiltered = skills.filter(s => s.category === cat.name);
      if (catSkillsFiltered.length === 0 && q) return;
      const catExists = currentCats.has(cat.name.toLowerCase());
      const catRefBadge = cat.ref_count > 0 ? `<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:var(--accent-dim);color:var(--accent);font-weight:600;white-space:nowrap">${cat.ref_count} 引用</span>` : "";
      html += `<div style="margin-top:12px;margin-bottom:6px;padding:8px 10px;background:var(--bg2);border-radius:6px;display:flex;align-items:center;gap:8px">
        <div style="flex:1">
          <span style="font-weight:700;color:var(--fg);font-size:13px">📁 ${cat.name}</span>
          <span style="font-size:11px;color:var(--fg3);margin-left:6px">${cat.skill_count} 个子 skill</span>
        </div>
        ${catRefBadge}
        ${catExists
          ? '<span style="font-size:10px;padding:2px 8px;border-radius:4px;background:var(--green-dim);color:var(--green);font-weight:600;white-space:nowrap">已引用</span>'
          : `<button class="btn" style="font-size:11px;padding:3px 10px" onclick="linkSharedCategory('${cat.name}', this)" title="引用整个分类（分类级 junction，所有子 skill 同步可用）">引用整个分类</button>`}
      </div>`;
      catSkillsFiltered.forEach(s => {
        const conflict = currentSkillNames.has(s.name.toLowerCase());
        const refBadge = s.ref_count > 0 ? `<span style="font-size:9px;padding:1px 5px;border-radius:4px;background:var(--accent-dim);color:var(--accent);font-weight:600;white-space:nowrap">${s.ref_count} 引用</span>` : "";
        html += `<div style="border:1px solid ${conflict?'var(--yellow-dim)':'var(--border)'};border-radius:6px;padding:8px 10px;margin-bottom:6px;margin-left:20px;display:flex;align-items:center;gap:8px">
          <div style="flex:1">
            <div style="font-weight:600;color:var(--fg);font-size:12px">${s.name}</div>
            <div style="font-size:10px;color:var(--fg3);margin-top:1px">${s.description||'无描述'}</div>
          </div>
          ${refBadge}
          ${conflict ? '<span style="font-size:9px;padding:2px 6px;border-radius:4px;background:var(--yellow-dim);color:var(--yellow);font-weight:600;white-space:nowrap">已存在</span>'
            : `<button class="btn" style="font-size:11px;padding:3px 8px" onclick="linkSharedSkill('${s.name}', this)">引用</button>`}
          <button class="btn danger" style="font-size:10px;padding:2px 6px" onclick="deleteSharedSkill('${s.name}', this)" title="从共享库删除">删除</button>
        </div>`;
      });
    });
    listEl.innerHTML = html;
  }
  searchEl.addEventListener("input", () => render(searchEl.value));
  try {
    const d = await api("/api/skills/shared/list");
    allSkills = d.skills || [];
    allCategories = d.categories || [];
    render("");
    if (allSkills.length) searchEl.focus();
  } catch(e) {
    listEl.innerHTML = `<div style="color:var(--red);padding:16px">加载失败: ${e.message}</div>`;
  }
}

async function linkSharedCategory(name, btn) {
  if (!state.currentProfile) return;
  btn.classList.add("loading"); btn.disabled = true;
  try {
    const d = await api("/api/skills/shared/link","POST",{profile:state.currentProfile,skill_name:name,link_category:true});
    toast(d.message||`已引用分类 ${name}`,"success");
    const card = btn.parentElement;
    if (card) {
      const badge = document.createElement("span");
      badge.style.cssText = "font-size:10px;padding:2px 8px;border-radius:4px;background:var(--green-dim);color:var(--green);font-weight:600;white-space:nowrap";
      badge.textContent = "✓ 已引用";
      btn.replaceWith(badge);
    }
    delete state.skills[state.currentProfile]; delete state.skillContents[state.currentProfile];
    await renderEditor();
    showSharedLibraryModal();
  } catch(e) {
    btn.classList.remove("loading"); btn.disabled = false;
    toast("引用分类失败: "+e.message,"error");
  }
}

async function linkSharedSkill(name, btn) {
  if (!state.currentProfile) return;
  btn.classList.add("loading"); btn.disabled = true;
  try {
    const d = await api("/api/skills/shared/link","POST",{profile:state.currentProfile,skill_name:name});
    toast(d.message||`已引用 ${name}`,"success");
    // 就地更新按钮为"已引用"绿标（用户能立即看到变化）
    const card = btn.parentElement;
    if (card) {
      const badge = document.createElement("span");
      badge.style.cssText = "font-size:10px;padding:2px 8px;border-radius:4px;background:var(--green-dim);color:var(--green);font-weight:600;white-space:nowrap";
      badge.textContent = "✓ 已引用";
      btn.replaceWith(badge);
    }
    // 刷新主界面技能列表缓存
    delete state.skills[state.currentProfile]; delete state.skillContents[state.currentProfile];
    await renderEditor();
  } catch(e) {
    btn.classList.remove("loading"); btn.disabled = false;
    toast("引用失败: "+e.message,"error");
  }
}

async function deleteSharedSkill(name, btn) {
  // 弹窗确认
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal"><h3>从共享库删除「${name}」？</h3>
    <p style="font-size:13px;color:var(--fg2);line-height:1.6">共享库中的此技能将移到 <code>.trash/</code>（可恢复）。<br>
    所有引用此技能的 profile 将获得<b>独立副本</b>（不再共同进化）。</p>
    <div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm danger">从共享库删除</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    o.remove();
    if (btn) { btn.classList.add("loading"); btn.disabled = true; }
    try {
      const d = await api("/api/skills/shared/delete","POST",{skill_name:name});
      toast(d.message||`已从共享库删除 ${name}`,"success");
      // 刷新共享库弹窗 + 主界面技能列表
      delete state.skills[state.currentProfile]; delete state.skillContents[state.currentProfile];
      await renderEditor();
      // 重新加载共享库弹窗
      showSharedLibraryModal();
    } catch(e) {
      if (btn) { btn.classList.remove("loading"); btn.disabled = false; }
      toast("删除失败: "+e.message,"error");
    }
  };
}

// ── 分类批量操作 ──
async function batchExtractCategory(cat) {
  if (!state.currentProfile) return;
  const skills = (state.skills[state.currentProfile]||[]).filter(s => s.category === cat && s.location === "user" && s.source !== "shared");
  if (skills.length === 0) return;
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal"><h3>批量抽取分类"${cat}"到共享库？</h3>
    <p style="font-size:13px;color:var(--fg2);line-height:1.6">将以下 <b style="color:var(--accent)">${skills.length}</b> 个技能全部抽取到共享库，原位置替换为 junction（共同进化）：<br>
    <code style="font-size:11px;color:var(--fg3)">${skills.map(s=>s.name).join(", ")}</code></p>
    <div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm">全部抽取</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    o.remove();  // 关闭弹窗，改用阻塞遮罩
    let ok = 0, skip = 0, fail = 0;
    const errs = [], skipped = [];
    showBlockingLoader("正在抽取分类技能到共享库...", `0 / ${skills.length}`);
    for (let i = 0; i < skills.length; i++) {
      const s = skills[i];
      updateBlockingLoader(`正在抽取: ${s.name}`, `${i + 1} / ${skills.length}`);
      try {
        await api("/api/skills/shared/extract","POST",{profile:state.currentProfile,skill_name:s.name});
        ok++;
      } catch(e) {
        if (e.status === 400 && (e.message.includes("已是共享") || e.message.includes("分类级"))) {
          skip++; skipped.push(s.name);
        } else if (e.data && e.data.differs === true) { skip++; skipped.push(s.name); }
        else { fail++; errs.push(`${s.name}: ${e.message}`); }
      }
    }
    hideBlockingLoader();
    delete state.skills[state.currentProfile]; delete state.skillContents[state.currentProfile];
    await renderEditor();
    if (fail === 0 && skip === 0) toast(`已抽取 ${ok} 个技能到共享库`,"success");
    else {
      const parts = [`成功 ${ok}`];
      if (skip > 0) parts.push(`跳过 ${skip}（内容不同: ${skipped.join(", ")}）`);
      if (fail > 0) parts.push(`失败 ${fail}（${errs.join("; ")}）`);
      toast(parts.join("，"), skip > 0 && fail === 0 ? "info" : "error");
    }
  };
}

async function batchDeleteCategory(cat) {
  if (!state.currentProfile) return;
  const skills = (state.skills[state.currentProfile]||[]).filter(s => s.category === cat && s.source !== "builtin");
  if (skills.length === 0) return;
  const sharedCount = skills.filter(s => s.source === "shared").length;
  const normalCount = skills.length - sharedCount;
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal"><h3>批量删除分类"${cat}"下的技能？</h3>
    <p style="font-size:13px;color:var(--fg2);line-height:1.6">将删除以下 <b style="color:var(--red)">${skills.length}</b> 个技能：<br>
    <code style="font-size:11px;color:var(--fg3);display:block;margin:8px 0;max-height:80px;overflow-y:auto">${skills.map(s => `[${s.source}] ${s.name}`).join(", ")}</code>
    ${sharedCount > 0 ? `<br><b style="color:var(--yellow)">共享技能</b>（${sharedCount} 个）：仅删除当前 profile 的 junction，共享库内容不受影响。<br>` : ""}
    ${normalCount > 0 ? `<br><b style="color:var(--fg)">非共享技能</b>（${normalCount} 个）：直接删除，不可恢复。` : ""}</p>
    <div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm danger">全部删除</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    o.remove();
    let ok = 0, fail = 0;
    const errs = [];
    let catJunctionDeleted = false;
    showBlockingLoader("正在删除技能...", `0 / ${skills.length}`);
    for (let i = 0; i < skills.length; i++) {
      const s = skills[i];
      if (catJunctionDeleted) { ok++; continue; }
      updateBlockingLoader(`正在删除: ${s.name}`, `${i + 1} / ${skills.length}`);
      try {
        const d = await api(`/api/profile/${state.currentProfile}/skills/${s.name}`, "DELETE");
        ok++;
        if (d.category_junction) catJunctionDeleted = true;
      } catch(e) {
        if (catJunctionDeleted) { ok++; continue; }
        fail++; errs.push(`${s.name}: ${e.message}`);
      }
    }
    hideBlockingLoader();
    state.currentSkill = null;
    delete state.skills[state.currentProfile]; delete state.skillContents[state.currentProfile];
    await renderEditor();
    if (fail === 0) {
      if (catJunctionDeleted) toast(`已删除分类"${cat}"的共享引用（共享库内容未删除）`, "success");
      else toast(`已删除 ${ok} 个技能` + (sharedCount > 0 ? `（其中 ${sharedCount} 个仅删除引用）` : ""), "success");
    } else {
      const parts = [`成功 ${ok}`];
      if (fail > 0) parts.push(`失败 ${fail}（${errs.join("; ")}）`);
      toast(parts.join("，"), "error");
    }
  };
}

async function batchUnlinkCategory(cat) {
  if (!state.currentProfile) return;
  const skills = (state.skills[state.currentProfile]||[]).filter(s => s.category === cat && s.source === "shared");
  if (skills.length === 0) return;
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal"><h3>批量解除分类"${cat}"的共享？</h3>
    <p style="font-size:13px;color:var(--fg2);line-height:1.6">将以下 <b style="color:var(--yellow)">${skills.length}</b> 个共享技能全部解除，复制为独立副本（断开共同进化）：<br>
    <code style="font-size:11px;color:var(--fg3)">${skills.map(s=>s.name).join(", ")}</code></p>
    <div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm">全部解除</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    const btn = o.querySelector(".confirm");
    btn.classList.add("loading");
    try {
      // 调用后端批量 unlink 路由（一次请求，统一服务端日志）
      const d = await api("/api/skills/shared/unlink-batch","POST",{
        profile: state.currentProfile,
        skill_names: skills.map(s => s.name),
      });
      o.remove();
      delete state.skills[state.currentProfile]; delete state.skillContents[state.currentProfile];
      await renderEditor();
      if (d.failed_count === 0) toast(d.message || `已解除 ${d.succeeded_count} 个技能的共享`,"success");
      else {
        const errs = (d.failed||[]).map(f => `${f.skill}: ${f.error}`).join("; ");
        toast(`成功 ${d.succeeded_count}，失败 ${d.failed_count}：${errs}`,"error");
      }
    } catch(e) { o.remove(); toast("批量解除失败: "+e.message,"error"); }
  };
}

// ── 共享技能 checkbox 多选 + 跨分类批量解除 ──
function updateSharedSelectionBar() {
  const bar = document.getElementById("shared-selection-bar");
  if (!bar) return;
  const allShared = [...document.querySelectorAll(".shared-select")];
  const checked = allShared.filter(c => c.checked);
  const names = checked.map(c => c.dataset.skill);
  const totalShared = allShared.length;
  if (totalShared === 0) {
    bar.style.display = "none"; bar.innerHTML = ""; return;
  }
  bar.style.display = "flex";
  const allChecked = checked.length === totalShared;
  bar.innerHTML = `<span style="color:var(--yellow);font-weight:600;font-size:11px">${names.length > 0 ? `已选 ${names.length}/${totalShared}` : `共 ${totalShared} 个共享`}</span>
    <button class="btn ghost" style="height:22px;padding:0 8px;font-size:10px" onclick="${allChecked ? 'clearSharedSelection()' : 'selectAllShared()'}">${allChecked ? '全不选' : '全选共享'}</button>
    ${names.length > 0 ? `<button class="btn" style="height:22px;padding:0 8px;font-size:10px" onclick="batchUnlinkSelected()">解除所选(${names.length})</button>` : ''}
    ${names.length > 0 ? `<button class="btn ghost" style="height:22px;padding:0 8px;font-size:10px" onclick="clearSharedSelection()">取消选择</button>` : ''}`;
}
function selectAllShared() {
  document.querySelectorAll(".shared-select").forEach(c => c.checked = true);
  updateSharedSelectionBar();
}
function clearSharedSelection() {
  document.querySelectorAll(".shared-select:checked").forEach(c => c.checked = false);
  updateSharedSelectionBar();
}
async function batchUnlinkSelected() {
  if (!state.currentProfile) return;
  const names = [...document.querySelectorAll(".shared-select:checked")].map(c => c.dataset.skill);
  if (names.length === 0) return;
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal"><h3>批量解除 ${names.length} 个技能的共享？</h3>
    <p style="font-size:13px;color:var(--fg2);line-height:1.6">将以下勾选的共享技能解除 junction，复制为独立副本（断开共同进化）：<br>
    <code style="font-size:11px;color:var(--fg3)">${names.join(", ")}</code></p>
    <div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm">全部解除</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    const btn = o.querySelector(".confirm");
    btn.classList.add("loading");
    try {
      const d = await api("/api/skills/shared/unlink-batch","POST",{
        profile: state.currentProfile, skill_names: names,
      });
      o.remove();
      delete state.skills[state.currentProfile]; delete state.skillContents[state.currentProfile];
      await renderEditor();
      if (d.failed_count === 0) toast(d.message || `已解除 ${d.succeeded_count} 个技能的共享`,"success");
      else {
        const errs = (d.failed||[]).map(f => `${f.skill}: ${f.error}`).join("; ");
        toast(`成功 ${d.succeeded_count}，失败 ${d.failed_count}：${errs}`,"error");
      }
    } catch(e) { o.remove(); toast("批量解除失败: "+e.message,"error"); }
  };
}

// ── 保存前 diff 预览 ──
// 保存 raw 文本前先对比磁盘内容，有差异时弹窗展示 unified diff 让用户确认。
// file_key: "config.yaml" | ".env" | "skills/<skill>/<file>"
async function confirmDiffSave(file_key, content, onConfirm) {
  const profile = state.currentProfile;
  if (!profile) { onConfirm(); return; }  // 无 profile → 跳过预览
  try {
    const d = await api(`/api/profile/${profile}/diff/${file_key}`, "POST", { content });
    if (!d.changed) { onConfirm(); return; }  // 无变化 → 直接保存
    // 显示 diff 弹窗
    const o = document.createElement("div"); o.className = "modal-overlay";
    const diffLines = d.diff.split("\n").map(l => {
      let cls = "", sym = "";
      if (l.startsWith("@@")) { cls = "color:var(--accent2);"; sym = " "; }
      else if (l.startsWith("+")) { cls = "color:var(--green);background:rgba(0,255,0,0.05);"; sym = "+"; }
      else if (l.startsWith("-")) { cls = "color:var(--red);background:rgba(255,0,0,0.05);"; sym = "-"; }
      else if (l.startsWith("---") || l.startsWith("+++")) { cls = "color:var(--fg3);font-weight:600;"; sym = " "; }
      else { cls = "color:var(--fg2);"; sym = " "; }
      return `<div style="white-space:pre;font-family:Consolas,monospace;font-size:11px;line-height:1.5;padding:0 6px;${cls}">${sym} ${escHtml(l)}</div>`;
    }).join("");
    o.innerHTML = `<div class="modal" style="max-width:700px;max-height:80vh;display:flex;flex-direction:column">
      <h3>📝 变更预览 — ${escHtml(file_key)}</h3>
      <p style="font-size:12px;color:var(--fg3);margin-bottom:8px">
        <span style="color:var(--green)">+${d.added} 行</span> &nbsp;
        <span style="color:var(--red)">-${d.removed} 行</span>
      </p>
      <div style="overflow-y:auto;flex:1;max-height:55vh;border:1px solid var(--border);border-radius:6px;background:var(--bg);padding:4px 0">${diffLines}</div>
      <div class="modal-actions" style="margin-top:12px">
        <button class="btn cancel">取消</button>
        <button class="btn confirm">确认保存</button>
      </div></div>`;
    document.body.appendChild(o);
    o.querySelector(".cancel").onclick = () => o.remove();
    o.querySelector(".confirm").onclick = () => { o.remove(); onConfirm(); };
  } catch(e) { toast("diff 检查失败: "+e.message, "warn"); onConfirm(); }  // 降级：直接保存
}
function escHtml(s) { const d=document.createElement("div"); d.textContent=s; return d.innerHTML; }

// ── 一键抽取当前 profile 技能（按来源类型勾选 + 冲突预选项）──
async function batchExtractAll() {
  if (!state.currentProfile) return;
  // 刷新技能列表，确保获取最新的 source 状态（避免用旧缓存抽取已变为 junction 的技能）
  delete state.skills[state.currentProfile];
  try { const d = await api(`/api/profile/${state.currentProfile}/skills`); state.skills[state.currentProfile] = d.skills; }
  catch(e) { state.skills[state.currentProfile] = []; }
  // 候选 = 非共享（shared 已是 junction 或分类级 junction，跳过）
  const all = (state.skills[state.currentProfile]||[]).filter(s => s.source !== "shared");
  if (all.length === 0) {
    toast("当前 profile 没有可抽取的技能（仅共享 junction 不可重复抽取）","info");
    return;
  }
  // 按来源类型分组统计：builtin=内置, user=内置→用户, custom=自定义
  const typeMap = [
    { key: "builtin", label: "内置", color: "var(--purple)", desc: "hermes-agent/skills 中的只读技能；抽取时在 profile/skills/ 下建 junction 遮蔽，hermes-agent 不动", defaultChecked: false },
    { key: "user",    label: "内置→用户", color: "var(--green)",  desc: "用户修改过的内置技能（同名但 SKILL.md 内容不同）", defaultChecked: true },
    { key: "custom",  label: "自定义", color: "var(--accent)", desc: "用户自建或新装的技能（内置库不存在）", defaultChecked: true },
  ];
  // 收集每个类型的 skill 列表
  const byType = {};
  all.forEach(s => { (byType[s.source] ||= []).push(s); });
  // 渲染类型行（只显示当前 profile 实际存在的类型）
  const typeRows = typeMap.filter(t => (byType[t.key]||[]).length > 0).map(t => {
    const items = byType[t.key] || [];
    const checked = t.defaultChecked ? "checked" : "";
    return `<label style="display:flex;align-items:flex-start;gap:10px;padding:10px 6px;border-bottom:1px solid var(--border);cursor:pointer">
      <input type="checkbox" class="type-extract-cb" data-type="${t.key}" ${checked} style="margin-top:3px">
      <div style="flex:1">
        <div><b style="color:${t.color}">${t.label}</b> <span style="color:var(--fg3);font-size:11px">(${items.length})</span></div>
        <div style="font-size:11px;color:var(--fg3);margin-top:2px">${t.desc}</div>
        <div style="font-size:10px;color:var(--fg3);margin-top:4px;max-height:60px;overflow:auto">${items.map(s=>s.name).join(" · ")}</div>
      </div>
    </label>`;
  }).join("");
  if (!typeRows) {
    toast("没有可抽取的技能类型","info");
    return;
  }
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal" style="max-width:560px"><h3>📦 一键抽取到共享库</h3>
    <p style="font-size:13px;color:var(--fg2);line-height:1.5">从 <b style="color:var(--accent)">${state.currentProfile}</b> 抽取选中类型的技能到 <code style="font-size:11px;color:var(--accent2)">AAAHermesHub/shared-skills/</code>，原位置替换为 junction（共同进化）。</p>
    <div style="display:flex;align-items:center;gap:12px;margin:8px 0 4px;font-size:12px">
      <label style="cursor:pointer"><input type="checkbox" id="type-extract-all"> 全选</label>
    </div>
    <div style="max-height:280px;overflow:auto;border:1px solid var(--border);border-radius:6px;padding:4px 8px;margin:6px 0">${typeRows}</div>
    <label style="display:flex;align-items:flex-start;gap:8px;font-size:12px;color:var(--fg2);margin:8px 0;cursor:pointer">
      <input type="checkbox" id="type-extract-overwrite" style="margin-top:2px">
      <span>内容不同的冲突<b style="color:var(--yellow)">替换为共享库版本</b>（默认跳过；替换会将本地原 skill 移到 <code>.trash/</code> 可恢复，原位置建 junction 指向共享库版本）</span>
    </label>
    <p style="font-size:11px;color:var(--fg3);margin:4px 0">ℹ 内容相同的冲突会自动合并（本地移到 .trash + 建 junction）；"内置"类型抽取会在 profile/skills/ 下建 junction 遮蔽内置，hermes-agent 不动。</p>
    <div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm">抽取选中</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  const cbEls = () => Array.from(o.querySelectorAll(".type-extract-cb"));
  const allToggle = o.querySelector("#type-extract-all");
  const syncAllToggle = () => {
    const cbs = cbEls(); allToggle.checked = cbs.length > 0 && cbs.every(c => c.checked);
  };
  cbEls().forEach(cb => cb.addEventListener("change", syncAllToggle));
  allToggle.addEventListener("change", e => {
    cbEls().forEach(cb => cb.checked = e.target.checked);
  });
  o.querySelector(".confirm").onclick = async () => {
    const btn = o.querySelector(".confirm");
    btn.classList.add("loading"); btn.disabled = true;
    const overwrite = o.querySelector("#type-extract-overwrite").checked;
    const selectedTypes = new Set(cbEls().filter(c => c.checked).map(c => c.dataset.type));
    const targets = all.filter(s => selectedTypes.has(s.source));
    if (targets.length === 0) { o.remove(); toast("未勾选任何类型","info"); return; }
    let ok = 0, skip = 0, fail = 0;
    const errs = [], skipped = [];
    o.remove();  // 关闭弹窗，改用阻塞遮罩
    showBlockingLoader("正在抽取技能到共享库...", `0 / ${targets.length}`);
    for (let i = 0; i < targets.length; i++) {
      const s = targets[i];
      updateBlockingLoader(`正在抽取: ${s.name}`, `${i + 1} / ${targets.length}`);
      try {
        await api("/api/skills/shared/extract","POST",{profile:state.currentProfile,skill_name:s.name,force:false});
        ok++;
      } catch(e) {
        if (e.status === 400 && (e.message.includes("已是共享") || e.message.includes("分类级"))) {
          // 已是共享 junction（含分类级）→ 跳过，不算失败
          skip++; skipped.push(s.name);
        } else if (e.data && e.data.differs === true) {
          if (overwrite) {
            try {
              await api("/api/skills/shared/extract","POST",{profile:state.currentProfile,skill_name:s.name,force:true});
              ok++;
            } catch(e2) { fail++; errs.push(`${s.name}: ${e2.message}`); }
          } else { skip++; skipped.push(s.name); }
        } else { fail++; errs.push(`${s.name}: ${e.message}`); }
      }
    }
    hideBlockingLoader();
    delete state.skills[state.currentProfile]; delete state.skillContents[state.currentProfile];
    await renderEditor();
    if (fail === 0 && skip === 0) toast(`已抽取 ${ok} 个技能到共享库`,"success");
    else {
      const parts = [`成功 ${ok}`];
      if (skip > 0) parts.push(`跳过 ${skip}（内容不同: ${skipped.join(", ")}）`);
      if (fail > 0) parts.push(`失败 ${fail}（${errs.join("; ")}）`);
      toast(parts.join("，"), skip > 0 && fail === 0 ? "info" : "error");
    }
  };
}

// ── new profile ──
function deleteProfile(name) {
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal"><h3>删除 Profile "${name}"？</h3><p style="font-size:13px;color:var(--fg2);margin-bottom:8px">将移入 .trash/ 目录，可手动恢复。此操作不可在 UI 中撤销。</p><div class="modal-actions"><button class="btn cancel">取消</button><button class="btn confirm danger">删除</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    o.remove();
    try {
      await api(`/api/profile/${name}`, "DELETE");
      if (state.currentProfile === name) state.currentProfile = null;
      await loadProfiles();
      toast(`Profile "${name}" 已删除~`, "success");
    } catch(e) { toast("删除失败: " + e.message, "error"); }
  };
}

// ── refresh / poll ──
async function reloadAll() {
  state.fileContents={}; state.originalContents={}; state.skills={}; state.skillContents={}; state.skillOriginals={};
  state.configData={}; state.configOriginal={}; state.envData={}; state.envOriginal={};
  await loadProfiles(); if (state.currentProfile) await renderEditor();
  toast("已刷新~","info");
}
let pollTimer = null;
function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const data = await api("/api/poll");
      const el = document.getElementById("poll-status"); if (el) el.textContent = `同步 ${new Date().toLocaleTimeString()}`;
      if (data.changes?.length > 0) {
        for (const c of data.changes) {
          if (c.profile === state.currentProfile && c.file === state.currentFile) {
            const isMod = (() => {
              if (c.file === "config.yaml") return isConfigModified();
              if (c.file === ".env") return isEnvModified();
              return (state.fileContents[c.profile]?.[c.file]??"") !== (state.originalContents[c.profile]?.[c.file]??"");
            })();
            if (!isMod) {
              // reload
              delete state.fileContents[c.profile]; delete state.configData[c.profile]; delete state.configOriginal[c.profile]; delete state.envData[c.profile]; delete state.envOriginal[c.profile];
              await renderEditor();
              toast(`${c.file} 被外部修改，已自动同步~`,"info");
            } else { toast(`${c.profile}/${c.file} 被外部修改，但你有未保存的改动~`,"error"); }
          } else { delete state.fileContents[c.profile]; }
        }
      }
    } catch(e) {}
  }, 3000);
}

// toast：6s 自动消失（原 3s 的 2 倍）；鼠标悬停时暂停消失，移开后 2s 再消失
function toast(msg, type="info") {
  const c = document.getElementById("toast-container"); const el = document.createElement("div");
  el.className = `toast ${type}`; el.textContent = msg; c.appendChild(el);
  let timer = null;
  const dismiss = () => { el.style.opacity = "0"; setTimeout(() => el.remove(), 300); };
  const start = (ms) => { if (timer) clearTimeout(timer); timer = setTimeout(dismiss, ms); };
  start(6000);
  // 鼠标悬停 → 清除定时器保留显示；移开 → 2s 后消失
  el.addEventListener("mouseenter", () => { if (timer) { clearTimeout(timer); timer = null; } });
  el.addEventListener("mouseleave", () => start(2000));
}

// ── 阻塞式加载遮罩：批量操作时禁止所有界面交互 ──
function showBlockingLoader(msg, progress) {
  let o = document.getElementById("blocking-overlay");
  if (!o) {
    o = document.createElement("div");
    o.id = "blocking-overlay";
    o.className = "blocking-overlay";
    o.innerHTML = `<div class="box"><div class="spinner"></div><div class="msg"></div><div class="progress" style="display:none"></div></div>`;
    document.body.appendChild(o);
  }
  o.querySelector(".msg").textContent = msg || "处理中...";
  const p = o.querySelector(".progress");
  if (progress) { p.textContent = progress; p.style.display = "block"; }
  else { p.style.display = "none"; }
  o.style.display = "flex";
}
function updateBlockingLoader(msg, progress) {
  const o = document.getElementById("blocking-overlay");
  if (!o) return;
  if (msg) o.querySelector(".msg").textContent = msg;
  const p = o.querySelector(".progress");
  if (progress) { p.textContent = progress; p.style.display = "block"; }
}
function hideBlockingLoader() {
  const o = document.getElementById("blocking-overlay");
  if (o) o.style.display = "none";
}

// ── 操作日志查看器 ──
const _logActionLabels = {
  extract:        { label: "抽取到共享库",   color: "var(--accent)" },
  link_shared:    { label: "引用共享",       color: "var(--green)" },
  unlink_shared:  { label: "解除共享",       color: "var(--yellow)" },
  delete_shared:  { label: "删除共享",       color: "var(--red)" },
  delete_skill:   { label: "删除技能",       color: "var(--red)" },
  copy_skill:     { label: "复制技能",       color: "var(--accent2)" },
  copy_skill_to:  { label: "跨 profile 复制",color: "var(--accent2)" },
  save_skill:     { label: "保存技能文件",   color: "var(--fg2)" },
  save_file:      { label: "保存配置文件",   color: "var(--fg2)" },
  backup:         { label: "备份",           color: "var(--green)" },
  restore:        { label: "恢复",           color: "var(--yellow)" },
  cleanup_backups:{ label: "清理备份",       color: "var(--fg3)" },
  fix_junctions:  { label: "修复链接",       color: "var(--accent)" },
};
async function showLogsModal() {
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal" style="max-width:760px;max-height:80vh;display:flex;flex-direction:column">
    <h3>📋 操作日志</h3>
    <div id="logs-loading" style="text-align:center;padding:20px;color:var(--fg3)">加载中...</div>
    <div id="logs-body" style="flex:1;overflow:auto;min-height:200px;display:none"></div>
    <div class="modal-actions">
      <span id="logs-path" style="font-size:10px;color:var(--fg3);margin-right:auto;max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>
      <button class="btn" id="logs-refresh">↻ 刷新</button>
      <button class="btn danger" id="logs-clear">清空</button>
      <button class="btn cancel">关闭</button>
    </div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  const body = o.querySelector("#logs-body");
  const pathEl = o.querySelector("#logs-path");
  async function loadLogs() {
    body.style.display = "none";
    o.querySelector("#logs-loading").style.display = "block";
    try {
      const d = await api("/api/logs/operations?limit=200");
      o.querySelector("#logs-loading").style.display = "none";
      body.style.display = "block";
      pathEl.textContent = d.path || "";
      if (!d.logs || d.logs.length === 0) {
        body.innerHTML = `<div style="text-align:center;padding:40px;color:var(--fg3)">暂无操作日志</div>`;
        return;
      }
      // 按日期分组渲染
      const rows = d.logs.map(e => {
        const meta = _logActionLabels[e.action] || { label: e.action, color: "var(--fg2)" };
        const isError = e.result === "error";
        const parts = [];
        if (e.profile) parts.push(`<span style="color:var(--accent)">${e.profile}</span>`);
        if (e.skill) parts.push(`<b>${e.skill}</b>`);
        if (e.target_profile) parts.push(`→ <span style="color:var(--accent)">${e.target_profile}</span>`);
        if (e.source_profile) parts.push(`← <span style="color:var(--accent)">${e.source_profile}</span>`);
        if (e.file) parts.push(`<code style="font-size:11px">${e.file}</code>`);
        if (e.conflict) parts.push(`<span style="color:var(--yellow);font-size:11px">[${e.conflict}]</span>`);
        if (e.force) parts.push(`<span style="color:var(--yellow);font-size:11px">force</span>`);
        if (e.fixed !== undefined) parts.push(`修复 ${e.fixed} 个`);
        if (e.deleted && Array.isArray(e.deleted)) parts.push(`删除 ${e.deleted.length} 项`);
        if (e.detail) parts.push(`<span style="color:var(--fg3);font-size:11px">${e.detail}</span>`);
        if (isError && e.error) parts.push(`<span style="color:var(--red);font-size:11px">❌ ${e.error}</span>`);
        // 恢复提示：含 trash 字段
        let recovery = "";
        if (e.trash) recovery = `<div style="font-size:10px;color:var(--fg3);margin-top:2px">↩ 可恢复: <code>${e.trash}</code></div>`;
        return `<div style="padding:8px 10px;border-bottom:1px solid var(--border);font-size:12px;${isError ? "background:var(--red-dim)" : ""}">
          <div style="display:flex;align-items:center;gap:8px">
            <span style="color:var(--fg3);font-size:11px;min-width:140px">${e.time}</span>
            <span style="color:${meta.color};font-weight:600;min-width:90px">${meta.label}</span>
            <span style="flex:1">${parts.join(" ") || "<span style='color:var(--fg3)'>-</span>"}</span>
          </div>${recovery}</div>`;
      }).join("");
      body.innerHTML = `<div style="font-size:11px;color:var(--fg3);padding:6px 10px;border-bottom:2px solid var(--border)">共 ${d.total} 条，显示最新 ${d.logs.length} 条（最新在前）</div>${rows}`;
    } catch(e) {
      o.querySelector("#logs-loading").textContent = "加载失败: " + e.message;
    }
  }
  o.querySelector("#logs-refresh").onclick = loadLogs;
  o.querySelector("#logs-clear").onclick = async () => {
    if (!confirm("确认清空所有操作日志？此操作不可恢复。")) return;
    try {
      await api("/api/logs/operations","DELETE");
      await loadLogs();
      toast("操作日志已清空","success");
    } catch(e) { toast("清空失败: " + e.message,"error"); }
  };
  await loadLogs();
}

document.addEventListener("keydown", e => {
  // Ctrl/Cmd+S：保存当前视图
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    if (state.currentView === "files") {
      if (state.currentFile === "config.yaml" && state.configMode === "form") saveConfig();
      else if (state.currentFile === "config.yaml" && state.configMode === "raw") saveConfigRaw();
      else if (state.currentFile === ".env" && state.envMode === "form") saveEnv();
      else if (state.currentFile === ".env" && state.envMode === "raw") saveEnvRaw();
      else saveMdFile();
    } else saveSkillFile();
    return;
  }
  // Ctrl/Cmd+B：折叠/展开侧栏
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") {
    e.preventDefault();
    toggleSidebar();
    return;
  }
  // Ctrl/Cmd+R / F5：刷新所有数据（覆盖浏览器默认刷新）
  if (((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "r") || e.key === "F5") {
    e.preventDefault();
    reloadAll();
    return;
  }
});

// ── 备份 / 恢复 / 清理 ─────────────────────────────────
let _backupsCache = [];  // 缓存备份列表，供恢复 modal 用

async function loadBackups() {
  try {
    const d = await api("/api/backups");
    _backupsCache = d.backups || [];
    const badge = document.getElementById("backup-count");
    if (_backupsCache.length > 0) {
      badge.textContent = `📦 ${_backupsCache.length}`;
      badge.style.display = "";
      badge.title = `${_backupsCache.length} 个备份\n最新: ${_backupsCache[0].readable}`;
    } else {
      badge.style.display = "none";
    }
  } catch(e) { console.error("load backups failed", e); }
}

async function doBackup(btn) {
  const o = document.createElement("div"); o.className = "modal-overlay";
  o.innerHTML = `<div class="modal"><h3>备份所有 Profile？</h3>
    <p style="font-size:13px;color:var(--fg2);line-height:1.6">将备份当前所有 profile 的 <code>config.yaml</code> + <code>.env</code> 到 <code style="color:var(--accent2)">AAAHermesHub/backups/&lt;时间戳&gt;/</code>。</p>
    <div class="modal-actions"><button class="btn cancel">取消</button><button class="btn primary confirm">确认备份</button></div></div>`;
  document.body.appendChild(o);
  o.querySelector(".cancel").onclick = () => o.remove();
  o.querySelector(".confirm").onclick = async () => {
    o.querySelector(".confirm").classList.add("loading");
    try {
      const d = await api("/api/backup", "POST");
      o.remove();
      toast(d.message, "success");
      await loadBackups();
    } catch(e) {
      o.remove();
      toast("备份失败: " + e.message, "error");
    }
  };
}

function showRestoreModal() {
  if (!_backupsCache.length) {
    toast("暂无备份，请先备份", "info");
    return;
  }
  // 第一级：备份日期
  const options = _backupsCache.map(b =>
    `<option value="${b.dir}">${b.readable} (${b.total_files} 文件)</option>`
  ).join("");
  showModal({
    title: "↩ 从备份恢复",
    bodyHtml: `
      <div style="display:flex;flex-direction:column;gap:12px">
        <div>
          <label style="font-size:11px;color:var(--fg3);text-transform:uppercase;letter-spacing:.3px">备份日期</label>
          <select id="restore-date" style="width:100%;margin-top:4px" onchange="updateRestoreProfiles()">
            ${options}
          </select>
        </div>
        <div>
          <label style="font-size:11px;color:var(--fg3);text-transform:uppercase;letter-spacing:.3px">Profile</label>
          <select id="restore-profile" style="width:100%;margin-top:4px" onchange="updateRestoreFiles()"></select>
        </div>
        <div>
          <label style="font-size:11px;color:var(--fg3);text-transform:uppercase;letter-spacing:.3px">文件</label>
          <select id="restore-file" style="width:100%;margin-top:4px"></select>
        </div>
        <p style="font-size:11px;color:var(--fg2);background:var(--bg3);padding:8px 10px;border-radius:6px;border-left:3px solid var(--accent)">
          ℹ 恢复前会自动把当前文件备份到 <code style="color:var(--accent2)">AAAHermesHub/backups/.backups/</code>
        </p>
      </div>`,
    confirmText: "恢复",
    onConfirm: async (modal) => {
      const backupDir = modal.querySelector("#restore-date").value;
      const profile = modal.querySelector("#restore-profile").value;
      const file = modal.querySelector("#restore-file").value;
      const d = await api("/api/restore", "POST", {backup_dir: backupDir, profile, file});
      if (d.warnings && d.warnings.length) {
        toast(d.message + "（有警告: " + d.warnings.join("; ") + "）", "info");
      } else {
        toast(d.message, "success");
      }
      // 恢复后重新加载当前视图
      if (state.currentProfile) {
        reloadAll();
      }
    },
  });
  // 初始化第二、三级下拉
  updateRestoreProfiles();
}

function updateRestoreProfiles() {
  const dateSel = document.getElementById("restore-date");
  const profSel = document.getElementById("restore-profile");
  if (!dateSel || !profSel) return;
  const backup = _backupsCache.find(b => b.dir === dateSel.value);
  if (!backup) return;
  profSel.innerHTML = backup.profiles.map(p =>
    `<option value="${p.name}">${p.name}</option>`
  ).join("");
  updateRestoreFiles();
}

function updateRestoreFiles() {
  const dateSel = document.getElementById("restore-date");
  const profSel = document.getElementById("restore-profile");
  const fileSel = document.getElementById("restore-file");
  if (!dateSel || !profSel || !fileSel) return;
  const backup = _backupsCache.find(b => b.dir === dateSel.value);
  if (!backup) return;
  const prof = backup.profiles.find(p => p.name === profSel.value);
  if (!prof) return;
  const opts = [`<option value="all">所有文件</option>`];
  opts.push(...prof.files.map(f => `<option value="${f}">${f}</option>`));
  fileSel.innerHTML = opts.join("");
}

function showCleanupModal() {
  if (!_backupsCache.length) {
    toast("暂无备份可清理", "info");
    return;
  }
  const newest = _backupsCache[0];
  const oldest = _backupsCache[_backupsCache.length - 1];
  showModal({
    title: "🧹 清理超期备份",
    bodyHtml: `
      <div style="display:flex;flex-direction:column;gap:12px">
        <div>
          <label style="font-size:11px;color:var(--fg3);text-transform:uppercase;letter-spacing:.3px">保留天数（超过此天数的备份将被删除）</label>
          <input id="cleanup-days" type="number" min="1" value="30" style="width:100%;margin-top:4px">
        </div>
        <div style="font-size:12px;color:var(--fg2);background:var(--bg3);padding:10px;border-radius:6px;border-left:3px solid var(--yellow)">
          <div>📊 当前共 <b style="color:var(--fg)">${_backupsCache.length}</b> 个备份</div>
          <div style="margin-top:4px">🆕 最新: <code style="color:var(--green)">${newest.readable}</code>（始终保留）</div>
          <div style="margin-top:4px">🕐 最早: <code style="color:var(--fg2)">${oldest.readable}</code></div>
        </div>
        <p style="font-size:11px;color:var(--fg2);opacity:.8">
          注意：最新的一份始终保留，不受天数限制。恢复前的自动备份（.backups/）不参与清理。
        </p>
      </div>`,
    confirmText: "清理",
    danger: false,
    onConfirm: async (modal) => {
      const days = parseInt(modal.querySelector("#cleanup-days").value, 10);
      if (!days || days < 1) throw new Error("请输入有效的天数（>=1）");
      const d = await api("/api/backups/cleanup", "POST", {max_age_days: days});
      toast(d.message, "success");
      await loadBackups();  // 刷新徽章
    },
  });
}

init();
