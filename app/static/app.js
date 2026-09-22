/* 活动席位编排 —— 原生 JS，无框架依赖 */
"use strict";

const state = { sections: [], tables: [], participants: [], rules: [], assignments: [] };
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data.detail ? JSON.stringify(data.detail) : `请求失败 (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

function banner(text, ok = false) {
  const el = $("#banner");
  el.textContent = text;
  el.className = "banner " + (ok ? "ok" : "err");
  clearTimeout(banner._t);
  banner._t = setTimeout(() => el.classList.add("hidden"), 5000);
}

const RULE_LABEL = { must_together: "必须同桌", must_not: "禁止同桌", fixed: "固定席位" };

function participantName(id) {
  const p = state.participants.find((x) => x.id === id);
  return p ? p.name : `#${id}`;
}
function tableName(id) {
  const t = state.tables.find((x) => x.id === id);
  return t ? t.name : `#${id}`;
}
function ruleText(r) {
  if (r.rule_type === "fixed") {
    return `${RULE_LABEL.fixed}：${participantName(r.pid)} → 桌位「${tableName(r.tid)}」`;
  }
  return `${RULE_LABEL[r.rule_type]}：${r.pids.map(participantName).join("、")}`;
}
const assignmentOf = (pid) => state.assignments.find((a) => a.participant_id === pid);

// ------------------------------------------------------------------ 渲染

async function refresh() {
  const data = await api("/api/state");
  Object.assign(state, data);
  renderAll();
}

function renderAll() {
  renderSummary();
  renderFloor();
  renderParticipants();
  renderVenues();
  renderRulesTab();
}

function renderSummary() {
  const seated = state.assignments.length;
  const locked = state.assignments.filter((a) => a.locked).length;
  const cap = state.tables.reduce((s, t) => s + t.capacity, 0);
  $("#arrange-summary").innerHTML =
    `参与者 <b>${state.participants.length}</b> 人 · 总座位 <b>${cap}</b> · ` +
    `已安排 <b>${seated}</b> · 已锁定 <b>${locked}</b>`;
}

function renderFloor() {
  const floor = $("#floor");
  floor.innerHTML = "";
  const secOf = (t) => state.sections.find((s) => s.id === t.section_id);

  for (const t of state.tables) {
    const seated = state.assignments.filter((a) => a.table_id === t.id);
    const sec = secOf(t);
    const card = document.createElement("div");
    card.className = "table-card" + (seated.length > t.capacity ? " overfull" : "");
    const head = document.createElement("h4");
    head.innerHTML =
      `<span>${t.name}</span>` +
      `<span class="meta">${sec ? sec.name + " · " : ""}${seated.length}/${t.capacity}</span>`;
    card.appendChild(head);

    const ul = document.createElement("ul");
    ul.className = "seat-list";
    for (const a of seated) {
      const p = state.participants.find((x) => x.id === a.participant_id);
      if (!p) continue;
      const li = document.createElement("li");
      li.className = "seat" + (a.locked ? " locked" : "");
      li.innerHTML =
        `<span class="pname">${p.name}` +
        (p.note ? ` <span class="pnote">(${p.note})</span>` : "") + `</span>`;
      const sel = document.createElement("select");
      for (const tb of state.tables) {
        const o = document.createElement("option");
        o.value = tb.id;
        o.textContent = tb.name;
        if (tb.id === t.id) o.selected = true;
        sel.appendChild(o);
      }
      sel.addEventListener("change", async () => {
        await api("/api/assignments/lock", {
          method: "POST",
          body: JSON.stringify({ participant_id: p.id, table_id: Number(sel.value), locked: true }),
        });
        await refresh();
      });
      const lock = document.createElement("label");
      lock.className = "lock-toggle";
      lock.innerHTML = `<input type="checkbox" ${a.locked ? "checked" : ""}> 锁定`;
      lock.querySelector("input").addEventListener("change", async (e) => {
        if (e.target.checked) {
          await api("/api/assignments/lock", {
            method: "POST",
            body: JSON.stringify({ participant_id: p.id, table_id: t.id, locked: true }),
          });
        } else {
          await api(`/api/assignments/unlock-one/${p.id}`, { method: "POST" });
        }
        await refresh();
      });
      li.appendChild(sel);
      li.appendChild(lock);
      ul.appendChild(li);
    }
    for (let i = seated.length; i < t.capacity; i++) {
      const li = document.createElement("li");
      li.className = "empty-seat";
      li.textContent = "空席位";
      ul.appendChild(li);
    }
    card.appendChild(ul);
    floor.appendChild(card);
  }

  // 未安排者
  const unseated = state.participants.filter((p) => !assignmentOf(p.id));
  const box = $("#unseated");
  if (unseated.length) {
    box.classList.remove("hidden");
    box.innerHTML = "<h4>未安排参与者</h4>";
    const list = document.createElement("div");
    list.className = "unseated-list";
    for (const p of unseated) {
      const seat = document.createElement("div");
      seat.className = "seat";
      seat.innerHTML = `<span class="pname">${p.name}</span>`;
      const sel = document.createElement("select");
      sel.innerHTML = `<option value="">手动安排到…</option>`;
      for (const tb of state.tables) {
        const o = document.createElement("option");
        o.value = tb.id;
        o.textContent = tb.name;
        sel.appendChild(o);
      }
      sel.addEventListener("change", async () => {
        await api("/api/assignments/lock", {
          method: "POST",
          body: JSON.stringify({ participant_id: p.id, table_id: Number(sel.value), locked: true }),
        });
        await refresh();
      });
      seat.appendChild(sel);
      list.appendChild(seat);
    }
    box.appendChild(list);
  } else {
    box.classList.add("hidden");
  }
}

function renderParticipants() {
  const tbody = $("#participant-rows");
  tbody.innerHTML = "";
  for (const p of state.participants) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${p.id}</td><td>${p.name}</td><td>${p.note || ""}</td>` +
      `<td style="text-align:right"><button class="btn-mini">删除</button></td>`;
    tr.querySelector("button").addEventListener("click", async () => {
      if (!confirm(`删除参与者「${p.name}」？相关规则与席位会一并删除。`)) return;
      await api(`/api/participants/${p.id}`, { method: "DELETE" });
      await refresh();
    });
    tbody.appendChild(tr);
  }
}

function renderVenues() {
  const sbody = $("#section-rows");
  sbody.innerHTML = "";
  for (const s of state.sections) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${s.id}</td><td>${s.name}</td>` +
      `<td>${s.capacity ? s.capacity + " 人" : "不限"}</td>` +
      `<td style="text-align:right"><button class="btn-mini">删除</button></td>`;
    tr.querySelector("button").addEventListener("click", async () => {
      await api(`/api/sections/${s.id}`, { method: "DELETE" });
      await refresh();
    });
    sbody.appendChild(tr);
  }

  const tbody = $("#table-rows");
  tbody.innerHTML = "";
  const secName = (id) => {
    const s = state.sections.find((x) => x.id === id);
    return s ? s.name : "—";
  };
  for (const t of state.tables) {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${t.id}</td><td>${t.name}</td><td>${t.capacity}</td>` +
      `<td>${secName(t.section_id)}</td>` +
      `<td style="text-align:right"><button class="btn-mini">删除</button></td>`;
    tr.querySelector("button").addEventListener("click", async () => {
      await api(`/api/tables/${t.id}`, { method: "DELETE" });
      await refresh();
    });
    tbody.appendChild(tr);
  }

  // 桌位表单的分区下拉
  const sel = $("#form-table select[name=section_id]");
  const cur = sel.value;
  sel.innerHTML = `<option value="">不属于分区</option>` +
    state.sections.map((s) => `<option value="${s.id}">${s.name}</option>`).join("");
  sel.value = cur;
}

function renderRulesTab() {
  // 参与者多选 chips
  const picker = $("#rule-pids");
  picker.innerHTML = "";
  const single = $("#rule-pid");
  single.innerHTML = "";
  if (!state.participants.length) {
    picker.innerHTML = `<span class="hint">请先在「参与者」页签录入参与者。</span>`;
  }
  for (const p of state.participants) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = p.name;
    chip.dataset.pid = p.id;
    chip.addEventListener("click", () => chip.classList.toggle("selected"));
    picker.appendChild(chip);

    const radio = document.createElement("label");
    radio.className = "chip";
    radio.innerHTML = `<input type="radio" name="fixed-pid" value="${p.id}"> ${p.name}`;
    single.appendChild(radio);
  }

  // 固定席位桌位下拉
  const tidSel = $("#rule-tid");
  tidSel.innerHTML = state.tables.map((t) => `<option value="${t.id}">${t.name}</option>`)
    .join("") || `<option value="">（请先创建桌位）</option>`;

  // 规则列表
  const ul = $("#rule-list");
  ul.innerHTML = "";
  if (!state.rules.length) {
    ul.innerHTML = `<li class="hint">暂无规则。</li>`;
  }
  for (const r of state.rules) {
    const li = document.createElement("li");
    li.id = `rule-row-${r.id}`;
    const span = document.createElement("span");
    const text = ruleText(r).replace(/^[^：]+：/, "");
    span.innerHTML =
      `<span class="rule-kind ${r.rule_type}">${RULE_LABEL[r.rule_type]}</span>` +
      text +
      (r.note ? `<span class="rule-note">（${r.note}）</span>` : "");
    const del = document.createElement("button");
    del.className = "btn-mini";
    del.textContent = "删除";
    del.addEventListener("click", async () => {
      await api(`/api/rules/${r.id}`, { method: "DELETE" });
      await refresh();
    });
    li.appendChild(span);
    li.appendChild(del);
    ul.appendChild(li);
  }
}

function showConflicts(conflicts) {
  const panel = $("#conflict-panel");
  panel.innerHTML = `<h4>无解：以下约束存在冲突</h4>`;
  for (const c of conflicts) {
    const div = document.createElement("div");
    div.className = "conflict-item";
    const tags = (c.involved_rules || [])
      .map((r) => `<span class="tag" data-rid="${r.rule_id}" title="点击定位规则">规则#${r.rule_id}</span>`)
      .join("");
    div.innerHTML = tags + c.reason;
    panel.appendChild(div);
  }
  panel.classList.remove("hidden");
  $$("#conflict-panel .tag").forEach((tag) =>
    tag.addEventListener("click", () => {
      switchTab("rules");
      const row = $(`#rule-row-${tag.dataset.rid}`);
      if (row) {
        row.classList.add("hl");
        row.scrollIntoView({ behavior: "smooth", block: "center" });
        setTimeout(() => row.classList.remove("hl"), 4000);
      }
    }));
}

function clearConflicts() {
  $("#conflict-panel").classList.add("hidden");
}

// ------------------------------------------------------------------ 事件

function switchTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".tabpanel").forEach((p) => p.classList.remove("active"));
  $(`#tab-${name}`).classList.add("active");
}

$$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

$("#btn-solve").addEventListener("click", async () => {
  clearConflicts();
  const seed = Number($("#seed").value) || 0;
  try {
    const res = await api("/api/solve", {
      method: "POST",
      body: JSON.stringify({ seed }),
    });
    await refresh();
    if (res.feasible) {
      banner(`编排成功：${res.seated} 人已落座（${res.locked} 个锁定席位保持不变，搜索节点 ${res.nodes}）`, true);
    } else {
      showConflicts(res.conflicts || [{ reason: "未找到可行安排。", involved_rules: [] }]);
      banner("当前约束无解，请查看具体冲突规则。");
    }
  } catch (e) {
    banner("求解失败：" + e.message);
  }
});

$("#btn-random-seed").addEventListener("click", () => {
  $("#seed").value = Math.floor(Math.random() * 1_000_000);
});

$("#btn-unlock-all").addEventListener("click", async () => {
  await api("/api/assignments/unlock-all", { method: "POST" });
  await refresh();
  banner("已全部解锁，下次编排时所有席位均可重排。", true);
});

$("#btn-clear").addEventListener("click", async () => {
  if (!confirm("清空全部席位安排（参与者、桌位与规则保留）？")) return;
  await api("/api/assignments/clear", { method: "POST" });
  clearConflicts();
  await refresh();
});

$("#btn-export").addEventListener("click", () => {
  window.location.href = "/api/export.csv";
});

$("#form-participant").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  try {
    await api("/api/participants", {
      method: "POST",
      body: JSON.stringify({ name: fd.get("name").trim(), note: fd.get("note").trim() }),
    });
    e.target.reset();
    await refresh();
  } catch (err) {
    banner(err.message);
  }
});

$("#form-section").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  await api("/api/sections", {
    method: "POST",
    body: JSON.stringify({ name: fd.get("name").trim(), capacity: Number(fd.get("capacity")) }),
  });
  e.target.reset();
  e.target.capacity.value = 0;
  await refresh();
});

$("#form-table").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const secVal = fd.get("section_id");
  await api("/api/tables", {
    method: "POST",
    body: JSON.stringify({
      name: fd.get("name").trim(),
      capacity: Number(fd.get("capacity")),
      section_id: secVal ? Number(secVal) : null,
    }),
  });
  e.target.reset();
  await refresh();
});

$("#rule-type").addEventListener("change", (e) => {
  const fixed = e.target.value === "fixed";
  $("#rule-pids").classList.toggle("hidden", fixed);
  $("#rule-pid").classList.toggle("hidden", !fixed);
  $("#rule-tid").classList.toggle("hidden", !fixed);
  const help = {
    must_together: "选择 2 名或更多参与者，求解时他们会被安排在同一桌。",
    must_not: "选择 2 名或更多参与者，求解时其中任意两人都不会在同一桌。",
    fixed: "选择 1 名参与者与一张桌位，该参与者必须坐在指定桌位。",
  };
  $("#rule-help").textContent = help[e.target.value];
});

$("#form-rule").addEventListener("submit", async (e) => {
  e.preventDefault();
  const type = $("#rule-type").value;
  const note = new FormData(e.target).get("note").trim();
  let body;
  if (type === "fixed") {
    const pidRadio = $('#rule-pid input[name="fixed-pid"]:checked');
    if (!pidRadio) return banner("请选择要固定的参与者。");
    body = { rule_type: "fixed", pid: Number(pidRadio.value), tid: Number($("#rule-tid").value), note };
  } else {
    const pids = $$("#rule-pids .chip.selected").map((c) => Number(c.dataset.pid));
    if (pids.length < 2) return banner("请至少选择 2 名参与者。");
    body = { rule_type: type, pids, note };
  }
  try {
    await api("/api/rules", { method: "POST", body: JSON.stringify(body) });
    $$("#rule-pids .chip.selected").forEach((c) => c.classList.remove("selected"));
    const checkedRadio = $('#rule-pid input[name="fixed-pid"]:checked');
    if (checkedRadio) checkedRadio.checked = false;
    e.target.note.value = "";
    await refresh();
  } catch (err) {
    banner(err.message);
  }
});

$("#btn-import-csv").addEventListener("click", () => $("#csv-file").click());
$("#csv-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch("/api/participants/import-csv", { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(JSON.stringify(data.detail));
    banner(`成功导入 ${data.imported} 名参与者。`, true);
    await refresh();
  } catch (err) {
    banner("CSV 导入失败：" + err.message);
  }
  e.target.value = "";
});

// ------------------------------------------------------------------ 启动

(async function init() {
  // 空库时自动载入演示数据，便于直接体验
  try {
    const s = await api("/api/state");
    if (!s.participants.length && !s.tables.length) {
      await api("/api/demo", { method: "POST" });
    }
  } catch (e) {
    // 忽略，等待手动操作
  }
  await refresh();
})();
