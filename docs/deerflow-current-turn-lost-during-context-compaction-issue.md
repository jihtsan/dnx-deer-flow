# Issue draft: Context compaction can discard the current user turn and restore a stale first-turn intent

## Suggested title

`Context compaction can discard the current user turn and restore a stale first-turn intent`

## GitHub Issue body

### Summary

During a tool-heavy follow-up run, automatic context compaction can remove the current
user message while permanently preserving the first user message associated with the
dynamic-context reminder triplet. If the generated summary also misidentifies the active
goal, the final model call sees a stale first-turn request as the only direct user request
and answers that request instead of the current one.

This is reproducible on the current upstream implementation. It is distinct from:

- recursive `__user` ID growth fixed by #3746;
- frontend/history ordering fixed by #4065;
- Skill selection or application-level intent routing.

In the observed run, the agent correctly loaded the relevant Skill, selected the correct
SOC time-series API, received 48 SOC points, and analyzed the SOC trend. A second automatic
compaction immediately before final generation then removed the current SOC request and
injected a summary that declared the old income request to be the primary goal. The final
answer consequently reported income.

### Environment

- DeerFlow upstream `main`: `d4fdc2758e9b23c79b1c148351700b52725a4644`
- Summarization enabled
- Trigger: `tokens: 32000`
- Retention: `keep: {type: messages, value: 10}`
- Same-day multi-turn thread
- Tool-heavy follow-up run that triggered automatic compaction more than once

The relevant local middleware and tests have no diff from the pinned upstream `main` for:

- `backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py`
- `backend/packages/harness/deerflow/agents/middlewares/dynamic_context_middleware.py`
- `backend/packages/harness/deerflow/agents/middlewares/durable_context_middleware.py`
- `backend/tests/test_summarization_middleware.py`

### Minimal semantic reproduction

1. Start a thread with user request A, for example:

   ```text
   Show the site's income for the last two days.
   ```

2. Allow `DynamicContextMiddleware` to replace that first user message with the
   ID-swap triplet:

   ```text
   SystemMessage(id=X)                 # dynamic-context reminder
   HumanMessage(id=X__memory)          # optional hidden memory
   HumanMessage(id=X__user)            # original request A
   ```

3. Accumulate enough history to trigger automatic summarization. The peer-rescue logic
   from #3746 preserves all three triplet members, including request A.

4. Send a different request B in the same thread, for example:

   ```text
   Show the site's SOC trend for the last 24 hours.
   ```

5. During request B, produce enough tool output/model steps to trigger a second automatic
   compaction before the final answer.

6. Inspect the final model request or checkpoints.

### Expected behavior

- Request B remains an authoritative direct `HumanMessage` throughout its run.
- Compaction may summarize older turns, but it must not make request A look newer or more
  authoritative than request B.
- The final answer addresses the SOC trend returned by the tools.

### Actual behavior

At the second compaction boundary, the state was partitioned as follows (content sanitized):

```text
messages_to_summarize:
  - previous answer to request A
  - current HumanMessage: request B (24-hour SOC trend)
  - request-B Skill/tool setup

preserved_messages:
  - first-turn dynamic SystemMessage
  - first-turn hidden memory HumanMessage
  - first-turn HumanMessage(id=X__user): request A (two-day income)
  - recent request-B SOC tool results and todos
```

The generated `summary_text` incorrectly stated, in effect:

```text
The current primary goal is the site's income for the last 48 hours.
The newer SOC request is a branch and must not replace the income goal.
```

After the rewrite performed by `_amaybe_summarize()`, the state contained:

- no direct `HumanMessage` for request B;
- the preserved first-turn `HumanMessage(id=X__user)` for request A;
- the incorrect summary injected by `DurableContextMiddleware`;
- valid request-B SOC tool results, but no authoritative user message explaining why they
  were collected.

The final model call therefore answered request A.

### Deterministic checkpoint evidence

The failure was confirmed from persisted run/checkpoint state, not inferred from UI order:

```text
run input:  request B (24-hour SOC trend)
run output: answer to request A (two-day income)
```

The checkpoint sequence showed:

1. The current SOC `HumanMessage` was present at run start.
2. The agent loaded the expected data Skill.
3. The agent called the SOC time-series path and received 48 points.
4. The agent computed SOC minimum/maximum and charge/discharge phases.
5. A later compaction placed the current SOC `HumanMessage` in
   `messages_to_summarize`, while peer rescue retained the first income `__user` message.
6. The next model checkpoint had no direct SOC user message and contained the stale-goal
   summary.
7. The final assistant message answered the income request.

This sequence rules out the frontend, Skill router, and data API as the source of the
intent switch.

### Root cause

Three individually reasonable behaviors interact incorrectly:

1. `DynamicContextMiddleware` applies the ID-swap triplet only to the first user message
   on a same-day thread. Later user turns remain ordinary `HumanMessage` objects.
2. `_preserve_dynamic_context_reminders()` rescues the first triplet and its `X__user`
   peer from every compaction, as required by #3746.
3. `_partition_messages()` retains only a recent message window. During a long current
   run, the current user turn can fall outside that window and be summarized, while the
   older first-turn `X__user` is moved back into `preserved_messages` unconditionally.

The result violates an important compaction invariant:

> A user message introduced by the active run must not be removed while an older user
> message remains as the only direct user instruction.

The summary is probabilistic, so it cannot be the sole carrier of the active request.
Once it incorrectly labels an older goal as primary, `DurableContextMiddleware` projects
that mistake into every subsequent model call.

### Relevant code

- Partition and rewrite:
  [`summarization_middleware.py`](https://github.com/bytedance/deer-flow/blob/d4fdc2758e9b23c79b1c148351700b52725a4644/backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py#L275-L365)
- Dynamic-context peer rescue:
  [`summarization_middleware.py`](https://github.com/bytedance/deer-flow/blob/d4fdc2758e9b23c79b1c148351700b52725a4644/backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py#L368-L420)
- First-turn ID swap and same-day no-op:
  [`dynamic_context_middleware.py`](https://github.com/bytedance/deer-flow/blob/d4fdc2758e9b23c79b1c148351700b52725a4644/backend/packages/harness/deerflow/agents/middlewares/dynamic_context_middleware.py#L187-L272)
- Summary projection into model requests:
  [`durable_context_middleware.py`](https://github.com/bytedance/deer-flow/blob/d4fdc2758e9b23c79b1c148351700b52725a4644/backend/packages/harness/deerflow/agents/middlewares/durable_context_middleware.py#L249-L271)
- Existing peer-rescue regression test:
  [`test_summarization_middleware.py`](https://github.com/bytedance/deer-flow/blob/d4fdc2758e9b23c79b1c148351700b52725a4644/backend/tests/test_summarization_middleware.py#L428-L489)

The existing test correctly proves that the first-turn `X__user` peer is not orphaned.
It does not exercise repeated compaction during a later run with a newer user intent.

### Proposed fix

Preserve the active run's user input as a direct message across automatic compaction.

The runtime already tracks the message IDs that existed before the current run. That
boundary is used elsewhere to identify current-run messages. The summarization path can
use the same boundary when partitioning messages:

1. Pass `runtime` (or the pre-existing message ID set) into compaction preparation.
2. After the normal keep-window partition, rescue direct user messages introduced by the
   current run from `messages_to_summarize` into `preserved_messages`.
3. Preserve chronological ordering so the active request remains later than the rescued
   first-turn triplet.
4. Keep the #3746 reminder/peer protections, but do not let them be the only reason an old
   user instruction survives.
5. Strengthen the summary prompt/invariant so the latest direct user turn is always the
   active intent unless the user explicitly changed or cancelled it. This is defense in
   depth; deterministic preservation must remain the primary fix.

An alternative is to stop permanently rescuing the first-turn `X__user` after a newer
direct user turn exists, but that changes the #3746 invariant and is riskier unless the
reminder triplet lifecycle is redesigned. Preserving current-run user input is the smaller
and safer repair.

### Required regression test

Add an integration-style middleware test with this sequence:

```text
turn 1: user asks for income
compaction 1: first dynamic-context triplet is preserved
turn 2: user asks for SOC
turn 2 tool loop: enough messages are added for compaction 2
compaction 2: current SOC HumanMessage would normally cross the cutoff
final model request: SOC remains the latest authoritative direct HumanMessage
```

Assertions should include:

- the current-run SOC `HumanMessage` is not in `messages_to_summarize`;
- the preserved sequence keeps the current SOC request after the old income `X__user`;
- repeated compaction cannot remove the current-run user request;
- the final request still contains valid tool-call/tool-result structure;
- a misleading mock summary cannot make the old request the only direct user instruction.

### Impact

- The agent can complete the correct tool workflow and still answer a different historical
  request at the final step.
- Tool-heavy workflows are more exposed because one user run can cross the compaction
  threshold multiple times.
- The output looks like a Skill/router failure, which sends diagnosis toward the wrong
  subsystem.
- Raising the trigger reduces frequency but does not restore the missing invariant.

### Temporary mitigations

- Disable automatic summarization for deployments whose model/provider can safely carry
  the complete context, or
- raise the explicit token trigger while leaving adequate input/output headroom.

These are mitigations only. Once compaction eventually runs, the same semantic failure can
still occur.

Do not switch directly to a fractional trigger until the model profile reports the real
provider input limit. A profile/provider mismatch can delay compaction past the actual
context window.

### Upstream status

As of `d4fdc2758e9b23c79b1c148351700b52725a4644`, no exact duplicate Issue or PR was found.

Related work:

- [#3725](https://github.com/bytedance/deer-flow/issues/3725) / [#3746](https://github.com/bytedance/deer-flow/pull/3746)
  fixed recursive `__user` re-injection and orphaned first-turn peers. The current issue is
  a later repeated-compaction case in which that preserved old peer outlives the current
  user message.
- [#4065](https://github.com/bytedance/deer-flow/pull/4065) fixed UI-facing history,
  pagination, and input-wrapper persistence issues. It does not change the three middleware
  files involved here.
- [#4346](https://github.com/bytedance/deer-flow/issues/4346) discusses working-memory and
  trigger calibration, including the 32K default, but does not implement the active-run
  user-message preservation invariant described above.

The latest upstream commits after the previously inspected baseline also do not modify the
relevant summarization, dynamic-context, or regression-test files. Therefore current
upstream `main` should be considered affected until this exact repeated-compaction case is
covered and fixed.

## 本地判断与配置建议

### 是否是 DeerFlow 的问题

是。这次复现使用的相关 middleware 与最新 upstream `main` 一致，故不是本地 DNX Skill
独有问题。上游曾修复两个相似问题，但没有修复本次“当前 run 用户消息被二次压缩删除”的路径。

### 是否应该把 32K 改成 70%

不建议直接改成 `fraction: 0.7`。

上游 [PR #3174](https://github.com/bytedance/deer-flow/pull/3174) 曾把默认触发值从
`15,564` 提高到 `32,000`，目的是减少工具密集任务中的反复压缩；它仍是一个模型无关的
固定折中值，并不代表 32K 对 256K 窗口最合适。

当前运行时模型 profile 报告 `max_input_tokens=1,050,000`，但实际代理/供应商窗口按
`256K` 理解。DeerFlow 的 fraction 会按 profile 计算，因此 `0.7` 大约在 `735,000`
token 才触发，远高于真实的 256K 上限。

如果真实输入窗口确为 256K：

- 70% 是约 `179,200` token；
- 更保守的临时值建议先用显式 `tokens: 128000`；
- 验证供应商稳定后可逐步提高到 `tokens: 160000`；
- 还需为 system prompt、工具 schema、输出、token 估算误差和代理差异预留空间。

在改用 fraction 前，应先让模型 profile 的 `max_input_tokens` 与真实供应商限制一致。
即使调高阈值，也仍需提交并修复上述 Issue，因为阈值只改变触发时间，不保证当前用户意图不丢失。
