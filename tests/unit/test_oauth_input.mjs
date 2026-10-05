import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { validateRedirect, readRedirect } from "../../bridge/oauth-input.mjs";

const redirect="http://localhost:1455/auth/callback";
const state="FAKE_STATE_FOR_OFFLINE_UNIT_ONLY";
const valid=redirect+"?code=FAKE_CODE_FOR_OFFLINE_UNIT_ONLY&state="+state;
const classify=value=>validateRedirect(value,redirect,state);

class Terminal extends EventEmitter {
  constructor(inputs=[]){super();this.inputs=[...inputs];this.questions=0;this.closed=false;}
  question(prompt,callback){
    this.questions++;
    const value=this.inputs.shift();
    if(value!==undefined)queueMicrotask(()=>callback(value));
  }
}
const options=(terminal,signal,notices=[])=>({
  terminal,signal,expectedRedirect:redirect,expectedState:state,notify:text=>notices.push(text)
});

test("only the complete current callback URL is accepted",()=>{
  assert.deepEqual(classify("  "+valid+"  "),{ok:true});
  for(const value of [
    "https://auth.openai.com/choose-an-account",
    "https://auth.openai.com/oauth/authorize?state="+state,
    "https://example.invalid/auth/callback?code=FAKE&state="+state,
    redirect+"?code=FAKE&state="+state+"#fragment",
    "http://user@localhost:1455/auth/callback?code=FAKE&state="+state,
  ])assert.equal(classify(value).ok,false);
});
test("missing code, duplicate parameters and stale state fail closed",()=>{
  assert.deepEqual(classify(redirect+"?state="+state),{ok:false,code:"missing_code"});
  assert.deepEqual(classify(redirect+"?code=A&code=B&state="+state),{ok:false,code:"missing_code"});
  assert.deepEqual(classify(redirect+"?code=FAKE&state=OLD_STATE"),{ok:false,code:"wrong_state"});
  assert.deepEqual(classify(valid+"&state="+state),{ok:false,code:"wrong_state"});
});
test("pasting the account page does not end the flow or echo input",async()=>{
  const wrong="https://auth.openai.com/choose-an-account";
  const terminal=new Terminal([wrong,valid]),notices=[];
  const result=await readRedirect(options(terminal,new AbortController().signal,notices));
  assert.equal(result,valid);
  assert.equal(terminal.questions,2);
  assert.equal(notices.length,1);
  assert.ok(notices[0].includes("当前流程仍在等待"));
  assert.ok(!notices[0].includes(wrong));
  assert.equal(terminal.listenerCount("close"),0);
});
test("a stale callback can be corrected within the same flow",async()=>{
  const terminal=new Terminal([redirect+"?code=OLD_CODE&state=OLD_STATE",valid]),notices=[];
  assert.equal(await readRedirect(options(terminal,new AbortController().signal,notices)),valid);
  assert.equal(terminal.questions,2);
  assert.ok(notices[0].includes("旧的或其他"));
  assert.ok(!notices.join("").includes("OLD_CODE"));
});
test("overall cancellation ends manual waiting and removes listeners",async()=>{
  const terminal=new Terminal(),controller=new AbortController();
  const wait=readRedirect(options(terminal,controller.signal));
  controller.abort();
  await assert.rejects(wait,{message:"oauth_input_cancelled"});
  assert.equal(terminal.listenerCount("close"),0);
});
test("terminal EOF ends waiting cleanly",async()=>{
  const terminal=new Terminal();
  const wait=readRedirect(options(terminal,new AbortController().signal));
  terminal.closed=true;terminal.emit("close");
  await assert.rejects(wait,{message:"oauth_input_closed"});
  assert.equal(terminal.listenerCount("close"),0);
});
test("manual retries are bounded",async()=>{
  const terminal=new Terminal(["wrong","wrong"]),notices=[];
  await assert.rejects(readRedirect({...options(terminal,new AbortController().signal,notices),maxAttempts:2}),
    {message:"oauth_input_attempts_exhausted"});
  assert.equal(terminal.questions,2);
});
test("provider authorization denial ends the flow",async()=>{
  const terminal=new Terminal([redirect+"?error=access_denied&state="+state]);
  await assert.rejects(readRedirect(options(terminal,new AbortController().signal)),
    {message:"oauth_authorization_denied"});
});
