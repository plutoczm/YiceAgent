import {whenSession,notifyFeedback,accountLabel,displayName} from "./workbench.js";
import {conversationControls} from "./ui-controls.js";
"use strict";
const node=id=>document.getElementById(id);
const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
let actorPersona="",csrf="",conversation=null,activeRun=null,epoch=0,busy=false,contexts=[],pendingControls=0,ready=false,cancelling=false;
function notice(text,error=false){node("agent-notice").textContent=text;node("agent-notice").className=error?"error":"";notifyFeedback(text,error);}
const messages={
 agent_step_budget_exceeded:"已达到本轮24步调度上限，运行已停止。",
 agent_transition_invalid:"运行状态未通过校验，已停止；请核对运行记录。",
 user_cancelled_or_application_stopped:"运行已取消，原始历史和可核对状态已保存。",
 business_outcome_unknown:"业务结果未知，请在运行记录中按已有请求或动作核对，不要重复提交。",
 demo_session_required:"账户会话已过期，请重新选择账户；历史会话仍保留。",
 conversation_busy:"当前会话有运行任务，结束或取消后再操作。",
 worker_protocol_invalid:"模型桥接进程未正常启动或返回协议错误，请检查本机资源及登录状态后核对运行记录。",
 local_compute_busy:"等待本机推理资源超时，运行已停止；请在其他本地推理结束后再试。",
 provider_request_failed:"模型请求失败，运行已停止，请核对通道和本机模型状态。",
 context_capacity_exceeded:"关键约束和当前任务已超出窗口，请增大应用窗口或拆分输入。原始历史仍保留。",
 summary_request_capacity_exceeded:"摘要请求也超出窗口，请增大窗口或拆分任务。",
 summary_validation_failed:"摘要未通过原文引用和关键约束校验，未保存该摘要。",
 repeated_tool_call:"相同工具与参数连续重复，已停止。",
 tool_call_cycle:"检测到重复工具调用环，已停止。",
 tool_no_new_information:"连续工具结果未增加信息，已停止。",
 model_call_budget_exceeded:"已达到本轮6次模型调用上限。",
 tool_call_budget_exceeded:"已达到本轮8次工具调用上限。",
 model_schema_invalid:"模型输出没有通过结构校验。",
 oauth_login_required:"项目ChatGPT登录失效，请到模型配置入口重新登录。",
 memory_version_conflict:"记忆版本已变化，请刷新后重试。",
 model_window_exceeded:"应用窗口超过所选模型的声明上限。",
 unverifiable_citation:"模型引用未通过证据校验，未发布回答。"
};
async function api(path,body){
 const captured=epoch;
 const r=await fetch("/api/v2"+path,{method:body===undefined?"GET":"POST",headers:body===undefined?{}:{"Content-Type":"application/json","X-WorkflowOps-CSRF":csrf},body:body===undefined?undefined:JSON.stringify(body)});
 const data=await r.json();
 if(captured!==epoch)throw new Error("账户已切换，旧结果已忽略");
 if(!r.ok){const code=data.error||data.detail;throw new Error(messages[code]||code||"请求失败");}
 return data;
}
const statusNames={running:"运行中",completed:"已完成",awaiting_confirmation:"等待确认",pending_confirmation:"等待确认",pending_approval:"等待运营审批",submitted:"业务已提交",cancelled:"已取消",failed:"失败",stopped:"已停止",interrupted:"已中断",timed_out:"已超时",unknown_outcome:"结果待核对",active:"有效",ready:"已就绪",expired:"已过期",invalidated:"已失效",rejected:"已拒绝"};
const statusLabel=value=>statusNames[value]||value;
const roleNames={system:"系统规则",user:"用户消息与数据",assistant:"助手消息",tool:"工具结果",tool_definitions:"工具定义",output_schema:"输出结构"};
const reasonNames={within_budget:"预算内，保留当前内容",manual_compression:"用户手动整理",threshold_75_percent:"达到75%预算阈值",capacity_exceeded:"关键内容超出窗口",call_budget_check:"调用前预算检查",validated_summary:"已纳入摘要",lower_relevance_or_duplicate:"低相关或重复"};
const reasonLabel=value=>reasonNames[value]||value;
const eventNames={tool_plan_validated:"工具计划已校验",loop_started:"自研 ReAct 循环启动",loop_transition:"状态流转",model_started:"开始模型调用",model_finished:"模型调用结束",tool_finished:"工具执行完成"};
const purposeNames={summary:"生成摘要",intent:"识别意图",agent:"选择工具与回答",memory:"提取记忆候选",review:"回答校验"};
const loopReasons={intent_ready:"当前请求已识别",validated_tool_request:"工具请求已通过校验",observations_recorded:"工具结果已保存，进入下一次决策",verified_handler_result:"结果已通过应用校验，结束本轮"};
const phaseNames={analyse:"分析请求",decide:"模型决策",act:"执行工具",finish:"结束"};
const eventLabel=event=>event.kind==="loop_transition"?(phaseNames[event.from]||event.from)+" → "+(phaseNames[event.to]||event.to)+" · 第 "+event.step+" 步":(eventNames[event.kind]||event.kind)+" · "+(purposeNames[event.purpose]||event.purpose||event.tool||"");
const number=value=>Number(value).toLocaleString("zh-CN");
function detail(title,value){const d=el("details");d.append(el("summary",title),el("pre",JSON.stringify(value,null,2)));return d;}
const command=body=>({...body,client_request_id:crypto.randomUUID()});
function btn(title,callback){const b=el("button",title);b.addEventListener("click",()=>guard(callback,b));return b;}
async function guard(callback,button,allowConcurrent=false){
 if(pendingControls&&!allowConcurrent)return;
 pendingControls++;syncControls();document.dispatchEvent(new CustomEvent("workflowops:busy",{detail:{owner:"agent-ui",busy:true}}));
 if(button){button.disabled=true;button.setAttribute("aria-busy","true");}
 try{await callback();}catch(error){notice(error.message,true);}finally{if(button){button.disabled=false;button.removeAttribute("aria-busy");}pendingControls--;syncControls();document.dispatchEvent(new CustomEvent("workflowops:busy",{detail:{owner:"agent-ui",busy:pendingControls>0}}));}
}
function renderHistory(rows){
 node("agent-messages").replaceChildren();
 node("agent-empty").hidden=rows.some(r=>r.role==="user"||(r.role==="assistant"&&!r.tool_calls?.length));
 for(const row of rows){
  if(row.role==="tool"||row.tool_calls?.length)continue;
  const box=el("article");box.className="card";box.dataset.role=row.role;
  box.append(el("h3",row.role==="user"?"你":"WorkflowOps · 已校验结果"));
  if(row.role==="assistant"){
   try{const data=JSON.parse(row.content);box.append(el("p",data.answer||data.reason||(data.status==="human_confirmation"?"已记录你的确认，业务结果见下方动作区。":statusLabel(data.status))));
       if(data.business)box.append(detail("查看本轮业务返回",data.business));
       if(data.source_quotes?.length)box.append(detail("核对原文与记忆来源",data.source_quotes));
       if(data.citations)for(const q of data.citations){const c=el("blockquote","证据 "+q.id);c.append(el("small","来源："+(q.title||q.document_id||q.id)+" · "+(q.version||"")+" · "+(q.source||"")));box.append(c);}
   }catch{box.append(el("p",row.content));}
  }else box.append(el("p",row.content));
  node("agent-messages").append(box);
 }
}
async function listConversations(){
 const rows=await api("/conversations");
 node("conversation-picker").replaceChildren();
 for(const row of rows){const o=el("option",displayName(row.title)+" · "+row.channel+" · "+row.id.slice(0,8));o.value=row.id;node("conversation-picker").append(o);}
 if(!rows.length){const option=el("option","暂无历史会话");option.value="";node("conversation-picker").append(option);}
 if(conversation)node("conversation-picker").value=conversation.id;
 syncControls();
}
async function openConversation(id){
 if(!id)throw new Error("暂无可打开的会话，请先新建会话。");
 const data=await api("/conversations/"+id);
 conversation=data.conversation;
 contexts=[];
 for(const id of ["context-panel","context-components","context-selection","context-picker","summary-panel","run-summary","run-timeline"])node(id).replaceChildren();
 for(const id of ["context-raw","run-events","context-run-label","agent-stream"])node(id).textContent="";

 node("conversation-picker").value=conversation.id;
 localStorage.setItem("workflowops.conversation."+actorPersona,conversation.id);
 node("agent-channel").value=conversation.channel;node("agent-window").value=String(conversation.context_window);
 renderHistory(data.messages);
 const action=data.action_states?.findLast(a=>a.status==="pending_confirmation"||a.status==="pending_approval")||data.action_states?.at(-1);
 renderAction(action?{action}:null);
 activeRun=conversation.active_run;syncControls();
 await listRuns();
 if(data.latest_run)await panels(data.latest_run.id);else renderContext(-1);
 renderSummary(data.summary);
 notice("当前会话："+conversation.id.slice(0,8)+" · "+conversation.channel);
 if(activeRun&&!busy)await watch(activeRun);
}
async function renderMemory(){
 const data=await api("/memories");
 node("memory-candidates").replaceChildren();node("memory-records").replaceChildren();
 for(const row of data.candidates){
  const c=el("article");c.className="card";c.append(el("h3","待确认的记忆"),el("p",row.text),el("p","提取模型："+row.model_id),detail("核对候选来源",{source_message_id:row.source_message_id,model_id:row.model_id}));
  if(row.status==="pending")for(const [confirm,label] of [[true,"确认保存"],[false,"拒绝候选"]])c.append(btn(label,async()=>{const r=await api("/memories/candidates/"+row.id+"/confirm",command({expected_version:row.version,confirm}));notice("记忆状态："+r.status+"；索引："+(r.index?.status||"未更新"));await renderMemory();}));
  if(row.status==="pending")node("memory-candidates").append(c);
 }
 for(const row of data.records){
  const c=el("article");c.className="card";c.append(el("h3","已确认 · 版本"+row.version),el("p",row.text),el("p","索引："+statusLabel(row.index_status)+" · 到期："+(row.expires?new Date(row.expires).toLocaleDateString():"未设置")));
  const input=el("textarea");input.value=row.text;input.maxLength=500;input.required=true;input.setAttribute("aria-label","修改记忆");
  const days=el("input");days.type="number";days.min="1";days.max="3650";days.placeholder="有效天数（可选）";days.setAttribute("aria-label","有效天数");
  c.append(input,days,btn("保存修改",async()=>{if(!input.reportValidity()||!days.reportValidity())return;await api("/memories/"+row.id+"/edit",command({expected_version:row.version,text:input.value,expires_in_days:days.value?Number(days.value):null}));await renderMemory();notice("记忆已更新，后续召回使用新版本。");}),btn("删除这条记忆",async()=>{await api("/memories/"+row.id+"/delete",command({expected_version:row.version}));await renderMemory();notice("权威记忆已删除，即使索引失败也不会再次使用。");}));
  node("memory-records").append(c);
 }
}
async function listRuns(){
 const rows=await api("/runs"+(conversation?"?conversation_id="+encodeURIComponent(conversation.id):""));
 const previous=node("run-picker").value;
 node("run-picker").replaceChildren(...rows.map(row=>{const n=el("option",row.channel+" · "+statusLabel(row.status)+" · "+new Date(row.created).toLocaleString());n.value=row.id;return n;}));
 if(!rows.length){const option=el("option","暂无运行记录");option.value="";node("run-picker").append(option);}
 if(rows.some(r=>r.id===previous))node("run-picker").value=previous;
 syncControls();
}
function renderSummary(summary){
 node("summary-panel").replaceChildren(el("h3","会话摘要"+(summary?" · 版本 "+summary.version:"")));
 if(!summary){node("summary-panel").append(el("p","尚未生成摘要，历史仍由预算选择。"));return;}
 const d=el("details");d.append(el("summary","查看摘要与原消息引用"),el("pre",JSON.stringify(summary,null,2)));node("summary-panel").append(d);
}
function renderContext(index){
 const item=contexts[index];node("context-panel").replaceChildren();node("context-components").replaceChildren();node("context-selection").replaceChildren();
 if(!item){const empty=el("p","此运行还没有上下文快照。可打开历史会话，或在运行记录中选择一次模型调用。");empty.className="empty-state";node("context-panel").append(empty);node("context-raw").textContent="";return;}
 const grid=el("div");grid.className="metric-grid";
 for(const [label,value,unit] of [["当前输入",item.input_tokens,"估算 token"],["输入预算",item.input_budget,"token"],["输出预留",item.output_reserved,"token"],["安全余量",item.safety_margin,"token"]]){
  const card=el("div");card.className="metric-card";card.append(el("span",label),el("strong",number(value)),el("small",unit));grid.append(card);
 }
 const meter=el("meter");meter.className="budget-meter";meter.min=0;meter.max=item.input_budget;meter.low=item.input_budget*.55;meter.high=item.input_budget*.75;meter.optimum=0;meter.value=item.input_tokens;meter.setAttribute("aria-label","输入预算使用量");
 const caption=el("p","输入预算已使用 "+Math.round(item.input_tokens/item.input_budget*100)+"% · 自动整理触发点 75%");caption.className="budget-caption";
 const note=el("p","计数来源："+item.source+" · 校准系数 "+Number(item.factor).toFixed(2)+" · 非供应商原生精确计数");note.className="estimator-note";
 const compression=el("div");compression.className="compression-strip";compression.append(el("div","整理前后"),el("strong",number(item.before_tokens)+" → "+number(item.after_tokens)),el("span",reasonLabel(item.reason)+" · 摘要 "+(item.summary_version?"v"+item.summary_version:"尚未生成")));
 node("context-panel").append(grid,meter,caption,compression,note);
 const table=el("table");table.append(el("caption","按角色汇总的估算，含封装开销；总预算以调用输入为准。"));
 const tr=el("tr");tr.append(el("th","内容来源"),el("th","条目"),el("th","估算 token"));table.append(tr);
 const groups=new Map();
 for(const row of item.components||[]){const g=groups.get(row.role)||{count:0,tokens:0};g.count++;g.tokens+=row.estimated_tokens;groups.set(row.role,g);}
 for(const [role,g] of groups){const r=el("tr");r.append(el("td",roleNames[role]||role),el("td",String(g.count)),el("td",number(g.tokens)));table.append(r);}node("context-components").append(table);
 node("context-selection").append(el("h2","选择与省略"));
 const chips=el("div");chips.className="selection-chips";chips.append(el("span","入选 "+(item.included_message_ids?.length||0)+" 条"),el("span","省略 "+(item.omitted?.length||0)+" 条"));node("context-selection").append(chips);
 const reasons=new Map();for(const row of item.omitted||[])reasons.set(row.reason,(reasons.get(row.reason)||0)+1);
 for(const [reason,count] of reasons)node("context-selection").append(el("p",reasonLabel(reason)+" · "+count+" 条"));
 node("context-selection").append(detail("核对消息 ID 与省略理由",{included:item.included_message_ids,omitted:item.omitted}));
 node("context-raw").textContent=JSON.stringify(item,null,2);
}
async function panels(rid){
 const [rows,events,record]=await Promise.all([api("/runs/"+rid+"/context"),api("/runs/"+rid+"/events"),api("/runs/"+rid)]);
 contexts=rows;
 node("context-run-label").textContent="选中运行 "+rid+" · "+record.channel+" · "+statusLabel(record.status);
 node("context-picker").replaceChildren(...rows.map((row,i)=>{const n=el("option",(i+1)+" · "+(row.snapshot_kind==="actual_model_request"?"实际调用":"上下文组装")+" · "+reasonLabel(row.reason));n.value=String(i);return n;}));
 node("context-picker").value=String(Math.max(0,rows.length-1));renderContext(rows.length-1);
 node("run-events").textContent=JSON.stringify(events,null,2);
 if(events.some(e=>e.kind==="summary_rejected"))node("context-panel").append(el("p","本轮摘要未通过原文校验，保留可容纳的原始历史；未保存新的摘要版本。"));
 node("run-summary").replaceChildren(el("h3",record.channel+" · "+statusLabel(record.status)),el("p","运行 "+rid+" · 模型 "+record.model_calls+" 次 · 工具 "+record.tool_calls+" 次 · 窗口 "+record.context_window));
 const loop=events.find(event=>event.kind==="loop_started");
 if(loop)node("run-summary").append(el("p","运行引擎：自研 ReAct · 状态迁移 "+events.filter(event=>event.kind==="loop_transition").length+" 次 · 调度上限 "+loop.max_steps+" 步"));
 if(record.result?.reason)node("run-summary").append(el("p",messages[record.result.reason]||record.result.reason));
 if(record.status==="unknown_outcome"){
  const refs=record.result?.domain_refs||{},requestId=refs.client_request_id||record.pending_command_id;
  node("run-summary").append(el("p","业务写入结果未知，请核对已有请求或动作，不要重复提交。"),el("pre",JSON.stringify(refs,null,2)));
  const path=requestId?"/actions/requests/"+encodeURIComponent(requestId):refs.action_id?"/actions/"+encodeURIComponent(refs.action_id):null;
  if(path)node("run-summary").append(btn("核对已有业务结果",async()=>{const receipt=await api(path);node("run-summary").append(el("pre",JSON.stringify(receipt,null,2)));notice("已读取业务回执；请在订单管理中核对最新状态。");}));
 }
 node("run-timeline").replaceChildren();
 for(const event of events){
  const li=el("li");li.append(el("strong",eventLabel(event)));
  if(event.usage)li.append(el("p","输入 "+(event.usage.input_tokens??"未知")+" token · 输出 "+(event.usage.output_tokens??"未知")+" token · "+(event.usage.source==="provider"?"供应商报告":"用量未知")));
  if(event.error||event.reason)li.append(el("p",messages[event.error]||loopReasons[event.reason]||event.error||event.reason));
  node("run-timeline").append(li);
 }
 node("run-picker").value=rid;syncControls();
}
function renderAction(result){
 node("agent-action").replaceChildren();
 const row=result?.action;if(!row)return;
 node("agent-action").append(el("h3",statusLabel(row.status)+" · 业务草案"),el("p","报价 "+(row.quoted_amount??"—")+" 元 · 版本 "+row.version),el("p",row.action_id));
 const d=el("details");d.append(el("summary","核对动作内容和结构化结果"),el("pre",JSON.stringify(row,null,2)));node("agent-action").append(d);
 if(row.status==="pending_confirmation")for(const [confirm,title] of [[true,"确认执行该动作"],[false,"取消草案"]])node("agent-action").append(btn(title,async()=>{const run=await api("/conversations/"+conversation.id+"/confirm",command({action_id:row.action_id,expected_version:row.version,confirm}));notice("确认结果："+statusLabel(run.status));await openConversation(conversation.id);renderAction(run.result);}));
 if(row.status==="pending_approval")node("agent-action").append(el("p","需运营先审批，请进入订单管理处理审批。审批后刷新会话再确认。"));
}
async function watch(rid){
 busy=true;activeRun=rid;syncControls();document.dispatchEvent(new CustomEvent("workflowops:busy",{detail:{owner:"agent",busy:true}}));node("agent-send").disabled=true;node("conversation-config").disabled=true;node("agent-login").disabled=true;
 const ownEpoch=epoch;
 try{
  await panels(rid);
  let final=null;
  try{
  const response=await fetch("/api/v2/runs/"+rid+"/stream");
  if(!response.ok)throw new Error("无法读取运行状态");
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer="";
  while(true){
   const {done,value}=await reader.read();if(done)break;
   buffer+=decoder.decode(value,{stream:true});let cut;
   while((cut=buffer.indexOf("\n\n"))>=0){
    const block=buffer.slice(0,cut);buffer=buffer.slice(cut+2);
    const line=block.split("\n").find(x=>x.startsWith("data: "));if(!line)continue;
    const event=JSON.parse(line.slice(6));
    if(ownEpoch!==epoch){await reader.cancel();return;}
    if(block.startsWith("event: final")){final=event;}
    else {node("agent-stream").textContent="处理中："+eventLabel(event)+"（步骤状态，尚非最终回答）";if(event.kind==="model_started")await panels(rid);}
   }
  }
  }catch{node("agent-stream").textContent="流式连接中断，正在核对已有运行；不会重新发送请求。";}
  const deadline=Date.now()+630000;
  while(!final?.ended){
   final=await api("/runs/"+rid);
   if(final.ended)break;
   if(Date.now()>deadline)throw new Error("页面等待已结束，请刷新运行记录核对状态。");
   await new Promise(resolve=>setTimeout(resolve,1500));
  }
  activeRun=null;syncControls();
  node("agent-stream").textContent="运行："+statusLabel(final.status)+" · 模型 "+final.model_calls+" 次 · 工具 "+final.tool_calls+" 次";
  if(final.result?.reason)notice(messages[final.result.reason]||final.result.reason,true);
  else notice("本轮结束："+statusLabel(final.status));
  await panels(rid);await openConversation(conversation.id);await renderMemory();renderAction(final.result);
  if(final.result?.reason)notice(messages[final.result.reason]||final.result.reason,true);
 }finally{busy=false;syncControls();document.dispatchEvent(new CustomEvent("workflowops:busy",{detail:{owner:"agent",busy:false}}));node("agent-login").disabled=false;}
}
whenSession(event=>guard(async()=>{
 ready=false;setReady(false);
 epoch++;actorPersona=event.detail.persona;csrf=event.detail.csrf_token;conversation=null;activeRun=null;contexts=[];
 for(const id of ["agent-messages","context-panel","context-components","context-selection","summary-panel","run-summary","run-timeline","agent-action","memory-candidates","memory-records","context-picker","run-picker","conversation-picker"])node(id).replaceChildren();
 for(const id of ["context-raw","run-events","agent-stream","context-run-label"])node(id).textContent="";
 node("agent-text").value="";
 node("agent-empty").hidden=false;
 await listConversations();await renderMemory();await listRuns();
 notice("当前账户："+accountLabel(event.detail.persona)+"，请选择或创建会话。");
 ready=true;syncControls();
 const saved=localStorage.getItem("workflowops.conversation."+actorPersona);
 if(saved&&[...node("conversation-picker").options].some(o=>o.value===saved))await openConversation(saved);
 ready=true;setReady(true);
}));
node("conversation-new").addEventListener("click",()=>guard(async()=>{if(busy)throw new Error("请先结束当前运行");conversation=await api("/conversations",{channel:node("agent-channel").value,context_window:Number(node("agent-window").value),title:"业务会话"});await listConversations();await openConversation(conversation.id);},node("conversation-new")));
node("conversation-open").addEventListener("click",()=>guard(async()=>{if(busy)throw new Error("请先结束当前运行");await openConversation(node("conversation-picker").value);},node("conversation-open")));
node("conversation-config").addEventListener("click",()=>guard(async()=>{if(!conversation)throw new Error("请先创建或打开会话");conversation=await api("/conversations/"+conversation.id+"/config",{channel:node("agent-channel").value,context_window:Number(node("agent-window").value)});await listConversations();notice("模型与预算已保存，下轮重新组装上下文。");},node("conversation-config")));
node("agent-form").addEventListener("submit",event=>{event.preventDefault();guard(async()=>{if(busy||activeRun)return;if(!conversation)throw new Error("请先创建会话");const run=await api("/conversations/"+conversation.id+"/turns",command({text:node("agent-text").value}));node("agent-text").value="";await watch(run.id);});});
node("agent-compress").addEventListener("click",()=>guard(async()=>{if(!conversation||busy)throw new Error("请先选择空闲会话");const r=await api("/conversations/"+conversation.id+"/compress",command({text:"手动整理上下文"}));await watch(r.id);}));
node("agent-cancel").addEventListener("click",()=>guard(async()=>{
 if(!activeRun||cancelling)return;
 cancelling=true;syncControls();
 try{await api("/runs/"+activeRun+"/cancel",{});notice("取消请求已提交，正在核对运行状态。");if(!busy)await openConversation(conversation.id);}
 finally{cancelling=false;syncControls();}
},node("agent-cancel"),true));
node("memory-refresh").addEventListener("click",()=>guard(renderMemory,node("memory-refresh")));
node("context-picker").addEventListener("change",()=>renderContext(Number(node("context-picker").value)));
node("runs-refresh").addEventListener("click",()=>guard(listRuns,node("runs-refresh")));
node("run-open").addEventListener("click",()=>guard(async()=>{const rid=node("run-picker").value;if(!rid)throw new Error("当前没有运行");await panels(rid);},node("run-open")));

function syncControls(){
 const state=conversationControls({ready,hasConversation:Boolean(conversation),hasConversations:[...node("conversation-picker").options].some(o=>o.value),hasRuns:[...node("run-picker").options].some(o=>o.value),hasContexts:contexts.length>0,running:Boolean(activeRun),pending:pendingControls>0,cancelling});
 for(const [id,disabled] of Object.entries(state))node(id).disabled=disabled;
 node("agent-cancel").title=activeRun?"取消当前运行":"当前没有运行任务";
 node("conversation-open").title=conversation?"打开所选会话":"选择一个历史会话";
}
function setReady(value){ready=value;syncControls();}
setReady(false);
