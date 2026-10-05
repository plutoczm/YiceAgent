// Fixed categories only. Provider bodies, redirect URLs and credential values never leave here.
export function loginFailure(error) {
  const known={
    oauth_input_cancelled:"等待已结束或取消，请重新启动登录并使用新链接。",
    oauth_input_closed:"登录输入终端已关闭，请重新启动登录。",
    oauth_input_attempts_exhausted:"回填错误次数过多，请重新启动登录。",
    oauth_authorization_denied:"浏览器授权未通过，本次登录已结束。",
    oauth_storage_locked:"项目凭据存储正被使用，请稍后重试。",
  };
  const message=typeof error?.message==="string"?error.message:"";
  if(Object.hasOwn(known,message))return {category:message,message:known[message]};
  const rejected=/^OpenAI Codex token (?:exchange|refresh) failed \(([1-5]\d{2})\):/.exec(message);
  if(rejected)return {category:"oauth_token_exchange_rejected",http_status:Number(rejected[1]),message:"授权服务拒绝了信息交换，请根据诊断状态核对网络或重新授权。"};
  if(message==="Failed to extract accountId from token")
    return {category:"oauth_account_missing",message:"授权响应没有可用的ChatGPT账号标识，未保存登录。"};
  if(message.startsWith("OpenAI Codex token exchange response missing fields:"))
    return {category:"oauth_token_response_invalid",message:"授权服务响应格式不完整，未保存登录。"};
  const code=error?.cause?.code;
  const networkCodes=new Set(["ETIMEDOUT","ECONNRESET","ECONNREFUSED","ENOTFOUND","UND_ERR_CONNECT_TIMEOUT","ERR_TLS_CERT_ALTNAME_INVALID","UNABLE_TO_VERIFY_LEAF_SIGNATURE","DEPTH_ZERO_SELF_SIGNED_CERT","CERT_HAS_EXPIRED"]);
  if(message==="fetch failed" || networkCodes.has(code))
    return {category:"oauth_network_failed",network_code:networkCodes.has(code)?code:"UNKNOWN",message:"连接授权服务失败，请核对本机网络和代理。"};
  if(message.startsWith("Credential store modify failed"))
    return {category:"oauth_storage_failed",message:"授权完成但项目凭据保存失败，请检查项目目录权限。"};
  return {category:"oauth_login_failed",message:"项目授权未完成，请查看本机诊断分类后重试。"};
}
