# VivyAtlas

## High-Level Architecture & Design

---

# Vision

The VivyAtlas is a personal AI platform that continuously learns about a user through the data they choose to provide and the way in which they interact with the system.

Unlike a traditional chatbot or Retrieval-Augmented Generation (RAG) system, the VivyAtlas aims to build and maintain a persistent understanding of the user's knowledge, interests, goals, preferences, projects, and history.

Its purpose is to become an intelligent companion capable of:

* Answering questions about the user's knowledge and history
* Performing personalized research
* Acting proactively when relevant information becomes available
* Remembering information across years
* Adapting its behavior as the user evolves

The long-term vision is not simply to build an AI assistant, but rather an extensible platform on which specialized AI agents can collaborate while sharing a common long-term memory.

---

# Core Design Principles

## User Ownership

The user owns all data.

VivyAtlas only accesses data sources that the user explicitly grants permission to access.

The user should always be able to:

* View stored information
* Delete information
* Disable data sources
* Export their data

---

## Local First

The initial implementation should prioritize local execution.

Benefits:

* Privacy
* Simplicity
* Lower operational cost
* Faster iteration

Cloud synchronization can be added later.

---

## Persistent Memory

The system should remember information across sessions.

Instead of conversations existing in isolation, the system maintains a continuously evolving memory of:

* Documents
* Projects
* Research
* User preferences
* Long-term goals
* Skills
* Historical interactions

---

## Extensible by Design

Every major component should be replaceable.

Examples:

* LLM provider
* Embedding model
* Vector database
* Knowledge graph
* Agent implementations
* Connectors

The architecture should allow new capabilities to be added without redesigning the system.

---

# System Goals

The system should eventually be able to answer questions such as:

> What have I learned about distributed systems this year?

> Which papers are most relevant to my current interests?

> Summarize everything I worked on last month.

> Which projects demonstrate my strongest backend experience?

> Based on everything you know about me, what should I learn next?

> Find contradictions in my notes.

> Have I solved a similar problem before?

---

# High-Level Architecture

```
                    User
                      │
                      ▼
               Go API Gateway
                      │
                      ▼
             Agent Orchestrator
        ┌─────────┼──────────┐
        │         │          │
        ▼         ▼          ▼
 Retrieval   User Model   Tool Execution
        │         │          │
        └─────────┼──────────┘
                  │
                  ▼
             Memory Layer
        ┌─────────┼────────────┐
        │         │            │
 Documents   User Profile   Knowledge Graph
        │         │            │
        └─────────┼────────────┘
                  │
                  ▼
          Python Processing Layer
                  │
                  ▼
             Data Connectors
```

---

# Technology Philosophy

The system intentionally separates online serving from offline AI processing.

## Go Responsibilities

Go acts as the runtime of the AI Operating System.

Responsibilities include:

* REST/gRPC API
* Agent orchestration
* Concurrent execution
* Request routing
* Tool execution
* Memory retrieval
* Authentication
* User model management
* Long-term memory writes
* Observability

Go is chosen because it excels at building highly concurrent backend services.

---

## Python Responsibilities

Python is responsible for computationally intensive AI workloads.

Responsibilities include:

* ETL pipelines
* Document parsing
* Dataset generation
* Embedding generation
* LLM preprocessing
* Entity extraction
* Summarization
* Batch processing
* Model experimentation
* Evaluation

Python is intentionally isolated from the serving layer.

---

# Memory Architecture

One of the core ideas of the project is that not all memory is equal.

The system distinguishes between multiple categories of memory.

## Document Memory

Stores raw information.

Examples:

* PDFs
* Notes
* Git repositories
* Emails
* Calendar events
* Browser history
* Research papers

Purpose:

Answer factual questions about existing data.

---

## User Memory

Stores durable information about the user.

Examples:

* Goals
* Interests
* Preferences
* Skills
* Learning progress
* Long-term projects

Purpose:

Personalize every future interaction.

---

## Structured Knowledge

Relationships extracted from information.

Examples:

* Technologies used together
* Project dependencies
* Research topics
* Connected concepts

Purpose:

Support reasoning beyond simple vector retrieval.

---

# User Model

The user model is the most important component that differentiates VivyAtlas from traditional RAG systems.

Rather than only storing documents, the system continuously develops a structured understanding of the user.

Examples:

* Current goals
* Technical interests
* Preferred learning style
* Skills
* Weaknesses
* Long-term projects
* Frequently used technologies

Each piece of stored knowledge should contain:

* Value
* Confidence
* Evidence
* Timestamp
* Last updated
* Source
* Sensitivity level

This allows memories to evolve rather than remain static.

---

# User Model Agent

A dedicated User Model Agent is responsible for maintaining the user profile.

Its responsibilities include:

* Identifying durable information
* Updating existing memories
* Detecting conflicting information
* Tracking changing interests
* Proposing new memories

Importantly, the User Model Agent does **not** directly answer user questions.

Instead, it improves every other agent by maintaining an accurate representation of the user.

---

# Memory Proposal Workflow

The system distinguishes between explicit user statements and inferred information.

Example:

Explicit:

> "I want to improve my AI engineering skills."

Inference:

> The user prefers infrastructure-focused AI projects.

Inferred memories should initially exist as proposals.

Example workflow:

```
Conversation

↓

Memory Proposal

↓

Confidence Assessment

↓

(Optional) User Approval

↓

Persistent User Memory
```

This prevents the system from silently creating incorrect long-term memories.

---

# Data Sources

The system is connector-based.

Each connector is independently enabled by the user.

Potential connectors include:

### Local

* Markdown notes
* PDFs
* Local projects
* Documents
* Images

### Development

* GitHub
* GitLab
* Local Git repositories

### Productivity

* Calendar
* Email
* Notes applications

### Learning

* YouTube history
* Browser bookmarks
* Reading lists
* Research papers

### Future

* Banking
* Fitness
* Photos
* Messaging platforms
* Smart home devices

---

# Agents

Agents are independent services that share a common memory layer.

Initial agents:

## Retrieval Agent

Retrieves relevant knowledge.

---

## User Model Agent

Maintains the user profile.

---

## Research Agent

Performs autonomous research on user-selected topics.

---

## Planner Agent

Breaks large goals into actionable plans.

---

## Critic Agent

Evaluates outputs from other agents.

---

## Tool Agent

Interfaces with external APIs and tools.

---

Additional agents should be easy to add without modifying existing ones.

---

# Data Flow

```
Connector

↓

Python ETL

↓

Normalization

↓

Chunking

↓

Entity Extraction

↓

Embedding Generation

↓

Memory Storage

↓

Go API

↓

Agents

↓

LLM

↓

User
```

---

# Development Roadmap

## Phase 1 — Personal Knowledge Base

Objective:

Create a searchable AI memory over technical notes and documents.

Features:

* Local document ingestion
* Embeddings
* Hybrid search
* Citation-based answers

---

## Phase 2 — User Model

Objective:

Build a persistent understanding of the user.

Features:

* User Model Agent
* Memory proposals
* Confidence scoring
* Editable profile

---

## Phase 3 — Research Assistant

Objective:

Perform personalized research.

Features:

* Web research
* Paper discovery
* GitHub discovery
* Personalized ranking

---

## Phase 4 — Proactive Intelligence

Objective:

Surface useful information without being asked.

Examples:

* Recommended papers
* Learning suggestions
* Skill gaps
* Project opportunities

---

## Phase 5 — Multi-Agent Platform

Objective:

Introduce collaborative specialized agents.

Features:

* Parallel execution
* Shared memory
* Task decomposition
* Long-running workflows

---

# Long-Term Vision

The system should become a continuously evolving representation of the user's digital life.

Rather than acting as a chatbot that simply answers questions, it should function as an intelligent platform that:

* Understands the user
* Learns over time
* Reasons across years of accumulated knowledge
* Coordinates specialized AI agents
* Provides increasingly personalized assistance

The project is intended to serve both as a powerful personal productivity platform and as an exploration of long-term AI memory systems, agent orchestration, and modern AI infrastructure.
