# Agents

## Component Solution Design

---

# Purpose

Agents are the active layer of VivyAtlas: everything that happens between a trigger and memory.

A trigger is a user message, a schedule, or a completed sync. An agent is what turns that trigger into work — retrieving knowledge, updating beliefs about the user, performing research — using the memory layer as its only shared state.

```
   Triggers
   ┌──────────────┬──────────────┬──────────────┐
   │ user request │   schedule   │  sync event  │
   └──────┬───────┴──────┬───────┴──────┬───────┘
          │              │              │
          ▼              ▼              ▼
   ┌─────────────────────────────────────────────┐
   │            Orchestrator (Go)                │
   │      intent → explicit, named flow          │
   └──────┬──────────────┬───────────────┬───────┘
          │              │               │
          ▼              ▼               ▼
     Retrieval       User Model      Research 
       Agent           Agent           Agent
          │              │               │
          └──────────────┼───────────────┘
                         ▼
              Memory Layer (store interfaces)
```

This document describes the agent framework — the interface, orchestration, and LLM abstraction — and the detailed designs of the two  main agents: the **Retrieval Agent** and the **User Model Agent**.

---

# Design Principles

## Agents Share Memory, Not State

All coordination between agents happens through the memory layer. There are no agent-to-agent channels, no shared in-process state, no private caches of knowledge.

This is the property that makes agents composable: what one agent learns, every agent can use — and an agent can be rewritten, replaced, or extracted without any other agent noticing.

---

## Logically Independent, Physically Co-located

Agents are independent components behind a common interface.
The boundaries are service-shaped on purpose: an agent's only inputs are its request and the store interfaces. 

---

## Deterministic Orchestration First

Flows are code, not LLM improvisation.

The orchestrator classifies what a trigger needs and runs a **named, code-defined flow**. Which agents run, in what order, is deterministic and debuggable. Dynamic, LLM-planned orchestration arrives with the Planner Agent in a later phase of this project.

---

## Every Run Is Recorded

Every agent execution produces an **agent run** in episodic memory (the `agent_runs` table in [databases.md](databases.md)): its trigger, inputs, outputs, and the memories it read and wrote.

This provides observability — system behavior is inspectable after the fact — and evidence: the User Model Agent cites runs and conversations when proposing beliefs.

---

## Extensible by Design

Adding an agent must not require changes to existing agents, the orchestrator core, or the memory layer. Agents register in an **agent registry** under a unique type name — the same pattern connectors use.

---

# Agent Framework

## Agent Interface

Illustrative contract (not final code):

```go
type Agent interface {
    Name() string
    Handle(ctx context.Context, req AgentRequest) (AgentResponse, error)
}

type AgentRequest struct {
    Trigger         TriggerType       // user_request | schedule | sync
    ConversationID  *string           // set when conversational
    Input           string            // the task or question
    UserContext     []UserMemory      // assembled slice of active user memory
    MaxSensitivity  SensitivityLevel  // ceiling for everything this run may read
}

type AgentResponse struct {
    Output    string
    Citations []Citation        // mandatory for anything shown to the user
    Writes    []MemoryWrite     // what the agent wants persisted
}
```

Key properties:

* The request carries the **user context** already assembled — agents don't each re-derive who the user is
* The request carries a **sensitivity ceiling**, propagated into every `MemoryQuery` the agent makes; an agent cannot read above its request's ceiling
* Memory writes are returned in the response and applied by the framework — which is what makes recording `memories_written` reliable rather than voluntary

## Run Lifecycle

```
Trigger
   │
   ▼
Orchestrator selects flow, assembles AgentRequest
   │
   ▼
Agent run record opened (episodic memory)
   │
   ▼
Agent.Handle() executes
   ├── LLM calls via provider abstraction
   ├── memory reads via store interfaces   (tracked)
   └── returns output + writes
   │
   ▼
Framework applies writes                   (tracked)
   │
   ▼
Run record closed: input, output, memories_read, memories_written
```

The framework, not the agent, owns the run record. An agent cannot forget to be observable.

---

# Orchestration

## Intent → Flow

When a trigger arrives, the orchestrator selects a flow:

* **User requests** — a lightweight intent classification (a cheap LLM call or heuristics; open question below) maps the message to a flow
* **Schedules and sync events** — mapped directly by configuration, no classification needed

## Flows Are Explicit

A flow is a named, code-defined composition of agents. The initial set:

| Flow | Trigger | Composition | Mode |
|---|---|---|---|
| `answer_question` | user request | assemble user context → Retrieval Agent → respond with citations | synchronous |
| `after_turn` | end of a conversation turn | User Model Agent pass over the new episode | asynchronous |
| `post_sync` | connector sync completed | User Model Agent reviews newly ingested documents for proposal candidates | asynchronous |
| `maintenance` | schedule | User Model Agent staleness sweep; graph curation | asynchronous |

**Synchronous** flows hold the user's request open; only `answer_question` is synchronous initially, and nothing slow may join it. **Asynchronous** flows run in the background — the User Model Agent never sits on the user-facing hot path.

## The Main Conversational Turn

```
User message
     │
     ▼
Append to conversation (episodic memory)
     │
     ▼
Intent classification ──▶ answer_question flow
     │
     ▼
Assemble user context (UserMemoryStore.ActiveContext)
     │
     ▼
Retrieval Agent → answer + citations
     │
     ▼
Respond to user, append to conversation
     │
     └──▶ enqueue after_turn (async)
              │
              ▼
     User Model Agent examines the episode:
     anything durable here? → proposals
```

Every turn feeds the user model, but the user never waits for it.

## Later Stage Evolution

Dynamic orchestration is deliberately just a future flow: the Planner Agent receives a goal, decomposes it, and invokes other agents through the same registry and request contract. The framework — interface, run records, sensitivity ceilings — does not change; the flow stops being static.

---

# LLM Provider Abstraction

Agents never call an LLM API directly. They use a single provider interface:

```go
type LLMProvider interface {
    Complete(ctx context.Context, req CompletionRequest) (Completion, error)
    // chat, tool use, and structured (schema-constrained) output
}
```

* **One configured cloud provider** at a time, chosen for answer quality; swapping providers is a configuration change, honoring the replaceability principle
* **Model tiers within the provider**: a cheap/fast model for intent classification and extraction, a strong model for synthesis — each flow step declares which tier it needs
* **Embeddings are separate**: embedding generation belongs to the Python pipeline (see [databases.md](databases.md)); the serving side only embeds query text, using the same model the corpus was embedded with

## The Privacy Trade-off

With a cloud provider, memory content — including sensitive content — leaves the machine at inference time. This is a deliberate trade-off for answer quality in the evaluation phases, and it is bounded in two ways:

* The **sensitivity ceiling** on every request limits *what* can be retrieved into a prompt at all
* Every LLM-bound payload is reconstructable from the run record, so *what was sent* is always auditable

The designed future path is **sensitivity-aware routing**: multiple configured providers, with high-sensitivity contexts restricted to a local model (e.g. via Ollama). 

---

# Retrieval Agent

The agent that answers factual questions from memory.

## Pipeline

```
Question
   │
   ▼
Query understanding          (cheap model tier)
   ├── rewrite for search
   ├── extract time range            → MemoryQuery.TimeRange
   ├── select memory categories      → MemoryQuery.Categories
   └── decide graph expansion        → MemoryQuery.GraphExpansion
   │
   ▼
DocumentStore.HybridSearch  (+ EpisodeStore.Search when episodic)
   │
   ▼
GraphStore.Expand            (when relational: "which projects use X?")
   │
   ▼
Context assembly             (token budget: user context + top chunks)
   │
   ▼
Synthesis                    (strong model tier)
   │
   ▼
Answer with citations
```

## Behavior

* **Citations are mandatory** — every claim in the answer points at source passages.
* **Insufficient evidence is an answer** — if retrieval returns nothing relevant, the agent says so. "I don't have anything on that" always beats a plausible hallucination over the user's own life
* **Time-aware** — "what did I work on last month?" becomes a `TimeRange`-scoped query, leaning on document timestamps and episodes
* **Relational questions route through the graph** — similarity search finds *mentions*; `GraphStore.Expand` finds *connections*

## Non-Goals

The Retrieval Agent does not update the user model, perform external research, or take actions. It reads memory and answers.

---

# User Model Agent

The differentiating agent. It maintains the user model that memory.md defines — and it never talks to the user.

## Position

The User Model Agent runs **only asynchronously**: after conversation turns, after syncs, and on maintenance schedules. Its output is never an answer; it is a better `UserContext` for every other agent's next request.

## Duties


1. **Observe** — examine new episodes and newly ingested documents for durable information about the user
2. **Distinguish explicit from inferred** — explicit statements become active memories directly; inferences become proposals, never active writes (`UserMemoryStore.Propose`)
3. **Assess confidence** — score proposals against accumulated evidence; attach evidence links (chunks, messages, runs) for everything
4. **Detect conflicts** — when new evidence contradicts an active memory: record the contradicting evidence and lower confidence, or create a successor and mark the old memory superseded (`UserMemoryStore.Supersede`) — never overwrite
5. **Sweep for staleness** — on the maintenance schedule, decay confidence of beliefs whose `last_confirmed_at` is old; long-unsupported interests fade
6. **Curate the graph** — resolve entity aliases and review low-confidence extraction candidates (`GraphStore.ResolveAlias`)

## Guardrails

* It may **propose** but not silently activate inferred beliefs — activation follows the proposal workflow in memory.md, including user review where configured
* It respects **rejected** proposals: a belief the user rejected is not re-proposed on the same kind of evidence
* It is the **only agent** that writes User Memory; every other agent is a reader. One writer keeps belief evolution coherent

---

# Later Agents

Sketches only — each gets its own design when its phase approaches. Listed to show the framework accommodates them without change.

## Research Agent 

Performs external research (web, papers, GitHub) on user-selected topics, ranked against the user context ("relevant *to me*", not just relevant). Findings enter Document Memory as documents with research provenance — citable and purgeable like any connector's output. Introduces the first external network calls from an agent.

## Planner Agent

Decomposes goals into multi-step plans and composes other agents dynamically through the registry. The arrival of non-static flows.

## Critic Agent

Evaluates other agents' outputs — answer quality, citation faithfulness, proposal soundness — before they reach the user or memory. Flow definitions include an evaluation hook from day one (a no-op initially) so inserting the Critic later requires no flow redesign.

## Tool Agent

The gateway for side-effecting external actions (APIs, tools). Confirm-before-acting by default.

---

# Security and Privacy

* **Sensitivity ceilings are request-scoped** — the orchestrator sets `MaxSensitivity` per flow and trigger; a future proactive/background flow can be capped lower than an interactive conversation, so high-sensitivity memories cannot surface where the user isn't actively present
* **Write permissions are per-category** — mirroring the ownership table in memory.md: only the User Model Agent writes User Memory; only the framework appends episodes; the Retrieval Agent writes nothing. Enforced by the framework applying writes, not by agent discipline
* **Full auditability** — run records plus citation-carrying responses mean every answer, every belief, and every LLM payload can be traced after the fact
* **No agent bypasses the store interfaces** — there is no other path to the data

---

# Future Work

* **Sensitivity-aware model routing** — multiple configured LLM providers with routing by the request's sensitivity ceiling; high-sensitivity contexts served by a local model. The seams (provider interface, per-request ceiling) exist by design
* **Agent extraction** — moving individual agents into separate services for parallelism and isolation in Phase 5
* **Streaming** — synchronous flows should stream synthesis output; omitted from contracts above for clarity
* **Cost accounting** — per-run token and cost tracking in run records, once cloud usage is nontrivial

---

# Open Questions

* **Intent classification** — heuristics, a cheap LLM call, or both (heuristics first, LLM fallback)? Latency on the hot path vs routing quality. Needs measurement.
* **Token budget policy** — how the context window is split between user context, retrieved chunks, and conversation history in `answer_question`. Interacts with memory.md's open "user context size" question.
* **`after_turn` granularity** — run the User Model Agent per turn, or batch per conversation? Per turn is fresher; per conversation is cheaper and sees complete exchanges.
* **Critic criteria** — what the Critic actually measures (citation faithfulness seems first). Deferred to its own design doc, but the evaluation hook's shape depends on it.
