// Pi owns OAuth/provider protocol only. Project Python owns loops, budgets and tools.
import { createModels } from "@earendil-works/pi-ai";
import { openaiCodexProvider } from "@earendil-works/pi-ai/providers/openai-codex";
import * as fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import readline from "node:readline";
import { createHash, randomUUID } from "node:crypto";
import { readRedirect } from "./oauth-input.mjs";
import { loginFailure } from "./oauth-errors.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const runtime = path.join(root, ".runtime", "v2");
const authPath = path.join(runtime, "chatgpt-oauth.json");
const lockPath = path.join(runtime, "chatgpt-oauth.lock");
const providerId = "openai-codex";
const rawMessages = new Map();
const hash = (x) => createHash("sha256").update(JSON.stringify(x)).digest("hex");

async function readAuth() {
  try {
    const data = JSON.parse(await fs.readFile(authPath, "utf8"));
    if (data.owner !== "workflowops-v2") throw new Error("oauth_storage_unowned");
    return data.credentials[providerId];
  } catch (error) {
    if (error.code === "ENOENT") return undefined;
    throw new Error("oauth_storage_invalid");
  }
}
async function locked(fn, signal) {
  await fs.mkdir(runtime, { recursive: true });
  let handle;
  const deadline = Date.now() + 30000;
  while (!handle) {
    if (signal?.aborted) throw new Error("oauth_cancelled");
    try { handle = await fs.open(lockPath, "wx"); }
    catch (error) {
      if (error.code !== "EEXIST" || Date.now() > deadline) throw new Error("oauth_storage_locked");
      await new Promise(resolve => setTimeout(resolve, 50));
    }
  }
  try { return await fn(); }
  finally { await handle.close(); await fs.unlink(lockPath); }
}
const credentials = {
  async read(id) { return id === providerId ? readAuth() : undefined; },
  async list() { return await readAuth() ? [{ providerId, type: "oauth" }] : []; },
  async modify(id, fn, options) {
    if (id !== providerId) throw new Error("provider_invalid");
    return locked(async () => {
      const existing = await readAuth();
      const result = await fn(existing);
      if (result) {
        const temp = authPath + "." + randomUUID() + ".tmp";
        await fs.writeFile(temp, JSON.stringify({ owner: "workflowops-v2", credentials: { [providerId]: result } }), { mode: 0o600 });
        await fs.rename(temp, authPath);
      }
      return result || existing;
    }, options?.signal);
  },
  async delete() { throw new Error("explicit_logout_not_implemented"); }
};
const models = createModels({
  credentials,
  // No $HOME credentials, API-key commands, environment credentials or another tool's auth.
  authContext: { env: async () => undefined, fileExists: async () => false }
});
models.setProvider(openaiCodexProvider());

const emptyUsage = () => ({ input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } });
function context(request, model, scope) {
  const toolNames = new Map(request.messages.flatMap(m => m.tool_calls || []).map(c => [c.id, c.name]));
  const messages = [];
  let systemPrompt = "";
  for (const message of request.messages) {
    if (message.role === "system") { systemPrompt += (systemPrompt ? "\n" : "") + message.content; continue; }
    if (message.role === "user") messages.push({ role: "user", content: message.content, timestamp: 0 });
    if (message.role === "tool") messages.push({ role: "toolResult", toolCallId: message.tool_call_id, toolName: toolNames.get(message.tool_call_id), content: [{ type: "text", text: message.content }], isError: false, timestamp: 0 });
    if (message.role === "assistant") {
      const key = scope + ":" + model.id + ":" + hash(message);
      const saved = rawMessages.get(key);
      messages.push(saved || {
        role: "assistant",
        content: [
          ...(message.content ? [{ type: "text", text: message.content }] : []),
          ...(message.tool_calls || []).map(c => ({ type: "toolCall", id: c.id, name: c.name, arguments: c.arguments }))
        ], api: model.api, provider: providerId, model: model.id, usage: emptyUsage(), stopReason: (message.tool_calls || []).length ? "toolUse" : "stop", timestamp: 0
      });
    }
  }
  if (request.output_schema) systemPrompt += "\nReturn valid JSON matching this schema: " + JSON.stringify(request.output_schema);
  return { systemPrompt, messages, tools: request.tools };
}

async function dispatch(req) {
  const payload = req.payload || {};
  if (req.method === "health") return { provider: providerId, logged_in: Boolean(await readAuth()), protocol: "jsonl-v1" };
  if (req.method === "models") {
    const authenticated = Boolean(await models.checkAuth(providerId));
    const candidates = models.getModels(providerId).map(m => ({ id: m.id, context_window: m.contextWindow, tools: true, structured_output: false, verified: false }));
    if (authenticated && payload.account_probe) {
      try {
        const auth = await models.getAuth(providerId);
        const credential = await readAuth();
        const response = await fetch("https://chatgpt.com/backend-api/codex/models?client_version=1.0.0", {
          headers: { Authorization: "Bearer " + auth.auth.apiKey, "ChatGPT-Account-Id": credential.accountId },
          signal: AbortSignal.timeout(15000)
        });
        if (!response.ok) throw new Error("catalog_unavailable");
        const data = await response.json();
        const entries = Array.isArray(data.models) ? data.models : Array.isArray(data.data) ? data.data : [];
        const ids = new Set(entries.map(m => m.slug || m.id).filter(id => typeof id === "string"));
        if (!ids.size) throw new Error("catalog_unavailable");
        return {source:"account_model_list", account_access_verified:true, logged_in:true,
          models:candidates.filter(m=>ids.has(m.id)).map(m=>({...m,verified:true}))};
      } catch {
        return {source:"pi_registry", account_access_verified:false, logged_in:true, reason:"account_catalog_unavailable", models:candidates};
      }
    }
    return {source:"pi_registry", account_access_verified:false, logged_in:authenticated, models:candidates};
  }
  if (req.method !== "generate") throw new Error("bridge_method_invalid");
  const { model_id, request, scope } = payload;
  if (!await readAuth()) throw new Error("oauth_login_required");
  const model = models.getModel(providerId, model_id);
  if (!model || request.context_window > model.contextWindow) throw new Error("model_unavailable");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), request.timeout_seconds * 1000);
  try {
    // This Pi Codex adapter omits native max_output_tokens. Enforce a conservative
    // application bound on streamed visible content/tool arguments (not exact token counting).
    const stream = models.streamSimple(model, context(request, model, scope), {
      signal: controller.signal, maxTokens: request.max_output_tokens, reasoning:"minimal",
      transport: "sse", maxRetryDelayMs: 0,
    });
    let outputLimit = false;
    for await (const event of stream) {
      if (event.partial) {
        const counted = event.partial.content.map(x => x.type === "text" ? x.text : x.type === "thinking" ? x.thinking : x.type === "toolCall" ? JSON.stringify(x.arguments || {}) : "").join("");
        if (Buffer.byteLength(counted) > request.max_output_tokens) {
          outputLimit = true; controller.abort();
        }
      }
    }
    const raw = await stream.result();
    if (outputLimit) throw new Error("provider_output_limit");
    if (raw.stopReason === "aborted") throw new Error("provider_timeout");
    if (raw.stopReason === "error") throw new Error("provider_request_failed");
    const text = raw.content.filter(x => x.type === "text").map(x => x.text).join("");
    const calls = raw.content.filter(x => x.type === "toolCall").map(x => ({ id: x.id, name: x.name, arguments: x.arguments }));
    const allowed = new Set((request.tools || []).map(t => t.name));
    if (calls.some(c => !allowed.has(c.name))) throw new Error("unrequested_tool");
    const message = { role: "assistant", content: text || (calls.length ? null : ""), tool_calls: calls, tool_call_id: null };
    rawMessages.set(scope + ":" + model.id + ":" + hash(message), raw);
    while (rawMessages.size > 128) rawMessages.delete(rawMessages.keys().next().value);
    // Mandatory provider fields stay in this private cache; never sent as debug/context content.
    const known = raw.usage.totalTokens > 0;
    return {
      message, model_id: raw.responseModel || raw.model || model.id,
      finish_reason: calls.length ? "tool_calls" : raw.stopReason === "length" ? "length" : "stop",
      usage: { input_tokens: known ? raw.usage.input + raw.usage.cacheRead + raw.usage.cacheWrite : null, output_tokens: known ? raw.usage.output : null, source: known ? "provider" : "unknown", estimated_cost_cny: null }
    };
  } finally { clearTimeout(timer); }
}

async function login() {
  if (!process.argv.includes("--approved")) throw new Error("explicit_approval_required");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 600000);
  const terminal = readline.createInterface({ input: process.stdin, output: process.stderr });
  let expectedRedirect, expectedState;
  try {
    await models.login(providerId, "oauth", {
      signal: controller.signal,
      prompt: async prompt => {
        if (prompt.type === "select") return prompt.options.find(o => o.label.startsWith("Browser"))?.id || prompt.options[0].id;
        return readRedirect({
          terminal, signal: AbortSignal.any([controller.signal, prompt.signal].filter(Boolean)),
          expectedRedirect, expectedState, notify: text => process.stderr.write(text)
        });
      },
      notify: event => {
        if (event.type === "auth_url") {
          const requestUrl = new URL(event.url);
          expectedRedirect = requestUrl.searchParams.get("redirect_uri");
          expectedState = requestUrl.searchParams.get("state");
          process.stderr.write("请打开下面的新项目授权链接，并保持本终端运行：\n" + event.url + "\n先完成浏览器中的账号选择和授权。只有地址变成 http://localhost:1455/auth/callback?... 后，才复制完整地址回填本终端。\n旧链接不能用于新的登录进程。\n");
        }
        else if (event.type === "progress") process.stderr.write("OAuth authorization in progress\n");
      }
    }, { getDeviceId: () => "workflowops-" + hash(root).slice(0, 32) });
    process.stderr.write("Project OAuth saved. No credentials were printed. Select and verify a model next.\n");
  } finally { clearTimeout(timeout); terminal.close(); }
}

if (process.argv.includes("--login")) {
  try { await login(); } catch (error) {
    const result = loginFailure(error);
    const diagnostic = {
      status:"FAILED",category:result.category,http_status:result.http_status,
      network_code:result.network_code,at:new Date().toISOString(),node_version:process.version,
      proxy_enabled:process.env.NODE_USE_ENV_PROXY==="1",
      https_proxy_configured:Boolean(process.env.HTTPS_PROXY)
    };
    await fs.mkdir(runtime,{recursive:true});
    await fs.writeFile(path.join(runtime,"oauth-login-diagnostic.json"),JSON.stringify(diagnostic,null,2)+"\n",{mode:0o600}).catch(()=>{});
    process.stderr.write("oauth_login_failed ["+result.category+"]"+(result.http_status?" HTTP "+result.http_status:"")+"\n"+result.message+"\n");
    process.exitCode = 1;
  }
} else {
  const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of lines) {
    let req;
    try {
      if (Buffer.byteLength(line) > 4 * 1024 * 1024) throw new Error("bridge_request_too_large");
      req = JSON.parse(line);
      if (!/^[a-f0-9-]{36}$/.test(req.request_id)) throw new Error("request_id_invalid");
      const result = await dispatch(req);
      process.stdout.write(JSON.stringify({ request_id: req.request_id, result, error: null }) + "\n");
    } catch (error) {
      if (!req?.request_id) break;
      const safe = new Set(["oauth_login_required", "model_unavailable", "provider_timeout", "provider_request_failed", "unrequested_tool", "bridge_method_invalid", "oauth_storage_locked", "provider_output_limit"]);
      const code = safe.has(error.message) ? error.message : "bridge_failed";
      process.stdout.write(JSON.stringify({ request_id: req.request_id, result: null, error: { code } }) + "\n");
    }
  }
}
