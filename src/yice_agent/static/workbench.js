const busy = new Map();
const views={
 chat:{title:"智能助手",eyebrow:"CONVERSATION",description:"围绕业务问题协作，随时查看回答依据和运行过程。"},
 business:{title:"订单管理",eyebrow:"BUSINESS OPERATIONS",description:"管理订单、操作申请和客户工单，跟踪每一次业务状态变化。"},
 context:{title:"上下文管理",eyebrow:"CONTEXT ENGINEERING",description:"查看模型看到了什么，以及哪些信息被保留、摘要或省略。"},
 memory:{title:"知识与记忆",eyebrow:"MEMORY & KNOWLEDGE",description:"管理已确认的偏好与稳定信息，追溯知识库的每一个来源。"},
 runs:{title:"运行中心",eyebrow:"RUN EXPLORER",description:"回看调用过程、工具校验与用量，找到结果背后的依据。"}
};
function view(name,replace=true){
 if(!["chat","business","context","memory","runs"].includes(name))name="chat";
 document.querySelectorAll("[data-panel]").forEach(n=>{n.hidden=n.dataset.panel!==name;});
 document.querySelectorAll("[data-view]").forEach(n=>{n.setAttribute("aria-pressed",String(n.dataset.view===name));});
 const item=views[name];
 document.getElementById("view-title").textContent=item.title;
 document.getElementById("breadcrumb-view").textContent=item.title;
 document.getElementById("view-description").textContent=item.description;
 document.getElementById("view-eyebrow").textContent=item.eyebrow;
 document.title=item.title+" · 忆策 Agent";
 if(location.hash!=="#"+name)history[replace?"replaceState":"pushState"](null,"","#"+name);
}
document.querySelectorAll("[data-view]").forEach(n=>n.addEventListener("click",()=>view(n.dataset.view,false)));
document.addEventListener("yice-agent:busy",event=>{
 busy.set(event.detail.owner,event.detail.busy);
 document.getElementById("switch").disabled=[...busy.values()].some(Boolean);
});
document.addEventListener("yice-agent:view",event=>view(event.detail));
document.addEventListener("yice-agent:session",()=>view(location.hash.slice(1)));
view(location.hash.slice(1));

let sessionState=null;
export function publishSession(data){
 sessionState=data;
 const sample={"customer-a1":"ECO-A-01","customer-a2":"ECO-A-02","customer-b1":"ECO-B-01","customer-b2":"ECO-B-02","operator-a":"ECO-A-01","operator-b":"ECO-B-01"}[data.persona];
 const prompt=document.querySelector("[data-order-prompt]");
 if(prompt&&sample)prompt.dataset.prompt="查询订单 "+sample+" 的实际状态。";
 document.dispatchEvent(new CustomEvent("yice-agent:session",{detail:data}));
}
export function whenSession(handler){
 document.addEventListener("yice-agent:session",handler);
 if(sessionState)handler({detail:sessionState});
}

document.querySelectorAll("[data-prompt]").forEach(button=>button.addEventListener("click",()=>{
 const input=document.getElementById("agent-text");
 input.value=button.dataset.prompt;input.focus();
}));

window.addEventListener("popstate",()=>view(location.hash.slice(1)));
let feedbackTimer;
export function notifyFeedback(text,error=false) {
 const toast=document.getElementById("feedback-toast");
 toast.textContent=String(text).slice(0,240);
 toast.className=error?"feedback-toast error":"feedback-toast";
 toast.hidden=!text;
 clearTimeout(feedbackTimer);
 feedbackTimer=setTimeout(()=>{toast.hidden=true;},error?9000:5000);
}

// Display aliases only. Stored records and source quotations remain unchanged.
export const accountLabel=value=>[...document.getElementById("persona").options].find(option=>option.value===value)?.textContent||value;
export const displayName=value=>({"演示会话":"业务会话","演示手机":"智能手机"}[value]||value);
export const policyLabel=value=>value==="支付、退款结果由运营模拟事件推进。"?"测试环境中的支付与退款状态由运营处理。":value;
