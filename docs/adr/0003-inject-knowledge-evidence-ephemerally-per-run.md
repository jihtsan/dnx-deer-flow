---
status: accepted
---

# Inject knowledge evidence ephemerally for one run

`KnowledgeContextMiddleware` will make one Retrieval Decision from the latest real user message and, when required, retrieve Knowledge Evidence once for the run. It will reuse that bounded result across model calls in the same Agent loop, but inject it only into `ModelRequest` through an ephemeral hidden context. It will not append raw excerpts to Agent state, checkpoints, or persistent conversation messages, and it will not use earlier conversation turns to build the retrieval query.

The final assistant answer and its citation markdown remain normal persisted conversation content. Observability may retain bounded metadata such as mode, decision reason, latency, result count, and cited document identifiers, but not raw excerpts.

## Considered Options

- Appending retrieved excerpts to `messages` would make replay straightforward, but would grow conversation history, preserve stale evidence across turns, pollute prompt caches, and expose source text to memory extraction.
- Retrieving again before every model call would avoid state handling, but would add unnecessary latency and could produce inconsistent evidence within one run.
- Building retrieval queries from conversation history could improve follow-up resolution, but was rejected for the MVP because the agreed behavior is strictly single-turn retrieval.

## Consequences

The middleware needs run-local retrieval state that is reusable by every `wrap_model_call` invocation without becoming a message-state update. Its injection should follow DeerFlow's `DurableContextMiddleware` pattern: a trusted authority contract plus escaped, bounded, untrusted data in hidden request-only messages. Because the injected Knowledge Context is absent from state, `MemoryMiddleware` continues to see only the actual user and final assistant messages rather than raw knowledge-base text.
