/* 资料库：列表 / 筛选 / 批量 / 抽屉 / 检索测试 */

const resState = {
  product: "all",
  industry: "all",
  folder: "all",
  folders: [],
  facets: null,
  sources: [],
  filter: "",
  selected: new Set(),
  current: null,
  busy: false,
};

const keyOf = (item) => `${item.collection}|${item.source}`;
const labelOf = (name) => {
  const found = state.collections.find((c) => c.name === name);
  return found ? found.label : name;
};

/* ───────── 视图 ───────── */

function switchView(view) {
  const isChat = view === "chat";
  document.querySelectorAll("#viewNav .viewlink").forEach((btn) =>
    btn.classList.toggle("active", btn.id === (isChat ? "viewChat" : "viewResources"))
  );
  document.getElementById("chatView").classList.toggle("hidden", !isChat);
  document.getElementById("resourcesView").classList.toggle("hidden", isChat);

  if (isChat) {
    setActive(state.active);
    return;
  }
  document.getElementById("crumbLabel").textContent = "资料库";
  document.getElementById("crumbCount").textContent = "";
  loadSources();
}

/* ───────── 数据 ───────── */

async function loadSources() {
  const meta = document.getElementById("resMeta");
  meta.textContent = "加载中…";
  await ensureFacets();
  try {
    const params = new URLSearchParams({ product: resState.product });
    if (resState.industry && resState.industry !== "all") params.set("industry", resState.industry);
    if (resState.folder && resState.folder !== "all") params.set("folder", resState.folder);
    const res = await fetch(`/api/library?${params.toString()}`);
    const data = await res.json();
    resState.sources = data.items || [];
    const alive = new Set(resState.sources.map(keyOf));
    [...resState.selected].forEach((key) => alive.has(key) || resState.selected.delete(key));
    renderResList();
  } catch (err) {
    meta.textContent = "加载失败";
    document.getElementById("resList").innerHTML = `<div class="res-empty">
      <div class="res-empty-title">加载失败</div>
      <div class="res-empty-sub">${esc(err.message)}</div></div>`;
  }
}

function renderSeg() {
  const seg = document.getElementById("resSeg");
  if (!seg) return;
  seg.innerHTML = state.collections
    .map((c) => `<option value="${esc(c.name)}">${esc(c.label)} · ${c.count || 0} 条</option>`)
    .join("");
  seg.value = resState.product;
  seg.onchange = () => {
    resState.product = seg.value;
    loadSources();
  };
}

async function ensureFacets() {
  if (resState.facets) return resState.facets;
  try {
    const res = await fetch("/api/applications");
    const data = await res.json();
    resState.facets = data.facets || {};
  } catch (err) {
    resState.facets = {};
  }
  renderResApp();
  return resState.facets;
}

function renderResApp() {
  const sel = document.getElementById("resApp");
  if (!sel) return;
  const list = (resState.facets && resState.facets.industries) || [];
  sel.innerHTML =
    `<option value="all">全部应用</option>` +
    list
      .map((a) => `<option value="${esc(a.label)}">${esc(a.label)} · ${a.count} 条</option>`)
      .join("");
  sel.value = resState.industry;
  sel.onchange = () => {
    resState.industry = sel.value;
    loadSources();
  };
}

async function loadFolders(force = false) {
  if (!force && resState.folders.length) {
    renderFolderSelects();
    return resState.folders;
  }
  try {
    const res = await fetch("/api/folders");
    const data = await res.json();
    resState.folders = data.items || [];
  } catch (err) {
    resState.folders = [];
  }
  renderFolderSelects();
  return resState.folders;
}

function renderFolderSelects() {
  const list = resState.folders;
  const resSel = document.getElementById("resFolder");
  if (resSel) {
    const prev = resState.folder;
    resSel.innerHTML =
      `<option value="all">全部文件夹</option>` +
      list
        .map((f) => `<option value="${esc(f.id)}">${esc(f.name)} · ${f.count || 0}</option>`)
        .join("");
    resSel.value = list.some((f) => f.id === prev) ? prev : "all";
    resState.folder = resSel.value;
    resSel.onchange = () => {
      resState.folder = resSel.value;
      loadSources();
    };
  }
  const upSel = document.getElementById("upFolder");
  if (upSel) {
    const prev = upSel.value || "auto";
    upSel.innerHTML =
      `<option value="auto">自动分类（按材质）</option>` +
      list
        .map((f) => `<option value="${esc(f.id)}">${esc(f.name)}</option>`)
        .join("");
    upSel.value = [...upSel.options].some((o) => o.value === prev) ? prev : "auto";
  }
  const rename = document.getElementById("folderRename");
  const del = document.getElementById("folderDelete");
  const current = list.find((f) => f.id === resState.folder);
  const editable = !!current && current.deletable;
  if (rename) rename.disabled = !editable;
  if (del) del.disabled = !editable;
}

async function reloadFolders() {
  await loadFolders(true);
  return resState.folders;
}

async function createFolder() {
  const name = window.prompt("新文件夹名称（例如：半导体、汽车、未分类2）");
  if (!name || !name.trim()) return;
  try {
    const res = await fetch("/api/folders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "新建失败");
    await reloadFolders();
    resState.folder = data.id;
    renderFolderSelects();
    await loadSources();
  } catch (err) {
    window.alert(err.message);
  }
}

async function renameFolder() {
  const current = resState.folders.find((f) => f.id === resState.folder);
  if (!current || !current.deletable) return;
  const name = window.prompt("重命名文件夹", current.name);
  if (!name || !name.trim() || name.trim() === current.name) return;
  try {
    const res = await fetch(`/api/folders/${encodeURIComponent(current.id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "重命名失败");
    await reloadFolders();
    await loadSources();
  } catch (err) {
    window.alert(err.message);
  }
}

async function deleteFolder() {
  const current = resState.folders.find((f) => f.id === resState.folder);
  if (!current || !current.deletable) return;
  if (!window.confirm(`删除文件夹「${current.name}」？里面的 ${current.count || 0} 份资料会移到「未分类」，不会删除文件。`)) return;
  try {
    const res = await fetch(`/api/folders/${encodeURIComponent(current.id)}`, { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "删除失败");
    resState.folder = "all";
    await reloadFolders();
    await loadSources();
  } catch (err) {
    window.alert(err.message);
  }
}

function visibleSources() {
  const q = resState.filter.trim().toLowerCase();
  if (!q) return resState.sources;
  return resState.sources.filter((item) =>
    [item.title, item.source, item.original_name, item.authors, item.publisher, item.patent_no, item.doi,
      item.product_name, item.industries, item.parts, item.media, item.certs, item.application_text]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(q))
  );
}

function confidenceCell(value) {
  const pct = Math.round((Number(value) || 0) * 100);
  return `<span class="conf-cell"><span class="conf-mini"><i style="width:${pct}%"></i></span><em>${pct}%</em></span>`;
}

const TYPE_LABEL = { patent: "专利", paper: "论文", standard: "标准", datasheet: "数据表", report: "报告", manual: "手册", other: "其他" };

function typeBadge(type) {
  const key = TYPE_LABEL[type] ? type : "other";
  return `<span class="type t-${key}">${TYPE_LABEL[key]}</span>`;
}

// 文件类型图标：色块 + 线性图标，比纯文字扩展名更好扫读
const FILE_ICON = {
  pdf: "filePdf",
  doc: "fileDoc", docx: "fileDoc", rtf: "fileDoc", odt: "fileDoc",
  xls: "fileSheet", xlsx: "fileSheet", xlsm: "fileSheet", csv: "fileSheet", ods: "fileSheet",
  ppt: "fileSlides", pptx: "fileSlides", odp: "fileSlides",
  html: "fileCode", htm: "fileCode", json: "fileCode", xml: "fileCode",
  md: "fileDoc", markdown: "fileDoc", txt: "fileDoc",
  png: "fileImage", jpg: "fileImage", jpeg: "fileImage", webp: "fileImage", tif: "fileImage", tiff: "fileImage",
};

function fileBadge(kind) {
  const key = String(kind || "file").toLowerCase().replace(/^\./, "");
  return `<span class="ficon f-${key}" title="${esc(key || "file")}">${icon(
    FILE_ICON[key] || "file",
    15
  )}</span>`;
}

function renderResList() {
  const list = visibleSources();
  const total = list.reduce((sum, item) => sum + (item.records || 0), 0);
  const all = resState.sources;
  document.getElementById("resMeta").textContent = list.length
    ? `${list.length} 份资料 · ${total} 条记录${resState.filter ? ` · 共 ${all.length} 份` : ""}`
    : "";

  const resCount = document.getElementById("resCount");
  if (resCount) resCount.textContent = all.length || "";

  const host = document.getElementById("resList");
  if (!list.length) {
    host.innerHTML = `<div class="res-empty">
      <div class="res-empty-title">${resState.filter ? "没有匹配的资料" : "还没有资料"}</div>
      <div class="res-empty-sub">${
        resState.filter ? "换个关键词试试" : "把 PDF / DOCX / XLSX 拖到页面，或放进 inbox 目录"
      }</div></div>`;
    renderBulk();
    return;
  }

  host.innerHTML = `
    <div class="res-table">
      <div class="res-row res-head">
        <span></span><span>标题 / 文件</span><span>类型</span><span class="num">年份</span><span class="num">记录</span><span></span>
      </div>
      ${(() => {
        const counts = new Map();
        list.forEach((item) => {
          const key = item.product_id || item.source;
          counts.set(key, (counts.get(key) || 0) + 1);
        });
        const ordered = [...list].sort((a, b) =>
          String(a.product_name || a.source).localeCompare(String(b.product_name || b.source))
        );
        const rows = [];
        let current = null;
        ordered.forEach((item) => {
          const key = item.product_id || item.source;
          if (key !== current) {
            current = key;
            const app = [item.industries, item.parts].filter(Boolean).join(" · ");
            rows.push(`<div class="res-group">
              <span class="g-name">${esc(item.product_name || "未关联产品")}</span>
              <span class="g-meta">${esc(item.supplier || "")}${
              item.supplier ? " · " : ""
            }${esc(app)}${app ? " · " : ""}${counts.get(key)} 份文件</span>
            </div>`);
          }
          const index = list.indexOf(item);
          const on = resState.selected.has(keyOf(item));
          const sub = [item.folder_name, item.authors || item.publisher, item.patent_no || item.doi]
            .filter(Boolean)
            .join(" · ");
          rows.push(`<div class="res-row" data-i="${index}">
            <span class="pick${on ? " on" : ""}" data-pick="${index}" role="checkbox" aria-checked="${on}" tabindex="0">${
            on ? icon("check", 11) : ""
          }</span>
            <div class="res-name">
              ${fileBadge(item.kind)}
              <span class="file" title="${esc(item.source)}">${esc(item.title || item.source)}</span>
              <span class="sub">${esc(sub || item.original_name || "")}</span>
            </div>
            ${typeBadge(item.doc_type)}
            <span class="num year">${item.year || "—"}</span>
            <span class="num recs">${item.records || 0}</span>
            <button class="row-more" type="button" aria-label="查看"><span class="ico">${icon(
              "chevronRight",
              15
            )}</span></button>
          </div>`);
        });
        return rows.join("");
      })()}
    </div>`;

  host.querySelectorAll(".res-row[data-i]").forEach((row) =>
    row.addEventListener("click", (e) => {
      if (e.target.closest(".pick")) return;
      openDrawer(list[Number(row.dataset.i)]);
    })
  );
  host.querySelectorAll(".pick").forEach((box) => {
    const toggle = () => {
      const item = list[Number(box.dataset.pick)];
      const key = keyOf(item);
      resState.selected.has(key) ? resState.selected.delete(key) : resState.selected.add(key);
      renderResList();
    };
    box.addEventListener("click", (e) => {
      e.stopPropagation();
      toggle();
    });
    box.addEventListener("keydown", (e) => {
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        toggle();
      }
    });
  });

  renderBulk();
}

function renderBulk() {
  const bar = document.getElementById("bulk");
  const count = resState.selected.size;
  bar.hidden = count === 0;
  if (count) document.getElementById("bulkCount").textContent = `已选 ${count} 项`;
}

/* ───────── 抽屉 ───────── */

function metaForm(item) {
  const options = Object.entries(TYPE_LABEL)
    .map(
      ([key, label]) =>
        `<option value="${key}"${item.doc_type === key ? " selected" : ""}>${label}</option>`
    )
    .join("");
  return `<form class="doc-meta" id="docMeta" autocomplete="off" onsubmit="return false">
    <div class="doc-meta-head"><span>文献信息</span><span class="doc-hint">${esc(
      item.canonical_name || ""
    )}</span></div>
    <label class="mfield wide"><span>标题</span><input id="mTitle" value="${esc(
      item.title || ""
    )}"></label>
    <div class="mrow">
      <label class="mfield"><span>类型</span><select id="mDocType">${options}</select></label>
      <label class="mfield"><span>年份</span><input id="mYear" type="number" min="1900" max="2035" value="${esc(
        item.year || ""
      )}"></label>
    </div>
    <div class="mrow">
      <label class="mfield"><span>作者 / 发明人</span><input id="mAuthors" value="${esc(
        item.authors || ""
      )}"></label>
      <label class="mfield"><span>机构 / 供应商</span><input id="mPublisher" value="${esc(
        item.publisher || ""
      )}"></label>
    </div>
    <div class="mrow">
      <label class="mfield"><span>文件夹</span><select id="mFolder">${resState.folders
        .map(
          (f) =>
            `<option value="${esc(f.id)}"${(item.folder_id || "unfiled") === f.id ? " selected" : ""}>${esc(
              f.name
            )}</option>`
        )
        .join("")}</select></label>
      <label class="mfield"><span>DOI</span><input id="mDoi" value="${esc(item.doi || "")}"></label>
      <label class="mfield"><span>专利号</span><input id="mPatent" value="${esc(
        item.patent_no || ""
      )}"></label>
    </div>
    <label class="mfield wide"><span>标签（逗号分隔）</span><input id="mTags" value="${esc(
      (item.tags || []).join(", ")
    )}"></label>
    <label class="mfield wide"><span>应用场景</span><input value="${esc(
      [item.application_text, item.industries, item.parts]
        .filter(Boolean)
        .join(" · ")
    )}" readonly></label>
    <div class="doc-meta-foot">
      <span class="meta-hint">原始文件：${esc(item.original_name || "")}</span>
      <button class="btn-ghost" type="button" id="metaRename"><span class="ico">${icon(
        "refresh",
        13
      )}</span>规范命名</button>
      <button class="btn-primary" type="button" id="metaSave">保存</button>
    </div>
  </form>`;
}

function bindMetaForm(item) {
  const save = document.getElementById("metaSave");
  const rename = document.getElementById("metaRename");
  if (save) save.addEventListener("click", () => saveMeta(item));
  if (rename) rename.addEventListener("click", () => renameDoc(item));
}

async function saveMeta(item) {
  const value = (id) => (document.getElementById(id) || {}).value || "";
  const year = Number(value("mYear"));
  const payload = {
    folder_id: value("mFolder"),
    title: value("mTitle").trim(),
    authors: value("mAuthors").trim(),
    year: Number.isFinite(year) && year > 0 ? year : null,
    doc_type: value("mDocType"),
    publisher: value("mPublisher").trim(),
    doi: value("mDoi").trim(),
    patent_no: value("mPatent").trim(),
    tags: value("mTags")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  };

  const btn = document.getElementById("metaSave");
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "保存中…";
  try {
    const res = await fetch(`/api/library/${encodeURIComponent(item.doc_id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "保存失败");
    resState.current = { ...item, ...data };
    document.getElementById("drawerTitle").textContent = data.title || item.source;
    document.getElementById("drawerSub").textContent = `${data.records || 0} 条记录 · ${labelOf(
      data.collection
    )} · ${data.source}`;
    btn.textContent = "已保存";
    await reloadFolders();
    await loadSources();
  } catch (err) {
    btn.textContent = `失败：${err.message}`;
  } finally {
    btn.disabled = false;
    setTimeout(() => (btn.textContent = label), 2000);
  }
}

async function renameDoc(item) {
  const btn = document.getElementById("metaRename");
  const label = btn.innerHTML;
  btn.disabled = true;
  btn.textContent = "重命名中…";
  try {
    const res = await fetch(`/api/library/${encodeURIComponent(item.doc_id)}/rename`, {
      method: "POST",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "重命名失败");
    btn.textContent = data.renamed ? "已重命名" : "已是规范命名";
    await loadSources();
    if (data.doc) openDrawer(data.doc);
  } catch (err) {
    btn.textContent = `失败：${err.message}`;
  } finally {
    btn.disabled = false;
    setTimeout(() => (btn.innerHTML = label), 2000);
  }
}

async function openDrawer(item) {
  if (!item) return;
  resState.current = item;
  document.getElementById("drawer").hidden = false;
  document.getElementById("drawerTitle").textContent = item.title || item.source;
  document.getElementById("drawerSub").textContent = `${item.records || 0} 条记录 · ${labelOf(
    item.collection
  )} · ${item.source}${item.industries ? ` · ${item.industries}` : ""}`;

  const open = document.getElementById("drawerOpen");
  if (open) {
    open.hidden = !item.doc_id;
    open.href = item.doc_id ? `/api/files/${item.doc_id}` : "#";
  }

  const body = document.getElementById("drawerBody");
  body.innerHTML =
    metaForm(item) +
    `<div class="doc-records" id="docRecords"><div class="skel"></div><div class="skel"></div></div>`;
  bindMetaForm(item);

  const host = document.getElementById("docRecords");
  try {
    const res = await fetch(
      `/api/sources/records?collection=${encodeURIComponent(
        item.collection
      )}&source=${encodeURIComponent(item.source)}`
    );
    const data = await res.json();
    const rows = data.items || [];
    if (!rows.length) {
      host.innerHTML = `<div class="res-empty-sub">没有可展示的记录</div>`;
      return;
    }

    host.innerHTML = rows
      .map((record) => {
        const conf = Math.round((Number(record.confidence) || 0) * 100);
        const fields = (record.fields || [])
          .filter((f) => !["grade", "supplier", "confidence"].includes(f.key))
          .map(
            (f) =>
              `<dt>${esc(f.label)}</dt><dd${
                /^[-+]?[\d.]/.test(String(f.value)) ? ' class="num"' : ""
              }>${esc(String(f.value))}</dd>`
          )
          .join("");
        return `<div class="rec">
          <div class="rec-head">
            <span class="badge">[${record.index}]</span>
            <span class="badge soft">${esc(record.label)}</span>
            <span class="sup">${esc(record.supplier || "")}</span>
            <span class="conf">${conf}%</span>
          </div>
          <dl class="kv">${fields}</dl>
          ${
            record.chunk
              ? `<details class="chunk"><summary>原始片段</summary><pre>${esc(record.chunk)}</pre></details>`
              : ""
          }
        </div>`;
      })
      .join("");
  } catch (err) {
    host.innerHTML = `<div class="res-empty-sub">${esc(err.message)}</div>`;
  }
}

function closeDrawer() {
  document.getElementById("drawer").hidden = true;
  resState.current = null;
}

async function deleteCurrent() {
  const item = resState.current;
  if (!item || resState.busy) return;
  if (!window.confirm(`删除「${item.source}」的全部 ${item.records} 条记录？此操作不可撤销。`)) return;
  resState.busy = true;
  try {
    await fetch(
      `/api/sources?collection=${encodeURIComponent(item.collection)}&source=${encodeURIComponent(
        item.source
      )}`,
      { method: "DELETE" }
    );
    closeDrawer();
    await loadSources();
    await refreshCollections();
  } catch (err) {
    window.alert(err.message);
  } finally {
    resState.busy = false;
  }
}

async function reparseCurrent() {
  const item = resState.current;
  if (!item || resState.busy) return;
  const sub = document.getElementById("drawerSub");
  const original = sub.textContent;
  resState.busy = true;
  sub.textContent = "重新解析中…";
  try {
    const res = await fetch("/api/sources/reparse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ collection: item.collection, source: item.source }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "重新解析失败");
    sub.textContent = `已更新 · +${data.added} 条 / 跳过 ${data.skipped}`;
    await loadSources();
    await refreshCollections();
  } catch (err) {
    sub.textContent = `失败：${err.message}`;
  } finally {
    resState.busy = false;
    setTimeout(() => (sub.textContent = original), 2600);
  }
}

/* ───────── 批量 ───────── */

function selectedItems() {
  return resState.sources.filter((item) => resState.selected.has(keyOf(item)));
}

async function bulkDelete() {
  const items = selectedItems();
  if (!items.length) return;
  if (!window.confirm(`删除所选 ${items.length} 份资料？此操作不可撤销。`)) return;
  for (const item of items) {
    await fetch(
      `/api/sources?collection=${encodeURIComponent(item.collection)}&source=${encodeURIComponent(
        item.source
      )}`,
      { method: "DELETE" }
    );
  }
  resState.selected.clear();
  await loadSources();
  await refreshCollections();
}

async function bulkExport() {
  const items = selectedItems();
  if (!items.length) return;
  const payload = [];
  for (const item of items) {
    const res = await fetch(
      `/api/sources/records?collection=${encodeURIComponent(item.collection)}&source=${encodeURIComponent(
        item.source
      )}`
    );
    const data = await res.json();
    payload.push({ collection: item.collection, source: item.source, records: data.items || [] });
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `mme-export-${new Date().toISOString().slice(0, 10)}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
}

/* ───────── 检索测试 ───────── */

async function runSearch() {
  const query = document.getElementById("searchTest").value.trim();
  if (!query) return;
  const panel = document.getElementById("searchHits");
  panel.hidden = false;
  panel.innerHTML = `<div class="res-empty-sub" style="padding:6px 0">检索中…</div>`;

  const scope = resState.product === "all" ? "全部产品" : labelOf(resState.product);

  try {
    const res = await fetch(
      `/api/search?q=${encodeURIComponent(query)}&collection=catalog`
    );
    const data = await res.json();
    const hits = (data.items || [])
      .filter((hit) => resState.product === "all" || hit.product_id === resState.product)
      .map((hit) => ({ ...hit, collection: "catalog" }))
      .sort((a, b) => (b.score || 0) - (a.score || 0))
      .slice(0, 8);

    if (!hits.length) {
      panel.innerHTML = `<div class="res-empty-sub" style="padding:6px 0">没有命中</div>`;
      return;
    }

    panel.innerHTML =
      `<div class="hits-head">命中 ${hits.length} 条 · ${esc(scope)}</div>` +
      hits
        .map(
          (hit) => `<div class="hit">
            <span class="rank">${hit.rank}</span>
            <div class="hit-main">
              <div class="hit-title">
                <span class="badge soft">${esc(hit.grade || "未标牌号")}</span>
                <span class="hit-lib">${esc(labelOf(hit.collection))}</span>
                <span class="hit-src">${esc(hit.source)}</span>
              </div>
              <div class="snippet">${esc((hit.snippet || "").replace(/\n/g, " "))}</div>
            </div>
            <span class="score">${hit.score === null ? "—" : hit.score.toFixed(3)}</span>
          </div>`
        )
        .join("");
  } catch (err) {
    panel.innerHTML = `<div class="res-empty-sub" style="padding:6px 0">${esc(err.message)}</div>`;
  }
}

/* ───────── 绑定 ───────── */

(function initResources() {
  hydrateIcons();
  renderSeg();

  const on = (id, event, fn) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener(event, fn);
  };

  on("viewChat", "click", () => switchView("chat"));
  on("viewResources", "click", () => switchView("resources"));

  on("resFilter", "input", (e) => {
    resState.filter = e.target.value;
    renderResList();
  });

  on("searchBtn", "click", runSearch);
  on("searchTest", "keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      runSearch();
    }
  });

  on("drawerClose", "click", closeDrawer);
  on("drawerCancel", "click", closeDrawer);
  on("drawerDelete", "click", deleteCurrent);
  on("drawerReparse", "click", reparseCurrent);

  on("bulkDelete", "click", bulkDelete);
  on("bulkExport", "click", bulkExport);

  on("folderNew", "click", createFolder);
  on("folderRename", "click", renameFolder);
  on("folderDelete", "click", deleteFolder);
  loadFolders();

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !document.getElementById("drawer").hidden) closeDrawer();
  });
})();
