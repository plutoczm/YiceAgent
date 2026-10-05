// Manual OAuth input remains local; never include pasted values in errors or logs.
export function validateRedirect(input, expectedRedirect, expectedState) {
  if (typeof input !== "string" || input.length > 8192) return {ok:false, code:"invalid_url"};
  let url, expected;
  try {url=new URL(input.trim());expected=new URL(expectedRedirect);}
  catch {return {ok:false, code:"invalid_url"};}
  if(url.origin!==expected.origin || url.pathname!==expected.pathname || url.username || url.password || url.hash)
    return {ok:false, code:"wrong_page"};
  if(!expectedState || url.searchParams.getAll("state").length!==1 || url.searchParams.get("state")!==expectedState)
    return {ok:false, code:"wrong_state"};
  if(url.searchParams.has("error")) return {ok:false, code:"authorization_denied"};
  if(url.searchParams.getAll("code").length!==1 || !url.searchParams.get("code")?.trim())
    return {ok:false, code:"missing_code"};
  return {ok:true};
}

function question(terminal, signal) {
  return new Promise((resolve,reject)=>{
    if(signal.aborted){reject(new Error("oauth_input_cancelled"));return;}
    if(terminal.closed){reject(new Error("oauth_input_closed"));return;}
    const cleanup=()=>{signal.removeEventListener("abort",onAbort);terminal.off("close",onClose);};
    const onAbort=()=>{cleanup();reject(new Error("oauth_input_cancelled"));};
    const onClose=()=>{cleanup();reject(new Error("oauth_input_closed"));};
    signal.addEventListener("abort",onAbort,{once:true});
    terminal.once("close",onClose);
    try {
      terminal.question("完成浏览器授权后，将 http://localhost:1455/auth/callback?... 的完整地址粘贴到本窗口后回车： ",value=>{
        cleanup();resolve(value);
      });
    } catch {cleanup();reject(new Error("oauth_input_closed"));}
  });
}

export async function readRedirect({terminal,signal,expectedRedirect,expectedState,notify,maxAttempts=8}) {
  if(!expectedRedirect || !expectedState) throw new Error("oauth_login_context_missing");
  for(let attempt=0;attempt<maxAttempts;attempt++) {
    const value=await question(terminal,signal);
    const result=validateRedirect(value,expectedRedirect,expectedState);
    if(result.ok) return value.trim();
    if(result.code==="authorization_denied") throw new Error("oauth_authorization_denied");
    notify(result.code==="wrong_state"
      ? "这个地址属于旧的或其他登录流程。请使用本终端这次生成的授权链接，完成浏览器授权后再回填；当前流程仍在等待。\n"
      : "这还不是登录后的本机回调地址。请继续在浏览器选择账号并完成授权，等地址以 http://localhost:1455/auth/callback? 开头再回填；当前流程仍在等待。\n");
  }
  throw new Error("oauth_input_attempts_exhausted");
}
