import {publishSession,whenSession,notifyFeedback,accountLabel,displayName,policyLabel} from "./workbench.js";
"use strict";
const errors={approval_required:"需要运营审批后再确认",cancellation_not_available:"当前订单状态不能取消，可创建异常工单",payment_not_available:"当前订单不可发起支付",payment_in_progress:"支付结果尚未确定，请先核对状态",refund_already_recorded:"已存在退款申请或模拟退款结果",unpaid_order_not_refundable:"未支付订单不能申请退款",action_version_conflict:"草案版本已变化，请刷新",order_version_conflict:"订单版本已变化，请刷新",payment_event_not_available:"此订单没有待处理的支付请求",delivery_event_not_available:"此订单不能继续推进配送",refund_event_not_available:"此订单没有待处理的退款请求",operator_required:"此操作需要运营账户",customer_required:"请切换到客户账户",object_not_found:"数据不存在或当前身份无权查看",csrf_required:"会话已变化，请重新选择账户",ticket_closed:"工单已关闭",illegal_ticket_transition:"当前工单不能转到该状态",invalid_request:"请检查输入字段和数量",request_id_conflict:"请求编号已用于其他操作",business_write_failed:"操作未完成，请刷新核对"};
Object.assign(errors,{
  oauth_login_required:"尚未完成项目 ChatGPT 登录，请先完成下方登录说明中的授权，再选择并验证模型。",
  demo_session_required:"账户会话已过期，请重新选择运营账户。",
  model_unavailable:"所选模型当前不可用，请读取模型状态后重新选择。",
  provider_timeout:"模型验证超时，请检查网络和项目登录状态。",
  model_validation_failed:"模型验证未完成，请检查项目登录和网络后再试。",
  chatgpt_bridge_failed:"ChatGPT 连接未完成，请检查项目登录和网络。",
  provider_request_failed:"模型请求失败，请检查项目登录、账号额度及网络。",
  provider_output_limit:"验证响应超过应用输出预算，请核对模型状态后再试。",
  model_validation_incomplete:"模型没有返回完整验证结果，未保存所选模型。"
});
let csrf = "", persona = "", operator = false, epoch = 0, busy = false, unknown = null;
const labels = {ecommerce:"电商",food:"外卖",awaiting_payment:"待支付",paid:"已支付",ready_to_ship:"待发货",shipped:"已发货",accepted:"已接单",preparing:"制作中",delivering:"配送中",delivered:"已送达",cancelled:"已取消",unpaid:"未支付",pending:"支付处理中",failed:"失败",none:"无退款",requested:"退款已申请",refunded:"退款完成",pending_confirmation:"等待确认",pending_approval:"等待审批",submitted:"业务已提交",expired:"已过期",rejected:"已拒绝",invalidated:"已失效",open:"待处理",assigned:"已分派",pending_customer:"等待客户",resolved:"已解决",closed:"已关闭",place_order:"下单",pay:"支付",cancel_order:"取消订单",refund:"整单退款",create_ticket:"创建工单",reply_ticket:"回复工单",assign_ticket:"分派工单",transition_ticket:"更新工单"};
const $ = id => document.getElementById(id);
function element(tag, text, cls) {const node=document.createElement(tag); if(text!==undefined)node.textContent=text; if(cls)node.className=cls; return node;}
function notice(text, error=false) {$("notice").textContent=text;$("notice").className=error?"error":"";notifyFeedback(text,error);}
function button(text, handler) {const b=element("button",text);b.addEventListener("click",()=>act(handler,b));return b;}
async function api(path, body, options={}) {
  const generation=epoch, headers={};
  if(body!==undefined){headers["Content-Type"]="application/json";headers["X-Yice-Agent-CSRF"]=csrf;}
  const controller=options.timeoutMs?new AbortController():null;
  const timer=controller?setTimeout(()=>controller.abort(),options.timeoutMs):null;
  try {
    const response=await fetch("/api/v2"+path,{method:body===undefined?"GET":"POST",headers,body:body===undefined?undefined:JSON.stringify(body),signal:controller?.signal});
    const data=await response.json();
    if(generation!==epoch)throw new Error("账户已切换，旧结果已忽略");
    if(!response.ok){
      if(data.status==="unknown_outcome"){unknown=data.domain_refs;notice("写入结果未知。请核对请求，不要重复提交。请求 ID："+unknown.client_request_id,true);}
      const code=typeof data.error==="string"?data.error:typeof data.detail==="string"?data.detail:"request_failed";
      const error=new Error(errors[code]||"请求失败，请刷新状态后核对");error.code=code;throw error;
    }
    return data;
  } catch(error) {
    if(error.name==="AbortError"){const timedOut=new Error("验证等待超过60秒。请读取模型状态核对结果，不要连续点击。");timedOut.code="request_timeout";throw timedOut;}
    throw error;
  } finally { if(timer!==null)clearTimeout(timer); }
}
function request(body){return {...body,client_request_id:crypto.randomUUID()};}
function syncBusinessControls(){
 document.querySelectorAll("#view-business button").forEach(b=>{b.disabled=!csrf||busy||Boolean(unknown);});
 $("refresh").disabled=!csrf||busy;
}
async function act(callback,control){
  if(busy)return; if(unknown){notice("有结果未知的写入，请先核对请求再继续操作。",true);return;} busy=true;syncBusinessControls();syncModelControls();document.dispatchEvent(new CustomEvent("yice-agent:busy",{detail:{owner:"business",busy:true}}));
  if(control)control.setAttribute("aria-busy","true");
  try{const result=await callback();if(result!==false){await refresh();if(result!==undefined)notice("操作已完成，业务数据已刷新。");}}catch(error){if(!unknown)notice(error.message,true);else{try{await refresh();}catch{}}}finally{busy=false;syncBusinessControls();syncModelControls();if(control)control.removeAttribute("aria-busy");syncModelControls();document.dispatchEvent(new CustomEvent("yice-agent:busy",{detail:{owner:"business",busy:false}}));}
}
async function draft(body){
  const row=await api("/actions",request(body));
  notice("草案："+row.action_id+" / "+(labels[row.status]||row.status)+" / 报价："+(row.quoted_amount===null?"—":row.quoted_amount+" 元"));
}
function card(title, lines) {const node=element("article",undefined,"card");node.append(element("h3",title));for(const line of lines)node.append(element("p",line));return node;}
function renderProducts(rows){
 $("product-list").replaceChildren();
 for(const row of rows){const c=card(displayName(row.name),[(labels[row.order_type]||row.order_type)+" · "+row.price+" 元",row.product_id]);const qty=element("input");qty.type="number";qty.min="1";qty.max="20";qty.value="1";qty.setAttribute("aria-label","商品数量");
 const controls=element("div",undefined,"controls");controls.append(qty);if(!operator)controls.append(button("生成下单草案",()=>{if(!qty.reportValidity())return false;return draft({kind:"place_order",order_type:row.order_type,items:[{product_id:row.product_id,quantity:Number(qty.value)}],note:""});}));c.append(controls);$("product-list").append(c);}
}
function renderOrders(rows){
 $("order-list").replaceChildren();
 for(const row of rows){const c=card(row.order_id,[(labels[row.order_type]||row.order_type)+" / "+(labels[row.status]||row.status),"金额 "+row.amount+" 元 · 支付 "+(labels[row.payment_status]||row.payment_status),"退款 "+(labels[row.refund_status]||row.refund_status)+" · 累计退款 "+row.refunded_amount+" 元","客户 "+accountLabel(row.customer_id)+" · 版本 "+row.version]);const controls=element("div",undefined,"controls");
 if(operator){for(const [event,title] of [["payment_succeeded","标记支付成功"],["payment_failed","标记支付失败"],["advance","推进配送"],["refund_succeeded","标记退款成功"],["refund_failed","标记退款失败"]])controls.append(button(title,()=>api("/simulator/events",request({order_id:row.order_id,expected_version:row.version,event}))));}
 else{for(const [kind,title] of [["pay","支付草案"],["cancel_order","取消草案"],["refund","退款草案"]])controls.append(button(title,()=>draft({kind,order_id:row.order_id})));controls.append(button("异常工单",async()=>{$("ticket-order").value=row.order_id;$("ticket-message").focus();notice("已填入订单，请描述问题并生成工单草案。");}));}
 c.append(controls);$("order-list").append(c);}
}
function renderActions(rows){
 $("action-list").replaceChildren();
 for(const row of rows){const c=card((labels[row.kind]||row.kind)+" · "+(labels[row.status]||row.status),[row.action_id,"报价 "+(row.quoted_amount===null?"—":row.quoted_amount+" 元")+" · 版本 "+row.version,"有效期 "+new Date(row.expires_at).toLocaleString(),"已审批 "+(row.human_approved?"是":"否")+" · 已确认 "+(row.confirmed?"是":"否")]);const detail=element("details");detail.append(element("summary","动作内容与业务回执"),element("pre",JSON.stringify({payload:row.payload,result:row.result},null,2)));c.append(detail);const controls=element("div",undefined,"controls");
 if(operator && row.status==="pending_approval"){for(const [decision,title] of [["approve","批准"],["reject","拒绝"]])controls.append(button(title,()=>api("/actions/"+row.action_id+"/review",request({decision,expected_version:row.version}))));}
 if(row.status==="pending_confirmation" || row.status==="pending_approval"){if(row.actor_id===persona){if(row.status==="pending_confirmation")controls.append(button("确认执行",()=>api("/actions/"+row.action_id+"/confirm",request({confirm:true,expected_version:row.version}))));controls.append(button("取消草案",()=>api("/actions/"+row.action_id+"/confirm",request({confirm:false,expected_version:row.version}))));}}
 c.append(controls);$("action-list").append(c);}
}
function renderTickets(rows){
 $("ticket-list").replaceChildren();
 for(const row of rows){const c=card(row.ticket_id,[(labels[row.status]||row.status)+" · 版本 "+row.version,"订单 "+(row.order_id||"无")+" · 分派 "+(row.assignee_id||"未分派")]);const messages=element("div"),text=element("textarea");text.maxLength=2000;text.required=true;text.setAttribute("aria-label","工单回复");const controls=element("div",undefined,"controls");
 const view=element("button","查看对话");view.addEventListener("click",async()=>{try{const values=await api("/tickets/"+row.ticket_id+"/messages");messages.replaceChildren();for(const m of values)messages.append(element("p",(m.sender_role==="operator"?"运营":"客户")+"："+m.message,"message"));}catch(error){notice(error.message,true);}});controls.append(view);
 if(row.status!=="closed"){c.append(text);controls.append(button("回复草案",()=>{if(!text.reportValidity())return false;return draft({kind:"reply_ticket",ticket_id:row.ticket_id,message:text.value});}));if(operator){controls.append(button("分派给自己",()=>draft({kind:"assign_ticket",ticket_id:row.ticket_id,assignee_id:persona})));for(const status of ["pending_customer","resolved","closed","assigned"])controls.append(button(labels[status]+"草案",()=>draft({kind:"transition_ticket",ticket_id:row.ticket_id,status})));}}
 c.append(controls,messages);$("ticket-list").append(c);}
}
async function refresh(){
 if(!csrf)return; const generation=epoch;
 const [products,orders,actions,tickets,policy]=await Promise.all([api("/catalog"),api("/orders"),api("/actions"),api("/tickets"),api("/policies")]);
 if(generation!==epoch)return;
 renderProducts(products);renderOrders(orders);renderActions(actions);renderTickets(tickets);$("policy-list").replaceChildren(...policy.rules.map(value=>element("p",policyLabel(value))));
 $("new-ticket").hidden=operator;syncBusinessControls();
 if(unknown){notice("写入结果未知。请核对请求，不要重复提交。请求 ID："+unknown.client_request_id,true);const b=element("button","核对未知请求");b.addEventListener("click",async()=>{try{const result=await api("/actions/requests/"+unknown.client_request_id);notice("已核对数据库回执："+JSON.stringify(result.result));unknown=null;await refresh();}catch(error){notice(error.message,true);}});$("notice").append(b);}
}
$("switch").addEventListener("click",async()=>{if(busy||$("switch").disabled)return;epoch++;csrf="";unknown=null;for(const id of ["product-list","order-list","action-list","ticket-list","policy-list"])$(id).replaceChildren();$("ticket-order").value="";$("ticket-message").value="";try{const response=await api("/demo/session",{persona:$("persona").value});csrf=response.csrf_token;persona=response.persona;operator=response.operator;publishSession(response);$("identity").textContent="当前账户："+accountLabel(persona)+"";notice("账户已切换，当前权限范围的数据已更新。");await refresh();}catch(error){notice(error.message,true);}});
$("refresh").addEventListener("click",async()=>{try{await refresh();if(!unknown)notice("数据已刷新");}catch(error){notice(error.message,true);}});
$("new-ticket").addEventListener("submit",event=>{event.preventDefault();act(()=>draft({kind:"create_ticket",order_id:$("ticket-order").value||null,message:$("ticket-message").value}));});
(async()=>{try{const row=await api("/demo/session");csrf=row.csrf_token;persona=row.persona;operator=row.operator;publishSession(row);$("persona").value=persona;$("identity").textContent="当前账户："+accountLabel(persona);await refresh();}catch{notice("请选择账户开始操作。");}})();


let modelBusy = false,statusLoading=false;
function syncModelControls(){
 for(const id of ["knowledge-search","knowledge-ask","intent-classify"])$(id).disabled=!csrf||modelBusy||statusLoading;
 $("model-status-refresh").disabled=!csrf||statusLoading||modelBusy;
 $("chatgpt-model-select").disabled=!csrf||!operator||modelBusy||statusLoading||!$("chatgpt-model-picker").value;
 $("chatgpt-model-picker").disabled=!csrf||!operator||modelBusy||statusLoading||!$("chatgpt-model-picker").value;
 $("model-channel").disabled=!csrf||modelBusy;
 $("knowledge-import").disabled=!csrf||!operator||busy||modelBusy||statusLoading;
 $("model-secret-setup").hidden=!csrf||!operator;
}
async function modelStatus() {
  if(statusLoading)return;statusLoading=true;syncModelControls();document.dispatchEvent(new CustomEvent("yice-agent:busy",{detail:{owner:"model-status",busy:true}}));
  try{
  $("model-status-message").className="";
  $("model-status-message").textContent="正在读取模型与索引状态……";
  const data = await api("/models");
  const knowledge = await api("/knowledge");
  $("model-status").textContent = JSON.stringify({models:data, knowledge},null,2);
  renderDocuments(knowledge);
  const chatgpt=data.channels.find(c=>c.channel==="chatgpt");
  $("model-status-message").textContent="状态已更新。ChatGPT 账号授权："+(chatgpt?.catalog?.logged_in?"已登录":"未完成");
  if(!modelBusy){
    $("chatgpt-selection-status").textContent=chatgpt?.catalog?.logged_in
      ? (chatgpt.model_id?"当前已选模型："+chatgpt.model_id:"账号授权已完成，请选择并验证模型。")
      : errors.oauth_login_required;
    $("chatgpt-selection-status").className=chatgpt?.catalog?.logged_in?"":"error";
  }
  const selected=$("chatgpt-model-picker").value||chatgpt?.model_id;
  const catalog=chatgpt?.catalog?.models || [];
  $("chatgpt-model-picker").replaceChildren(...catalog.map(m=>{
    const option=element("option",m.id+(m.verified?" · 账号目录":" · 候选待验证"));
    option.value=m.id;return option;
  }));
  if(!catalog.length){const empty=element("option","暂无可用候选，请检查项目登录");empty.value="";$("chatgpt-model-picker").append(empty);}
  if(catalog.some(m=>m.id===selected))$("chatgpt-model-picker").value=selected;
  }finally{statusLoading=false;syncModelControls();document.dispatchEvent(new CustomEvent("yice-agent:busy",{detail:{owner:"model-status",busy:false}}));}
}
$("model-status-refresh").addEventListener("click",()=>modelStatus().catch(e=>{
  $("model-status-message").textContent=e.message;$("model-status-message").className="error";
}));
async function modelQuery(path) {
  if(modelBusy||statusLoading)return;
  if(!csrf){$("model-output").textContent="请先选择账户。";return;}
  if(!$("model-query").value.trim()){$("model-output").textContent="请先输入问题。";$("model-query").focus();notifyFeedback("请先输入问题。",true);return;}
  modelBusy=true;syncModelControls();document.dispatchEvent(new CustomEvent("yice-agent:busy",{detail:{owner:"model",busy:true}}));
  const currentEpoch=epoch;
  $("model-output").textContent="运行中：仅已校验的最终引用会发布，请等待。";
  try {
    const result=await api(path,{query:$("model-query").value,channel:$("model-channel").value});
    if(currentEpoch===epoch)$("model-output").textContent=JSON.stringify(result,null,2);
  } catch(e) {
    if(currentEpoch===epoch)$("model-output").textContent=e.message;
  } finally { modelBusy=false;syncModelControls();document.dispatchEvent(new CustomEvent("yice-agent:busy",{detail:{owner:"model",busy:false}})); }
}
$("knowledge-search").addEventListener("click",()=>modelQuery("/knowledge/search"));
$("knowledge-ask").addEventListener("click",()=>modelQuery("/knowledge/ask"));
$("intent-classify").addEventListener("click",()=>modelQuery("/models/classify"));
$("deepseek-setup").addEventListener("submit",async event=>{
  event.preventDefault();
  const secret=$("deepseek-key").value;
  $("deepseek-key").value="";
  try {
    const result=await api("/models/deepseek/configure",{api_key:secret,enable_paid:true});
    $("model-output").textContent="连接配置已保存。费用预算："+result.billing_cap_cny+"元。";
    await modelStatus();
  } catch(e){notice(e.message,true);}
});
$("switch").addEventListener("click",()=>{
  $("model-output").textContent=""; $("model-status").textContent="";
  $("model-query").value=""; $("deepseek-key").value="";
  $("chatgpt-selection-status").textContent=""; $("model-status-message").textContent="";
});


$("chatgpt-model-select").addEventListener("click",async ()=>{
  if(modelBusy)return;
  const chosen=$("chatgpt-model-picker").value;
  const status=$("chatgpt-selection-status"),control=$("chatgpt-model-select");
  if(!operator){status.textContent="请在顶部选择运营账户后验证模型。";status.className="error";return;}
  if(!chosen){status.textContent="请先读取模型状态，再选择一个模型候选。";status.className="error";return;}
  modelBusy=true;syncModelControls();document.dispatchEvent(new CustomEvent("yice-agent:busy",{detail:{owner:"model",busy:true}}));
  const selectionEpoch=epoch,originalLabel=control.textContent;
  control.disabled=true;control.textContent="正在验证……";control.setAttribute("aria-busy","true");
  status.className="";status.textContent="正在验证 "+chosen+"，最多等待60秒；验证成功后才会保存模型。";
  $("model-output").textContent=status.textContent;
  try {
    const result=await api("/models/chatgpt/select",{model_id:chosen},{timeoutMs:60000});
    if(selectionEpoch!==epoch)return;
    status.textContent="验证成功，已保存模型："+result.configured_id+"。实际返回模型："+result.model_id;
    $("model-output").textContent=JSON.stringify(result,null,2);
    try{await modelStatus();}catch(e){$("model-status-message").textContent="模型已验证，但状态刷新失败："+e.message;}
  } catch(e){
    if(selectionEpoch===epoch){
      status.textContent=e.message;status.className="error";$("model-output").textContent=e.message;
      if(e.code==="oauth_login_required")$("chatgpt-login-help").open=true;
    }
  } finally{
    modelBusy=false;syncModelControls();document.dispatchEvent(new CustomEvent("yice-agent:busy",{detail:{owner:"model",busy:false}}));control.disabled=false;control.textContent=originalLabel;control.removeAttribute("aria-busy");syncModelControls();
  }
});

function renderDocuments(data){
 $("knowledge-documents").replaceChildren();
 for(const doc of data.documents){
  const c=card(doc.title,["业务文档","版本 "+doc.version.replace(/^demo-business\//,"")]);
  const d=element("details");d.append(element("summary","来源与版本记录"),element("pre",JSON.stringify({id:doc.id,version:doc.version,source:doc.source,license:doc.license,sha256:doc.sha256,index_id:data.index_id},null,2)));c.append(d);$("knowledge-documents").append(c);
 }
}
$("knowledge-import").addEventListener("click",()=>act(async()=>{
 $("knowledge-import-status").textContent="正在同步业务知识与检索索引；相同版本会复用。";
 const r=await api("/knowledge/import",{corpus:"demo-cn"});
 $("knowledge-import-status").textContent="业务知识同步完成。"+(r.status==="no_op"?"当前版本已就绪。":"新版本已就绪。");
 await modelStatus();
},$("knowledge-import")));
whenSession(event=>{
 $("knowledge-documents").replaceChildren();
 $("chatgpt-model-picker").replaceChildren();
 $("chatgpt-selection-status").textContent="";$("model-status-message").textContent="";
 $("knowledge-import-status").textContent="";
 $("model-status").textContent="";
 $("model-output").textContent="";
 $("knowledge-import").disabled=!event.detail.operator;
 $("model-secret-setup").hidden=!event.detail.operator;
 $("chatgpt-model-select").disabled=!event.detail.operator;syncModelControls();
});

syncBusinessControls();syncModelControls();
