"use strict";
// Reuse SupportOps' textContent DOM rendering and explicit action interaction.
// No caller auth headers, persistence of customer text, automatic retries or polling writes.
const $ = (id) => document.getElementById(id);
const nodes = (selector) => [...document.querySelectorAll(selector)];
let persona = "", conversation = "", busy = false, generation = 0;
let actionId = null, review = null, recent = [], parent = null;
let recordWarnings = [];
const labels = {
  completed: "完成", waiting_confirmation: "等待客户明确确认", waiting_approval: "等待独立运营审批",
  failed: "失败 / 已拒绝", unknown_outcome: "写入结果未知：请人工核对，禁止自动重放",
  pending_confirmation: "等待客户确认", pending_approval: "等待运营审批", submitted: "退款申请已提交（非退款到账）",
  cancelled: "已取消", rejected: "已拒绝", expired: "已过期", invalidated: "状态已变化，请重新核对",
};
const errors = { authorization_denied: "权限拒绝", domain_rejected: "领域拒绝（归属或状态不允许）",
  downstream_timeout: "下游超时", downstream_unavailable: "下游服务不可用",
  invalid_domain_response: "下游响应无法验证", internal_error: "内部处理失败" };
function element(tag, className = "", text = "") {
  const el = document.createElement(tag); el.className = className; el.textContent = String(text); return el;
}
function show(id, text, kind = "") { const el = $(id); el.className = `notice ${kind}`; el.replaceChildren(element("p", "", text)); }
function fields(target, values) {
  const dl = element("dl");
  for (const [key, value] of Object.entries(values)) dl.append(element("dt", "", key), element("dd", "", value ?? "未提供"));
  target.append(dl);
}
function table(target, columns, rows) {
  if (!rows.length) { target.append(element("p", "muted", "空数据：本快照查询返回 0 行")); return; }
  const wrap = element("div", "table-wrap"), t = element("table"), head = element("tr");
  for (const col of columns) head.append(element("th", "", col));
  const thead = element("thead"); thead.append(head); t.append(thead);
  const body = element("tbody");
  for (const row of rows) { const tr = element("tr"); for (const cell of row) tr.append(element("td", "", cell ?? "NULL")); body.append(tr); }
  t.append(body); wrap.append(t); target.append(wrap);
}
function syncControls() {
  nodes("button").forEach((el) => { el.disabled = busy || !persona; });
  $("persona").disabled = busy;
  $("confirm-refund").disabled = busy || !actionId;
  $("cancel-refund").disabled = busy || !actionId;
  $("approve-operation").disabled = busy || !review || review.status !== "pending_approval";
  $("reject-operation").disabled = $("approve-operation").disabled;
}
async function api(path, body, write = false) {
  let response;
  try { response = await fetch(path, { method: body === undefined ? "GET" : "POST", credentials: "same-origin",
    redirect: "error", headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) }); }
  catch { throw new Error(write ? "连接中断：写入结果未知。请查询已知 operation_id / 工单，勿重复提交。" : "连接失败：无法读取服务。请检查本地服务。"); }
  if (!response.ok) throw new Error(response.status === 401 ? "会话已失效，请重新选择演示身份。" :
    response.status === 404 ? "对象不可读取或不存在（不披露他人信息）。" :
    response.status === 422 ? "输入不符合固定工作流契约。" :
    write && response.status >= 500 ? "服务异常：写入结果未知，请核对业务状态。" : "服务请求被拒绝或服务不可用。");
  try { return await response.json(); }
  catch { throw new Error(write ? "响应无法读取：写入结果未知，请核对业务状态。" : "服务响应无法读取。"); }
}
const readActions = new Set(["list", "get", "messages", "operation"]);
function isWrite(workflow, payload) {
  return workflow !== "support_lookup" && workflow !== "analytics_report" &&
    !(workflow === "ticket_action" && readActions.has(payload.action));
}
async function run(workflow, payload) {
  const epoch = generation;
  const data = await api(`/api/workflows/${workflow}/runs`, { payload, parent_run_id: parent }, isWrite(workflow, payload));
  if (epoch !== generation) throw new Error("身份已切换，旧结果已丢弃。");
  recent.unshift({ run_id: data.run_id, workflow_id: data.workflow_id, status: data.status });
  recent = recent.slice(0, 60); parent = data.run_id; renderRecent();
  $("run-id").value = data.run_id; renderRun(data);
  const status = `${labels[data.status] || data.status}${data.error ? " · " + (errors[data.error] || data.error) : ""}\nrun_id: ${data.run_id}`;
  show("global-status", status, data.error ? "error" : "success");
  // Retain acknowledged business effects even if trace formatting failed.
  if (data.result?.operation_status === "submitted") {
    show("operation-result", "退款申请已提交（非退款到账）。分析快照尚需运营显式刷新。", "success");
  }
  if (data.status === "unknown_outcome") {
    actionId = null; review = null; $("confirmation").hidden = true;
    if (data.domain_refs.operation_id) $("operation-id").value = data.domain_refs.operation_id;
  }
  if (data.error) {
    if (data.error === "internal_error" && data.result != null && data.status !== "unknown_outcome") {
      recordWarnings.push(`业务结果已确认；运行记录失败。请勿重放已确认的写入。\nrun_id: ${data.run_id}`);
    } else throw new Error(status);
  }
  return data.result;
}
function view(name) {
  if (!persona) return;
  if ((name === "operations" || name === "analytics") && persona !== "operator-a") return;
  if (name === "customer" && persona === "operator-a") return;
  nodes(".view").forEach((el) => { el.hidden = el.id !== `${name}-view`; });
  nodes("[data-view]").forEach((el) => el.classList.toggle("is-active", el.dataset.view === name));
}
function clearContext() {
  generation++; parent = null; actionId = null; review = null; recent = []; recordWarnings = [];
  conversation = "ui-" + crypto.randomUUID().replaceAll("-", "");
  nodes("main input, main textarea").forEach((el) => { el.value = ""; });
  for (const id of ["order-result", "operation-result", "answer", "customer-ticket-status", "customer-thread", "ticket-list",
    "operator-ticket-status", "operator-thread", "customer-reply-status", "operator-reply-status", "review-result", "report-result", "run-result", "run-list"])
    $(id).replaceChildren(element("p", "muted", "本身份上下文为空，尚未读取"));
  show("snapshot-status", "尚未读取快照；客户动作不会自动刷新分析。若已有快照，报表将读取当前发布版本。");
  $("confirmation").hidden = true;
  nodes(".view").forEach((el) => { el.hidden = true; });
}
function bind(id, handler) {
  $(id).addEventListener("click", async () => {
    if (busy || !persona) return;
    busy = true; recordWarnings = []; syncControls();
    try { await handler(); } catch (error) { show("global-status", error.message, "error"); }
    finally {
      if (recordWarnings.length) show("global-status", recordWarnings.join("\n") + "\n后续检查：\n" + $("global-status").textContent);
      busy = false; syncControls();
    }
  });
}
function required(id) { const value = $(id).value.trim(); if (!value) throw new Error("请填写所需编号或内容。"); return value; }
function renderOperation(op, target = "operation-result") {
  const el = $(target); el.replaceChildren();
  fields(el, { "操作编号": op.operation_id, "订单": op.order_id, "业务状态": labels[op.status] || op.status,
    "当前版本": op.version, "客户已确认": op.customer_confirmed, "需独立审批": op.requires_human_approval,
    "运营已批准": op.human_approved, "报价 CNY": op.quoted_amount, "有效期": op.expires_at, "关联工单": op.ticket_id });
  if (target === "operation-result") {
    $("operation-id").value = op.operation_id;
    actionId = op.status === "pending_confirmation" ? op.operation_id : null;
    $("confirmation").hidden = !actionId;
    if (op.status === "submitted") el.append(element("p", "notice success", "业务申请已提交；分析快照仍由运营另行刷新。"));
  } else { review = op; $("review-operation-id").value = op.operation_id; }
}
function renderAnswer(data) {
  const el = $("answer"); el.replaceChildren(element("p", "notice", data.answer));
  el.append(element("p", "muted", "来源：offline 规则 / BM25 证据摘录（非模型生成）"));
  for (const citation of data.citations || []) {
    const card = element("div", "ticket-card");
    card.append(element("strong", "", citation.title), element("p", "", citation.snippet),
      element("p", "mono", `来源 ${citation.source_uri || citation.document_id} · 版本 ${citation.document_version || "未提供"} · ${citation.chunk_id || ""}`));
    el.append(card);
  }
  if (data.ticket_id) { $("customer-ticket-id").value = data.ticket_id; el.append(element("p", "mono", `ticket_id: ${data.ticket_id}`)); }
}
async function customerMessage(action) {
  const payload = { action, conversation_id: conversation };
  if (action === "refund") payload.order_id = required("order-id");
  else payload.message = required("question");
  actionId = null; $("confirmation").hidden = true;
  const data = await run("support_message", payload); renderAnswer(data);
  if (data.operation_id) {
    $("operation-id").value = data.operation_id;
    renderOperation(await run("support_lookup", { kind: "operation", object_id: data.operation_id }));
  }
}
async function ticketThread(operator) {
  const prefix = operator ? "operator" : "customer", id = required(prefix + "-ticket-id");
  const target = $(prefix + "-ticket-status"); target.replaceChildren();
  $(prefix + "-thread").replaceChildren();
  const ticket = await run("ticket_action", { action: "get", ticket_id: id });
  fields(target, { "工单": ticket.ticket_id, "状态": ticket.status, "分派": ticket.assignee_id, "SLA": ticket.sla_due_at });
  const messages = await run("ticket_action", { action: "messages", ticket_id: id });
  const thread = $(prefix + "-thread");
  if (!messages.length) thread.append(element("p", "muted", "暂无工单对话"));
  for (const msg of messages) thread.append(element("p", "", `${msg.sender_role} · ${msg.created_at}\n${msg.body}`));
}
function renderRecent() {
  const el = $("run-list"); el.replaceChildren();
  for (const item of recent) {
    const button = element("button", "quiet-button run-item", `${item.workflow_id} · ${labels[item.status] || item.status}\n${item.run_id}`);
    button.addEventListener("click", () => { $("run-id").value = item.run_id; $("read-run").click(); }); el.append(button);
  }
}
function renderRun(data) {
  const el = $("run-result"); el.replaceChildren();
  fields(el, { run_id: data.run_id, "工作流": data.workflow_id, "终态": labels[data.status] || data.status,
    "安全错误": data.error ? errors[data.error] || data.error : "无", "模式": data.mode || "offline", "父运行": data.parent_run_id });
  table(el, ["实际步骤", "状态", "耗时 ms"], (data.steps || []).map((s) => [s.name, s.status, s.elapsed_ms]));
  el.append(element("h3", "", "关联业务 / 快照 ID")); fields(el, data.domain_refs || {});
  if (data.status === "unknown_outcome") el.append(element("p", "notice error", "写入结果未知。按关联 ID 人工核对；系统不会自动重放。"));
}
$("persona").addEventListener("change", async () => {
  const selected = $("persona").value;
  if (busy) return;
  busy = true; persona = ""; clearContext(); syncControls();
  try {
    if (!selected) { show("global-status", "请选择预置演示身份。"); return; }
    const session = await api("/api/demo/session", { persona: selected }); persona = session.persona;
    const operator = persona === "operator-a";
    nodes("[data-view]").forEach((el) => { el.hidden = el.dataset.view === "customer" ? operator :
      ["operations", "analytics"].includes(el.dataset.view) ? !operator : false; });
    view(operator ? "operations" : "customer");
    show("global-status", `当前身份：${persona} / ${session.mode} / 本地演示（非登录）。旧会话上下文已清空。`);
  } catch (error) { show("global-status", error.message, "error"); $("persona").value = ""; }
  finally { busy = false; syncControls(); }
});
nodes("[data-view]").forEach((el) => el.addEventListener("click", () => view(el.dataset.view)));
bind("lookup-order", async () => {
  $("order-result").replaceChildren();
  const order = await run("support_lookup", { object_id: required("order-id") });
  fields($("order-result"), { "订单": order.order_id, "状态": order.status, "金额 CNY": order.amount,
    "可申请金额 CNY": order.refundable_amount, "退款状态": order.refund_status, "退货状态": order.return_status });
});
bind("prepare-refund", () => customerMessage("refund"));
bind("send-question", () => customerMessage("message"));
bind("handoff", () => customerMessage("handoff"));
bind("new-conversation", async () => { conversation = "ui-" + crypto.randomUUID().replaceAll("-", ""); actionId = null;
  $("confirmation").hidden = true; $("answer").replaceChildren(); show("global-status", "新会话已创建；已有业务申请保持原状态。"); });
bind("lookup-operation", async () => { actionId = null; $("confirmation").hidden = true; $("operation-result").replaceChildren();
  renderOperation(await run("support_lookup", { kind: "operation", object_id: required("operation-id") })); });
for (const [id, confirm] of [["confirm-refund", true], ["cancel-refund", false]]) bind(id, async () => {
  const known = actionId; if (!known) return; actionId = null; $("confirmation").hidden = true;
  const data = await run("support_confirm", { action_id: known, confirm });
  show("operation-result", labels[data.operation_status || data.status] || data.status, "success");
  renderOperation(await run("support_lookup", { kind: "operation", object_id: known }));
});
bind("load-customer-ticket", () => ticketThread(false));
bind("load-operator-ticket", () => ticketThread(true));
for (const prefix of ["customer", "operator"]) bind(`send-${prefix}-reply`, async () => {
  const ticketId = required(prefix + "-ticket-id"), message = required(prefix + "-reply");
  const reply = await run("ticket_action", { action: "reply", ticket_id: ticketId, message });
  if ($(prefix + "-reply").value.trim() === message) $(prefix + "-reply").value = "";
  show(prefix + "-reply-status", `工单回复已发送。ticket_id: ${reply.ticket_id} / message_id: ${reply.message_id}`, "success");
  await ticketThread(prefix === "operator");
});
bind("load-tickets", async () => {
  $("ticket-list").replaceChildren(); const tickets = await run("ticket_action", { action: "list" });
  if (!tickets.length) $("ticket-list").append(element("p", "muted", "空数据：暂无工单"));
  for (const ticket of tickets) {
    const card = element("div", "ticket-card"); fields(card, { "工单": ticket.ticket_id, "状态": ticket.status, "客户": ticket.customer_id });
    const button = element("button", "quiet-button", "选择此工单");
    button.addEventListener("click", () => { if (busy) return; $("operator-ticket-id").value = ticket.ticket_id; $("load-operator-ticket").click(); });
    card.append(button); $("ticket-list").append(card);
  }
});
bind("assign-ticket", async () => { await run("ticket_action", { action: "assign", ticket_id: required("operator-ticket-id") }); await ticketThread(true); });
bind("transition-ticket", async () => { await run("ticket_action", { action: "transition", ticket_id: required("operator-ticket-id"), status: $("ticket-status").value }); await ticketThread(true); });
bind("ticket-operation", async () => { review = null; $("review-result").replaceChildren();
  renderOperation(await run("ticket_action", { action: "operation", ticket_id: required("operator-ticket-id") }), "review-result"); });
bind("load-review", async () => { review = null; $("review-result").replaceChildren();
  renderOperation(await run("support_lookup", { kind: "operation", object_id: required("review-operation-id") }), "review-result"); });
for (const decision of ["approve", "reject"]) bind(`${decision}-operation`, async () => {
  const known = review; review = null; if (!known) return;
  const result = await run("support_review", { operation_id: known.operation_id, decision, expected_version: known.version });
  renderOperation(result, "review-result");
});
bind("refresh-snapshot", async () => {
  const previous = $("snapshot-status").textContent;
  try { const snapshot = await run("refresh_snapshot", {});
    $("snapshot-status").replaceChildren(); fields($("snapshot-status"), { "刷新": "成功（已有报表仍对应其原快照，请重新执行）",
      "snapshot_id": snapshot.snapshot_id, "生成时间": snapshot.generated_at, "数据来源": snapshot.source });
    table($("snapshot-status"), ["数据集", "实际行数"], Object.entries(snapshot.dataset_row_counts));
  } catch (error) { show("snapshot-status", `刷新失败；业务提交状态不受影响。上次读取信息（未重新核验）：\n${previous}`, "error"); throw error; }
});
bind("load-report", async () => {
  $("report-result").replaceChildren();
  try {
    const report = await run("analytics_report", { report_id: $("report-id").value }), data = report.data, snapshot = data.snapshot;
    fields($("report-result"), { "报表": report.report_id, "来源": `${report.source}/${report.template_version} · 实际 DuckDB 查询`,
      "row_count": data.row_count, "snapshot_id": snapshot.snapshot_id, "生成时间": snapshot.generated_at,
      "as_of（服务端）": report.as_of, "查询耗时 ms": data.duration_ms, "delivery_fingerprint": snapshot.delivery_fingerprint });
    table($("report-result"), data.columns.map((col) => col.name), data.rows);
    $("report-result").append(element("h3", "", "快照水位（非实时承诺）"));
    table($("report-result"), ["数据集", "水位", "字段 / 不可用原因"], snapshot.datasets.map((ds) =>
      [ds.dataset_name, ds.watermark_value || ds.watermark_kind, ds.watermark_field || ds.unavailable_reason]));
    show("snapshot-status", `本报表读取 snapshot_id: ${snapshot.snapshot_id}\n生成时间: ${snapshot.generated_at}\n业务变化需另行显式刷新；不同报表按各自快照 ID 判断新鲜度。`);
  } catch (error) { show("report-result", "报表未完成，未提供查询结果。请检查是否已发布有效快照或下游服务。", "error"); throw error; }
});
bind("read-run", async () => { $("run-result").replaceChildren(); renderRun(await api("/api/runs/" + encodeURIComponent(required("run-id")))); });
syncControls();
