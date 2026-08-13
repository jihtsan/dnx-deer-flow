---
status: accepted
---

# Use rule-based automatic and forced knowledge retrieval modes

The first knowledge-base release will expose one optional `knowledge_mode` request field with `auto` and `force` values. The request boundary normalizes an absent field to `auto`; `force` attempts retrieval for every real user turn, while `auto` uses deterministic code rules to skip only clearly irrelevant turns and retrieves by default for other eligible turns. This keeps the initial behavior testable, avoids contradictory Boolean-plus-mode states, and avoids adding a second LLM routing call before enough decision and retrieval data exists to evaluate one.

## Considered Options

- A Boolean flag cannot distinguish an explicit retrieval requirement from permission to decide automatically.
- A separate selection Boolean plus Retrieval Mode can represent contradictory states and was rejected in favor of one optional mode whose default is `auto`.
- An LLM router can handle ambiguous intent, but initially adds latency, cost, and model-dependent behavior without a project-specific evaluation set.
- A raw character-count threshold was rejected as a standalone rule because short Chinese questions such as “退款期限？” can still require knowledge.

## Consequences

Automatic decisions must record stable reason codes, including explicit request, explicit skip, trivial message, answer transformation, and default retrieval. The decision uses only the current real user message, so it has no history-dependent “knowledge follow-up” rule. The internal middleware always receives a normalized `auto | force` value; the first release has no separate knowledge-selection Boolean or public `off` mode. These records can later support an evaluated LLM router for ambiguous cases without changing the public Retrieval Mode contract.
