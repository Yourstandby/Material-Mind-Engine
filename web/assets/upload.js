/* 投喂：后台入库任务 + 进度落盘，刷新后仍能看到队列 */

const upState = { total: 0, done: 0, failed: 0, active: 0 };
const UP_POLL_MS = 800;
const UP_RESTORE_MS = 30 * 60 * 1000;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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

function showUploader(clear = true) {
  const panel = document.getElementById("uploader");
  if (!panel) return;
  panel.hidden = false;
  if (clear) document.getElementById("upList").innerHTML = "";
  updateSummary();
}

function updateSummary() {
  const sum = document.getElementById("upSum");
  if (!sum) return;
  const parts = [];
  if (upState.active) parts.push(`进行中 ${upState.active}`);
  if (upState.done) parts.push(`完成 ${upState.done}`);
  if (upState.failed) parts.push(`失败 ${upState.failed}`);
  if (!parts.length) parts.push(`共 ${upState.total} 份`);
  sum.textContent = parts.join(" · ");
}

function addRow(name, size, collection) {
  const row = document.createElement("div");
  row.className = "up-row";
  row.innerHTML = `
    <div class="up-row-head">
      <span class="up-file">${esc(name)}</span>
      <span class="up-arrow">→</span>
      <span class="up-lib">${esc(collection || "catalog")}</span>
      <span class="up-stage">排队中</span>
    </div>
    <div class="up-track"><i style="width:2%"></i></div>
    <div class="up-note">${size ? `${(size / 1024).toFixed(0)} KB` : "已提交"}</div>`;
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
    jobId: "",
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

/* ───────── 进度渲染（新任务与刷新恢复共用） ───────── */

function renderJob(row, job) {
  if (!job) return;
  row.found = job.found || row.found;
  row.warns = (job.warns || []).length;

  if (job.status === "running") {
    if (job.stage === "extract") {
      if (job.total) {
        const pct = 15 + Math.round((72 * (job.done || 0)) / Math.max(job.total, 1));
        setStage(row, `抽取 ${job.done || 0}/${job.total}`, pct);
      } else {
        setStage(row, "抽取中", 15);
      }
    } else if (job.stage === "embed") {
      setStage(row, "向量化", 92);
    } else if (job.stage === "parse") {
      setStage(row, "解析文档", 8);
    } else {
      setStage(row, "排队中", 4);
    }
    let text = row.found ? `已抽到 ${row.found} 条` : job.total ? `切分 ${job.total} 块` : "处理中…";
    if (row.warns) text += ` · ${row.warns} 处警告`;
    note(row, text, row.warns ? "warn" : "");
    return;
  }

  row.finished = true;
  if (job.status === "error") {
    row.ok = false;
    row.el.classList.add("bad");
    setStage(row, "失败", 100);
    note(row, job.error || "入库失败", "err");
    return;
  }

  row.ok = (job.added || 0) > 0;
  const extra = job.quarantined ? ` · 隔离 ${job.quarantined}` : "";
  const base = `+${job.added || 0} 条 · 跳过 ${job.skipped || 0}${extra}`;
  if (row.ok) {
    row.el.classList.add("ok");
    setStage(row, "完成", 100);
    note(row, row.warns ? `${base} · ${row.warns} 处警告` : base, "ok");
  } else {
    row.el.classList.add("zero");
    setStage(row, "无数据", 100);
    note(row, job.message || base, "warn");
  }
}

let settleTimer = null;
function onJobSettled() {
  if (settleTimer) clearTimeout(settleTimer);
  settleTimer = setTimeout(async () => {
    settleTimer = null;
    if (typeof refreshCollections === "function") await refreshCollections();
    const onResources = !document.getElementById("resourcesView").classList.contains("hidden");
    if (onResources && typeof loadSources === "function") loadSources();
  }, 300);
}

/* ───────── 轮询 ───────── */

async function pollJob(row, jobId) {
  row.jobId = jobId;
  for (;;) {
    let job = null;
    try {
      const res = await fetch(`/api/ingest/jobs/${encodeURIComponent(jobId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      job = await res.json();
    } catch (err) {
      row.el.classList.add("bad");
      setStage(row, "失联", 100);
      note(row, `无法获取进度：${err.message}`, "err");
      row.finished = true;
      return;
    }
    renderJob(row, job);
    if (job.status !== "running") return job;
    await sleep(UP_POLL_MS);
  }
}

/* ───────── 入库 ───────── */

async function ingestFiles(fileList) {
  const files = [...fileList].filter(Boolean);
  if (!files.length) return;

  upState.total = files.length;
  upState.done = 0;
  upState.failed = 0;
  showUploader();

  const rows = files.map((file) => addRow(file.name, file.size, "catalog"));
  const btn = document.getElementById("upBtn");

  for (let i = 0; i < files.length; i += 1) {
    const row = rows[i];
    upState.active += 1;
    if (btn) btn.classList.add("busy");
    updateSummary();
    await ingestFile(files[i], row);
    upState.active = Math.max(0, upState.active - 1);
    if (row.ok) upState.done += 1;
    else upState.failed += 1;
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
  // 产品是检索主单位，材质只由入库流程从文档内容识别，前端不再传分类。
  form.append("collection", "catalog");

  setStage(row, "上传中", 6);
  try {
    const res = await fetch("/api/ingest", { method: "POST", body: form });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (!data.job_id) throw new Error("服务端未返回任务编号");
    setStage(row, "已提交", 8);
    await pollJob(row, data.job_id);
  } catch (err) {
    row.ok = false;
    row.finished = true;
    row.el.classList.add("bad");
    setStage(row, "失败", 100);
    note(row, err.message, "err");
  }
}

/* ───────── 刷新后恢复 ───────── */

async function restoreJobs() {
  let items = [];
  try {
    const res = await fetch("/api/ingest/jobs?limit=8");
    if (!res.ok) return;
    items = (await res.json()).items || [];
  } catch (err) {
    return;
  }
  const recent = items.filter(
    (job) => job.status === "running" || Date.now() / 1000 - (job.updated_at || 0) < UP_RESTORE_MS / 1000
  );
  if (!recent.length) return;

  showUploader(false);
  const rows = recent.map((job) => {
    const row = addRow(job.filename, 0, job.collection);
    row.finished = job.status !== "running";
    return { row, job };
  });

  rows.forEach(({ row, job }) => renderJob(row, job));

  const running = rows.filter(({ job }) => job.status === "running");
  if (!running.length) return;
  const btn = document.getElementById("upBtn");
  if (btn) btn.classList.add("busy");
  upState.total = rows.length;
  upState.active = running.length;
  updateSummary();

  await Promise.all(
    running.map(async ({ row, job }) => {
      await pollJob(row, job.id);
      upState.active = Math.max(0, upState.active - 1);
      if (row.ok) upState.done += 1;
      else upState.failed += 1;
      updateSummary();
    })
  );

  if (btn) btn.classList.remove("busy");
  onJobSettled();
}

/* ───────── 绑定 ───────── */

(function initUploader() {
  const close = document.getElementById("upClose");
  if (close) close.addEventListener("click", () => (document.getElementById("uploader").hidden = true));
  restoreJobs();
})();
