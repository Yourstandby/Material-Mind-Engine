/* 模型接入密钥（设置在「账号设置」弹窗里，仅管理员可见）
   1) 读写后端 .env：LLM / Embedding 的端点、Key、模型，保存即热生效
   2) 连通性自检：真打一次上游，看 Key 对不对、端点通不通
   3) 网关下游 Key：签发（明文只出现一次）、停用、看 24h 用量 */

const ak$ = (s) => document.querySelector(s);
const akEsc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let AKCFG = null;
let AK_LOADED = false;

function akToast(text) {
  if (typeof window.toast === "function") return window.toast(text);
  const host = document.getElementById("toasts");
  if (!host) return;
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<span class="toast-dot"></span><span>${akEsc(text)}</span>`;
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add("in"));
  setTimeout(() => el.remove(), 2200);
}

async function akApi(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "请求失败");
  return data;
}

function akState(set, okText = "已设置") {
  return `<span class="key-state ${set ? "ok" : "bad"}">${set ? okText : "缺失"}</span>`;
}

function akRenderFields() {
  const box = ak$("#akFields");
  if (!box || !AKCFG) return;
  box.innerHTML = (AKCFG.fields || [])
    .map((f) => {
      const input = f.secret
        ? `<input class="key-input" data-name="${akEsc(f.name)}" data-secret="1" value="" placeholder="${
            f.set ? akEsc(f.value) + "（已设置，留空不改）" : "粘贴 Key"
          }"><button type="button" class="key-mini" data-clear="${akEsc(f.name)}">清除</button>`
        : `<input class="key-input" data-name="${akEsc(f.name)}" value="${akEsc(f.value)}" placeholder="${akEsc(f.name)}">`;
      const extra = f.secret ? akState(f.set) : f.overridden ? `<span class="key-state">环境变量覆盖 .env</span>` : "";
      return `<div class="key-label">${akEsc(f.label)}</div><div class="key-cell">${input}${extra}</div>`;
    })
    .join("");

  const llm = AKCFG.llm || {};
  const emb = AKCFG.embedding || {};
  const head = ak$("#akHeadNote");
  if (head) {
    head.innerHTML =
      `<span class="key-state ${llm.ready ? "ok" : "bad"}">LLM ${llm.ready ? "就绪" : "未就绪"}</span> · ` +
      `<span class="key-state ${emb.ready ? "ok" : "bad"}">Embedding ${emb.ready ? "就绪" : "未就绪"}</span>` +
      (llm.gateway ? ` · <span class="key-state ok">走本机网关</span>` : "");
  }
  akRenderGateway(AKCFG.gateway);
}

function akRenderGateway(gateway) {
  const box = ak$("#akList");
  if (!box) return;
  const items = (gateway && gateway.items) || [];
  if (!items.length) {
    box.innerHTML = `<div class="acct-note">还没有签发过下游 Key。</div>`;
    return;
  }
  box.innerHTML =
    `<div class="key-row head"><div>用途</div><div class="num">rpm</div>` +
    `<div class="num">24h 调用 / tokens</div><div></div></div>` +
    items
      .map(
        (k) =>
          `<div class="key-row${k.active ? "" : " off"}">` +
          `<div class="kn">${akEsc(k.name)}<small>${akEsc(k.prefix)}… · ${akEsc((k.created_at || "").slice(0, 10))}` +
          `${k.active ? "" : " · 已停用"}</small></div>` +
          `<div class="num">${k.rpm}</div>` +
          `<div class="num">${k.calls} / ${k.tokens}</div>` +
          `<div class="key-cell"><button type="button" class="key-mini danger" data-revoke="${k.id}"${k.active ? "" : " disabled"}>停用</button></div>` +
          `</div>`
      )
      .join("");
}

async function akLoad(force = false) {
  const box = ak$("#akFields");
  if (!box) return;
  if (AK_LOADED && !force) return;
  try {
    AKCFG = await akApi("GET", "/api/apikeys");
    AK_LOADED = true;
    akRenderFields();
  } catch (err) {
    box.innerHTML = `<div class="acct-note">加载失败：${akEsc(err.message)}</div>`;
  }
}

async function akSave() {
  const values = {};
  document.querySelectorAll("#akFields .key-input").forEach((el) => {
    const name = el.dataset.name;
    const value = el.value.trim();
    if (el.dataset.secret === "1") {
      if (value) values[name] = value;
    } else {
      values[name] = value;
    }
  });
  const btn = ak$("#akSave");
  btn.disabled = true;
  btn.textContent = "保存中…";
  try {
    AKCFG = await akApi("POST", "/api/apikeys/env", { values });
    akRenderFields();
    akToast("已保存，进程内立即生效");
  } catch (err) {
    akToast(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "保存接入配置";
  }
}

async function akClear(name) {
  if (!confirm(`清空 ${name}？清空后对应模型立刻不可用。`)) return;
  try {
    AKCFG = await akApi("POST", "/api/apikeys/env", { values: { [name]: "" } });
    akRenderFields();
    akToast(`${name} 已清空`);
  } catch (err) {
    akToast(err.message);
  }
}

async function akTest(target) {
  const box = ak$("#akResult");
  box.textContent = target === "embedding" ? "正在测 Embedding…" : "正在测 LLM…";
  try {
    const r = await akApi("POST", "/api/apikeys/test", { target });
    box.innerHTML =
      `<span class="key-state ${r.ok ? "ok" : "bad"}">${r.ok ? "通" : "不通"} ${akEsc(r.target)}</span> ` +
      akEsc(r.detail || r.error || "") +
      (r.ms ? ` · ${r.ms}ms` : "") +
      (r.models && r.models.length ? ` · 可用模型：${akEsc(r.models.join(", "))}` : "");
  } catch (err) {
    box.textContent = err.message;
  }
}

async function akCreate() {
  const name = (ak$("#akName").value || "").trim();
  const rpm = Number(ak$("#akRpm").value) || 120;
  const apply_scope = ak$("#akScope").value;
  try {
    const data = await akApi("POST", "/api/apikeys/gateway", { name, rpm, apply_scope });
    AKCFG.gateway = data.gateway;
    akRenderGateway(data.gateway);
    ak$("#akNewKey").innerHTML =
      `<div class="key-new">新 Key（只显示这一次）：<br><code>${akEsc(data.key)}</code>` +
      `<button type="button" class="key-mini" data-copy="${akEsc(data.key)}">复制</button>` +
      (data.applied && data.applied.length
        ? `<div class="acct-note">已写入后端：${akEsc(data.applied.join("、"))}</div>`
        : "") +
      `</div>`;
    ak$("#akName").value = "";
    akToast("已签发");
    if (data.applied && data.applied.length) akLoad(true);
  } catch (err) {
    akToast(err.message);
  }
}

async function akRevoke(btn) {
  if (!confirm("停用这个 Key？调用方会立刻被 401。")) return;
  btn.disabled = true;
  try {
    const data = await akApi("POST", `/api/apikeys/gateway/${btn.dataset.revoke}/revoke`);
    AKCFG.gateway = data.gateway;
    akRenderGateway(data.gateway);
    akToast("已停用");
  } catch (err) {
    btn.disabled = false;
    akToast(err.message);
  }
}

function akCopy(btn) {
  const text = btn.dataset.copy || "";
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(
      () => akToast("已复制"),
      () => akToast("复制失败，请手动选中")
    );
  } else {
    const code = btn.previousElementSibling;
    const range = document.createRange();
    range.selectNodeContents(code);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    akToast("已选中，Ctrl+C 复制");
  }
}

document.addEventListener("click", (event) => {
  const el = event.target;
  if (el.closest("#akSave")) akSave();
  else if (el.closest("#akTestLLM")) akTest("llm");
  else if (el.closest("#akTestEmbed")) akTest("embedding");
  else if (el.closest("#akCreate")) akCreate();
  else if (el.closest("[data-clear]")) akClear(el.closest("[data-clear]").dataset.clear);
  else if (el.closest("[data-revoke]")) akRevoke(el.closest("[data-revoke]"));
  else if (el.closest("[data-copy]")) akCopy(el.closest("[data-copy]"));
});

// app.js 在确认当前账号是管理员后调用
window.loadKeys = akLoad;
