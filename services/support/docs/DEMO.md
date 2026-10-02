# SupportOps AI Demo Scenario

## Scenario: Refund Request

### User

> I want to refund order SO-20260801.

### Agent workflow

```text
User request
    |
Identity verification
    |
Intent classification
    |
Order lookup
    |
Refund policy evaluation
    |
Risk assessment
    |
Create pending refund action
    |
Authenticated confirmation
    |
Execute mutation
    |
Write audit record
```

## Safety behavior

The Agent does not directly execute sensitive operations from natural language.

Before mutation:

- customer identity must be verified;
- refund policy must pass;
- high-risk requests require human review;
- confirmation is required;
- action execution uses an idempotency key.

## Knowledge QA scenario

### User

> How long does a refund normally take?

### Retrieval path

```text
Query
 |
BM25 retrieval
 |
Dense retrieval
 |
RRF fusion
 |
Reranking
 |
Answerability gate
 |
Citation-grounded response
```

## Human Handoff Conversation Scenario

### Customer

> I want to complain about the service attitude.

### SupportOps AI

The request is handed off to a ticket and the customer/AI context is stored with it. The customer
workspace automatically enters human-handoff mode for that conversation.

### Assigned agent

1. Open the local workspace at `http://localhost:8002/`.
2. Switch to **客服工单**, refresh the queue and assign the ticket to the current agent.
3. Select **打开会话 / 回复**, review the customer and AI context, then send a reply.
4. The ticket moves to `pending_customer` and the reply appears in the customer workspace.

### Customer follow-up

The customer replies from the same conversation. The message is stored on the ticket, becomes
visible to the assigned agent, and moves the ticket back to `assigned` for follow-up.

### Safety behavior

- Internal processing notes are not sent to the customer.
- Only the ticket customer can load or send customer-side messages.
- Only an assigned agent can reply; resolved and closed tickets reject messages.
- The user can reload the local workspace: the current conversation recovers its own handoff
  ticket without exposing another customer's ticket.

## Evaluation coverage

The E2E suite evaluates:

- workflow/tool selection;
- task completion;
- tool arguments;
- citation consistency;
- hallucinated actions;
- confirmation safety;
- cross-customer isolation.
