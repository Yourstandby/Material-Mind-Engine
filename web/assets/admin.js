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

document.addEventListener("click", (event) => {
  const btn = event.target.closest(".miss-row button");
  if (btn) resolveRow(btn);
});

load();
