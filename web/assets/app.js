/* Material Mind Engine — 前端交互 */

const $ = (s) => document.querySelector(s);

const EXAMPLES = {
  materials: [
    "FFKM 的耐温范围是多少？",
    "300°C 老化 70 小时后的压缩永久变形是多少？",
    "哪些供应商提供全氟醚橡胶？",
  ],
  fkm: [
    "FKM 的低温极限是多少？",
    "FKM 与 FFKM 的耐化学性差异？",
    "氟含量如何影响耐油性？",
  ],
  peek: [
    "PEEK 的长期使用温度？",
    "PEEK 在等离子体环境下的表现？",
    "PEEK 的压缩永久变形数据？",
  ],
};

const state = { collections: [], active: "all", busy: false, controller: null };

/* ───────── 工具 ───────── */

const esc = (s) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[(\d{1,2})\]/g, '<span class="cite" data-i="$1">$1</span>')
    .replace(/\s*\|\s*/g, '<span class="sep">|</span>');
}

/* 轻量 Markdown：表格 / 标题 / 列表 / 段落 / 行内标记 / 引用角标 */
function renderMD(md) {
  const lines = String(md).replace(/\r/g, "").split("\n");
  let html = "";
  let buf = [];
  let list = null;
  let i = 0;

  const flush = () => {
    // 逐行输出是设计如此（一行一条证据），单换行也要保留
    if (buf.length) html += `<p>${buf.map((l) => inline(l)).join("<br>")}</p>`;
    buf = [];
  };
  const closeList = () => {
    if (list) html += `</${list}>`;
    list = null;
  };
  const isRow = (l) => /^\s*\|/.test(l || "");
  const isSep = (l) => /^\s*\|[\s:|-]+\|\s*$/.test(l || "");
  const cells = (l) =>
    l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

  while (i < lines.length) {
    const line = lines[i];

    if (isRow(line) && isSep(lines[i + 1])) {
      flush();
      closeList();
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && isRow(lines[i])) rows.push(cells(lines[i++]));
      html +=
        "<table><thead><tr>" +
        head.map((c) => `<th>${inline(c)}</th>`).join("") +
        "</tr></thead><tbody>" +
        rows
          .map(
            (r) =>
              "<tr>" +
              r.map((c) => `<td${/^[-+]?[\d.,%°]/.test(c) ? ' class="num"' : ""}>${inline(c)}</td>`).join("") +
              "</tr>"
          )
          .join("") +
        "</tbody></table>";
      continue;
    }

    if (/^#{2,3}\s+/.test(line)) {
      flush();
      closeList();
      html += `<h3>${inline(line.replace(/^#+\s+/, ""))}</h3>`;
      i++;
      continue;
    }

    // 证据行（「- 产品 | 材质 | 供应商 | 数据 | 条件」）连续出现时转成表格，方便横向对比
    const evCells = (l) => {
      const m = (l || "").match(/^\s*[-*]\s+(.*)$/);
      if (!m) return null;
      if ((m[1].match(/\s\|\s/g) || []).length < 3) return null;
      return m[1].split("|").map((c) => c.trim());
    };

    if (evCells(line) && evCells(lines[i + 1] || "")) {
      flush();
      closeList();
      const head = ["产品", "材质", "供应商", "关键数据", "测试条件"];
      const rows = [];
      while (i < lines.length) {
        const row = evCells(lines[i]);
        if (!row) break;
        rows.push(row);
        i++;
      }
      const norm = (r) => {
        let c = r.slice();
        // 多出来的列（比如引用编号单独成列）并进「关键数据」，最后一列留给测试条件
        if (c.length > head.length)
          c = [...c.slice(0, 3), c.slice(3, c.length - 1).join(" "), c[c.length - 1]];
        while (c.length < head.length) c.push("");
        return c.slice(0, head.length);
      };
      html +=
        '<div class="tbl"><table><thead><tr>' +
        head.map((c) => `<th>${c}</th>`).join("") +
        "</tr></thead><tbody>" +
        rows
          .map((r) => "<tr>" + norm(r).map((c) => `<td>${c ? inline(c) : "—"}</td>`).join("") + "</tr>")
          .join("") +
        "</tbody></table></div>";
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      flush();
      if (list !== "ul") {
        closeList();
        html += "<ul>";
        list = "ul";
      }
      html += `<li>${inline(line.replace(/^\s*[-*]\s+/, ""))}</li>`;
      i++;
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      flush();
      if (list !== "ol") {
        closeList();
        html += "<ol>";
        list = "ol";
      }
      html += `<li>${inline(line.replace(/^\s*\d+[.)]\s+/, ""))}</li>`;
      i++;
      continue;
    }

    if (!line.trim()) {
      flush();
      closeList();
      i++;
      continue;
    }

    buf.push(line.trim());
    i++;
  }

  flush();
  closeList();
  return html || "<p></p>";
}

/* ───────── 侧栏 / 顶栏 ───────── */

function renderNav() {
  $("#nav").innerHTML = state.collections
    .map(
      (c) => `<button class="nav-item" data-name="${esc(c.name)}" type="button" title="${esc(c.full)}">
        <span class="nav-name">${esc(c.label)}</span>
        <span class="nav-count">${c.count || 0}</span>
      </button>`
    )
    .join("");
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.addEventListener("click", () => setActive(b.dataset.name))
  );
}

function renderChips() {
  $("#chips").innerHTML = state.collections
    .map(
      (c) => `<button class="chip" data-name="${esc(c.name)}" type="button">${esc(c.label)}</button>`
    )
    .join("");
  document.querySelectorAll(".chip").forEach((b) =>
    b.addEventListener("click", () => setActive(b.dataset.name))
  );
}

function renderExamples() {
  const list = EXAMPLES[state.active] || EXAMPLES.materials;
  $("#examples").innerHTML = list
    .map(
      (q, idx) =>
        `<button class="example" type="button" data-q="${esc(q)}">
          <span>0${idx + 1}</span><span>${esc(q)}</span><span class="arrow">↗</span>
        </button>`
    )
    .join("");
  document.querySelectorAll(".example").forEach((b) =>
    b.addEventListener("click", () => ask(b.dataset.q))
  );
}

function setActive(name) {
  if (!name) return;
  state.active = name;
  localStorage.setItem("mme.collection", name);
  localStorage.setItem("mme.lastLabel", name);
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.name === name)
  );
  document.querySelectorAll(".chip").forEach((b) =>
    b.classList.toggle("active", b.dataset.name === name)
  );

  const meta = state.collections.find((c) => c.name === name) || state.collections[0] || {};
  $("#crumbLabel").textContent = meta.label || name;
  $("#crumbCount").textContent = `${meta.count || 0} 条记录`;
  $("#statusText").textContent = meta.hint || "本地知识库";
  $("#input").placeholder = `询问 ${meta.label || ""} 的牌号、耐温、压缩变形…`;
  renderExamples();
}

/* ───────── 消息 ───────── */

function scrollToBottom(force) {
  const el = $("#scroll");
  const near = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
  if (force || near) el.scrollTop = el.scrollHeight;
}

function appendUser(text) {
  const hero = $("#hero");
  if (hero) hero.remove();
  const turn = document.createElement("div");
  turn.className = "turn user";
  turn.innerHTML = `<div class="bubble">${esc(text)}</div>`;
  $("#stack").appendChild(turn);
  scrollToBottom(true);
}

function createTurn() {
  const turn = document.createElement("div");
  turn.className = "turn";
  turn.innerHTML = `
    <div class="t-meta"><span class="who">Material Mind</span><span class="stat">检索中…</span></div>
    <div class="answer"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>
    <div class="sources" hidden></div>`;
  $("#stack").appendChild(turn);
  scrollToBottom(true);
  return {
    turn,
    stat: turn.querySelector(".stat"),
    body: turn.querySelector(".answer"),
    sources: turn.querySelector(".sources"),
  };
}

function renderSources(container, items) {
  container.hidden = false;
  container.innerHTML =
    `<div class="sources-head">引用 · ${items.length}</div>` +
    items
      .map((s) => {
        const conf = typeof s.confidence === "number" ? Math.round(s.confidence * 100) : null;
        const href = s.doc_id
          ? `/api/files/${s.doc_id}${s.page ? `#page=${s.page}` : ""}`
          : "";
        const preview =
          (s.fields.find((f) => f.key === "temp_range") ||
            s.fields.find((f) => f.key === "cs") ||
            {}).value || "";
        const rows = s.fields
          .filter((f) => !["grade", "supplier", "confidence"].includes(f.key))
          .map(
            (f) =>
              `<dt>${esc(f.label)}</dt><dd${/^[-+]?[\d.]/.test(String(f.value)) ? ' class="num"' : ""}>${esc(
                String(f.value)
              )}</dd>`
          )
          .join("");
        return `<div class="src" data-idx="${s.index}">
          <div class="src-head">
            <span class="src-idx">${s.index}</span>
            <span class="src-title">${esc(s.label)}</span>
            <span class="src-sub">${esc(s.supplier || "")}</span>
            <span class="src-key">${esc(String(preview))}</span>
            <span class="src-right">
              ${conf !== null ? `<span class="conf" title="置信度 ${conf}%"><i style="width:${conf}%"></i></span>` : ""}
              ${href ? `<a class="src-link" href="${esc(href)}" target="_blank" rel="noopener" title="打开原文${s.page ? ` 第 ${s.page} 页` : ""}">原文</a>` : ""}
              ${s.url ? `<a class="src-link" href="${esc(s.url)}" target="_blank" rel="noopener" title="打开原始来源">↗</a>` : ""}
              <span class="src-chev">${icon("chevronRight", 13)}</span>
            </span>
          </div>
          <div class="src-body"><dl class="kv">${rows}</dl></div>
        </div>`;
      })
      .join("");

  container.querySelectorAll(".src-head").forEach((head) =>
    head.addEventListener("click", (e) => {
      if (e.target.closest("a")) return;
      head.parentElement.classList.toggle("open");
    })
  );
}

// 只保留正文里真正引用到的来源，编号按正文出现顺序
function citedSources(items, text) {
  const used = new Set();
  const re = /\[(\d{1,2})\]/g;
  let m;
  while ((m = re.exec(text || ""))) used.add(Number(m[1]));
  return (items || []).filter((s) => used.has(s.index)).sort((a, b) => a.index - b.index);
}

function showError(body, message, onRetry) {
  body.innerHTML = "";
  const box = document.createElement("div");
  box.className = "err";
  box.innerHTML = `<span>${esc(message)}</span><button class="retry" type="button">重试</button>`;
  box.querySelector(".retry").addEventListener("click", onRetry);
  body.appendChild(box);
}

/* ───────── 投喂资料 ───────── */

async function refreshCollections() {
  try {
    const res = await fetch("/api/bootstrap");
    const data = await res.json();
    if (data.collections) state.collections = data.collections;
    renderNav();
    renderChips();
    setActive(state.active);
  } catch (e) {
    /* 忽略：计数刷新失败不影响使用 */
  }
}

async function ingestFiles(fileList) {
  const files = [...fileList].filter(Boolean);
  for (const file of files) await ingestOne(file);
  await refreshCollections();
}

function ingestOne(file) {
  const hero = $("#hero");
  if (hero) hero.remove();

  const card = document.createElement("div");
  card.className = "turn";
  card.innerHTML = `
    <div class="t-meta"><span class="who">投喂</span><span class="name">${esc(file.name)}</span><span class="stat">上传中…</span></div>
    <div class="up-card"><div class="up-bar"><i></i></div><div class="up-log"></div></div>`;
  $("#stack").appendChild(card);
  scrollToBottom(true);

  const ui = {
    bar: card.querySelector(".up-bar i"),
    stat: card.querySelector(".stat"),
    line(text, cls = "") {
      const el = document.createElement("div");
      el.className = `up-line ${cls}`;
      el.textContent = text;
      card.querySelector(".up-log").appendChild(el);
      scrollToBottom();
    },
  };

  const form = new FormData();
  form.append("file", file);
  form.append("collection", "catalog");
  form.append("material", state.active);

  return fetch("/api/ingest", { method: "POST", body: form })
    .then(async (res) => {
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
          const chunk = buffer.slice(0, at).trim();
          buffer = buffer.slice(at + 2);
          if (!chunk.startsWith("data:")) continue;
          try {
            handleIngest(JSON.parse(chunk.slice(5).trim()), ui);
          } catch (e) {
            /* 忽略不完整帧 */
          }
        }
      }
    })
    .catch((err) => {
      ui.stat.textContent = "失败";
      ui.line(err.message, "err");
    });
}

const STAGE_LABEL = { parse: "解析文档", extract: "抽取结构化数据", embed: "向量化" };

function handleIngest(evt, ui) {
  if (evt.type === "stage") {
    ui.stat.textContent = STAGE_LABEL[evt.stage] || evt.stage;
    if (evt.stage === "extract") ui.line(`切分为 ${evt.chunks} 块`);
    if (evt.stage === "embed") ui.line(`准备向量化 ${evt.records} 条记录`);
  } else if (evt.type === "progress") {
    ui.bar.style.width = `${Math.round((evt.done / Math.max(evt.total, 1)) * 100)}%`;
    ui.stat.textContent = `抽取 ${evt.done}/${evt.total}`;
  } else if (evt.type === "record") {
    const r = evt.record || {};
    const conf = Math.round((r.confidence || 0) * 100);
    ui.line(`+ ${r.grade_key || r.grade_raw || "未标牌号"} · ${r.supplier || "未知厂商"} · ${conf}%`);
  } else if (evt.type === "warn") {
    ui.line(evt.message, "warn");
  } else if (evt.type === "error") {
    ui.stat.textContent = "失败";
    ui.bar.classList.add("bad");
    ui.line(evt.message, "err");
  } else if (evt.type === "done") {
    ui.bar.style.width = "100%";
    ui.stat.textContent = `+${evt.added} 条 · 跳过 ${evt.skipped}`;
    if (evt.quarantined) ui.line(`${evt.quarantined} 条低置信度已隔离`, "warn");
    if (evt.message) ui.line(evt.message, "warn");
  }
}

/* ───────── 流式问答 ───────── */

async function ask(question) {
  const q = (question || "").trim();
  if (!q || state.busy) return;

  $("#input").value = "";
  autosize();
  appendUser(q);

  const t = createTurn();
  state.busy = true;
  setBusy(true);

  let text = "";
  let sources = [];
  let started = performance.now();
  let frame = 0;

  const paint = () => {
    frame = 0;
    t.body.innerHTML = renderMD(text) + '<span class="caret"></span>';
    scrollToBottom();
  };

  const handle = (evt) => {
    if (evt.type === "meta") {
      t.stat.textContent = `${evt.hits} 条命中`;
      $("#hits").textContent = `${evt.hits} 条命中 · ${state.active}`;
      $("#hits").classList.add("on");
    } else if (evt.type === "sources") {
      sources = evt.items || [];
    } else if (evt.type === "token") {
      text += evt.text || "";
      if (!frame) frame = requestAnimationFrame(paint);
    } else if (evt.type === "done") {
      state.controller = null;
      if (!text) return;
      const ms = evt.elapsed_ms || Math.round(performance.now() - started);
      t.stat.textContent = `${ms}ms · ${Math.round(text.length)} 字`;
      t.body.innerHTML = renderMD(text);
      // 引用等正文全部落定后再出现，且只列正文真正引用到的编号
      const cited = citedSources(sources, text);
      if (cited.length) renderSources(t.sources, cited);
      else t.sources.hidden = true;
      const actions = document.createElement("div");
      actions.className = "t-actions";
      actions.innerHTML = `<button type="button">复制</button><button type="button">重新生成</button><button type="button" class="bad">不准确</button>`;
      const [copy, again, bad] = actions.querySelectorAll("button");
      copy.addEventListener("click", async () => {
        await navigator.clipboard.writeText(text);
        copy.textContent = "已复制";
        setTimeout(() => (copy.textContent = "复制"), 1400);
      });
      again.addEventListener("click", () => {
        t.turn.remove();
        ask(q);
      });
      bad.addEventListener("click", () => {
        if (actions.querySelector(".verdicts")) return;
        const box = document.createElement("div");
        box.className = "verdicts";
        box.innerHTML = [
          ["A", "检索没找到"],
          ["B", "答错了"],
          ["C", "资料里没有"],
          ["D", "问题不合适"],
        ]
          .map(([v, label]) => `<button type="button" data-v="${v}">${label}</button>`)
          .join("");
        actions.appendChild(box);
        box.querySelectorAll("button").forEach((btn) =>
          btn.addEventListener("click", async () => {
            try {
              await fetch("/api/failures", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ question: q, answer: text, verdict: btn.dataset.v }),
              });
              box.innerHTML = `<span class="verdict-done">已记录，谢谢</span>`;
            } catch (e) {
              box.innerHTML = `<span class="verdict-done">记录失败</span>`;
            }
          })
        );
      });
      t.turn.appendChild(actions);
      scrollToBottom();
    } else if (evt.type === "error") {
      t.stat.textContent = "失败";
      showError(t.body, evt.message || "请求失败", () => {
        t.turn.remove();
        ask(q);
      });
      scrollToBottom();
    }
  };

  try {
    state.controller = new AbortController();
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, material: state.active }),
      signal: state.controller.signal,
    });
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
        const chunk = buffer.slice(0, at).trim();
        buffer = buffer.slice(at + 2);
        if (!chunk.startsWith("data:")) continue;
        try {
          handle(JSON.parse(chunk.slice(5).trim()));
        } catch (e) {
          /* 忽略不完整帧 */
        }
      }
    }
  } catch (e) {
    if (e.name === "AbortError") {
      t.stat.textContent = "已停止";
      t.body.innerHTML = renderMD(text || "已停止");
    } else {
      t.stat.textContent = "失败";
      showError(t.body, `${e.message}（确认后端已启动）`, () => {
        t.turn.remove();
        ask(q);
      });
    }
  } finally {
    state.busy = false;
    state.controller = null;
    setBusy(false);
  }
}

function setBusy(busy) {
  const btn = $("#send");
  btn.classList.toggle("stop", busy);
  btn.innerHTML = icon(busy ? "square" : "arrowUp", busy ? 11 : 15);
  btn.type = busy ? "button" : "submit";
  btn.disabled = false;
  btn.onclick = busy
    ? (e) => {
        e.preventDefault();
        if (state.controller) state.controller.abort();
      }
    : null;
}

/* ───────── 输入框 ───────── */

function autosize() {
  const el = $("#input");
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 168) + "px";
}

/* ───────── 命令面板 ───────── */

let paletteItems = [];
let paletteSel = 0;

function paletteData(query) {
  const q = query.trim().toLowerCase();
  const items = [];
  state.collections.forEach((c) =>
    items.push({ kind: "库", label: `${c.label} · ${c.full}`, action: () => setActive(c.name), text: `${c.label} ${c.full} ${c.name}` })
  );
  (EXAMPLES[state.active] || []).forEach((ex) =>
    items.push({ kind: "问", label: ex, action: () => ask(ex), text: ex })
  );
  return q ? items.filter((it) => it.text.toLowerCase().includes(q)) : items;
}

function renderPalette(query) {
  paletteItems = paletteData(query);
  paletteSel = 0;
  $("#paletteList").innerHTML = paletteItems.length
    ? paletteItems
        .map(
          (it, idx) =>
            `<button class="p-item${idx === 0 ? " sel" : ""}" data-idx="${idx}" type="button">
              <span class="kind">${it.kind}</span><span>${esc(it.label)}</span>
            </button>`
        )
        .join("")
    : '<div class="p-item" style="cursor:default"><span class="kind">—</span><span>无匹配项</span></div>';

  document.querySelectorAll(".p-item[data-idx]").forEach((b) =>
    b.addEventListener("click", () => {
      closePalette();
      paletteItems[Number(b.dataset.idx)].action();
    })
  );
}

function moveSel(delta) {
  if (!paletteItems.length) return;
  paletteSel = (paletteSel + delta + paletteItems.length) % paletteItems.length;
  document.querySelectorAll(".p-item[data-idx]").forEach((b) =>
    b.classList.toggle("sel", Number(b.dataset.idx) === paletteSel)
  );
  const cur = document.querySelector(`.p-item[data-idx="${paletteSel}"]`);
  if (cur) cur.scrollIntoView({ block: "nearest" });
}

function openPalette() {
  $("#palette").hidden = false;
  const input = $("#paletteInput");
  input.value = "";
  renderPalette("");
  input.focus();
}

function closePalette() {
  $("#palette").hidden = true;
}

/* ───────── 外观 ───────── */

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("mme.theme", theme);
  $("#themeIco").innerHTML = icon(theme === "dark" ? "sun" : "moon", 15);
}

/* ───────── 事件绑定 ───────── */

function bindEvents() {
  $("#form").addEventListener("submit", (e) => {
    e.preventDefault();
    if (state.busy) return;
    ask($("#input").value);
  });

  $("#input").addEventListener("input", autosize);
  $("#input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!state.busy) ask($("#input").value);
    }
  });

  let dragDepth = 0;
  const drop = $("#drop");
  window.addEventListener("dragenter", (e) => {
    if (!e.dataTransfer || ![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    dragDepth += 1;
    drop.classList.add("on");
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) drop.classList.remove("on");
  });
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    dragDepth = 0;
    drop.classList.remove("on");
    if (e.dataTransfer && e.dataTransfer.files.length) ingestFiles(e.dataTransfer.files);
  });

  $("#upBtn").addEventListener("click", () => $("#fileInput").click());
  $("#fileInput").addEventListener("change", (e) => {
    if (e.target.files.length) ingestFiles(e.target.files);
    e.target.value = "";
  });

  $("#themeBtn").addEventListener("click", () =>
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark")
  );

  $("#paletteBtn").addEventListener("click", openPalette);
  $("#paletteInput").addEventListener("input", (e) => renderPalette(e.target.value));
  $("#palette").addEventListener("click", (e) => {
    if (e.target.id === "palette") closePalette();
  });

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      $("#palette").hidden ? openPalette() : closePalette();
      return;
    }
    if ($("#palette").hidden) return;
    if (e.key === "Escape") closePalette();
    if (e.key === "ArrowDown") {
      e.preventDefault();
      moveSel(1);
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      moveSel(-1);
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const it = paletteItems[paletteSel];
      if (it) {
        closePalette();
        it.action();
      }
    }
  });

  // 引用角标 → 定位来源卡片
  $("#stack").addEventListener("click", (e) => {
    const cite = e.target.closest(".cite");
    if (!cite) return;
    const turn = cite.closest(".turn");
    const card = turn && turn.querySelector(`.src[data-idx="${cite.dataset.i}"]`);
    if (!card) return;
    card.classList.add("open", "flash");
    card.scrollIntoView({ block: "center", behavior: "smooth" });
    setTimeout(() => card.classList.remove("flash"), 1200);
  });
}

/* ───────── 启动 ───────── */

async function boot() {
  applyTheme(
    localStorage.getItem("mme.theme") ||
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
  );
  bindEvents();
  autosize();
  hydrateIcons();

  try {
    const res = await fetch("/api/bootstrap");
    const data = await res.json();
    state.collections = data.collections || [];
  } catch (e) {
    state.collections = [{ name: "materials", label: "FFKM", full: "全氟醚橡胶", hint: "离线", count: 0 }];
  }

  if (!state.collections.length) {
    state.collections = [{ name: "materials", label: "FFKM", full: "全氟醚橡胶", hint: "", count: 0 }];
  }

  const saved = localStorage.getItem("mme.collection");
  if (saved && state.collections.some((c) => c.name === saved)) state.active = saved;

  renderNav();
  renderChips();
  setActive(state.active);
}

const acctBtn = document.getElementById("acctBtn");
const acctModal = document.getElementById("acctModal");
if (acctBtn && acctModal) {
  const msgEl = () => document.getElementById("acctMsg");
  const closeAcct = () => {
    acctModal.hidden = true;
    ["pwOld", "pwNew", "pwNew2"].forEach((id) => (document.getElementById(id).value = ""));
    msgEl().textContent = "";
  };

  acctBtn.addEventListener("click", async () => {
    acctModal.hidden = false;
    guessUser().then((name) => {
      document.getElementById("acctWho").textContent = name ? `当前账号：${name}` : "";
    });
    loadAcctUsers();
    document.getElementById("pwOld").focus();
  });
  document.getElementById("acctClose").addEventListener("click", closeAcct);
  acctModal.addEventListener("click", (event) => {
    if (event.target === acctModal) closeAcct();
  });

  const resetBtn = document.getElementById("acctReset");
  if (resetBtn) {
    resetBtn.addEventListener("click", async () => {
      const note = document.getElementById("acctNote");
      const uid = document.getElementById("acctUserSel").value;
      const pw = document.getElementById("acctNewPw").value;
      if (pw.length < 8) return (note.textContent = "密码至少 8 位");
      note.textContent = "重置中…";
      try {
        const res = await fetch(`/api/users/${uid}/reset`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: pw }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.detail || "重置失败");
        note.textContent = "已重置，该成员需重新登录";
        document.getElementById("acctNewPw").value = "";
      } catch (err) {
        note.textContent = err.message;
      }
    });
  }

  document.getElementById("pwSave").addEventListener("click", async () => {
    const oldPw = document.getElementById("pwOld").value;
    const next = document.getElementById("pwNew").value;
    const again = document.getElementById("pwNew2").value;
    if (next.length < 8) return (msgEl().textContent = "新密码至少 8 位");
    if (next !== again) return (msgEl().textContent = "两次输入不一致");
    msgEl().textContent = "保存中…";
    try {
      const res = await fetch("/api/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old: oldPw, new: next }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "保存失败");
      msgEl().textContent = "已更新，其他设备需重新登录";
      setTimeout(closeAcct, 1400);
    } catch (err) {
      msgEl().textContent = err.message;
    }
  });
}

async function guessUser() {
  try {
    const data = await (await fetch("/api/auth/me")).json();
    if (!data.user) return "";
    return `${data.user.username}（${data.user.role === "admin" ? "管理员" : "成员"}）`;
  } catch (e) {
    return "";
  }
}

async function loadAcctUsers() {
  const box = document.getElementById("acctAdmin");
  const select = document.getElementById("acctUserSel");
  if (!box || !select) return;
  try {
    const me = await (await fetch("/api/auth/me")).json();
    if (!me.user || me.user.role !== "admin") {
      box.hidden = true;
      return;
    }
    const data = await (await fetch("/api/users")).json();
    select.innerHTML = (data.items || [])
      .map((u) => `<option value="${u.id}">${esc(u.username)}（${u.role === "admin" ? "管理员" : "成员"}）</option>`)
      .join("");
    box.hidden = false;
  } catch (e) {
    box.hidden = true;
  }
}

const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) {
  logoutBtn.addEventListener("click", async () => {
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
    location.href = "/login";
  });
}

boot();
