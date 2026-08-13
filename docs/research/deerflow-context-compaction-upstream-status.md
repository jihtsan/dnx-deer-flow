# DeerFlow upstream context-compaction status

Date: 2026-07-23

## Bottom line

The exact reproduced failure is still present in current DeerFlow `main` at
`d4fdc2758e9b23c79b1c148351700b52725a4644`.

Upstream has fixed two similar context-compaction defects, but neither fix covers this
repeated-compaction sequence:

1. The first-turn dynamic-context `X__user` peer is deliberately preserved.
2. A later run introduces a different user request.
3. A second compaction summarizes and removes that current request while rescuing the much
   older first-turn peer.
4. A mistaken summary plus the stale direct `HumanMessage` makes the old intent authoritative
   again.

No exact duplicate Issue or PR was found in the official repository searches performed for
this investigation.

## Scope and sources

Only first-party sources were used:

- official Issues and PRs in [`bytedance/deer-flow`](https://github.com/bytedance/deer-flow);
- the fetched upstream `main` commit and its source history;
- the pinned source files at `d4fdc2758e9b23c79b1c148351700b52725a4644`.

Relevant local middleware and test files have no diff from the pinned upstream `main`, so
the deterministic local checkpoint reproduction applies to the current upstream code path.

## Related upstream work

### Issue #2811 and PR #2823: preserve dynamic reminders

- [Issue #2811](https://github.com/bytedance/deer-flow/issues/2811)
- [PR #2823](https://github.com/bytedance/deer-flow/pull/2823)

This work addressed dynamic-context reminders disappearing during summarization and being
re-injected at an incorrect position. It established the need to preserve hidden reminder
state, but it did not cover a later direct user turn being removed while an old first-turn
user peer survives.

### Issue #3725 and PR #3746: recursive ID swap and orphan peer

- [Issue #3725](https://github.com/bytedance/deer-flow/issues/3725)
- [PR #3746](https://github.com/bytedance/deer-flow/pull/3746)

`#3725` reported recursive `__user` suffix growth. An old message was repeatedly ID-swapped,
appended after the current user input, and executed as the latest instruction.

`#3746` fixed two paths:

- `DynamicContextMiddleware` rejects an ID ending in `__user` as a new injection target.
- `SummarizationMiddleware` rescues the untagged `X__user` peer together with its tagged
  dynamic-context reminder messages.

The current reproduction contains no recursive suffix growth. Instead, the peer-rescue fix
becomes one side of a new ordering/retention invariant failure: `X__user` is permanently
preserved, while the ordinary `HumanMessage` introduced by a later active run can cross the
summarization cutoff and be deleted.

The existing upstream regression test proves that `X__user` is rescued. It does not perform
a second compaction during a later run with a different user intent.

### PR #4065: history ordering and UI persistence

- [PR #4065](https://github.com/bytedance/deer-flow/pull/4065)

Despite the broad title `fix(context): resolve context compress bug`, this PR addresses
thread-history pagination, frontend transient bridges, message ordering, and persistence of
the original UI-facing user input. It does not modify:

- `summarization_middleware.py`;
- `dynamic_context_middleware.py`;
- `durable_context_middleware.py`;
- the repeated-compaction invariant described here.

It fixed a separate display/history problem and cannot explain or prevent the checkpoint
state mutation observed before the wrong final model answer.

### Issue #4346: working-memory and trigger calibration RFC

- [Issue #4346](https://github.com/bytedance/deer-flow/issues/4346)

This open RFC discusses the 32K default, model-window-aware triggers, summary-model ownership,
failure handling, and possible future compaction improvements. It is relevant to how often
compaction runs, but it does not implement preservation of the active run's user request.

Raising the trigger can reduce exposure to the bug; it does not repair it once compaction
eventually occurs.

## Current `main` evidence

Pinned upstream source:

- [partition and state rewrite](https://github.com/bytedance/deer-flow/blob/d4fdc2758e9b23c79b1c148351700b52725a4644/backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py#L275-L365)
- [dynamic-context peer rescue](https://github.com/bytedance/deer-flow/blob/d4fdc2758e9b23c79b1c148351700b52725a4644/backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py#L368-L420)
- [first-turn ID swap and same-day behavior](https://github.com/bytedance/deer-flow/blob/d4fdc2758e9b23c79b1c148351700b52725a4644/backend/packages/harness/deerflow/agents/middlewares/dynamic_context_middleware.py#L187-L272)
- [durable summary injection](https://github.com/bytedance/deer-flow/blob/d4fdc2758e9b23c79b1c148351700b52725a4644/backend/packages/harness/deerflow/agents/middlewares/durable_context_middleware.py#L249-L271)
- [peer-rescue regression test](https://github.com/bytedance/deer-flow/blob/d4fdc2758e9b23c79b1c148351700b52725a4644/backend/tests/test_summarization_middleware.py#L428-L489)

The relevant implementation still:

1. partitions messages according to the configured keep window;
2. rescues the first dynamic-context triplet and its old `X__user` peer;
3. rewrites the entire message state to only `preserved_messages`;
4. does not protect direct user messages introduced by the active run;
5. injects `summary_text` into subsequent model calls.

The latest commits after the previously inspected upstream baseline only changed frontend run
duration display, webhook delivery wording, and branch run-event seeding. None changed the
relevant middleware or summarization tests.

## Status classification

| Question | Result |
| --- | --- |
| Has upstream fixed similar context bugs? | Yes: `#2823`, `#3746`, and adjacent UI work in `#4065`. |
| Is this the recursive `__user` bug from `#3725`? | No. No recursive ID swap is required. |
| Does `#4065` fix the final model's semantic context? | No. It fixes history/UI persistence and ordering. |
| Is an exact duplicate Issue or PR known? | No exact duplicate was found. |
| Is current `main` structurally affected? | Yes. It contains the same partition, peer-rescue, rewrite, and injection path used in the deterministic reproduction. |
| Would a larger token threshold fix it? | No. It only delays the failing compaction. |

## Confidence

High.

The conclusion is based on a persisted checkpoint reproduction, exact source comparison with
the pinned upstream `main`, and official Issue/PR history. The remaining uncertainty is only
whether maintainers consider this a new regression of `#3746` or a missing invariant in the
broader compaction design; either classification leads to the same required regression test.
