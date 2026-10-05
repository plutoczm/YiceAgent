import test from "node:test";
import assert from "node:assert/strict";
import { loginFailure } from "../../bridge/oauth-errors.mjs";
const privateValue="SYNTHETIC_PRIVATE_CODE_AND_TOKEN";
const safe=value=>assert.ok(!JSON.stringify(value).includes(privateValue));
test("HTTP rejection reports status without provider body",()=>{
  const result=loginFailure(new Error("OpenAI Codex token exchange failed (403): "+privateValue));
  assert.equal(result.category,"oauth_token_exchange_rejected");
  assert.equal(result.http_status,403);safe(result);
});
test("invalid token response never echoes credential fields",()=>{
  const result=loginFailure(new Error('OpenAI Codex token exchange response missing fields: {"access_token":"'+privateValue+'"}'));
  assert.equal(result.category,"oauth_token_response_invalid");safe(result);
});
test("network diagnostics whitelist only fixed codes",()=>{
  const error=new Error("fetch failed",{cause:{code:"ECONNRESET",detail:privateValue}});
  assert.equal(loginFailure(error).network_code,"ECONNRESET");safe(loginFailure(error));
  assert.equal(loginFailure(new Error("fetch failed",{cause:{code:privateValue}})).network_code,"UNKNOWN");
});
test("unknown provider error is private",()=>{
  const result=loginFailure(new Error(privateValue));
  assert.equal(result.category,"oauth_login_failed");safe(result);
});
test("known input and storage failures are actionable",()=>{
  assert.equal(loginFailure(new Error("oauth_input_closed")).category,"oauth_input_closed");
  assert.equal(loginFailure(new Error("Credential store modify failed for provider "+privateValue)).category,"oauth_storage_failed");
  assert.equal(loginFailure(new Error("Failed to extract accountId from token")).category,"oauth_account_missing");
});
