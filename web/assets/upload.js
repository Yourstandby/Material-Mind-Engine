/* 投喂：全局上传队列 + toast 通知（覆盖 app.js 中的 ingestFiles） */

const upState = { total: 0, done: 0, failed: 0 };

/* ───────── toast ───────── */

function toast(message, type = "info", ms = 4200) {
  const host = document.getElementById("toasts");
  if (!host) return;
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.innerHTML = `<span class="toast-dot"></span><span class="toast-text">${esc(message)}</span>`;
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add("in"));
  const remove = () => {
    el.classList.remove("in");
    setTimeout(() => el.remove(), 200);
  };
  el.addEventListener("click", () => {
    if (type === "ok" && typeof switchView === "function") switchView("resources");
    remove();
  });
  setTimeout(remove, ms);
}

/* ───────── 队列面板 ───────── */

function showUploader() {
  const panel = document.getElementById("uploader");
  panel.hidden = false;
  document.getElementById("upList").innerHTML = "";
  updateSummary();
}

function updateSummary() {
  const sum = document.getElementById("upSum");
  if (!sum) return;
  const parts = [];
  if (upState.done) parts.push(`完成 ${upState.done}`);
  if (upState.failed) parts.push(`失败 ${upState.failed}`);
  if (!parts.length) parts.push(`共 ${upState.total} 份`);
  sum.textContent = parts.join(" · ");
}

function addRow(file) {
  const row = document.createElement("div");
  row.className = "up-row";
  row.innerHTML = `
    <div class="up-row-head">
      <span class="up-file">${esc(file.name)}</span>
      <span class="up-arrow">→</span>
      <span class="up-lib">${esc(state.active)}</span>
      <span class="up-stage">排队中</span>
    </div>
    <div class="up-track"><i style="width:2%"></i></div>
    <div class="up-note">${(file.size / 1024).toFixed(0)} KB</div>`;
  document.getElementById("upList").appendChild(row);

  return {
    el: row,
    bar: row.querySelector(".up-track i"),
    stage: row.querySelector(".up-stage"),
    note: row.querySelector(".up-note"),
    found: 0,
    warns: 0,
    lastWarn: "",
    ok: false,
    finished: false,
  };
}

function setStage(row, text, percent) {
  row.stage.textContent = text;
  if (percent !== undefined) row.bar.style.width = `${Math.min(100, percent)}%`;
}

function note(row, text, cls = "") {
  row.note.textContent = text;
  row.note.className = `up-note ${cls}`;
}

/* ───────── 入库 ───────── */

async function ingestFiles(fileList) {
  const files = [...fileList].filter(Boolean);
  if (!files.length) return;

  upState.total = files.length;
  upState.done = 0;
  upState.failed = 0;
  showUploader();

  const btn = document.getElementById("upBtn");
  if (btn) btn.classList.add("busy");

  const rows = files.map(addRow);
  for (let i = 0; i < files.length; i += 1) {
    await ingestFile(files[i], rows[i]);
    updateSummary();
  }

  if (btn) btn.classList.remove("busy");

  await refreshCollections();
  const onResources = !document.getElementById("resourcesView").classList.contains("hidden");
  if (onResources && typeof loadSources === "function") loadSources();

  if (upState.done && !upState.failed) {
    toast(`${upState.done} 份资料已入库，点击查看`, "ok");
  } else if (upState.failed) {
    toast(`${upState.failed} 份失败${upState.done ? ` · ${upState.done} 份成功` : ""}`, "error", 6000);
  }
}

async function ingestFile(file, row) {
  const form = new FormData();
  form.append("file", file);
  // 注意：这里必须写真实集合名。state.active 是「材质筛选」（all / FFKM / FKM…），
  // 不是库名；以前直接传它，会把资料写进一个没人读的集合。
  form.append("collection", "catalog");
  form.append("material", state.active === "all" ? "" : state.active);

  setStage(row, "上传中", 6);

  try {
    const res = await fetch("/api/ingest", { method: "POST", body: form });
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let at;
      while ((at = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, at).trim();
        buffer = buffer.slice(at + 2);
        if (!frame.startsWith("data:")) continue;
        try {
          onIngestEvent(JSON.parse(frame.slice(5).trim()), row);
        } catch (e) {
          /* 忽略不完整帧 */
        }
      }
    }

    row.finished ? null : setStage(row, "中断", 100);
    row.ok ? upState.done++ : upState.failed++;
  } catch (err) {
    upState.failed++;
    row.ok = false;
    row.el.classList.add("bad");
    setStage(row, "失败", 100);
    note(row, err.message, "err");
  }
}

function onIngestEvent(evt, row) {
  if (evt.type === "stage") {
    if (evt.stage === "parse") setStage(row, "解析文档", 8);
    if (evt.stage === "extract") {
      setStage(row, "抽取中", 15);
      note(row, `切分 ${evt.chunks} 块`);
    }
    if (evt.stage === "embed") setStage(row, "向量化", 92);
    return;
  }

  if (evt.type === "progress") {
    const pct = Math.max(evt.total, 1);
    setStage(row, `抽取 ${evt.done}/${evt.total}`, 15 + Math.round((72 * evt.done) / pct));
    if (row.found) note(row, `已抽到 ${row.found} 条`);
    return;
  }

  if (evt.type === "record") {
    row.found += 1;
    note(row, `已抽到 ${row.found} 条`);
    return;
  }

  if (evt.type === "warn") {
    row.warns += 1;
    row.lastWarn = evt.message;
    note(row, `${row.warns} 处警告 · ${evt.message}`, "warn");
    return;
  }

  if (evt.type === "error") {
    row.ok = false;
    row.finished = true;
    row.el.classList.add("bad");
    setStage(row, "失败", 100);
    note(row, evt.message, "err");
    return;
  }

  if (evt.type === "done") {
    row.ok = evt.added > 0;
    row.finished = true;
    setStage(row, row.ok ? "完成" : "无数据", 100);
    const extra = evt.quarantined ? ` · 隔离 ${evt.quarantined}` : "";
    const base = `+${evt.added} 条 · 跳过 ${evt.skipped}${extra}`;

    if (row.ok) {
      row.el.classList.add("ok");
      note(row, row.warns ? `${base} · ${row.warns} 处警告` : base, "ok");
    } else {
      row.el.classList.add("zero");
      note(row, evt.message || base, "warn");
    }
  }
}

/* ───────── 绑定 ───────── */

(function initUploader() {
  const close = document.getElementById("upClose");
  if (close) close.addEventListener("click", () => (document.getElementById("uploader").hidden = true));
})();
