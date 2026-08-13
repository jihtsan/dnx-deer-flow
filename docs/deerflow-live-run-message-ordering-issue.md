# DeerFlow 实时多轮消息倒序问题：根因、修复路径与 Issue 草稿

## 1. 结论摘要

这是一个已稳定复现的前端历史消息合并问题。

当聊天页面保持打开，并连续完成多个 run 时，后完成的 run 会被插入已经加载的历史消息之前，导致页面按 run 倒序显示。重新加载页面后，初始历史回填可能恢复为正常顺序，因此问题容易被误判为模型串话、相似会话污染或分支错误。

本次证据表明：

- 所有相关消息属于同一个 thread，不是其他可见会话串入。
- 后端持久化的 run 创建时间和请求输入顺序正确。
- 页面 DOM 中三条用户消息的纵向顺序与后端时间顺序完全相反。
- 根因是前端对所有新加载的 run 都使用同一个“前插”操作，没有区分“向上加载更旧历史”和“实时发现更新 run”。
- 官方 DeerFlow `main` 已不再使用这套逐 run 拼接逻辑，而是改为按 thread 全局事件序列分页。关联 Issue/PR 的检索结论见配套研究文档：[`docs/research/deerflow-live-run-message-ordering-upstream.md`](research/deerflow-live-run-message-ordering-upstream.md)。

另有两个独立问题，不应与本 Issue 混在一起：

1. 删除 thread 后仍保留 orphan runs 和用户 memory，说明“清空会话”并不等于清空全部持久化上下文。
2. 本次新 thread 的第一条“收入”请求确实由浏览器提交到后端，但当前日志没有记录客户端触发来源，无法继续判断是误触、旧客户端状态还是其他提交入口。

## 2. 影响

### 用户影响

- 用户看到后输入的消息出现在先输入的消息上方。
- 助手回答与用户问题在视觉上错配，表现得像模型把 SOC 问题变成了收入问题。
- thread 标题仍来自真实第一条输入，而正文显示顺序被反转，进一步加重“会话串线”的观感。
- 用户刷新页面后现象可能变化，导致问题难以稳定描述。

### 受影响范围

- Frontend：`useThreadHistory` 的 run 列表加载和消息合并。
- Backend contract：`GET /threads/{thread_id}/runs` 返回 newest-first；单 run 消息按 `seq` 升序返回。
- 触发条件：同一页面生命周期内完成多个 run，并在每次 run 完成后刷新 run 查询。

## 3. 已验证复现

### 后端真实时间顺序

以下时间为 Asia/Shanghai：

| 时间 | run 输入 |
| --- | --- |
| 08:42:10 | `帮我看看凤凰谷这两天场站的收入是多少` |
| 08:43:48 | 收入问题的结构化澄清回复（隐藏消息） |
| 08:45:56 | `1 可以` |
| 08:47:02 | `今天 SOC 趋势` |

`runs.kwargs_json` 也确认第一条 run 的请求体包含“收入”消息，因此这条用户消息不是模型或 memory 生成的。

### 页面实际顺序

保持页面不刷新时，浏览器 DOM 中三条可见用户消息的位置为：

```text
今天 SOC 趋势                                  top = 84
1 可以                                         top = 352
帮我看看凤凰谷这两天场站的收入是多少          top = 1966
```

期望顺序是“收入 -> 1 可以 -> SOC”，实际顺序是“SOC -> 1 可以 -> 收入”。

红灯检查结果：

```text
expectedOrder:
  - 帮我看看凤凰谷这两天场站的收入是多少
  - 1 可以
  - 今天 SOC 趋势

observedTop:
  - 1966
  - 352
  - 84

verdict: FAIL
```

### 最小复现模型

```text
后端 run 列表：newest-first

第一次完成：runs = [R1]
页面消息：    [R1]

第二次完成：runs = [R2, R1]
R2 未加载，因此 fetch R2
当前合并：    [R2] + [R1] = [R2, R1]
期望合并：    [R1, R2]

第三次完成：runs = [R3, R2, R1]
当前合并：    [R3] + [R2, R1] = [R3, R2, R1]
```

## 4. 代码路径

### 4.1 run 完成后触发刷新

`useThreadStream.onFinish` 调用 `invalidateStoppedThreadCaches`，后者使 `['thread', threadId]` 查询失效：

- 本地：`frontend/src/core/threads/hooks.ts:1073`
- 本地：`frontend/src/core/threads/hooks.ts:668`
- 本地：`frontend/src/core/threads/hooks.ts:682`

### 4.2 后端返回 newest-first run 列表

`RunRepository.list_by_thread` 按 `created_at DESC` 排序：

- 本地：`backend/packages/harness/deerflow/persistence/run/sql.py:143`
- 本地：`backend/packages/harness/deerflow/persistence/run/sql.py:154`
- 上游基线：[run/sql.py at c0b917c](https://github.com/bytedance/deer-flow/blob/c0b917c/backend/packages/harness/deerflow/persistence/run/sql.py#L143-L157)

对应后端测试也明确约定 `list_by_thread returns newest first`：

- 本地：`backend/tests/test_run_repository.py:394`

### 4.3 前端选择第一个未加载 run

`findLatestUnloadedRunIndex` 从 newest-first 数组下标 `0` 开始扫描，所以实时新增的最新 run 会首先被选中：

- 本地：`frontend/src/core/threads/hooks.ts:282`
- 本地：`frontend/src/core/threads/hooks.ts:1638`

### 4.4 所有 fetch 结果都被前插

问题语句：

```ts
setMessageRows((prev) =>
  dedupeRunMessagesByIdentity([..._messages, ...prev]),
);
```

位置：

- 本地：`frontend/src/core/threads/hooks.ts:1677`
- 上游基线：[hooks.ts at c0b917c](https://github.com/bytedance/deer-flow/blob/c0b917c/frontend/src/core/threads/hooks.ts#L1674-L1680)

这个前插操作只在以下场景正确：

- 页面首次加载时，run 列表为 newest-first，前端逐个加载 R3、R2、R1；每次前插最终得到 R1、R2、R3。
- 同一个 run 向后翻页，获取更小的 `before_seq`；旧页需要放在当前页之前。

但它在以下场景错误：

- 页面已经加载 R1，随后实时出现更新的 R2；R2 应追加到尾部，却仍被前插。

### 4.5 现有测试遗漏实时新增 run

现有回归测试只模拟首次加载 newest-first run 列表并不断前插：

- 本地：`frontend/tests/unit/core/threads/message-merge.test.ts:479`
- 上游基线：[message-merge.test.ts at c0b917c](https://github.com/bytedance/deer-flow/blob/c0b917c/frontend/tests/unit/core/threads/message-merge.test.ts#L479-L525)

缺失的测试场景是：

```text
Given: R1 已经加载并显示
When:  run 查询刷新为 [R2, R1]，随后加载 R2
Then:  页面应显示 [R1, R2]，而不是 [R2, R1]
```

## 5. 修复思路

### 方案 A：回移官方 thread-global sequence 分页（推荐）

官方最新 `main` 已把历史加载从“先列 run，再逐 run 拉消息”改为“直接按 thread 全局事件序列分页”：

- 前端使用 `useInfiniteQuery` 请求 `/api/threads/{thread_id}/messages/page`。
- 后端使用 thread-global `seq` 返回按时间升序排列的消息页。
- 前端只需要将向后分页得到的 pages 反转后展开。
- run 完成时使 `threadHistoryQueryKey(threadId)` 失效，重新获取最新 canonical page。

官方当前实现：

- [current `useThreadHistory`](https://github.com/bytedance/deer-flow/blob/a38b1daec392a335016e41f052ae42d90c9ceccd/frontend/src/core/threads/hooks.ts#L1761-L1826)
- [current `flattenThreadHistoryPages`](https://github.com/bytedance/deer-flow/blob/a38b1daec392a335016e41f052ae42d90c9ceccd/frontend/src/core/threads/hooks.ts#L302-L310)
- [current thread message page endpoint](https://github.com/bytedance/deer-flow/blob/a38b1daec392a335016e41f052ae42d90c9ceccd/backend/app/gateway/routers/thread_runs.py#L878-L903)

优点：

- 排序依据是 thread-global `seq`，不会再依赖 run 到达方向。
- 同时统一解决首次历史加载、向后分页、实时新 run、跨 run 消息排序。
- 减少前端状态机：不再需要 `loadedRunIdsRef`、`runBeforeSeqRef`、`findLatestUnloadedRunIndex` 等逐 run 管理。
- 与官方后续演进保持一致，长期维护风险最低。

注意事项：

- 需要同时回移 backend endpoint、response model、过滤/补页逻辑、feedback/turn duration enrich 以及 frontend query cache invalidation。
- 不能只复制前端 hook，否则旧后端没有 `/messages/page` contract。
- 本地分支包含较多知识库改动，建议按关联上游 PR 的完整文件列表回移，而不是直接合并整个官方 `main`。

### 方案 B：在现有逐 run 架构内按全局 `seq` 排序（短期补丁）

如果暂时不能回移方案 A，可在 `setMessageRows` 合并后按 thread-global `RunMessage.seq` 升序排序：

```ts
const merged = dedupeRunMessagesByIdentity([...prev, ...incoming]);
return merged.toSorted(compareRunMessagesChronologically);
```

比较器建议：

1. 两条消息都有 `seq`：按 `seq` 升序。
2. `seq` 缺失：使用 `created_at`。
3. 时间仍相同或不可解析：保持稳定插入顺序。

优点：

- 改动小，可以直接修复本次倒序。
- 同一 run 的 backward page 和实时新 run 都能由同一个排序规则处理。

风险：

- `RunMessage.seq` 当前是可选字段，必须保留 legacy fallback。
- 仍保留复杂的逐 run 加载状态，未来容易出现分页、空 run、并发 refetch 等其他边界问题。
- 不能自动获得官方 thread-page endpoint 后续包含的过滤和 enrich 修复。

因此，方案 B 适合作为紧急修复，方案 A 才是长期路径。

## 6. 回归测试计划

### Frontend unit tests

1. 已加载 R1 后新增 R2，最终顺序必须是 R1、R2。
2. 连续新增 R2、R3，最终顺序必须是 R1、R2、R3。
3. 初次加载 newest-first runs 时，结果仍为 oldest-first messages。
4. 同一 run 使用 `before_seq` 加载旧页时，旧页必须在新页之前。
5. middleware-only / empty run 不应阻止继续寻找有内容的 run。
6. 重复 message identity 仍按当前规则去重，且最终版本获胜。
7. 缺失 `seq` 的 legacy row 不应导致不稳定重排。

如果采用官方 thread-page 方案，还应覆盖：

8. `flattenThreadHistoryPages` 将 backward pages 按全局 `seq` 展开。
9. run 完成后 `threadHistoryQueryKey` 被 invalidated，最新 page 替换 canonical cache。
10. 切换 thread 时，旧 thread 的 pending query 不得写入新 thread。

### Backend tests

1. `/messages/page` 默认返回最新一页，但页内按 thread-global `seq` 升序。
2. `before_seq` 为 exclusive cursor。
3. `has_more=true` 时 `next_before_seq` 等于当前页第一条消息的 `seq`。
4. middleware/filter 后页面不足 `limit` 时继续扫描，避免错误报告没有更多数据。
5. owner isolation 保持有效。
6. feedback 只挂在相应 run 的最后一条 AI 消息上。
7. turn duration enrich 不改变消息顺序。

### E2E

在同一个浏览器页面中连续提交三条消息，每条等待 run 完成，不刷新页面：

```text
first prompt
second prompt
third prompt
```

断言 DOM 中三个用户气泡的 `top` 严格递增。然后刷新页面，再次断言顺序不变。

## 7. 与其他问题的边界

### 7.1 删除 thread 后的 orphan runs / memory

本地账号当前有 35 条 runs，其中 31 条对应已删除 thread。删除接口只清理线程目录、checkpoint/writes 和 `threads_meta`，没有删除 `runs` 或用户 memory：

- 本地：`backend/app/gateway/routers/threads.py:471`

这说明“清空会话”语义不完整，但不是本次页面倒序的直接原因：run 查询仍按当前 `thread_id` 过滤，memory 也不能创建用户消息气泡。

建议另开 Issue 讨论：

- 删除 thread 是否应级联删除 runs、run events、feedback。
- UI 的“清空会话”是否应提供可选的 memory 清理。
- 保留审计数据时，应如何让用户明确知道它没有被删除。

### 7.2 第一条“收入”请求为何被浏览器提交

数据库可以证明该文本位于第一条 run 的 `kwargs_json.input.messages`，但当前没有以下信息：

- 客户端提交入口：composer / human-input card / follow-up / goal / regenerate。
- 客户端 dispatch ID。
- 提交前 textarea 值及其来源。
- 页面 thread ID 与 request thread ID 的边界日志。

建议后续增加不记录全文的诊断字段：

```text
client_dispatch_id
submission_source
display_thread_id
request_thread_id
input_sha256
input_length
submitted_at
```

这个未知来源不应写成当前消息排序 Issue 的根因，否则会降低 Issue 的可复现性和可处理性。

## 8. 是否应该向上游提交 Issue

提交前先检查配套上游研究：[`docs/research/deerflow-live-run-message-ordering-upstream.md`](research/deerflow-live-run-message-ordering-upstream.md)。

判断规则：

- 如果关联修复已经合并到官方 `main`，并且最新 `main` 无法复现：不要提交新的 upstream bug；应在本地 fork 创建“回移关联 PR”的维护任务。
- 如果官方已有 open Issue/PR：在原 Issue 补充本次稳定复现、DOM 顺序和最小测试，不要重复创建。
- 只有在官方最新 `main` 仍可复现，或现有修复没有覆盖实时 refetch 时，才提交下面的 Issue 草稿。

## 9. Upstream Issue Draft

### Title

```text
[bug] New runs are prepended to loaded thread history, reversing live conversation order
```

### Before you start

- [x] I searched existing issues and pull requests for message ordering, reversed history, run history, newest-first, prepend/append, `useThreadHistory`, and issue `#3352`.
- [ ] I can reproduce this on the latest `main`.

> Do not check the second item unless the bug is reproduced after updating to the latest official `main`.

### Problem summary

```text
When multiple runs complete while a chat page stays open, each newly discovered run is prepended to the already loaded messages, so the conversation is displayed in reverse run order until a reload.
```

### Affected area(s)

```text
Frontend (UI / Next.js)
Backend API (gateway / endpoints / SSE)
```

### What happened?

```text
The backend persists runs in the correct chronological order and lists runs newest-first. The frontend initially relies on that newest-first ordering to backfill older history by prepending each fetched run.

However, the same merge path is also used after a run completes and the runs query is invalidated. A newly completed run becomes the first unloaded entry in the newest-first run list, but its messages are still prepended:

setMessageRows((prev) =>
  dedupeRunMessagesByIdentity([...newMessages, ...prev]),
);

With three runs, the visible order evolves as follows:

[R1]
[R2, R1]
[R3, R2, R1]

The persisted order remains R1 -> R2 -> R3. In a reproduced browser session, the three user bubbles had vertical positions 1966, 352, and 84 in expected chronological order, confirming that the DOM rendered them in reverse.
```

### Expected behavior

```text
Runs completed in the same thread should remain in chronological display order while the page stays open. Adding R2 after R1 must produce [R1, R2]. Reloading the page must not change the relative order.
```

### Steps to reproduce

```text
1. Start DeerFlow locally with `make dev`.
2. Open a new chat.
3. Submit a first prompt and wait until the run completes.
4. Without refreshing or navigating away, submit a second prompt and wait until it completes.
5. Submit a third prompt and wait until it completes.
6. Inspect the visible user-message order.

Actual: third prompt -> second prompt -> first prompt.
Expected: first prompt -> second prompt -> third prompt.
```

### Relevant logs

```shell
Backend persisted run inputs, ordered by created_at ascending:
08:42:10 first prompt
08:45:56 second prompt
08:47:02 third prompt

Browser DOM top positions in the same expected order:
first prompt  top=1966
second prompt top=352
third prompt  top=84

verdict: FAIL
```

### How are you running DeerFlow?

```text
Local (make dev)
```

### Operating system

```text
macOS
```

### Platform details

```text
arm64, zsh, macOS 26.5.2
```

### Versions observed locally

```text
Frontend package: 2.1.0
Node.js: v25.7.0
pnpm: 10.30.3
Python: 3.14.3
uv: 0.10.8
```

### Git state

```text
branch: main
upstream baseline: c0b917c
local head: cfcfa741f71248597a1a78708db1305d84ee65dd
```

### Additional context

```text
The existing unit test covers initial loading from a newest-first run list and verifies that repeatedly prepending older runs yields chronological history. It does not cover a newer run arriving after existing history is already loaded.

A robust fix is to use thread-global message sequence pagination rather than merging independent run histories by arrival direction. A smaller fix is to sort deduplicated RunMessage rows by the thread-global `seq`, with a legacy fallback for rows without `seq`.
```

## 10. 提交前检查

- 删除或匿名化真实业务问题、场站名称、账号 ID、thread ID 和 API 返回数据。
- 使用 `first prompt / second prompt / third prompt` 代替真实业务文本。
- 不要上传 `.env`、cookie、认证信息、完整数据库或 `memory.json`。
- 截图中若含业务指标、用户名或内部路径，先打码。
- 先在官方最新 `main` 上运行最小复现；若已修复，改为本地 backport 任务。
- 如果提交 upstream Issue，附上关联 Issue/PR 链接，避免维护者重复定位。
