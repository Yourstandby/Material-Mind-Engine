/* 未命中看板：库里没有资料的提问聚合 + 待补厂商/牌号 + 关单 */

const $ = (s) => document.querySelector(s);
const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let toastTimer = null;
function toast(text) {
  const el = $("#adbToast");
  if (!el) return;
  el.textContent = text;
  el.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("on"), 1800);
}

function renderStats(stats) {
  const box = $("#adbStats");
  if (!box) return;
  const misses = (stats && stats.misses) || { questions: 0, asks: 0 };
  const data = [
    [misses.questions, "未命中问题"],
    [misses.asks, "累计需求次数"],
    [((stats && stats.status) || {}).open || 0, "待处理"],
    [((stats && stats.status) || {}).fixed || 0, "已补"],
  ];
  box.innerHTML = data
    .map(([n, label]) => `<div><div class="stat-n">${n}</div><div class="stat-l">${esc(label)}</div></div>`)
    .join("");
}

function renderWanted(wanted) {
  const box = $("#adbWanted");
  if (!box) return;
  if (!wanted || !wanted.length) {
    box.innerHTML = `<span class="empty">还没有可抽取的关键词</span>`;
    return;
  }
  box.innerHTML = wanted
    .map(
      (w) =>
        `<span class="chip" title="${esc((w.questions || []).join('\n'))}">${esc(w.word)}` +
        `<b>${w.asks}</b></span>`
    )
    .join("");
}

function renderList(items) {
  const box = $("#adbList");
  if (!box) return;
  if (!items || !items.length) {
    box.innerHTML = `<div class="empty">暂无未命中的问题——库里覆盖得不错。</div>`;
    return;
  }
  const head = `<div class="miss-row head"><div>问题</div><div class="miss-n">次数</div>` +
    `<div>提问人</div><div>最近</div><div></div></div>`;
  box.innerHTML =
    head +
    items
      .map((it) => {
        const users = (it.users || []).filter(Boolean);
        const who = users.length ? esc(users.slice(0, 3).join("、")) + (users.length > 3 ? ` +${users.length - 3}` : "") : "—";
        const tag = it.trigger === "manual_no_hit" ? "用户催" : "自动记录";
        return (
          `<div class="miss-row" data-ids="${(it.ids || []).join(",")}">` +
          `<div class="miss-q">${esc(it.question)}<div class="miss-tag">${tag}</div></div>` +
          `<div class="miss-n">${it.hits}</div>` +
          `<div class="miss-t">${who}</div>` +
          `<div class="miss-t">${esc((it.last_ts || "").slice(5, 16))}</div>` +
          `<div><button type="button">已补资料</button></div>` +
          `</div>`
        );
      })
      .join("");
}

async function resolveRow(btn) {
  const row = btn.closest(".miss-row");
  const ids = (row.dataset.ids || "")
    .split(",")
    .map((x) => Number(x))
    .filter((x) => Number.isFinite(x) && x > 0);
  if (!ids.length) return;
  btn.disabled = true;
  btn.textContent = "处理中…";
  try {
    const res = await fetch("/api/failures/misses/resolve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids, note: "资料已补" }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || "关单失败");
    }
    row.remove();
    toast("已关单，刷新中…");
    load();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "已补资料";
    toast(err.message);
  }
}

async function load() {
  try {
    const res = await fetch("/api/failures/misses?limit=100");
    if (res.status === 401 || res.status === 403) {
      location.href = "/login";
      return;
    }
    const data = await res.json();
    renderStats(data.stats);
    renderWanted(data.wanted);
    renderList(data.items);
  } catch (err) {
    $("#adbList").innerHTML = `<div class="empty">加载失败：${esc(err.message)}</div>`;
  }
}

let ACCESS = null;

function levelLabel(id) {
  const hit = ((ACCESS && ACCESS.levels) || []).find((x) => x.id === id);
  return hit ? hit.label : id;
}

function fillLevelSelect(el, selected) {
  if (!el) return;
  el.innerHTML = ((ACCESS && ACCESS.levels) || [])
    .map((x) => `<option value="${esc(x.id)}"${x.id === selected ? " selected" : ""}>${esc(x.label)}</option>`)
    .join("");
}

function renderDefaults(data) {
  fillLevelSelect($("#adbDefault"), data.default_level);
  fillLevelSelect($("#adbLevelPick"), "admin");
  const folders = $("#adbFolder");
  if (folders) {
    folders.innerHTML =
      `<option value="">选文件夹…</option>` +
      (data.folders || [])
        .map((f) => `<option value="${esc(f.id)}">${esc(f.name)}（${f.count}）</option>`)
        .join("");
  }
}

function renderDocs(data) {
  const box = $("#adbDocs");
  if (!box) return;
  const docs = data.docs || [];
  if (!docs.length) {
    box.innerHTML = `<div class="empty">资料库还是空的。</div>`;
    return;
  }
  const folderName = (id) => ((data.folders || []).find((f) => f.id === id) || {}).name || "未分类";
  const head = `<div class="tier-row head"><div>资料</div><div>文件夹</div><div>可见级别</div></div>`;
  box.innerHTML =
    head +
    docs
      .map(
        (d) =>
          `<div class="tier-row">` +
          `<div class="tier-name">${esc(d.title || d.name || d.doc_id)}<small>${esc(d.name || "")} · ${d.records} 条</small></div>` +
          `<div class="miss-t">${esc(folderName(d.folder_id))}</div>` +
          `<div><select class="doc-level" data-doc="${esc(d.doc_id)}">` +
          ((ACCESS && ACCESS.levels) || [])
            .map((x) => `<option value="${esc(x.id)}"${x.id === d.level ? " selected" : ""}>${esc(x.label)}</option>`)
            .join("") +
          `</select></div></div>`
      )
      .join("");
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "保存失败");
  return data;
}

async function loadAccess() {
  try {
    const res = await fetch("/api/access");
    if (res.status === 401 || res.status === 403) return;
    if (!res.ok) throw new Error("加载失败");
    ACCESS = await res.json();
    renderDefaults(ACCESS);
    renderDocs(ACCESS);
  } catch (err) {
    $("#adbDocs").innerHTML = `<div class="empty">分级加载失败：${esc(err.message)}</div>`;
  }
}

async function setDocLevel(select) {
  try {
    const data = await api("PATCH", `/api/library/${encodeURIComponent(select.dataset.doc)}/level`, {
      level: select.value,
    });
    const doc = (ACCESS.docs || []).find((d) => d.doc_id === select.dataset.doc);
    if (doc) doc.level = data.level;
    toast(`已设为${levelLabel(data.level)}`);
  } catch (err) {
    toast(err.message);
    loadAccess();
  }
}

async function applyFolderLevel() {
  const fid = $("#adbFolder").value;
  const level = $("#adbLevelPick").value;
  if (!fid) {
    toast("先选文件夹");
    return;
  }
  try {
    const data = await api("POST", "/api/access/folder", { folder_id: fid, level });
    toast(`已把 ${data.changed} 份资料设为${levelLabel(data.level)}`);
    await loadAccess();
  } catch (err) {
    toast(err.message);
  }
}

async function saveDefaultLevel(select) {
  try {
    const data = await api("PATCH", "/api/access/policy", { default_level: select.value });
    ACCESS.default_level = data.default_level;
    toast(`新投喂资料默认：${levelLabel(data.default_level)}`);
  } catch (err) {
    toast(err.message);
    loadAccess();
  }
}

document.addEventListener("click", (event) => {
  if (event.target.closest("#adbFolderGo")) applyFolderLevel();
});

document.addEventListener("change", (event) => {
  const el = event.target;
  if (el.classList.contains("doc-level")) setDocLevel(el);
  else if (el.id === "adbDefault") saveDefaultLevel(el);
});

loadAccess();
load();
