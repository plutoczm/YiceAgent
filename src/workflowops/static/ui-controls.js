// Presentation guards only; server authorization and business validation remain authoritative.
export function conversationControls({ready=false,hasConversation=false,hasConversations=false,hasRuns=false,hasContexts=false,running=false,pending=false,cancelling=false}={}) {
 const blocked=!ready||running||pending;
 return {
  "conversation-new":blocked,
  "conversation-open":blocked||!hasConversations,
  "conversation-picker":blocked||!hasConversations,
  "conversation-config":blocked||!hasConversation,
  "agent-send":blocked||!hasConversation,
  "agent-compress":blocked||!hasConversation,
  "agent-channel":blocked,
  "agent-window":blocked,
  "agent-cancel":!ready||!running||cancelling,
  "run-open":!ready||pending||!hasRuns,
  "run-picker":!ready||pending||!hasRuns,
  "context-picker":!ready||!hasContexts
 };
}
