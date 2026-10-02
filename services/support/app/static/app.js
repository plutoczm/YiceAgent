"use strict";

const elements = {
  tabs: [...document.querySelectorAll(".workspace-tab")],
  workspaces: [...document.querySelectorAll(".workspace")],
  systemStatus: document.querySelector("#system-status"),
  systemStatusText: document.querySelector("#system-status-text"),
  customerId: document.querySelector("#customer-id"),
  customerPrincipal: document.querySelector("#customer-principal"),
  conversationId: document.querySelector("#conversation-id"),
  newConversation: document.querySelector("#new-conversation"),
  clearChat: document.querySelector("#clear-chat"),
  chatForm: document.querySelector("#chat-form"),
  messageInput: document.querySelector("#message-input"),
  sendButton: document.querySelector("#send-button"),
  messageStream: document.querySelector("#message-stream"),
  customerSyncLabel: document.querySelector("#customer-sync-label"),
  quickPrompts: [...document.querySelectorAll("[data-prompt]")],
  agentId: document.querySelector("#agent-id"),
  ticketFilter: document.querySelector("#ticket-filter"),
  refreshTickets: document.querySelector("#refresh-tickets"),
  ticketList: document.querySelector("#ticket-list"),
  ticketSyncLabel: document.querySelector("#ticket-sync-label"),
  statTotal: document.querySelector("#stat-total"),
  statUrgent: document.querySelector("#stat-urgent"),
  statBreached: document.querySelector("#stat-breached"),
  toastRegion: document.querySelector("#toast-region"),
};

const statusLabels = {
  open: "待处理",
  assigned: "处理中",
  pending_customer: "等待用户",
  resolved: "已解决",
  closed: "已关闭",
};

const priorityLabels = {
  urgent: "紧急",
  high: "高",
  normal: "普通",
  low: "低",
};

const allowedTransitions = {
  open: [],
  assigned: ["pending_customer", "resolved", "open"],
  pending_customer: ["assigned", "resolved"],
  resolved: ["closed", "open"],
  closed: [],
};

let activeTicketId = localStorage.getItem("supportops.activeTicketId") || "";
let renderedTicketMessageKey = "";

function newConversationId() {
  const suffix = globalThis.crypto?.randomUUID?.().replaceAll("-", "").slice(0, 10)
    ?? Date.now().toString(36);
  return `CONV-LOCAL-${suffix.toUpperCase()}`;
}

function restorePreferences() {
  elements.customerId.value = localStorage.getItem("supportops.customerId") || "CUST-001";
  elements.customerPrincipal.value =
    localStorage.getItem("supportops.customerPrincipal") || "local-user";
  elements.agentId.value = localStorage.getItem("supportops.agentId") || "local-agent";
  elements.conversationId.value =
    localStorage.getItem("supportops.conversationId") || newConversationId();
}

function persistPreferences() {
  localStorage.setItem("supportops.customerId", elements.customerId.value.trim());
  localStorage.setItem("supportops.customerPrincipal", elements.customerPrincipal.value.trim());
  localStorage.setItem("supportops.agentId", elements.agentId.value.trim());
  localStorage.setItem("supportops.conversationId", elements.conversationId.value.trim());
}

function setActiveTicket(ticketId) {
  activeTicketId = ticketId || "";
  renderedTicketMessageKey = "";
  if (activeTicketId) {
    localStorage.setItem("supportops.activeTicketId", activeTicketId);
  } else {
    localStorage.removeItem("supportops.activeTicketId");
  }
  updateCustomerMode();
}

function updateCustomerMode() {
  if (activeTicketId) {
    elements.customerSyncLabel.textContent = `人工接管 · ${activeTicketId}`;
  } else {
    elements.customerSyncLabel.textContent = "AI 支持模式";
  }
}

function customerHeaders() {
  return {
    "Content-Type": "application/json",
    "X-Principal-Id": elements.customerPrincipal.value.trim() || "local-user",
    "X-Customer-Id": elements.customerId.value.trim() || "CUST-001",
    "X-Roles": "customer",
  };
}

function agentHeaders() {
  return {
    "Content-Type": "application/json",
    "X-Principal-Id": elements.agentId.value.trim() || "local-agent",
    "X-Roles": "support_agent",
  };
}

async function apiFetch(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" ? payload.detail : payload;
    throw new Error(detail || `请求失败 (${response.status})`);
  }
  return payload;
}

function switchWorkspace(name) {
  elements.tabs.forEach((tab) => {
    const active = tab.dataset.workspace === name;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  elements.workspaces.forEach((workspace) => {
    workspace.classList.toggle("is-active", workspace.id === `${name}-workspace`);
  });
  if (name === "agent") {
    loadTickets();
  }
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function addMessage(role, text, meta = "", target = elements.messageStream) {
  const visualRole = role === "customer" ? "user" : role;
  const article = element("article", `message ${visualRole}-message`);
  const avatarText = { assistant: "AI", customer: "YOU", agent: "客服" }[role] || "AI";
  const avatar = element("div", "avatar", avatarText);
  const content = element("div", "message-content");
  content.append(element("div", "message-meta", meta));
  content.append(element("p", "", text));
  article.append(avatar, content);
  target.append(article);
  if (target === elements.messageStream) {
    target.scrollTop = target.scrollHeight;
  }
  return { article, content };
}

function addWelcomeMessage() {
  const welcome = addMessage(
    "assistant",
    "你好，我可以解释支持政策、查询订单、发起退款或退货，以及把复杂问题转交人工客服。",
    "SupportOps · 已连接安全工作流",
  );
  welcome.article.classList.add("welcome-message");
}

function renderTicketMessages(messages, target = elements.messageStream) {
  if (target === elements.messageStream) {
    target.replaceChildren();
    if (!messages.length) {
      addWelcomeMessage();
      return;
    }
  } else {
    target.replaceChildren();
  }

  messages.forEach((message) => {
    const role = message.sender_role;
    const meta = role === "agent"
      ? `人工客服 · ${message.sender_id}`
      : role === "customer"
        ? `${message.customer_id} · 你`
        : `SupportOps · ${message.context?.intent || "handoff"}`;
    const rendered = addMessage(role, message.body, meta, target);
    if (role === "assistant" && message.context) {
      addResponseDetails(rendered.content, message.context);
    }
  });
  if (target === elements.messageStream) {
    target.scrollTop = target.scrollHeight;
  }
}

async function loadCustomerTicketMessages({ force = false, silent = true } = {}) {
  updateCustomerMode();
  if (!activeTicketId) return;
  try {
    const messages = await apiFetch(`/v1/tickets/${encodeURIComponent(activeTicketId)}/messages`, {
      headers: customerHeaders(),
    });
    const key = messages.map((message) => message.message_id).join(",");
    if (force || key !== renderedTicketMessageKey) {
      renderedTicketMessageKey = key;
      renderTicketMessages(messages);
    }
  } catch (error) {
    if (!silent) showToast(error.message, true);
    if (error.message === "ticket_not_found") setActiveTicket("");
  }
}

async function recoverCustomerTicket() {
  if (activeTicketId) {
    await loadCustomerTicketMessages({ force: true });
    return;
  }
  const conversationId = elements.conversationId.value.trim();
  if (!conversationId) return;
  try {
    const ticket = await apiFetch(`/v1/conversations/${encodeURIComponent(conversationId)}/ticket`, {
      headers: customerHeaders(),
    });
    setActiveTicket(ticket.ticket_id);
    await loadCustomerTicketMessages({ force: true });
  } catch {
    updateCustomerMode();
  }
}

function addResponseDetails(content, response) {
  const badges = element("div", "message-badges");
  if (response.intent) badges.append(element("span", "badge", `意图 · ${response.intent}`));
  if (response.trace_id) badges.append(element("span", "badge", `Trace · ${response.trace_id}`));
  if (response.retrieval_degraded) {
    badges.append(
      element(
        "span",
        "badge is-warning",
        `检索降级 · ${response.retrieval_degradation_reason || "unknown"}`,
      ),
    );
  }
  if (response.handoff) {
    badges.append(element("span", "badge is-warning", `人工工单 · ${response.ticket_id}`));
  }
  content.append(badges);

  if (response.citations?.length) {
    const list = element("div", "citation-list");
    response.citations.forEach((citation) => {
      const item = element("div", "citation");
      item.append(
        element(
          "strong",
          "",
          `${citation.document_id} · ${citation.title} · ${Number(citation.score).toFixed(3)}`,
        ),
        element("span", "", citation.snippet),
      );
      list.append(item);
    });
    content.append(list);
  }

  if (response.pending_action_id) {
    content.append(createActionCard(response));
  }
}

function createActionCard(response) {
  const card = element("div", "action-card");
  card.append(
    element("strong", "", "需要你的明确确认"),
    element(
      "small",
      "",
      `操作 ${response.pending_action_id} · 到期 ${formatDate(response.pending_action_expires_at)}`,
    ),
  );
  const buttons = element("div", "action-buttons");
  const confirm = element("button", "confirm-button", "确认执行");
  confirm.type = "button";
  const cancel = element("button", "cancel-button", "取消操作");
  cancel.type = "button";
  confirm.addEventListener("click", () => resolveAction(response.pending_action_id, true, card));
  cancel.addEventListener("click", () => resolveAction(response.pending_action_id, false, card));
  buttons.append(confirm, cancel);
  card.append(buttons);
  return card;
}

async function resolveAction(actionId, confirm, card) {
  const buttons = [...card.querySelectorAll("button")];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const result = await apiFetch(`/v1/actions/${encodeURIComponent(actionId)}/confirm`, {
      method: "POST",
      headers: customerHeaders(),
      body: JSON.stringify({ confirm }),
    });
    card.replaceChildren(
      element("strong", "", confirm ? "操作已确认" : "操作已取消"),
      element("small", "", `状态：${result.status} · Trace：${result.trace_id}`),
    );
    showToast(confirm ? "敏感操作已执行" : "操作已取消");
  } catch (error) {
    buttons.forEach((button) => { button.disabled = false; });
    showToast(error.message, true);
  }
}

async function sendMessage(message) {
  persistPreferences();
  if (activeTicketId) {
    await sendCustomerTicketMessage(message);
    return;
  }
  addMessage("user", message, `${elements.customerId.value.trim()} · 你`);
  const pending = addMessage("assistant", "正在分析问题", "SupportOps · 处理中");
  pending.article.classList.add("loading-dots");
  elements.sendButton.disabled = true;

  try {
    const response = await apiFetch("/v1/support/messages", {
      method: "POST",
      headers: customerHeaders(),
      body: JSON.stringify({
        conversation_id: elements.conversationId.value.trim(),
        message,
      }),
    });
    pending.article.classList.remove("loading-dots");
    pending.content.replaceChildren(
      element("div", "message-meta", `SupportOps · ${response.intent}`),
      element("p", "", response.answer),
    );
    addResponseDetails(pending.content, response);
    if (response.ticket_id) {
      showToast(`已创建人工工单 ${response.ticket_id}`);
      setActiveTicket(response.ticket_id);
      await loadCustomerTicketMessages({ force: true, silent: false });
    }
  } catch (error) {
    pending.article.classList.remove("loading-dots");
    pending.content.replaceChildren(
      element("div", "message-meta", "SupportOps · 请求失败"),
      element("p", "", error.message),
    );
    showToast(error.message, true);
  } finally {
    elements.sendButton.disabled = false;
    elements.messageStream.scrollTop = elements.messageStream.scrollHeight;
  }
}

async function sendCustomerTicketMessage(message) {
  elements.sendButton.disabled = true;
  try {
    await apiFetch(`/v1/tickets/${encodeURIComponent(activeTicketId)}/messages`, {
      method: "POST",
      headers: customerHeaders(),
      body: JSON.stringify({ message }),
    });
    await loadCustomerTicketMessages({ force: true, silent: false });
    showToast("已发送给人工客服");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.sendButton.disabled = false;
  }
}

function emptyTickets(title = "当前没有匹配工单", detail = "更换筛选条件或稍后刷新。") {
  const empty = element("div", "empty-state");
  empty.append(
    element("span", "", "QUEUE CLEAR"),
    element("h3", "", title),
    element("p", "", detail),
  );
  elements.ticketList.replaceChildren(empty);
}

function updateTicketStats(tickets) {
  elements.statTotal.textContent = String(tickets.length);
  elements.statUrgent.textContent = String(
    tickets.filter((ticket) => ["urgent", "high"].includes(ticket.priority)).length,
  );
  elements.statBreached.textContent = String(
    tickets.filter((ticket) => ticket.sla_breached).length,
  );
}

function renderTickets(tickets) {
  updateTicketStats(tickets);
  if (!tickets.length) {
    emptyTickets();
    return;
  }
  const fragment = document.createDocumentFragment();
  tickets.forEach((ticket) => fragment.append(createTicketCard(ticket)));
  elements.ticketList.replaceChildren(fragment);
}

function createTicketCard(ticket) {
  const card = element("article", `ticket-card${ticket.sla_breached ? " is-breached" : ""}`);
  const body = element("div", "ticket-body");
  const topline = element("div", "ticket-topline");
  topline.append(
    element("span", "ticket-id", ticket.ticket_id),
    element("span", "ticket-status", statusLabels[ticket.status] || ticket.status),
    element("span", "ticket-priority", priorityLabels[ticket.priority] || ticket.priority),
  );
  body.append(
    topline,
    element("p", "ticket-reason", ticket.reason),
  );
  const meta = element("div", "ticket-meta");
  meta.append(
    element("span", "", `客户 ${ticket.customer_id}`),
    element("span", "", `会话 ${ticket.conversation_id}`),
    element("span", "", `负责人 ${ticket.assignee_id || "未指派"}`),
    element("span", "", `SLA ${formatDate(ticket.sla_due_at)}`),
  );
  body.append(meta);
  card.append(body, createTicketActions(ticket, card));
  return card;
}

function createTicketActions(ticket, card) {
  const actions = element("div", "ticket-actions");
  const conversation = element("button", "ticket-conversation-button", "打开会话 / 回复");
  conversation.type = "button";
  conversation.addEventListener("click", () => toggleTicketConversation(ticket, card, conversation));
  actions.append(conversation);
  if (!ticket.assignee_id && !["resolved", "closed"].includes(ticket.status)) {
    const assign = element("button", "", "指派给当前客服");
    assign.type = "button";
    assign.addEventListener("click", () => assignTicket(ticket.ticket_id, assign));
    actions.append(assign);
  }

  const transitions = allowedTransitions[ticket.status] || [];
  if (transitions.length) {
    const select = element("select", "");
    select.setAttribute("aria-label", `${ticket.ticket_id} 目标状态`);
    transitions.forEach((status) => {
      const option = element("option", "", statusLabels[status] || status);
      option.value = status;
      select.append(option);
    });
    const note = element("input", "");
    note.placeholder = "处理备注（可选）";
    note.maxLength = 1000;
    note.setAttribute("aria-label", `${ticket.ticket_id} 处理备注`);
    const transition = element("button", "", "更新状态");
    transition.type = "button";
    transition.addEventListener("click", () => {
      transitionTicket(ticket.ticket_id, select.value, note.value, transition);
    });
    actions.append(select, note, transition);
  }

  if (!actions.children.length) {
    actions.append(element("span", "sync-label", "无需进一步操作"));
  }
  return actions;
}

async function toggleTicketConversation(ticket, card, button) {
  const existing = card.querySelector(".ticket-conversation");
  if (existing) {
    existing.remove();
    button.textContent = "打开会话 / 回复";
    return;
  }
  const panel = element("section", "ticket-conversation");
  panel.append(element("div", "ticket-conversation-loading", "正在加载会话…"));
  card.append(panel);
  button.textContent = "收起会话";
  try {
    const messages = await apiFetch(
      `/v1/agent/tickets/${encodeURIComponent(ticket.ticket_id)}/messages`,
      { headers: agentHeaders() },
    );
    renderAgentConversation(ticket, panel, messages);
  } catch (error) {
    panel.replaceChildren(element("p", "ticket-conversation-error", error.message));
    showToast(error.message, true);
  }
}

function renderAgentConversation(ticket, panel, messages) {
  panel.replaceChildren();
  const header = element("div", "ticket-conversation-header");
  header.append(
    element("strong", "", "用户与客服会话"),
    element("span", "", `${ticket.conversation_id} · ${messages.length} 条消息`),
  );
  const history = element("div", "ticket-message-history");
  if (!messages.length) {
    history.append(element("p", "ticket-conversation-empty", "暂无已保存的会话消息。"));
  } else {
    messages.forEach((message) => {
      const item = element("article", `ticket-message is-${message.sender_role}`);
      const label = { customer: "用户", assistant: "AI", agent: "客服" }[message.sender_role];
      item.append(
        element("span", "ticket-message-author", `${label} · ${message.sender_id}`),
        element("p", "", message.body),
        element("time", "", formatDate(message.created_at)),
      );
      history.append(item);
    });
  }
  panel.append(header, history);

  if (!["closed", "resolved"].includes(ticket.status)) {
    const composer = element("div", "agent-reply-composer");
    const input = element("textarea", "");
    input.rows = 3;
    input.maxLength = 8000;
    input.placeholder = ticket.assignee_id
      ? "输入回复，发送后用户聊天区会自动收到"
      : "请先指派给当前客服，再发送回复";
    input.disabled = !ticket.assignee_id;
    input.setAttribute("aria-label", `${ticket.ticket_id} 人工回复`);
    const send = element("button", "", "发送人工回复");
    send.type = "button";
    send.disabled = !ticket.assignee_id;
    send.addEventListener("click", () => sendAgentTicketMessage(ticket, input, send, panel));
    composer.append(input, send);
    panel.append(composer);
  }
}

async function sendAgentTicketMessage(ticket, input, button, panel) {
  const message = input.value.trim();
  if (!message) return;
  button.disabled = true;
  try {
    await apiFetch(`/v1/agent/tickets/${encodeURIComponent(ticket.ticket_id)}/messages`, {
      method: "POST",
      headers: agentHeaders(),
      body: JSON.stringify({ message }),
    });
    showToast("人工回复已发送给用户");
    const messages = await apiFetch(
      `/v1/agent/tickets/${encodeURIComponent(ticket.ticket_id)}/messages`,
      { headers: agentHeaders() },
    );
    renderAgentConversation({ ...ticket, status: "pending_customer" }, panel, messages);
  } catch (error) {
    button.disabled = false;
    showToast(error.message, true);
  }
}

async function loadTickets() {
  persistPreferences();
  elements.refreshTickets.disabled = true;
  elements.ticketSyncLabel.textContent = "正在同步…";
  try {
    const status = elements.ticketFilter.value;
    const query = status ? `?status=${encodeURIComponent(status)}` : "";
    const tickets = await apiFetch(`/v1/agent/tickets${query}`, { headers: agentHeaders() });
    renderTickets(tickets);
    elements.ticketSyncLabel.textContent = `更新于 ${new Date().toLocaleTimeString("zh-CN")}`;
  } catch (error) {
    emptyTickets("无法加载工单", error.message);
    elements.ticketSyncLabel.textContent = "同步失败";
    showToast(error.message, true);
  } finally {
    elements.refreshTickets.disabled = false;
  }
}

async function assignTicket(ticketId, button) {
  button.disabled = true;
  try {
    await apiFetch(`/v1/agent/tickets/${encodeURIComponent(ticketId)}/assign`, {
      method: "POST",
      headers: agentHeaders(),
      body: JSON.stringify({ assignee_id: elements.agentId.value.trim() || "local-agent" }),
    });
    showToast(`${ticketId} 已指派`);
    await loadTickets();
  } catch (error) {
    button.disabled = false;
    showToast(error.message, true);
  }
}

async function transitionTicket(ticketId, status, note, button) {
  button.disabled = true;
  try {
    await apiFetch(`/v1/agent/tickets/${encodeURIComponent(ticketId)}/transition`, {
      method: "POST",
      headers: agentHeaders(),
      body: JSON.stringify({ status, note: note.trim() || null }),
    });
    showToast(`${ticketId} 已更新为${statusLabels[status] || status}`);
    await loadTickets();
  } catch (error) {
    button.disabled = false;
    showToast(error.message, true);
  }
}

async function checkHealth() {
  try {
    const health = await apiFetch("/health");
    elements.systemStatus.className = "system-status is-online";
    elements.systemStatusText.textContent = `在线 · ${health.reliability_backend}`;
  } catch {
    elements.systemStatus.className = "system-status is-offline";
    elements.systemStatusText.textContent = "服务离线";
  }
}

function showToast(message, isError = false) {
  const toast = element("div", `toast${isError ? " is-error" : ""}`, message);
  elements.toastRegion.append(toast);
  globalThis.setTimeout(() => toast.remove(), 4200);
}

function formatDate(value) {
  if (!value) return "未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN");
}

elements.tabs.forEach((tab) => {
  tab.addEventListener("click", () => switchWorkspace(tab.dataset.workspace));
});

elements.chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = elements.messageInput.value.trim();
  if (!message) return;
  elements.messageInput.value = "";
  sendMessage(message);
});

elements.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.chatForm.requestSubmit();
  }
});

elements.quickPrompts.forEach((button) => {
  button.addEventListener("click", () => {
    elements.messageInput.value = button.dataset.prompt;
    elements.messageInput.focus();
  });
});

elements.newConversation.addEventListener("click", () => {
  elements.conversationId.value = newConversationId();
  setActiveTicket("");
  persistPreferences();
  elements.messageStream.replaceChildren();
  addWelcomeMessage();
  showToast("已创建新会话编号");
});

elements.clearChat.addEventListener("click", () => {
  loadCustomerTicketMessages({ force: true, silent: false });
});

elements.refreshTickets.addEventListener("click", loadTickets);
elements.ticketFilter.addEventListener("change", loadTickets);
[elements.customerId, elements.customerPrincipal, elements.conversationId, elements.agentId]
  .forEach((input) => input.addEventListener("change", persistPreferences));
elements.conversationId.addEventListener("change", recoverCustomerTicket);

restorePreferences();
updateCustomerMode();
checkHealth();
recoverCustomerTicket();
globalThis.setInterval(checkHealth, 30000);
globalThis.setInterval(() => loadCustomerTicketMessages(), 6000);
