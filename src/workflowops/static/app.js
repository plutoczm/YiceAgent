"use strict";
const errors={approval_required:"需要运营审批后再确认",cancellation_not_available:"当前订单状态不能取消，可创建异常工单",payment_not_available:"当前订单不可发起支付",payment_in_progress:"支付结果尚未确定，请先核对状态",refund_already_recorded:"已存在退款申请或模拟退款结果",unpaid_order_not_refundable:"未支付订单不能申请退款",action_version_conflict:"草案版本已变化，请刷新",order_version_conflict:"订单版本已变化，请刷新",payment_event_not_available:"此订单没有待处理的支付请求",delivery_event_not_available:"此订单不能继续推进配送",refund_event_not_available:"此订单没有待处理的退款请求",operator_required:"此操作需要运营身份",customer_required:"请切换到客户身份",object_not_found:"数据不存在或当前身份无权查看",csrf_required:"会话已变化，请重新选择身份",ticket_closed:"工单已关闭",illegal_ticket_transition:"当前工单不能转到该状态",invalid_request:"请检查输入字段和数量",request_id_conflict:"请求编号已用于其他操作",business_write_failed:"操作未完成，请刷新核对"};
let csrf = "", persona = "", operator = false, epoch = 0, busy = false, unknown = null;
const labels = {ecommerce:"电商",food:"外卖",awaiting_payment:"待支付",paid:"已支付",ready_to_ship:"待发货",shipped:"已发货",accepted:"已接单",preparing:"制作中",delivering:"配送中",delivered:"已送达",cancelled:"已取消",unpaid:"未支付",pending:"待模拟支付结果",failed:"失败",none:"无退款",requested:"退款已申请",refunded:"模拟退款完成",pending_confirmation:"等待确认",pending_approval:"等待审批",submitted:"业务已提交",expired:"已过期",rejected:"已拒绝",invalidated:"已失效",open:"待处理",assigned:"已分派",pending_customer:"等待客户",resolved:"已解决",closed:"已关闭",place_order:"下单",pay:"支付",cancel_order:"取消订单",refund:"整单退款",create_ticket:"创建工单",reply_ticket:"回复工单",assign_ticket:"分派工单",transition_ticket:"更新工单"};
const $ = id => document.getElementById(id);
function element(tag, text, cls) {const node=document.createElement(tag); if(text!==undefined)node.textContent=text; if(cls)node.className=cls; return node;}
function notice(text, error=false) {$("notice").textContent=text;$("notice").className=error?"error":"";}
function button(text, handler) {const b=element("button",text);b.addEventListener("click",()=>act(handler));return b;}
async function api(path, body) {
  const generation=epoch, headers={};
  if(body!==undefined){headers["Content-Type"]="application/json";headers["X-WorkflowOps-CSRF"]=csrf;}
  const response=await fetch("/api/v2"+path,{method:body===undefined?"GET":"POST",headers,body:body===undefined?undefined:JSON.stringify(body)});
  const data=await response.json();
  if(generation!==epoch)throw new Error("身份已切换，旧结果已忽略");
  if(!response.ok){if(data.status==="unknown_outcome"){unknown=data.domain_refs;notice("写入结果未知。请核对请求，不要重复提交。请求 ID："+unknown.client_request_id,true);}throw new Error(errors[data.error]||errors[data.detail]||"请求失败，请刷新后核对");}
  return data;
}
function request(body){return {...body,client_request_id:crypto.randomUUID()};}
async function act(callback){
  if(busy)return; if(unknown){notice("有结果未知的写入，请先核对请求再继续操作。",true);return;} busy=true;$("switch").disabled=true;
  try{await callback();await refresh();}catch(error){if(!unknown)notice(error.message,true);else{try{await refresh();}catch{}}}finally{busy=false;$("switch").disabled=false;}
}
async function draft(body){
  const row=await api("/actions",request(body));
  notice("草案："+row.action_id+" / "+(labels[row.status]||row.status)+" / 报价："+(row.quoted_amount===null?"—":row.quoted_amount+" 元"));
}
function card(title, lines) {const node=element("article",undefined,"card");node.append(element("h3",title));for(const line of lines)node.append(element("p",line));return node;}
function renderProducts(rows){
 $("product-list").replaceChildren();
 for(const row of rows){const c=card(row.name,[(labels[row.order_type]||row.order_type)+" · "+row.price+" 元",row.product_id]);const qty=element("input");qty.type="number";qty.min="1";qty.max="20";qty.value="1";qty.setAttribute("aria-label","商品数量");
 const controls=element("div",undefined,"controls");controls.append(qty);if(!operator)controls.append(button("生成下单草案",()=>draft({kind:"place_order",order_type:row.order_type,items:[{product_id:row.product_id,quantity:Number(qty.value)}],note:""})));c.append(controls);$("product-list").append(c);}
}
function renderOrders(rows){
 $("order-list").replaceChildren();
 for(const row of rows){const c=card(row.order_id,[(labels[row.order_type]||row.order_type)+" / "+(labels[row.status]||row.status),"金额 "+row.amount+" 元 · 支付 "+(labels[row.payment_status]||row.payment_status),"退款 "+(labels[row.refund_status]||row.refund_status)+" · 实际模拟已退 "+row.refunded_amount+" 元","客户 "+row.customer_id+" · 版本 "+row.version]);const controls=element("div",undefined,"controls");
 if(operator){for(const [event,title] of [["payment_succeeded","模拟支付成功"],["payment_failed","模拟支付失败"],["advance","推进配送"],["refund_succeeded","模拟退款成功"],["refund_failed","模拟退款失败"]])controls.append(button(title,()=>api("/simulator/events",request({order_id:row.order_id,expected_version:row.version,event}))));}
 else{for(const [kind,title] of [["pay","支付草案"],["cancel_order","取消草案"],["refund","退款草案"]])controls.append(button(title,()=>draft({kind,order_id:row.order_id})));controls.append(button("异常工单",async()=>{$("ticket-order").value=row.order_id;$("ticket-message").focus();}));}
 c.append(controls);$("order-list").append(c);}
}
function renderActions(rows){
 $("action-list").replaceChildren();
 for(const row of rows){const c=card((labels[row.kind]||row.kind)+" · "+(labels[row.status]||row.status),[row.action_id,"报价 "+(row.quoted_amount===null?"—":row.quoted_amount+" 元")+" · 版本 "+row.version,"有效期 "+new Date(row.expires_at).toLocaleString(),"已审批 "+(row.human_approved?"是":"否")+" · 已确认 "+(row.confirmed?"是":"否")]);c.append(element("p","动作内容"),element("pre",JSON.stringify(row.payload,null,2)),element("p","业务结果"),element("pre",JSON.stringify(row.result,null,2)));const controls=element("div",undefined,"controls");
 if(operator && row.status==="pending_approval"){for(const [decision,title] of [["approve","批准"],["reject","拒绝"]])controls.append(button(title,()=>api("/actions/"+row.action_id+"/review",request({decision,expected_version:row.version}))));}
 if(row.status==="pending_confirmation" || row.status==="pending_approval"){if(row.actor_id===persona){if(row.status==="pending_confirmation")controls.append(button("确认执行",()=>api("/actions/"+row.action_id+"/confirm",request({confirm:true,expected_version:row.version}))));controls.append(button("取消草案",()=>api("/actions/"+row.action_id+"/confirm",request({confirm:false,expected_version:row.version}))));}}
 c.append(controls);$("action-list").append(c);}
}
function renderTickets(rows){
 $("ticket-list").replaceChildren();
 for(const row of rows){const c=card(row.ticket_id,[(labels[row.status]||row.status)+" · 版本 "+row.version,"订单 "+(row.order_id||"无")+" · 分派 "+(row.assignee_id||"未分派")]);const messages=element("div"),text=element("textarea");text.maxLength=2000;text.setAttribute("aria-label","工单回复");const controls=element("div",undefined,"controls");
 const view=element("button","查看对话");view.addEventListener("click",async()=>{try{const values=await api("/tickets/"+row.ticket_id+"/messages");messages.replaceChildren();for(const m of values)messages.append(element("p",(m.sender_role==="operator"?"运营":"客户")+"："+m.message,"message"));}catch(error){notice(error.message,true);}});controls.append(view);
 if(row.status!=="closed"){c.append(text);controls.append(button("回复草案",()=>draft({kind:"reply_ticket",ticket_id:row.ticket_id,message:text.value})));if(operator){controls.append(button("分派给自己",()=>draft({kind:"assign_ticket",ticket_id:row.ticket_id,assignee_id:persona})));for(const status of ["pending_customer","resolved","closed","assigned"])controls.append(button(labels[status]+"草案",()=>draft({kind:"transition_ticket",ticket_id:row.ticket_id,status})));}}
 c.append(controls,messages);$("ticket-list").append(c);}
}
async function refresh(){
 if(!csrf)return; const generation=epoch;
 const [products,orders,actions,tickets,policy]=await Promise.all([api("/catalog"),api("/orders"),api("/actions"),api("/tickets"),api("/policies")]);
 if(generation!==epoch)return;
 renderProducts(products);renderOrders(orders);renderActions(actions);renderTickets(tickets);$("policy-list").replaceChildren(...policy.rules.map(value=>element("p",value)));
 $("new-ticket").hidden=operator;
 if(unknown){notice("写入结果未知。请核对请求，不要重复提交。请求 ID："+unknown.client_request_id,true);const b=element("button","核对未知请求");b.addEventListener("click",async()=>{try{const result=await api("/actions/requests/"+unknown.client_request_id);notice("已核对数据库回执："+JSON.stringify(result.result));unknown=null;await refresh();}catch(error){notice(error.message,true);}});$("notice").append(b);}
}
$("switch").addEventListener("click",async()=>{if(busy)return;epoch++;csrf="";unknown=null;for(const id of ["product-list","order-list","action-list","ticket-list","policy-list"])$(id).replaceChildren();$("ticket-order").value="";$("ticket-message").value="";try{const response=await api("/demo/session",{persona:$("persona").value});csrf=response.csrf_token;persona=response.persona;operator=response.operator;$("identity").textContent="当前身份："+persona+" · 本地演示";notice("身份已选择，页面显示当前权限下的业务数据。");await refresh();}catch(error){notice(error.message,true);}});
$("refresh").addEventListener("click",async()=>{try{await refresh();if(!unknown)notice("数据已刷新");}catch(error){notice(error.message,true);}});
$("new-ticket").addEventListener("submit",event=>{event.preventDefault();act(()=>draft({kind:"create_ticket",order_id:$("ticket-order").value||null,message:$("ticket-message").value}));});
(async()=>{try{const row=await api("/demo/session");csrf=row.csrf_token;persona=row.persona;operator=row.operator;$("persona").value=persona;$("identity").textContent="当前身份："+persona;await refresh();}catch{notice("请选择演示身份开始操作。");}})();
