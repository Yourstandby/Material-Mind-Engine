/* Material Mind Engine — 前端交互 */

const $ = (s) => document.querySelector(s);

const EXAMPLES = [
  "这个产品 300°C 老化 70 小时后的压缩永久变形是多少？",
  "这个产品的耐温范围是多少？",
  "这个产品在哪些介质里耐化学性最好？",
];

const state = {
  collections: [], applications: [], active: "all", industry: "",
  busy: false, controller: null,
  reasoning: localStorage.getItem("mme.reasoning") || "minimal",
};

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
      while (i < lines.length && isRow(lines[i])) {
        const row = cells(lines[i++]);
        if (rows.some((r) => r.join("|") === row.join("|"))) continue;
        rows.push(row);
      }
      // 空列/空行直接丢掉：库里抽不全的字段会拼出「｜ ｜ ｜」这种空壳表格
      const keep = head.map((_, idx) => rows.some((r) => (r[idx] || "").trim()) || (head[idx] || "").trim());
      const cols = head.map((_, idx) => idx).filter((idx) => keep[idx]);
      if (cols.length < 2) {
        // 只剩一列时表格没意义，按普通行输出
        html += `<p>${inline(cols.length ? head[cols[0]] : head.join(" "))}</p>`;
        continue;
      }
      const trimmed = rows
        .map((r) => cols.map((idx) => (r[idx] || "").trim()))
        .filter((r) => r.some(Boolean));
      if (!trimmed.length) continue;
      html +=
        '<div class="tbl-scroll"><table class="tbl"><thead><tr>' +
        cols.map((idx) => `<th>${inline(head[idx] || "")}</th>`).join("") +
        "</tr></thead><tbody>" +
        trimmed
          .map(
            (r) =>
              "<tr>" +
              r.map((c) => `<td${/^[-+]?[\d.,%°]/.test(c) ? ' class="num"' : ""}>${inline(c)}</td>`).join("") +
              "</tr>"
          )
          .join("") +
        "</tbody></table></div>";
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
      // 模型偶尔会照抄字段名当作内容（表头回声），这类行没有信息量，直接丢掉
      const headSet = new Set([...head, "编号", "应用场景", "字段", "位置1", "位置2", "位置3", "位置4", "位置5", "[编号]"]);
      const isEcho = (r) =>
        r.length > 0 &&
        r.every((c) => !c || headSet.has(c.replace(/\[\d+\]/g, "").trim())) &&
        r.some((c) => headSet.has(c.trim()));
      const rows = [];
      while (i < lines.length) {
        const row = evCells(lines[i]);
        if (!row) break;
        i++;
        if (isEcho(row)) continue;
        // 同一行重复出现（同产品同值的多条来源）只留一条，否则用户看到一屏重复
        if (rows.some((r) => r.join("|") === row.join("|"))) continue;
        rows.push(row);
      }
      if (!rows.length) continue;
      const norm = (r) => {
        let c = r.slice();
        // 多出来的列（比如引用编号单独成列）并进「关键数据」，最后一列留给测试条件
        if (c.length > head.length)
          c = [...c.slice(0, 3), c.slice(3, c.length - 1).join(" "), c[c.length - 1]];
        while (c.length < head.length) c.push("");
        return c.slice(0, head.length);
      };
      const body = rows.map(norm);
      // 末列如果全是引用编号，就别再顶着「测试条件」的表头
      const refOnly = body.every((r) => /^\[?\d+\]?$/.test((r[r.length - 1] || "").trim() || "x"));
      const heads = head.slice();
      if (refOnly) heads[heads.length - 1] = "引用";
      html +=
        '<div class="tbl-scroll"><table class="tbl"><thead><tr>' +
        heads.map((c, idx) => `<th${idx === heads.length - 1 && refOnly ? ' class="num"' : ""}>${c}</th>`).join("") +
        "</tr></thead><tbody>" +
        body
          .map(
            (r) =>
              "<tr>" +
              r.map((c, idx) => {
                const num = /^[-+]?[\d.,%°]/.test(c) || (refOnly && idx === r.length - 1);
                return `<td${num ? ' class="num"' : ""}>${c ? inline(c) : "—"}</td>`;
              }).join("") +
              "</tr>"
          )
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
  const select = $("#productSelect");
  if (!select) return;
  select.innerHTML = state.collections
    .map(
      (c) =>
        `<option value="${esc(c.name)}" title="${esc(c.full)}">${esc(c.label)} · ${c.count || 0} 条</option>`
    )
    .join("");
  select.value = state.active;
  select.onchange = () => setActive(select.value);
  renderAppNav();
}

function renderAppNav() {
  const select = $("#appSelect");
  if (!select) return;
  const options = [{ code: "", label: "全部应用" }].concat(state.applications || []);
  select.innerHTML = options
    .map(
      (a) =>
        `<option value="${esc(a.code)}" title="${esc(a.label)}">${esc(a.label)}${
          a.count ? ` · ${a.count}` : ""
        }</option>`
    )
    .join("");
  select.value = state.industry;
  select.onchange = () => {
    state.industry = select.value;
    localStorage.setItem("mme.industry", state.industry);
    const label = (state.applications.find((a) => a.code === state.industry) || {}).label;
    $("#statusText").textContent = label ? `应用：${label}` : "本地知识库";
  };
}

function renderChips() {}

function renderExamples() {
  const list = EXAMPLES;
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
  const select = $("#productSelect");
  if (select) select.value = name;

  const meta = state.collections.find((c) => c.name === name) || state.collections[0] || {};
  $("#crumbLabel").textContent = meta.label || name;
  $("#crumbCount").textContent = `${meta.count || 0} 条记录`;
  $("#statusText").textContent = meta.hint || "本地知识库";
  $("#input").placeholder =
    name === "all" ? "提问…" : `询问 ${meta.label} 的耐温、压缩变形、耐化学性…`;
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
    <div class="think" hidden></div>
    <div class="answer"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>
    <div class="sources" hidden></div>`;
  $("#stack").appendChild(turn);
  scrollToBottom(true);
  return {
    turn,
    stat: turn.querySelector(".stat"),
    body: turn.querySelector(".answer"),
    sources: turn.querySelector(".sources"),
    think: turn.querySelector(".think"),
    thinkTimer: 0,
  };
}

/* 检索阶段：按「工具调用」呈现 —— 一个工具一位，折叠成人话摘要 */
const STAGE_META = {
  route: { name: "确定性路由", icon: "sparkle" },
  vector: { name: "向量召回", icon: "layers" },
  bm25: { name: "词法召回", icon: "search" },
  rrf: { name: "结果融合", icon: "layers" },
  rerank: { name: "重排", icon: "sparkle" },
  table: { name: "结构化比对", icon: "database" },
};

// 后端 label 带实现细节（k=60、cross-encoder、模型名），展示层只留人话
function stageName(s) {
  const meta = STAGE_META[s.id];
  if (meta) return meta.name;
  return String(s.label || "步骤")
    .split(" · ")[0]
    .replace(/\s*k=\d+/gi, "")
    .replace(/^cross-encoder\s*/i, "")
    .trim();
}

function stageIconName(s) {
  return (STAGE_META[s.id] || {}).icon || "layers";
}

// 跳过原因是环境变量名之类的实现细节，换成用户能读的说法
const SKIP_TEXT = {
  "RERANK_BASE_URL 未配置": "未启用重排",
  "HYBRID_ENABLED=0": "未启用词法召回",
  "仅向量结果": "只用向量结果",
};

// "10 候选" -> {value:"10", unit:"候选"}；"索引不可用" -> {value:"", unit:"索引不可用"}
function stageMetric(s) {
  const detail = String(s.detail || "");
  const m = detail.match(/^([\d.,]+)\s*(.*)$/);
  if (m) return { value: m[1], unit: m[2] };
  return { value: "", unit: SKIP_TEXT[detail] || detail };
}

function stageRight(s) {
  const metric = stageMetric(s);
  const head = metric.value ? `${metric.value} ${metric.unit}`.trim() : metric.unit;
  return [head, s.ms != null ? `${s.ms}ms` : ""].filter(Boolean).join(" · ");
}

function toolChip(s) {
  const metric = stageMetric(s);
  const skip = s.state === "skip";
  return `<span class="tool-chip${skip ? " skip" : ""}${s.state === "run" ? " running" : ""}" title="${esc(
    s.label || ""
  )}"><span class="tool-ic">${icon(stageIconName(s), 12)}</span>${esc(stageName(s))}${
    !skip && metric.value ? `<b>${esc(metric.value)}</b>` : ""
  }</span>`;
}

function toolRow(s) {
  const skip = s.state === "skip";
  return `<div class="tool${skip ? " skip" : ""}${s.state === "run" ? " running" : ""}">
    <span class="tool-ic">${icon(stageIconName(s), 13)}</span>
    <span class="tool-name">${esc(stageName(s))}</span>
    <span class="tool-detail">${esc(stageRight(s) || (s.state === "run" ? "进行中…" : ""))}</span>
    <span class="tool-state">${skip ? "跳过" : s.state === "run" ? "进行中" : "完成"}</span>
  </div>`;
}

// 后端会把同一阶段先后推两次（run → done），合并成一条，别在 UI 里排两个「向量召回」
function mergeStages(items) {
  const merged = new Map();
  for (const item of items) {
    const key = item.id || item.label;
    const prev = merged.get(key);
    merged.set(key, prev ? { ...prev, ...item, label: item.label || prev.label } : item);
  }
  return [...merged.values()];
}

function renderStages(t, items) {
  items = mergeStages(items || []);
  if (!t.think || !items.length) return;
  t.think.hidden = false;
  const running = items.some((s) => s.state === "run");
  t.think.innerHTML = `<div class="tools${running ? " running" : ""}">
    <div class="tools-head">
      <span class="tools-title">${running ? "检索中" : `${items.length} 个工具`}</span>
      <span class="tools-chips">${items.map(toolChip).join("")}</span>
    </div>
    <div class="tools-list" hidden>${items.map(toolRow).join("")}</div>
  </div>`;
  clearTimeout(t.thinkTimer);
  t.thinkTimer = setTimeout(() => collapseStages(t, items), 380 + items.length * 40);
}

// 折叠：只留一行摘要（工具名 + 命中数），点开是每个工具的明细
function collapseStages(t, items) {
  const box = t.think.querySelector(".tools");
  const list = box && box.querySelector(".tools-list");
  if (!box || !list) return;
  box.classList.remove("running");
  const ms = items.reduce((sum, s) => sum + (s.ms || 0), 0);
  box.querySelector(".tools-head").classList.add("clickable");
  const title = box.querySelector(".tools-title");
  title.textContent = `${items.length} 个工具`;
  if (ms) {
    const tail = document.createElement("span");
    tail.className = "tools-ms";
    tail.textContent = `${ms}ms`;
    box.querySelector(".tools-head").appendChild(tail);
  }
  const chev = document.createElement("span");
  chev.className = "tools-chev";
  chev.setAttribute("aria-hidden", "true");
  chev.textContent = "⌄";
  box.querySelector(".tools-head").appendChild(chev);
  box.querySelector(".tools-head").addEventListener("click", () => {
    const open = list.hidden;
    list.hidden = !open;
    chev.classList.toggle("open", open);
  });
}

/* 没有阶段数据（结构化直达、报错）时不要把“检索中”留在界面上 */
function finishThink(t) {
  if (!t.think) return;
  clearTimeout(t.thinkTimer);
  if (!t.think.querySelector(".tools")) t.think.hidden = true;
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

// 结构化对比：一个产品一张卡、一条记录一行键值（后端 /api/chat 的 table 事件）
const SAMPLE_PAYLOAD = {
  title: "命中 2 个产品 · 3 条记录 · 按材质",
  reason: "按材质",
  records: 3,
  products: 2,
  conclusion: "结论: Perlast G75M 温度范围 -20 ~ 230 °C，跨度 250 °C",
  conclusion_refs: [1],
  missing: [],
  missing_total: 0,
  truncated: 0,
  notes: [],
  groups: [
    {
      product: "Perlast G75M",
      shared_with: [],
      records: [
        {
          fields: [
            { key: "temp_range", label: "温度范围", value: "-20 ~ 230 °C" },
            { key: "test_temp", label: "测试温度", value: "200 °C" },
            { key: "test_duration", label: "测试时长", value: "70 h" },
            { key: "cs", label: "压缩永久变形", value: "18%" },
            { key: "material", label: "材质", value: "全氟醚橡胶" },
          ],
          refs: [1],
        },
      ],
    },
    {
      product: "Syensqo Tecnoflon P 959",
      shared_with: ["Perlast G75M"],
      records: [
        {
          fields: [
            { key: "temp_range", label: "温度范围", value: "-20 ~ 200 °C" },
            { key: "test_temp", label: "测试温度", value: "175 °C" },
            { key: "test_duration", label: "测试时长", value: "70 h" },
            { key: "cs", label: "压缩压缩永久变形", value: "25%" },
            { key: "material", label: "材质", value: "氟橡胶" },
          ],
          refs: [2],
        },
      ],
    },
  ],
};

/* 按视图配置裁字段：显隐 → 中文名 → 顺序。服务端已按同一份配置裁过一次，
   这里再做一遍是为了面板里的实时预览（草稿配置还没保存）。 */
function applyFieldView(fields, view) {
  const list = fields || [];
  if (!view || !Array.isArray(view.fields) || !view.fields.length) return list;
  const meta = new Map(view.fields.map((f) => [f.key, f]));
  const rank = new Map(view.fields.map((f, i) => [f.key, i]));
  return list
    .filter((f) => (meta.has(f.key) ? meta.get(f.key).visible !== false : true))
    .map((f) => ({ ...f, label: (meta.get(f.key) && meta.get(f.key).label) || f.label }))
    .sort((a, b) => (rank.has(a.key) ? rank.get(a.key) : 999) - (rank.has(b.key) ? rank.get(b.key) : 999));
}

function renderCompare(p, view) {
  if (!p || !p.groups || !p.groups.length) return "";
  view = view || state.cardView || null;
  const show = (view && view.show) || {};
  const limit = (view && view.records_per_product) || 0;
  const cols = (view && view.columns) || 1;
  const refChips = (list) =>
    show.refs === false
      ? ""
      : (list || [])
          .map((n) => `<button type="button" class="cite" data-idx="${n}" title="跳到引用 ${n}">${n}</button>`)
          .join("");

  const cards = p.groups
    .map((group) => {
      const recordsRaw = limit ? (group.records || []).slice(0, limit) : group.records || [];
      const records = recordsRaw
        .map((record) => {
          const fields = applyFieldView(record.fields, view)
            .map(
              (f) =>
                `<div class="cmp-pair"><dt>${esc(f.label)}${
                  f.extra ? `<em class="cmp-extra">等 ${f.extra} 个值</em>` : ""
                }</dt><dd>${esc(f.value)}</dd></div>`
            )
            .join("");
          return `<div class="cmp-rec"><dl class="cmp-fields" style="--cv-cols:${cols}">${fields}</dl>${
            record.refs && record.refs.length ? `<div class="cmp-refs">${refChips(record.refs)}</div>` : ""
          }</div>`;
        })
        .join("");
      const shared =
        show.shared !== false && (group.shared_with || []).length
          ? `<span class="cmp-shared" title="与 ${esc((group.shared_with || []).join("、"))} 数值完全相同，可能同源">同源 · ${esc((group.shared_with || []).join("、"))}</span>`
          : "";
      return `<article class="cmp-card">
        <header class="cmp-card-head">
          <h4 class="cmp-product">${esc(group.product || "未标产品")}</h4>
          <span class="cmp-rec-count">${recordsRaw.length} 条记录</span>
          ${shared}
        </header>
        ${show.note !== false && group.note ? `<p class="cmp-note-inline">${esc(group.note)}</p>` : ""}
        ${records}
      </article>`;
    })
    .join("");

  const conclusion =
    show.conclusion === false ? "" : (p.conclusion || "").replace(/^结论[:：]\s*/, "");
  const notes = show.footer === false ? [] : [...(p.notes || [])];
  if (show.footer !== false) {
    if (p.missing_total) notes.push(`另有 ${p.missing_total} 个产品库内没有可对比的记录：${(p.missing || []).join("、")}`);
    if (p.truncated) notes.push(`还有 ${p.truncated} 个产品未列出，加供应商或牌号可缩小范围`);
  }
  const footer =
    show.footer === false
      ? ""
      : `<footer class="cmp-foot">
      <span>数值取自库内记录；缺单位的按字段补齐单位，未换算、未推断；点引用编号可核对原文。</span>
      ${notes.map((n) => `<span class="cmp-note">${esc(n)}</span>`).join("")}
    </footer>`;
  return `<section class="cmp">
    <header class="cmp-head">
      <span class="cmp-title">${esc(p.title || "产品对比")}</span>
      <span class="cmp-count">${/条记录/.test(p.title || "") ? "" : `${p.records || 0} 条记录`}</span>
    </header>
    ${conclusion ? `<div class="cmp-conclusion"><span class="cmp-badge">结论</span><span>${esc(conclusion)}</span>${refChips(p.conclusion_refs)}</div>` : ""}
    <div class="cmp-body">${cards}</div>
    ${footer}
  </section>`;
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

/* 库里没有这个资料时，让用户一句话上报需求（合并计数，后台能看到谁在等） */
function renderMissReport(body, question) {
  if (body.querySelector(".miss")) return;
  const box = document.createElement("div");
  box.className = "miss";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = "需要这个资料，催一下";
  const note = document.createElement("span");
  note.className = "miss-note";
  box.appendChild(btn);
  box.appendChild(note);
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    note.textContent = "上报中…";
    try {
      const res = await fetch("/api/failures", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, trigger: "manual_no_hit" }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "上报失败");
      note.textContent =
        (data.hits || 1) > 1 ? `已记录，共 ${data.hits} 人在等这份资料` : "已记录，会按需补充资料";
      btn.remove();
    } catch (err) {
      btn.disabled = false;
      note.textContent = err.message;
    }
  });
  body.appendChild(box);
}

/* ───────── 投喂资料 ───────── */

async function refreshCollections() {
  try {
    const res = await fetch("/api/bootstrap");
    const data = await res.json();
    if (data.collections) state.collections = data.collections;
    if (data.applications) state.applications = data.applications.industries || [];
    renderNav();
    if (typeof renderSeg === "function") renderSeg();
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
  let cards = null;
  let started = performance.now();
  let frame = 0;

  const paint = () => {
    frame = 0;
    t.body.innerHTML = renderMD(text) + (text ? '<span class="caret"></span>' : "");
    scrollToBottom();
  };

  const handle = (evt) => {
    if (evt.type === "stage") {
      renderStages(t, evt.items || []);
    } else if (evt.type === "meta") {
      t.stat.textContent = `${evt.hits} 条命中`;
      $("#hits").textContent = `${evt.hits} 条命中 · ${state.active}`;
      $("#hits").classList.add("on");
    } else if (evt.type === "table") {
      // 结构化对比：卡片自己带引用编号，随后的 Markdown 宽表就不再往正文里塞
      const payload = evt.data || null;
      if (payload && payload.groups && payload.groups.length) {
        cards = payload;
        state.lastCompare = payload;
        t.body.innerHTML = renderCompare(payload);
        scrollToBottom();
      }
    } else if (evt.type === "sources") {
      sources = evt.items || [];
    } else if (evt.type === "token") {
      text += evt.text || "";
      if (cards) return; // 正文已由卡片承担，token 只留着给「复制」用
      if (!frame) frame = requestAnimationFrame(paint);
    } else if (evt.type === "done") {
      state.controller = null;
      finishThink(t);
      if (!text && !cards) return;
      const ms = evt.elapsed_ms || Math.round(performance.now() - started);
      t.stat.textContent = cards
        ? `${ms}ms · ${cards.products || 0} 个产品 · ${cards.records || 0} 条记录`
        : `${ms}ms · ${Math.round(text.length)} 字`;
      if (!cards) t.body.innerHTML = renderMD(text);
      // 卡片模式下列出的引用就是卡片里点到的那几条
      const cited = cards ? sources : citedSources(sources, text);
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
      finishThink(t);
      showError(t.body, evt.message || "请求失败", () => {
        t.turn.remove();
        ask(q);
      });
      if (evt.miss) renderMissReport(t.body, q);
      scrollToBottom();
    }
  };

  try {
    state.controller = new AbortController();
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: q,
        collection: "catalog",
        product: state.active,
        industry: state.industry,
        reasoning: state.reasoning,
      }),
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
    items.push({ kind: "产品", label: `${c.label} · ${c.full}`, action: () => setActive(c.name), text: `${c.label} ${c.full} ${c.name}` })
  );
  EXAMPLES.forEach((ex) =>
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
  // 思考强度：默认最低，选过就记住；只影响下一次提问
  const reasoningBox = $("#reasoning");
  const paintReasoning = () => {
    reasoningBox.querySelectorAll("button").forEach((btn) =>
      btn.classList.toggle("on", btn.dataset.r === state.reasoning)
    );
  };
  reasoningBox.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-r]");
    if (!btn) return;
    state.reasoning = btn.dataset.r;
    localStorage.setItem("mme.reasoning", state.reasoning);
    paintReasoning();
  });
  paintReasoning();

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

  $("#upBtn").addEventListener("click", () => {
    // 先展开入库队列：刷新后也能从这里查看正在投喂的进度
    if (typeof refreshQueue === "function") refreshQueue();
    $("#fileInput").click();
  });
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
    state.applications = (data.applications && data.applications.industries) || [];
  } catch (e) {
    state.collections = [{ name: "all", label: "全部产品", full: "离线", hint: "离线", count: 0 }];
  }

  if (!state.collections.length) {
    state.collections = [{ name: "all", label: "全部产品", full: "", hint: "", count: 0 }];
  }

  const saved = localStorage.getItem("mme.collection");
  if (saved && state.collections.some((c) => c.name === saved)) state.active = saved;
  const savedApp = localStorage.getItem("mme.industry") || "";
  if (savedApp && state.applications.some((a) => a.code === savedApp)) state.industry = savedApp;

  renderNav();
  if (typeof renderSeg === "function") renderSeg();
  if (typeof renderFolderSelects === "function") renderFolderSelects();
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

/* 未命中看板入口：只给管理员显示，普通成员看不到这个按钮 */
async function revealAdminEntry() {
  const link = document.getElementById("admBtn");
  if (!link) return;
  try {
    const data = await (await fetch("/api/auth/me")).json();
    link.hidden = !(data.user && data.user.role === "admin");
  } catch (e) {
    link.hidden = true;
  }
}
revealAdminEntry();

/* 游客（未登录）只能看，隐藏投喂、账号、退出这些要登录的入口 */
async function applyGuestMode() {
  try {
    const data = await (await fetch("/api/auth/me")).json();
    if (data.user) return;
  } catch (e) {
    /* 拿不到登录态也按游客处理 */
  }
  ["upBtn", "acctBtn", "logoutBtn", "admBtn"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.hidden = true;
  });
}
applyGuestMode();

async function loadAcctUsers() {
  const box = document.getElementById("acctAdmin");
  const select = document.getElementById("acctUserSel");
  if (!box || !select) return;
  try {
    const me = await (await fetch("/api/auth/me")).json();
    const keysBox = document.getElementById("acctKeys");
    if (!me.user || me.user.role !== "admin") {
      box.hidden = true;
      if (keysBox) keysBox.hidden = true;
      return;
    }
    const data = await (await fetch("/api/users")).json();
    select.innerHTML = (data.items || [])
      .map((u) => `<option value="${u.id}">${esc(u.username)}（${u.role === "admin" ? "管理员" : "成员"}）</option>`)
      .join("");
    box.hidden = false;
    if (keysBox) keysBox.hidden = false;
    if (window.loadKeys) window.loadKeys();
    loadAcctMisses();
  } catch (e) {
    box.hidden = true;
    const keysBox = document.getElementById("acctKeys");
    if (keysBox) keysBox.hidden = true;
  }
}

async function loadAcctMisses() {
  const box = document.getElementById("acctMisses");
  const list = document.getElementById("acctMissList");
  if (!box || !list) return;
  try {
    const data = await (await fetch("/api/failures/misses?limit=50")).json();
    const items = data.items || [];
    if (!items.length) {
      list.innerHTML = `<div class="acct-note">暂无未命中的问题</div>`;
      box.hidden = false;
      return;
    }
    const rows = items
      .slice(0, 10)
      .map(
        (it) =>
          `<div class="acct-miss"><span class="acct-miss-n">${it.hits}</span>` +
          `<span class="acct-miss-q">${esc(it.question)}</span></div>`
      )
      .join("");
    const wanted = (data.wanted || []).slice(0, 8).map((w) => esc(w.word));
    list.innerHTML =
      rows + (wanted.length ? `<div class="acct-miss-want">待补：${wanted.join(" · ")}</div>` : "");
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

/* ───────── 卡片视图设置面板 ─────────
   字段顺序 / 显隐 / 中文名、每产品记录数、卡片列数、元素开关。
   改的是 core/cardview.py 存的那份配置，保存后所有人生效，不用改代码。 */

let cvDraft = null;

async function loadCardView() {
  try {
    const data = await (await fetch("/api/card-view")).json();
    state.cardView = data.view;
  } catch (err) {
    state.cardView = null;
  }
}

function cvPreview() {
  const base = state.lastCompare || SAMPLE_PAYLOAD;
  const box = $("#cvPreview");
  if (!box || !cvDraft) return;
  box.innerHTML = `<div class="cv-preview-head">实时预览</div>${renderCompare(base, cvDraft)}`;
}

function renderCardViewPanel() {
  const v = cvDraft;
  if (!v || !Array.isArray(v.fields) || !v.fields.length) {
    $("#cvFields").innerHTML = '<li class="cv-empty">没有可配置的字段</li>';
    $("#cvControls").innerHTML = "";
    $("#cvPreview").innerHTML = "";
    return;
  }
  v.show = v.show || {};
  $("#cvFields").innerHTML = v.fields
    .map(
      (f, i) => `<li class="cv-field${f.visible ? "" : " off"}" data-i="${i}">
        <span class="cv-move">
          <button type="button" class="cv-up" ${i === 0 ? "disabled" : ""} aria-label="上移">↑</button>
          <button type="button" class="cv-down" ${i === v.fields.length - 1 ? "disabled" : ""} aria-label="下移">↓</button>
        </span>
        <input type="checkbox" class="cv-vis" ${f.visible ? "checked" : ""} aria-label="显示该字段">
        <input class="cv-label" value="${esc(f.label)}" maxlength="24" aria-label="字段中文名">
        <code class="cv-key">${esc(f.key)}</code>
      </li>`
    )
    .join("");

  const showToggles = [
    ["conclusion", "结论"],
    ["refs", "引用角标"],
    ["shared", "同源标记"],
    ["note", "缺区间提示"],
    ["missing", "缺资料说明"],
    ["footer", "页脚说明"],
  ];
  $("#cvControls").innerHTML = `
    <label class="cv-row">每产品记录数
      <select id="cvRecords">${[1, 2, 3, 4, 5, 6, 7, 8]
        .map((n) => `<option value="${n}" ${n === v.records_per_product ? "selected" : ""}>${n}</option>`)
        .join("")}</select>
    </label>
    <label class="cv-row">卡片列数
      <select id="cvCols">${[1, 2, 3, 4]
        .map((n) => `<option value="${n}" ${n === v.columns ? "selected" : ""}>${n}</option>`)
        .join("")}</select>
    </label>
    <fieldset class="cv-show">${showToggles
      .map(
        ([key, label]) =>
          `<label><input type="checkbox" class="cv-show-${key}" ${v.show[key] ? "checked" : ""}>${label}</label>`
      )
      .join("")}</fieldset>`;
  cvPreview();
}

/* 兜底视图：接口拿不到时（服务没重启、未登录、路由 404）面板仍然可用，
   否则用户点开只看到一片空白，还以为功能坏了。 */
function fallbackCardView() {
  if (state.cardView && Array.isArray(state.cardView.fields) && state.cardView.fields.length) {
    return state.cardView;
  }
  const labels = {
    grade: "牌号", supplier: "供应商", composition: "组成", material: "材质",
    application: "应用场景", industries: "行业", parts: "部件", media: "介质",
    certs: "认证", temp_range: "温度范围", cs: "压缩永久变形",
    chem_resistance: "耐化学性", test_medium: "测试介质", test_temp: "测试温度",
    test_duration: "测试时长", confidence: "置信度",
  };
  return {
    fields: Object.keys(labels).map((key) => ({ key, label: labels[key], visible: true })),
    records_per_product: 3,
    columns: 3,
    show: { conclusion: true, refs: true, shared: true, note: true, missing: true, footer: true },
  };
}

async function fetchCardView() {
  const res = await fetch("/api/card-view", { headers: { Accept: "application/json" } });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch (err) {
      /* 非 JSON 响应，保留 HTTP 状态 */
    }
    if (res.status === 404) detail = "接口不存在（服务未重启？）";
    throw new Error(detail);
  }
  const data = await res.json();
  const view = data && data.view;
  if (!view || !Array.isArray(view.fields) || !view.fields.length) {
    throw new Error("配置为空");
  }
  return view;
}

async function openCardView() {
  $("#cardView").hidden = false;
  $("#cvStatus").textContent = "";
  try {
    cvDraft = await fetchCardView();
    state.cardView = cvDraft;
  } catch (err) {
    cvDraft = fallbackCardView();
    $("#cvStatus").textContent = `读不到服务端配置（${err.message}），已用本地默认值；保存可能失败`;
    $("#cvStatus").classList.add("bad");
  }
  try {
    renderCardViewPanel();
  } catch (err) {
    $("#cvFields").innerHTML = `<li class="cv-empty">面板渲染失败：${esc(err.message)}</li>`;
    $("#cvStatus").textContent = `面板渲染失败：${err.message}`;
    $("#cvStatus").classList.add("bad");
  }
}

function moveField(from, to) {
  if (!cvDraft || to < 0 || to >= cvDraft.fields.length) return;
  const [item] = cvDraft.fields.splice(from, 1);
  cvDraft.fields.splice(to, 0, item);
  renderCardViewPanel();
}

function bindCardView() {
  const btn = $("#cardViewBtn");
  if (!btn) return;
  btn.addEventListener("click", openCardView);
  $("#cvClose").addEventListener("click", () => ($("#cardView").hidden = true));
  $("#cardView").addEventListener("click", (e) => {
    if (e.target.id === "cardView") $("#cardView").hidden = true;
  });

  $("#cvFields").addEventListener("click", (e) => {
    const row = e.target.closest(".cv-field");
    if (!row || !cvDraft) return;
    const index = Number(row.dataset.i);
    if (e.target.closest(".cv-up")) return moveField(index, index - 1);
    if (e.target.closest(".cv-down")) return moveField(index, index + 1);
  });
  $("#cvFields").addEventListener("change", (e) => {
    const row = e.target.closest(".cv-field");
    if (!row || !cvDraft) return;
    const index = Number(row.dataset.i);
    if (e.target.classList.contains("cv-vis")) {
      cvDraft.fields[index].visible = e.target.checked;
      row.classList.toggle("off", !e.target.checked);
      cvPreview();
    }
  });
  $("#cvFields").addEventListener("input", (e) => {
    const row = e.target.closest(".cv-field");
    if (!row || !cvDraft || !e.target.classList.contains("cv-label")) return;
    cvDraft.fields[Number(row.dataset.i)].label = e.target.value;
    cvPreview();
  });

  $("#cvControls").addEventListener("change", (e) => {
    if (!cvDraft) return;
    if (e.target.id === "cvRecords") cvDraft.records_per_product = Number(e.target.value);
    else if (e.target.id === "cvCols") cvDraft.columns = Number(e.target.value);
    else if (e.target.classList.contains("cv-show")) {
      const key = e.target.className.replace("cv-show-", "");
      cvDraft.show[key] = e.target.checked;
    } else return;
    cvPreview();
  });

  $("#cvSave").addEventListener("click", async () => {
    if (!cvDraft) return;
    $("#cvStatus").textContent = "保存中…";
    try {
      const res = await fetch("/api/card-view", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cvDraft),
      });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (body && body.detail) detail = body.detail;
        } catch (err) {
          /* 非 JSON */
        }
        throw new Error(detail);
      }
      const data = await res.json();
      state.cardView = data.view;
      cvDraft = data.view;
      $("#cvStatus").textContent = "已保存，之后的卡片按新配置渲染";
      $("#cvStatus").classList.remove("bad");
      renderCardViewPanel();
    } catch (err) {
      $("#cvStatus").textContent = `保存失败：${err.message}`;
      $("#cvStatus").classList.add("bad");
    }
  });

  $("#cvReset").addEventListener("click", async () => {
    try {
      const data = await (await fetch("/api/card-view/reset", { method: "POST" })).json();
      state.cardView = data.view;
      cvDraft = data.view;
      $("#cvStatus").textContent = "已恢复默认";
      $("#cvStatus").classList.remove("bad");
      renderCardViewPanel();
    } catch (err) {
      $("#cvStatus").textContent = `重置失败：${err.message}`;
    }
  });
}

loadCardView();
bindCardView();
if (location.hash === "#cardview") openCardView();
