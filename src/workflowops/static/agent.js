"use strict";
const node=id=>document.getElementById(id);
const el=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
let csrf="",conversation=null,activeRun=null,epoch=0,busy=false;
function notice(text,error=false){node("agent-notice").textContent=text;node("agent-notice").className=error?"error":"";}
const messages={
 conversation_busy:"当前会话有运行任务，结束或取消后再操作。",
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
 if(captured!==epoch)throw new Error("身份已切换，旧结果已忽略");
 if(!r.ok){const code=data.error||data.detail;throw new Error(messages[code]||code||"请求失败");}
 return data;
}
const command=body=>({...body,client_request_id:crypto.randomUUID()});
function btn(title,callback){const b=el("button",title);b.addEventListener("click",()=>guard(callback,b));return b;}
async function guard(callback,button){
 if(button)button.disabled=true;
 try{await callback();}catch(error){notice(error.message,true);}finally{if(button)button.disabled=false;}
}
function renderHistory(rows){
 node("agent-messages").replaceChildren();
 for(const row of rows){
  if(row.role==="tool"||row.tool_calls?.length)continue;
  const box=el("article");box.className="card";
  box.append(el("h3",row.role==="user"?"你":"已验证结果"));
  if(row.role==="assistant"){
   try{const data=JSON.parse(row.content);box.append(el("p",data.answer||data.reason||data.status));
       if(data.business)box.append(el("pre",JSON.stringify(data.business,null,2)));
       if(data.source_quotes)box.append(el("pre",JSON.stringify(data.source_quotes,null,2)));
       if(data.citations)box.append(el("pre",JSON.stringify(data.citations,null,2)));
   }catch{box.append(el("p",row.content));}
  }else box.append(el("p",row.content));
  node("agent-messages").append(box);
 }
}
async function listConversations(){
 const rows=await api("/conversations");
 node("conversation-picker").replaceChildren();
 for(const row of rows){const o=el("option",row.title+" · "+row.channel+" · "+row.id.slice(0,8));o.value=row.id;node("conversation-picker").append(o);}
 if(conversation)node("conversation-picker").value=conversation.id;
}
async function openConversation(id){
 const data=await api("/conversations/"+id);
 conversation=data.conversation;
 node("agent-channel").value=conversation.channel;node("agent-window").value=String(conversation.context_window);
 renderHistory(data.messages);
 const action=data.action_states?.findLast(a=>a.status==="pending_confirmation"||a.status==="pending_approval")||data.action_states?.at(-1);
 renderAction(action?{action}:null);
 activeRun=conversation.active_run;
 notice("当前会话："+conversation.id.slice(0,8)+" · "+conversation.channel);
 if(activeRun&&!busy)await watch(activeRun);
}
async function renderMemory(){
 const data=await api("/memories");
 node("memory-candidates").replaceChildren();node("memory-records").replaceChildren();
 for(const row of data.candidates){
  const c=el("article");c.className="card";c.append(el("h3","候选 · "+row.status),el("p",row.text),el("p","来源："+row.source_message_id+" · "+row.model_id));
  if(row.status==="pending")for(const [confirm,label] of [[true,"确认保存"],[false,"拒绝候选"]])c.append(btn(label,async()=>{const r=await api("/memories/candidates/"+row.id+"/confirm",command({expected_version:row.version,confirm}));notice("记忆状态："+r.status+"；索引："+(r.index?.status||"未更新"));await renderMemory();}));
  node("memory-candidates").append(c);
 }
 for(const row of data.records){
  const c=el("article");c.className="card";c.append(el("h3","已确认 · 版本"+row.version),el("p",row.text),el("p","索引："+row.index_status+" · 到期："+(row.expires||"未设置")));
  const input=el("textarea");input.value=row.text;input.maxLength=500;input.setAttribute("aria-label","修改记忆");
  const days=el("input");days.type="number";days.min="1";days.max="3650";days.placeholder="有效天数（可选）";days.setAttribute("aria-label","有效天数");
  c.append(input,days,btn("保存修改",async()=>{await api("/memories/"+row.id+"/edit",command({expected_version:row.version,text:input.value,expires_in_days:days.value?Number(days.value):null}));await renderMemory();}),btn("删除这条记忆",async()=>{await api("/memories/"+row.id+"/delete",command({expected_version:row.version}));await renderMemory();notice("权威记忆已删除，即使索引失败也不会再次使用。");}));
  node("memory-records").append(c);
 }
}
async function panels(rid){
 const [contexts,events]=await Promise.all([api("/runs/"+rid+"/context"),api("/runs/"+rid+"/events")]);
 node("context-raw").textContent=JSON.stringify(contexts,null,2);node("run-events").textContent=JSON.stringify(events,null,2);
 node("context-panel").replaceChildren();
 const latest=contexts.at(-1);
 if(latest){node("context-panel").append(el("p","输入 "+latest.input_tokens+" / "+latest.input_budget+"（估算） · 输出预留 "+latest.output_reserved+" · 安全余量 "+latest.safety_margin),el("p","来源 "+latest.source+" · 校准系数 "+latest.factor.toFixed(2)),el("p","整理前 "+latest.before_tokens+" → 后 "+latest.after_tokens+" · "+latest.reason+" · 摘要版本 "+(latest.summary_version??"无")),el("p","入选消息 "+latest.included_message_ids.length+" · 省略消息 "+latest.omitted.length));}
}
function renderAction(result){
 node("agent-action").replaceChildren();
 const row=result?.action;if(!row)return;
 node("agent-action").append(el("h3","业务草案："+row.action_id),el("pre",JSON.stringify(row,null,2)));
 if(row.status==="pending_confirmation")for(const [confirm,title] of [[true,"确认执行该动作"],[false,"取消草案"]])node("agent-action").append(btn(title,async()=>{const run=await api("/conversations/"+conversation.id+"/confirm",command({action_id:row.action_id,expected_version:row.version,confirm}));notice("确认结果："+run.status);await openConversation(conversation.id);renderAction(run.result);}));
 if(row.status==="pending_approval")node("agent-action").append(el("p","需运营先审批，请进入订单与模拟控制台。审批后刷新会话再确认。"));
}
async function watch(rid){
 busy=true;activeRun=rid;node("agent-send").disabled=true;node("conversation-config").disabled=true;node("agent-login").disabled=true;
 const ownEpoch=epoch;
 try{
  const response=await fetch("/api/v2/runs/"+rid+"/stream");
  if(!response.ok)throw new Error("无法读取运行状态");
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer="",final=null;
  while(true){
   const {done,value}=await reader.read();if(done)break;
   buffer+=decoder.decode(value,{stream:true});let cut;
   while((cut=buffer.indexOf("\n\n"))>=0){
    const block=buffer.slice(0,cut);buffer=buffer.slice(cut+2);
    const line=block.split("\n").find(x=>x.startsWith("data: "));if(!line)continue;
    const event=JSON.parse(line.slice(6));
    if(ownEpoch!==epoch){await reader.cancel();return;}
    if(block.startsWith("event: final")){final=event;}
    else node("agent-stream").textContent="处理中："+event.kind+" · "+(event.purpose||event.tool||"")+"（步骤状态，尚非最终回答）";
   }
  }
  if(!final)final=await api("/runs/"+rid);
  node("agent-stream").textContent="运行："+final.status+" · 模型 "+final.model_calls+" 次 · 工具 "+final.tool_calls+" 次";
  if(final.result?.reason)notice(messages[final.result.reason]||final.result.reason,true);
  else notice("本轮结束："+final.status);
  await panels(rid);await openConversation(conversation.id);await renderMemory();renderAction(final.result);
 }finally{busy=false;activeRun=null;node("agent-send").disabled=false;node("conversation-config").disabled=false;node("agent-login").disabled=false;}
}
node("agent-login").addEventListener("click",()=>guard(async()=>{epoch++;const r=await api("/demo/session",{persona:node("agent-persona").value});csrf=r.csrf_token;conversation=null;activeRun=null;node("agent-messages").replaceChildren();node("context-panel").replaceChildren();node("context-raw").textContent="";node("run-events").textContent="";node("agent-action").replaceChildren();await listConversations();await renderMemory();notice("身份："+r.persona);}));
node("conversation-new").addEventListener("click",()=>guard(async()=>{if(busy)throw new Error("请先结束当前运行");conversation=await api("/conversations",{channel:node("agent-channel").value,context_window:Number(node("agent-window").value),title:"演示会话"});await listConversations();await openConversation(conversation.id);}));
node("conversation-open").addEventListener("click",()=>guard(async()=>{if(busy)throw new Error("请先结束当前运行");await openConversation(node("conversation-picker").value);}));
node("conversation-config").addEventListener("click",()=>guard(async()=>{if(!conversation)throw new Error("请先创建或打开会话");conversation=await api("/conversations/"+conversation.id+"/config",{channel:node("agent-channel").value,context_window:Number(node("agent-window").value)});notice("模型与预算已保存，下轮重新组装上下文。");}));
node("agent-form").addEventListener("submit",event=>{event.preventDefault();guard(async()=>{if(busy)return;if(!conversation)throw new Error("请先创建会话");const run=await api("/conversations/"+conversation.id+"/turns",command({text:node("agent-text").value}));node("agent-text").value="";await watch(run.id);});});
node("agent-compress").addEventListener("click",()=>guard(async()=>{if(!conversation||busy)throw new Error("请先选择空闲会话");const r=await api("/conversations/"+conversation.id+"/compress",command({text:"手动整理上下文"}));await watch(r.id);}));
node("agent-cancel").addEventListener("click",()=>guard(async()=>{if(activeRun)await api("/runs/"+activeRun+"/cancel",{});}));
node("memory-refresh").addEventListener("click",()=>guard(renderMemory));
(async()=>{try{const r=await api("/demo/session");csrf=r.csrf_token;node("agent-persona").value=r.persona;await listConversations();await renderMemory();notice("身份："+r.persona);}catch{notice("请选择演示身份。");}})();
