import test from "node:test";
import assert from "node:assert/strict";
import {conversationControls} from "../../src/workflowops/static/ui-controls.js";

test("before identity selection no conversation or execution control is actionable",()=>{
 const state=conversationControls();
 assert.ok(Object.values(state).every(Boolean));
});
test("empty account permits new conversation while unavailable reads and execution remain disabled",()=>{
 const state=conversationControls({ready:true});
 assert.equal(state["conversation-new"],false);
 assert.equal(state["agent-channel"],false);
 for(const key of ["conversation-open","conversation-config","agent-send","agent-compress","agent-cancel","run-open"])assert.equal(state[key],true,key);
});
test("loaded idle conversation supports turns and configuration while idle cancel stays disabled",()=>{
 const state=conversationControls({ready:true,hasConversation:true,hasConversations:true,hasRuns:true,hasContexts:true});
 for(const key of ["conversation-new","conversation-open","conversation-config","agent-send","agent-compress","run-open","context-picker"])assert.equal(state[key],false,key);
 assert.equal(state["agent-cancel"],true);
});
test("pending run keeps cancellation accessible but blocks mutation and duplicate conversation commands",()=>{
 const state=conversationControls({ready:true,hasConversation:true,hasConversations:true,running:true,pending:true});
 assert.equal(state["agent-cancel"],false);
 for(const key of ["conversation-new","conversation-open","conversation-config","agent-send","agent-compress","agent-channel","agent-window"])assert.equal(state[key],true,key);
 assert.equal(conversationControls({ready:true,running:true,pending:true,cancelling:true})["agent-cancel"],true);
});
test("loading any UI command prevents repeated conversation actions until completion",()=>{
 const state=conversationControls({ready:true,hasConversation:true,hasConversations:true,pending:true});
 assert.equal(state["conversation-new"],true);
 assert.equal(state["conversation-open"],true);
 assert.equal(state["agent-send"],true);
});
