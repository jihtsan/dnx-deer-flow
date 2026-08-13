# DeerFlow Knowledge Context

This context defines the language used when external document knowledge is made available to an Agent conversation. It separates document evidence from conversation history and personalized memory.

## Language

**Knowledge Base**:
A user-owned collection of indexed documents that can provide attributable evidence to an Agent.
_Avoid_: Memory, conversation history, file upload

**Knowledge Graph**:
The entities, relationships, and source-document associations produced from the documents indexed by the configured LightRAG workspace. It does not represent live energy-site topology, telemetry, or control state.
_Avoid_: Operational topology, station control graph, digital twin

**Loaded Graph**:
The bounded Knowledge Graph dataset returned to the current page for rendering. It may be truncated and must not be described as the complete workspace graph when the response reports truncation.
_Avoid_: Full graph, complete workspace graph

**Entity Category**:
The category assigned to an entity by LightRAG, used to group entities that share a semantic type. It is not a graph community or an individual entity.
_Avoid_: Group, cluster, popular entity

**Relationship Weight**:
The numeric relationship-strength value supplied by LightRAG for an edge. It is not an entity-recognition confidence or a probability.
_Avoid_: Confidence, probability, accuracy

**Relationship Edge**:
A directed LightRAG relationship connecting a source entity to a target entity in the Knowledge Graph. It is graph data, not a source-document file path.
_Avoid_: File path, document path, entity list

**Knowledge Binding**:
An association that can make a Knowledge Base eligible for a specific Agent in a future multi-base release. The single-base MVP does not expose binding selection.
_Avoid_: Permission, attachment

**Knowledge Scope**:
The server-resolved Knowledge Base eligible to answer the current turn. The MVP scope is implicit and contains at most one active Knowledge Base; clients do not send knowledge-base identifiers.
_Avoid_: Retrieval Mode, permission

**Retrieval Mode**:
The normalized per-turn policy that determines whether an eligible Knowledge Base must be queried or may be queried automatically. The request field may be omitted; omission normalizes to Automatic Retrieval.
_Avoid_: Knowledge enabled, RAG switch

**Forced Retrieval**:
A Retrieval Mode that requires an eligible Knowledge Base query for every real user turn without bypassing access, readiness, or availability checks.
_Avoid_: Always-on permission

**Automatic Retrieval**:
A Retrieval Mode, and the request default, that skips clearly irrelevant turns and retrieves for other eligible turns, including ambiguous knowledge-bearing questions.
_Avoid_: Optional knowledge, model tool choice

**Retrieval Decision**:
The attributable outcome of applying a Retrieval Mode to the current user message, including whether retrieval occurs and why. It does not inspect earlier conversation turns in the MVP.
_Avoid_: Router guess

**Knowledge Evidence**:
A bounded, attributable excerpt returned from a Knowledge Base for the current user message. It is untrusted source data, is reused only inside the current run, and is never appended to persistent conversation messages.
_Avoid_: Memory fact, system context

**Knowledge Context**:
The hidden, bounded model-request projection of the current run's Retrieval Decision and Knowledge Evidence. It disappears after the run and is not conversation history.
_Avoid_: Message history, Memory, tool result

**Knowledge Source**:
A cited document identified within its Knowledge Base. Multiple cited excerpts from the same document remain one Knowledge Source.
_Avoid_: Chunk, citation link

**Citation Occurrence**:
One inline use of Knowledge Evidence in an answer, retaining the exact excerpt location while contributing to its document's citation count.
_Avoid_: Source, document

**Memory**:
Durable user- or Agent-specific context distilled from conversations and reused for personalization; it is not a document corpus or a source of citations.
_Avoid_: Knowledge Base, document retrieval
