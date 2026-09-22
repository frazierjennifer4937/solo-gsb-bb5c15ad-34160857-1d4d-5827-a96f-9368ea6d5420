/* 活动席位编排 —— 原生 JavaScript 前端（无框架、无构建） */
"use strict";

const state = {
  participants: [],
  sections: [],
  tables: [],
  rules: [],
  assignments: [],
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

// ----------------------------------------------------------------------
// API 与提示
// ----------------------------------------------------------------------
async function api(path, options = {}) {
  const opts = Object.assign({ headers: {} }, options);
  if (opts.body && !(opts.body instanceof FormData)) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, opts);
  if (res.status === 204) return null;
  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    data = null;
  }
  if (!res.ok) {
    const msg =
      (data && (data.detail || data.message)) ||
      `请求失败（HTTP ${res.status}）`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

let toastTimer = null;
function toast(message, kind = "ok") {
  const el = $("#toast");
  el.textContent = message;
  el.className = `toast ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3000);
}

async function refreshState() {
  const data = await api("/api/state");
  Object.assign(state, data);
  renderAll();
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function participantMap() {
  return new Map(state.participants.map((p) => [p.id, p]));
}

function ruleTypeLabel(type) {
  return { together: "必须同桌", separate: "禁止同桌", fixed: "固定席位" }[type] || type;
}

// ----------------------------------------------------------------------
// Tab 切换
// ----------------------------------------------------------------------
$$(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    const tab = btn.dataset.tab;
    $$(".tab-panel").forEach((p) =>
      p.classList.toggle("active", p.id === `tab-${tab}`)
    );
  });
});

// ----------------------------------------------------------------------
// 渲染
// ----------------------------------------------------------------------
function renderAll() {
  renderParticipants();
  renderSections();
  renderTables();
  renderRules();
  renderSeating();
}

// ---------- 参与者 ----------
function renderParticipants() {
  $("#p-count").textContent = state.participants.length;
  const tbody = $("#p-tbody");
  tbody.innerHTML = state.participants
    .map(
      (p) => `
      <tr>
        <td class="muted">${p.id}</td>
        <td>${escapeHtml(p.name)}</td>
        <td>${escapeHtml(p.group_name || "—")}</td>
        <td class="muted">${escapeHtml(p.note || "")}</td>
        <td class="col-actions">
          <button class="btn small secondary" data-act="p-edit" data-id="${p.id}">编辑</button>
          <button class="btn small danger ghost" data-act="p-del" data-id="${p.id}">删除</button>
        </td>
      </tr>`
    )
    .join("");
}

$("#btn-add-participant").addEventListener("click", () => {
  $("#p-id").value = "";
  ["p-name", "p-group", "p-note"].forEach((id) => ($(`#${id}`).value = ""));
  $("#participant-form").classList.remove("hidden");
  $("#p-name").focus();
});
$("#p-cancel").addEventListener("click", () =>
  $("#participant-form").classList.add("hidden")
);
$("#p-save").addEventListener("click", async () => {
  const body = {
    name: $("#p-name").value.trim(),
    group_name: $("#p-group").value.trim() || null,
    note: $("#p-note").value.trim() || null,
  };
  if (!body.name) return toast("请填写姓名", "err");
  const id = $("#p-id").value;
  try {
    if (id) await api(`/api/participants/${id}`, { method: "PUT", body });
    else await api("/api/participants", { method: "POST", body });
    $("#participant-form").classList.add("hidden");
    await refreshState();
    toast("已保存");
  } catch (e) {
    toast(e.message, "err");
  }
});

$("#p-tbody").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = Number(btn.dataset.id);
  const p = state.participants.find((x) => x.id === id);
  if (btn.dataset.act === "p-edit") {
    $("#p-id").value = p.id;
    $("#p-name").value = p.name;
    $("#p-group").value = p.group_name || "";
    $("#p-note").value = p.note || "";
    $("#participant-form").classList.remove("hidden");
  } else if (btn.dataset.act === "p-del") {
    if (!confirm(`确定删除参与者「${p.name}」？相关安排与规则也会被清理。`)) return;
    try {
      await api(`/api/participants/${id}`, { method: "DELETE" });
      await refreshState();
      toast("已删除");
    } catch (err) {
      toast(err.message, "err");
    }
  }
});

$("#csv-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await api("/api/participants/import", { method: "POST", body: fd });
    toast(`导入完成：新增 ${r.added} 人，跳过 ${r.skipped} 行`);
    await refreshState();
  } catch (err) {
    toast(err.message, "err");
  }
  e.target.value = "";
});

// ---------- 分区 ----------
function renderSections() {
  $("#s-count").textContent = state.sections.length;
  $("#s-tbody").innerHTML = state.sections
    .map(
      (s) => `
      <tr>
        <td>${escapeHtml(s.name)}</td>
        <td>${s.capacity == null ? "不限" : s.capacity}</td>
        <td class="col-actions">
          <button class="btn small secondary" data-act="s-edit" data-id="${s.id}">编辑</button>
          <button class="btn small danger ghost" data-act="s-del" data-id="${s.id}">删除</button>
        </td>
      </tr>`
    )
    .join("");
}

$("#btn-add-section").addEventListener("click", () => {
  $("#s-id").value = "";
  $("#s-name").value = "";
  $("#s-capacity").value = "";
  $("#section-form").classList.remove("hidden");
  $("#s-name").focus();
});
$("#s-cancel").addEventListener("click", () =>
  $("#section-form").classList.add("hidden")
);
$("#s-save").addEventListener("click", async () => {
  const capVal = $("#s-capacity").value.trim();
  const body = {
    name: $("#s-name").value.trim(),
    capacity: capVal ? Number(capVal) : null,
  };
  if (!body.name) return toast("请填写分区名称", "err");
  const id = $("#s-id").value;
  try {
    if (id) await api(`/api/sections/${id}`, { method: "PUT", body });
    else await api("/api/sections", { method: "POST", body });
    $("#section-form").classList.add("hidden");
    await refreshState();
    toast("已保存");
  } catch (e) {
    toast(e.message, "err");
  }
});
$("#s-tbody").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = Number(btn.dataset.id);
  const s = state.sections.find((x) => x.id === id);
  if (btn.dataset.act === "s-edit") {
    $("#s-id").value = s.id;
    $("#s-name").value = s.name;
    $("#s-capacity").value = s.capacity ?? "";
    $("#section-form").classList.remove("hidden");
  } else if (btn.dataset.act === "s-del") {
    if (!confirm(`删除分区「${s.name}」？下属桌位将变为未分区。`)) return;
    try {
      await api(`/api/sections/${id}`, { method: "DELETE" });
      await refreshState();
    } catch (err) {
      toast(err.message, "err");
    }
  }
});

// ---------- 桌位 ----------
function renderTables() {
  $("#t-count").textContent = state.tables.length;
  const opts =
    '<option value="">（不分区）</option>' +
    state.sections.map((s) => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join("");
  $("#t-section").innerHTML = opts;

  const secName = new Map(state.sections.map((s) => [s.id, s.name]));
  $("#t-tbody").innerHTML = state.tables
    .map(
      (t) => `
      <tr>
        <td>${escapeHtml(t.name)}</td>
        <td>${t.capacity}</td>
        <td>${escapeHtml(t.section_id != null ? secName.get(t.section_id) || "—" : "—")}</td>
        <td class="col-actions">
          <button class="btn small secondary" data-act="t-edit" data-id="${t.id}">编辑</button>
          <button class="btn small danger ghost" data-act="t-del" data-id="${t.id}">删除</button>
        </td>
      </tr>`
    )
    .join("");
}

$("#btn-add-table").addEventListener("click", () => {
  $("#t-id").value = "";
  $("#t-name").value = "";
  $("#t-capacity").value = "8";
  $("#t-section").value = "";
  $("#table-form").classList.remove("hidden");
  $("#t-name").focus();
});
$("#t-cancel").addEventListener("click", () =>
  $("#table-form").classList.add("hidden")
);
$("#t-save").addEventListener("click", async () => {
  const body = {
    name: $("#t-name").value.trim(),
    capacity: Number($("#t-capacity").value),
    section_id: $("#t-section").value ? Number($("#t-section").value) : null,
  };
  if (!body.name) return toast("请填写桌位名称", "err");
  if (!body.capacity || body.capacity < 1) return toast("座位数必须 ≥ 1", "err");
  const id = $("#t-id").value;
  try {
    if (id) await api(`/api/tables/${id}`, { method: "PUT", body });
    else await api("/api/tables", { method: "POST", body });
    $("#table-form").classList.add("hidden");
    await refreshState();
    toast("已保存");
  } catch (e) {
    toast(e.message, "err");
  }
});
$("#t-tbody").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = Number(btn.dataset.id);
  const t = state.tables.find((x) => x.id === id);
  if (btn.dataset.act === "t-edit") {
    $("#t-id").value = t.id;
    $("#t-name").value = t.name;
    $("#t-capacity").value = t.capacity;
    $("#t-section").value = t.section_id ?? "";
    $("#table-form").classList.remove("hidden");
  } else if (btn.dataset.act === "t-del") {
    if (!confirm(`删除桌位「${t.name}」？该桌的安排与固定规则将一并删除。`)) return;
    try {
      await api(`/api/tables/${id}`, { method: "DELETE" });
      await refreshState();
    } catch (err) {
      toast(err.message, "err");
    }
  }
});

// ---------- 规则 ----------
let ruleSelection = new Set();

function renderRules() {
  $("#r-count").textContent = state.rules.length;
  const pm = participantMap();
  $("#r-tbody").innerHTML = state.rules
    .map((r) => {
      const names = r.participant_ids.map((id) => escapeHtml(pm.get(id)?.name || `#${id}`));
      let target = "—";
      if (r.type === "fixed") {
        const t = state.tables.find((x) => x.id === r.table_id);
        target = escapeHtml(t ? t.name : `#${r.table_id}`);
        if (r.seat_no != null) target += ` 第 ${r.seat_no} 席`;
      }
      return `
      <tr${r.enabled ? "" : ' style="opacity:.55"'}>
        <td><span class="type-tag type-${r.type}">${ruleTypeLabel(r.type)}</span></td>
        <td>${names.map((n) => `<span class="rule-pill">${n}</span>`).join("")}</td>
        <td>${target}</td>
        <td>${r.enabled ? '<span class="status-on">● 启用</span>' : '<span class="status-off">○ 停用</span>'}</td>
        <td class="muted">${escapeHtml(r.note || "")}</td>
        <td class="col-actions">
          <button class="btn small secondary" data-act="r-edit" data-id="${r.id}">编辑</button>
          <button class="btn small secondary" data-act="r-toggle" data-id="${r.id}">${r.enabled ? "停用" : "启用"}</button>
          <button class="btn small danger ghost" data-act="r-del" data-id="${r.id}">删除</button>
        </td>
      </tr>`;
    })
    .join("");
}

function buildPeoplePicker(containerId, selectedSet, filterValue = "") {
  const f = filterValue.trim().toLowerCase();
  const list = state.participants.filter((p) => {
    if (!f) return true;
    return (
      p.name.toLowerCase().includes(f) ||
      (p.group_name || "").toLowerCase().includes(f)
    );
  });
  $(`#${containerId}`).innerHTML = list
    .map(
      (p) => `
      <label>
        <input type="checkbox" value="${p.id}" ${selectedSet.has(p.id) ? "checked" : ""} />
        ${escapeHtml(p.name)}
        ${p.group_name ? `<span class="group-tag">${escapeHtml(p.group_name)}</span>` : ""}
      </label>`
    )
    .join("");
  if (list.length === 0) {
    $(`#${containerId}`).innerHTML = '<p class="hint" style="padding:8px">无匹配参与者</p>';
  }
}

function updateSelectedNames() {
  const pm = participantMap();
  const names = [...ruleSelection].map((id) => pm.get(id)?.name || `#${id}`);
  $("#r-selected-count").textContent = ruleSelection.size;
  $("#r-selected-names").textContent = names.join("、");
}

function openRuleForm(rule) {
  $("#r-id").value = rule ? rule.id : "";
  ruleSelection = new Set(rule ? rule.participant_ids : []);
  const type = rule ? rule.type : "together";
  $$('input[name="r-type"]').forEach((radio) => {
    radio.checked = radio.value === type;
  });
  $("#r-filter").value = "";
  $("#r-note").value = rule?.note || "";
  $("#r-enabled").checked = rule ? rule.enabled : true;

  const tableOpts = state.tables
    .map((t) => `<option value="${t.id}">${escapeHtml(t.name)}</option>`)
    .join("");
  $("#r-table").innerHTML = tableOpts;
  if (rule?.table_id) $("#r-table").value = rule.table_id;
  $("#r-seat").value = rule?.seat_no ?? "";

  buildPeoplePicker("r-people", ruleSelection);
  updateSelectedNames();
  applyRuleTypeVisibility();
  $("#rule-form").classList.remove("hidden");
}

function applyRuleTypeVisibility() {
  const type = $('input[name="r-type"]:checked').value;
  $("#r-target-box").style.display = type === "fixed" ? "flex" : "none";
}

$$('input[name="r-type"]').forEach((r) =>
  r.addEventListener("change", applyRuleTypeVisibility)
);
$("#r-filter").addEventListener("input", () =>
  buildPeoplePicker("r-people", ruleSelection, $("#r-filter").value)
);
$("#r-people").addEventListener("change", (e) => {
  const cb = e.target.closest('input[type="checkbox"]');
  if (!cb) return;
  const id = Number(cb.value);
  if (cb.checked) ruleSelection.add(id);
  else ruleSelection.delete(id);
  updateSelectedNames();
});
$("#btn-add-rule").addEventListener("click", () => {
  if (state.participants.length === 0) return toast("请先录入参与者", "err");
  openRuleForm(null);
});
$("#r-cancel").addEventListener("click", () =>
  $("#rule-form").classList.add("hidden")
);
$("#r-save").addEventListener("click", async () => {
  const type = $('input[name="r-type"]:checked').value;
  const ids = [...ruleSelection].sort((a, b) => a - b);
  if (ids.length < 2 && type !== "fixed")
    return toast("必须同桌 / 禁止同桌规则至少选择 2 人", "err");
  if (ids.length < 1) return toast("请选择参与者", "err");
  const body = {
    type,
    participant_ids: ids,
    table_id: null,
    seat_no: null,
    enabled: $("#r-enabled").checked,
    note: $("#r-note").value.trim() || null,
  };
  if (type === "fixed") {
    if (!state.tables.length) return toast("请先创建桌位", "err");
    body.table_id = Number($("#r-table").value);
    const seat = $("#r-seat").value.trim();
    body.seat_no = seat ? Number(seat) : null;
  }
  const id = $("#r-id").value;
  try {
    if (id) await api(`/api/rules/${id}`, { method: "PUT", body });
    else await api("/api/rules", { method: "POST", body });
    $("#rule-form").classList.add("hidden");
    await refreshState();
    toast("规则已保存");
  } catch (e) {
    toast(e.message, "err");
  }
});
$("#r-tbody").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = Number(btn.dataset.id);
  const r = state.rules.find((x) => x.id === id);
  if (btn.dataset.act === "r-edit") {
    openRuleForm(r);
  } else if (btn.dataset.act === "r-toggle") {
    try {
      await api(`/api/rules/${id}`, {
        method: "PATCH",
        body: { enabled: !r.enabled },
      });
      await refreshState();
    } catch (err) {
      toast(err.message, "err");
    }
  } else if (btn.dataset.act === "r-del") {
    if (!confirm("确定删除该规则？")) return;
    try {
      await api(`/api/rules/${id}`, { method: "DELETE" });
      await refreshState();
    } catch (err) {
      toast(err.message, "err");
    }
  }
});

// ---------- 编排结果 ----------
function assignmentBySeat() {
  const m = new Map();
  state.assignments.forEach((a) => m.set(`${a.table_id}:${a.seat_no}`, a));
  return m;
}

function renderSeating() {
  const seatMap = $("#seat-map");
  const bySeat = assignmentBySeat();
  const assignedPids = new Set(state.assignments.map((a) => a.participant_id));

  if (state.tables.length === 0) {
    seatMap.innerHTML = '<p class="hint">还没有桌位，请先在「分区与桌位」中创建。</p>';
  } else {
    seatMap.innerHTML = state.tables
      .map((t) => {
        const section = t.section_name ? ` · ${escapeHtml(t.section_name)} 区` : "";
        const occupied = [...bySeat.values()].filter((a) => a.table_id === t.id).length;
        const seats = [];
        for (let no = 1; no <= t.capacity; no++) {
          const a = bySeat.get(`${t.id}:${no}`);
          if (a) {
            seats.push(`
              <div class="seat occupied ${a.locked ? "locked" : ""}"
                   data-table="${t.id}" data-seat="${no}" data-pid="${a.participant_id}"
                   title="${a.locked ? "已锁定，" : ""}点击可换人或解锁">
                <span class="seat-no">${no}</span>
                <span class="p-name">${escapeHtml(a.participant_name)}</span>
                ${a.group_name ? `<span class="p-group">${escapeHtml(a.group_name)}</span>` : ""}
              </div>`);
          } else {
            seats.push(`
              <div class="seat" data-table="${t.id}" data-seat="${no}" title="点击安排参与者">
                <span class="seat-no">${no}</span>
                <span class="muted">空</span>
              </div>`);
          }
        }
        return `
          <div class="table-card">
            <div class="table-head">
              <span class="t-name">${escapeHtml(t.name)}</span>
              <span class="t-meta">${section}</span>
              <span class="t-count">${occupied}/${t.capacity} 人</span>
            </div>
            <div class="seat-grid">${seats.join("")}</div>
          </div>`;
      })
      .join("");
  }

  const unassigned = state.participants.filter((p) => !assignedPids.has(p.id));
  $("#unassigned-count").textContent = unassigned.length;
  $("#unassigned-list").innerHTML = unassigned
    .map(
      (p) =>
        `<span class="chip">${escapeHtml(p.name)}${
          p.group_name ? `（${escapeHtml(p.group_name)}）` : ""
        }</span>`
    )
    .join("") || '<span class="hint">所有人均已安排 🎉</span>';
}

// ---------- 求解 ----------
$("#btn-solve").addEventListener("click", async () => {
  const seed = Number($("#seed").value) || 0;
  const btn = $("#btn-solve");
  btn.disabled = true;
  btn.textContent = "求解中…";
  try {
    const r = await api("/api/solve", { method: "POST", body: { seed } });
    if (r.solvable) {
      state.assignments = r.assignments;
      renderSeating();
      const box = $("#solve-summary");
      box.classList.remove("hidden");
      box.innerHTML = `<span class="big">✅ 求解成功</span>
        <span class="hint">种子 ${r.seed} · 搜索节点 ${r.nodes} · 共安排 ${r.assignments.length} 人</span>`;
      $("#conflict-box").classList.add("hidden");
      toast("编排完成");
    } else {
      $("#solve-summary").classList.add("hidden");
      const box = $("#conflict-box");
      box.classList.remove("hidden");
      $("#conflict-list").innerHTML = r.conflicts
        .map((c) => `<li>${escapeHtml(c.message)}</li>`)
        .join("");
      toast("当前约束下无解", "err");
    }
  } catch (e) {
    toast(e.message, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "🔄 求解 / 重新求解";
  }
});

$("#btn-reset").addEventListener("click", async () => {
  if (!confirm("确定清空全部安排（包括锁定席位）？")) return;
  try {
    await api("/api/arrangements/reset", { method: "POST" });
    await refreshState();
    $("#solve-summary").classList.add("hidden");
    $("#conflict-box").classList.add("hidden");
    toast("已清空安排");
  } catch (e) {
    toast(e.message, "err");
  }
});

$("#btn-clear-locks").addEventListener("click", async () => {
  try {
    const r = await api("/api/arrangements/clear-locks", { method: "POST" });
    await refreshState();
    toast(`已解除 ${r.unlocked} 个锁定`);
  } catch (e) {
    toast(e.message, "err");
  }
});

// ---------- 席位点击（手动入座 / 换人 / 解锁） ----------
let modalTarget = null;

$("#seat-map").addEventListener("click", (e) => {
  const seat = e.target.closest(".seat");
  if (!seat) return;
  modalTarget = {
    tableId: Number(seat.dataset.table),
    seatNo: Number(seat.dataset.seat),
    currentPid: seat.dataset.pid ? Number(seat.dataset.pid) : null,
    locked: seat.classList.contains("locked"),
  };
  openSeatModal();
});

function openSeatModal() {
  const t = state.tables.find((x) => x.id === modalTarget.tableId);
  const pm = participantMap();
  const currentName = modalTarget.currentPid
    ? pm.get(modalTarget.currentPid)?.name
    : null;
  $("#modal-title").textContent = `${t.name} · 第 ${modalTarget.seatNo} 席${
    currentName ? `（当前：${currentName}${modalTarget.locked ? "，🔒已锁定" : ""}）` : ""
  }`;
  $("#modal-filter").value = "";
  buildModalPeople("");
  $("#seat-modal").classList.remove("hidden");
  $("#modal-filter").focus();
}

function buildModalPeople(filterValue) {
  const f = filterValue.trim().toLowerCase();
  // 可选：所有参与者（可跨桌移动）
  const list = state.participants.filter((p) => {
    if (!f) return true;
    return (
      p.name.toLowerCase().includes(f) ||
      (p.group_name || "").toLowerCase().includes(f)
    );
  });
  const bySeat = assignmentBySeat();
  $("#modal-people").innerHTML =
    (modalTarget.currentPid
      ? `<button class="btn small danger ghost" id="modal-unlock" style="margin:4px">🔓 解锁该席位</button>
         <button class="btn small danger ghost" id="modal-vacate" style="margin:4px">✖ 置空（取消安排）</button>`
      : "") +
    list
      .map((p) => {
        const a = state.assignments.find((x) => x.participant_id === p.id);
        let where = "";
        if (a) {
          const tt = state.tables.find((x) => x.id === a.table_id);
          where = `<span class="group-tag">${tt ? escapeHtml(tt.name) : ""} 第${a.seat_no}席${a.locked ? " 🔒" : ""}</span>`;
        } else {
          where = '<span class="group-tag">未安排</span>';
        }
        return `<label data-pid="${p.id}">
          <input type="radio" name="modal-person" value="${p.id}"
            ${p.id === modalTarget.currentPid ? "checked" : ""} />
          ${escapeHtml(p.name)} ${where}
        </label>`;
      })
      .join("");
}

$("#modal-filter").addEventListener("input", () =>
  buildModalPeople($("#modal-filter").value)
);
$("#modal-cancel").addEventListener("click", () =>
  $("#seat-modal").classList.add("hidden")
);
$("#seat-modal").addEventListener("click", (e) => {
  if (e.target.id === "seat-modal") $("#seat-modal").classList.add("hidden");

  const unlockBtn = e.target.closest("#modal-unlock");
  const vacateBtn = e.target.closest("#modal-vacate");
  const row = e.target.closest("label[data-pid]");

  if (unlockBtn && modalTarget.currentPid) {
    doLock(modalTarget.currentPid, false);
    return;
  }
  if (vacateBtn && modalTarget.currentPid) {
    if (!confirm("确定置空该席位？")) return;
    doVacate(modalTarget.currentPid);
    return;
  }
  if (row) {
    const pid = Number(row.dataset.pid);
    placePerson(pid);
  }
});

async function placePerson(pid) {
  try {
    await api(`/api/arrangements/${pid}`, {
      method: "PUT",
      body: { table_id: modalTarget.tableId, seat_no: modalTarget.seatNo, locked: true },
    });
    $("#seat-modal").classList.add("hidden");
    await refreshState();
    toast("已入座并锁定");
  } catch (e) {
    toast(e.message, "err");
  }
}

async function doLock(pid, locked) {
  try {
    await api(`/api/arrangements/${pid}/lock?locked=${locked ? "true" : "false"}`, {
      method: "PATCH",
    });
    $("#seat-modal").classList.add("hidden");
    await refreshState();
    toast(locked ? "已锁定" : "已解锁");
  } catch (e) {
    toast(e.message, "err");
  }
}

async function doVacate(pid) {
  try {
    await api(`/api/arrangements/${pid}`, { method: "DELETE" });
    $("#seat-modal").classList.add("hidden");
    await refreshState();
    toast("该席位已置空");
  } catch (e) {
    toast(e.message, "err");
  }
}

// ----------------------------------------------------------------------
// 启动
// ----------------------------------------------------------------------
refreshState().catch((e) => toast(`加载失败：${e.message}`, "err"));
