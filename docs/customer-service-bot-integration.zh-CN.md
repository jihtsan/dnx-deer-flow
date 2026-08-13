# 客服 Bot 默认账户、会话接入与系统内嵌方案

本文基于当前 DeerFlow 仓库实现，说明如何为客服 Bot 准备专用账户和专用
Agent，如何创建、继续和定位会话，以及如何从 DeerFlow 页面逐步演进到企业系统
内的服务端接入。

本文中的“默认账户”指部署方为客服 Bot 准备的 DeerFlow 专用普通账户，不是
DeerFlow 启动时自动创建的系统账户。当前实现不会自动创建任何用户；首次启动
必须先由操作员创建管理员。

## 1. 结论与推荐架构

推荐采用“企业后端 BFF + DeerFlow Gateway + 可替换聊天 UI”的边界：

```text
终端客户浏览器
    |
    | 企业登录态、业务会话 ID
    v
企业系统 / BFF
    |-- 拥有：客户身份、租户、客服权限、业务会话映射、幂等和审计
    |
    | HTTPS + SSE（服务端凭据）
    v
DeerFlow Gateway
    |-- 拥有：DeerFlow 用户、Custom Agent、Skill、Thread、Run、Checkpoint
    |         AI 记忆、上传文件和产物
    v
模型、MCP、Sandbox、知识库
```

核心原则如下：

- 企业后端是终端客户身份和业务会话的唯一所有者。
- DeerFlow 是 AI 会话运行时，不拥有企业客户主数据，也不直接写企业业务库。
- 企业后端只能通过 DeerFlow API 操作 AI 会话，不能直接写 DeerFlow 数据库。
- 浏览器不持有客服专用账户的 DeerFlow Cookie、密码或内部认证令牌。
- 企业后端持久化
  `(tenant_id, external_user_id, business_conversation_id) -> thread_id` 映射。
- 无论先使用 DeerFlow 页面还是后续自建 UI，都复用同一个 `thread_id`，无需迁移
  对话历史。

一期可以通过同源反向代理复用 DeerFlow 整页；目标形态应是企业 BFF 代理
Gateway 的 SSE，自建面向客户的最小聊天 UI。

## 2. 能力状态总览

| 能力                         | 当前状态         | 说明                                         |
| ---------------------------- | ---------------- | -------------------------------------------- |
| 首次管理员初始化             | 已有             | 仅在没有管理员时可执行                       |
| 普通用户自助注册             | 已有、可关闭     | 只能创建 `user`，不是服务账户管理面          |
| 自动创建默认/客服账户        | 未实现           | 启动不会创建任何用户                         |
| Cookie 登录及 CSRF           | 已有             | 面向浏览器会话，不是正式 S2S 认证            |
| Custom Agent 与 Skill 白名单 | 已有             | Agent、Custom Skill 均按 DeerFlow 用户隔离   |
| Thread 创建、搜索、历史      | 已有             | Thread 创建支持客户端指定 ID，并具有幂等语义 |
| Run 创建、SSE、重连、取消    | 已有             | 支持 LangGraph 兼容协议                      |
| 企业后端正式 S2S 凭据        | 未实现           | 需要 client credential 或签名 token 契约     |
| 业务会话到 Thread 的映射     | 需在企业后端新增 | 不应放在 DeerFlow metadata 中作为唯一事实源  |
| Run 业务幂等键               | 未实现           | Thread 创建幂等不等于消息发送幂等            |
| 每终端客户独立长期记忆       | 需确认/新增      | 共享 DeerFlow 用户会共享该用户的记忆边界     |

## 3. 入口与认证边界

### 3.1 API 入口

Gateway 原生路由位于 `/api/*`。统一 Nginx 入口将
`/api/langgraph/*` 重写为 Gateway 的 `/api/*`，并关闭 SSE 响应缓冲。现有前端
LangGraph SDK 默认连接同源 `/api/langgraph`。

因此：

- 企业 BFF 直连 Gateway 时使用 `http://gateway:8001/api/*`。
- 企业 BFF 通过统一 Nginx 时可使用 `https://host/api/*`。
- LangGraph SDK 客户端通过统一 Nginx 时使用
  `https://host/api/langgraph` 作为 base URL。
- 不应同时在业务代码中混用两套 base URL；由部署配置决定入口。

代码依据：

- `docker/nginx/nginx.local.conf:50-87`
- `frontend/src/core/config/index.ts:21-39`
- `backend/app/gateway/app.py:493-563`

### 3.2 当前外部认证

所有非公开路径都由 `AuthMiddleware` fail-closed。普通外部调用的认证凭据是
`access_token` HttpOnly Cookie，不是响应体中的 Bearer token 或 API Key。

登录、注册或初始化成功后，Gateway 同时设置：

- `access_token`：HttpOnly，`SameSite=Lax`；
- `csrf_token`：允许 JavaScript 读取，`SameSite=Strict`。

所有 `POST`、`PUT`、`PATCH`、`DELETE` 请求除携带这两个 Cookie 外，还必须把
`csrf_token` 原样放入 `X-CSRF-Token` 请求头。缺失或不一致时返回 `403`。

跨源浏览器客户端还必须在 Gateway 配置明确的 `GATEWAY_CORS_ORIGINS`，并使用
`credentials: include`。统一 Nginx 的同源部署不需要 CORS。

代码依据：

- `backend/app/gateway/auth_middleware.py:31-82`
- `backend/app/gateway/auth_middleware.py:88-159`
- `backend/app/gateway/auth/session_cookie.py:65-110`
- `backend/app/gateway/csrf_middleware.py:36-56`
- `backend/app/gateway/csrf_middleware.py:199-254`
- `backend/app/gateway/app.py:464-481`

### 3.3 内部认证不是公开集成接口

Gateway 还存在 `X-DeerFlow-Internal-Token` 和内部 owner header，用于 IM channel、
scheduler 等受信任的进程内或服务内调用。该令牌赋予内部系统身份，并允许服务端
传递真实 owner，属于 DeerFlow 内部信任边界。

不得把该令牌：

- 下发到终端浏览器；
- 作为企业 BFF 的通用 API Key；
- 写入 Thread metadata、日志或前端配置；
- 用来绕过正式的服务账户与最小权限设计。

代码依据：

- `backend/app/gateway/internal_auth.py:1-85`
- `backend/app/gateway/auth_middleware.py:92-112`
- `backend/app/gateway/services.py:324-389`

## 4. 账户初始化与客服专用账户

### 4.1 首次创建管理员

先检查系统是否已经初始化：

```http
GET /api/v1/auth/setup-status
```

响应示例：

```json
{
  "needs_setup": true,
  "registration_enabled": true
}
```

当且仅当 `needs_setup=true` 时创建首个管理员：

```http
POST /api/v1/auth/initialize
Content-Type: application/json
```

```json
{
  "email": "admin@example.com",
  "password": "a-strong-password",
  "remember_me": true
}
```

| 参数          | 必填 | 约束                                       |
| ------------- | ---- | ------------------------------------------ |
| `email`       | 是   | 合法电子邮箱                               |
| `password`    | 是   | 至少 8 位，且不能命中内置常见密码列表      |
| `remember_me` | 否   | 默认 `true`，决定持久 Cookie 或会话 Cookie |

系统已有管理员时，`POST /initialize` 返回 `409`。Gateway 启动钩子只提示访问
`/setup`，不会自动创建账户。

代码依据：

- `backend/app/gateway/app.py:66-112`
- `backend/app/gateway/routers/auth.py:464-565`

### 4.2 创建客服专用普通账户

当前唯一的普通本地账户创建接口是：

```http
POST /api/v1/auth/register
Content-Type: application/json
```

```json
{
  "email": "customer-service-bot@example.com",
  "password": "another-strong-password",
  "remember_me": true
}
```

参数约束与初始化接口一致。该接口总是创建 `system_role=user`，并在成功后直接
建立 Cookie 会话。配置 `auth.local.allow_registration=false` 时接口返回 `403`。

需要明确：

- 这是自助注册接口，不是管理员创建、停用、轮换服务账户的管理接口。
- 生产部署若关闭自助注册，应在部署初始化阶段受控创建一次客服账户，或先新增
  正式的管理员服务账户 API。
- 不建议把首个管理员本身当作客服 Bot 账户。

代码依据：

- `backend/app/gateway/routers/auth.py:318-369`
- `backend/packages/harness/deerflow/config/auth_config.py:73-87`

### 4.3 登录客服账户

```http
POST /api/v1/auth/login/local
Content-Type: application/x-www-form-urlencoded
```

```text
username=customer-service-bot%40example.com&password=...&remember_me=true
```

| 参数          | 必填 | 说明                    |
| ------------- | ---- | ----------------------- |
| `username`    | 是   | 实际值是账户邮箱        |
| `password`    | 是   | 账户密码                |
| `remember_me` | 否   | 表单布尔值，默认 `true` |

响应体只包含 `expires_in` 和 `needs_setup`。访问 token 只存在于
`Set-Cookie: access_token=...`，不能从 JSON 响应读取。可用以下接口确认当前会话：

```http
GET /api/v1/auth/me
Cookie: access_token=...
```

代码依据：

- `backend/app/gateway/routers/auth.py:288-315`
- `backend/app/gateway/routers/auth.py:440-450`

## 5. 创建客服 Custom Agent

客服账户登录后，可在 `agents_api.enabled=true` 时创建专用 Agent：

```http
POST /api/agents
Content-Type: application/json
Cookie: access_token=...; csrf_token=...
X-CSRF-Token: <csrf_token cookie value>
```

```json
{
  "name": "customer-service",
  "description": "面向客户的产品咨询 Agent",
  "model": "configured-model-name",
  "tool_groups": ["search"],
  "skills": ["product-support", "return-policy"],
  "thinking_enabled": false,
  "soul": "只回答已授权的产品与售后问题；无法确认时转人工。"
}
```

| 参数               | 必填 | 说明                                                             |
| ------------------ | ---- | ---------------------------------------------------------------- |
| `name`             | 是   | 匹配 `^[A-Za-z0-9-]+$`，保存时转小写                             |
| `description`      | 否   | Agent 描述                                                       |
| `model`            | 否   | 必须是已配置模型                                                 |
| `tool_groups`      | 否   | 工具组白名单                                                     |
| `skills`           | 否   | `null`/省略表示全部启用 Skill，`[]` 表示禁用全部，列表表示白名单 |
| `model_settings`   | 否   | 当前支持温度和最大输出 Token 等覆盖                              |
| `thinking_enabled` | 否   | Agent 默认 thinking 模式                                         |
| `reasoning_effort` | 否   | `low`、`medium` 或 `high`                                        |
| `soul`             | 否   | `SOUL.md` 内容，建议生产场景显式提供                             |

客服场景必须显式配置 `skills` 和 `tool_groups` 白名单。省略 `skills` 会加载所有
已启用 Skill，不符合最小权限原则。

Custom Agent 由当前 DeerFlow 用户拥有。后续 Run 的 `assistant_id` 传
`customer-service` 时，Gateway 将其规范化为 `agent_name`，仍使用 lead agent 图，
但会加载匹配 Agent 的 `config.yaml`、`SOUL.md` 和 Skill 白名单。

代码依据：

- `backend/app/gateway/routers/agents.py:58-82`
- `backend/app/gateway/routers/agents.py:294-347`
- `backend/packages/harness/deerflow/config/agents_config.py:191-216`
- `backend/app/gateway/services.py:392-403`
- `backend/app/gateway/services.py:441-559`

## 6. Thread 与业务会话映射

### 6.1 映射模型

企业数据库应保存类似以下记录：

| 字段                       | 所有者             | 说明                    |
| -------------------------- | ------------------ | ----------------------- |
| `tenant_id`                | 企业系统           | 租户 ID                 |
| `external_user_id`         | 企业系统           | 终端客户 ID             |
| `business_conversation_id` | 企业系统           | 工单、咨询或页面会话 ID |
| `deerflow_thread_id`       | 企业系统映射       | DeerFlow Thread UUID    |
| `assistant_id`             | 企业系统 allowlist | 固定客服 Agent 名       |
| `created_at`、`closed_at`  | 企业系统           | 生命周期和审计          |

建议对 `(tenant_id, external_user_id, business_conversation_id)` 建唯一约束。不要只
把这些字段写进 Thread metadata 后再反向搜索；metadata 可以辅助检索，但不是企业
业务映射的唯一事实源。

产品必须先选择 Thread 粒度：

- 每个客户一个长期 Thread：跨工单上下文连续，但上下文增长和删除边界较差；
- 每个工单/咨询一个 Thread：推荐，隔离、审计和关闭语义清晰；
- 每次打开页面一个 Thread：最简单，但无法天然跨设备续聊。

### 6.2 显式创建 Thread

```http
POST /api/threads
Content-Type: application/json
Cookie: access_token=...; csrf_token=...
X-CSRF-Token: <csrf_token cookie value>
```

```json
{
  "thread_id": "9f44520f-5d49-42d6-85b8-3a42ce01f8d1",
  "assistant_id": "customer-service",
  "metadata": {
    "tenant_ref": "tenant-hash-or-opaque-id",
    "conversation_ref": "opaque-business-reference",
    "agent_name": "customer-service"
  }
}
```

| 参数           | API 必填 | 集成建议                                                 |
| -------------- | -------- | -------------------------------------------------------- |
| `thread_id`    | 否       | 建议由企业后端生成 UUID 并显式传入，便于幂等             |
| `assistant_id` | 否       | 客服场景应固定传专用 Agent 名                            |
| `metadata`     | 否       | 只放非敏感、可公开给该账户的引用，不放密钥和完整客户资料 |

响应包含 `thread_id`、`status`、`created_at`、`updated_at`、`metadata`、`values`
和 `interrupts`。同一 owner 再次用相同 `thread_id` 创建时返回现有记录，具有幂等
语义。

服务端会剥离保留 metadata 字段，调用方不能通过 metadata 伪造 owner。

代码依据：

- `backend/app/gateway/routers/threads.py:94-98`
- `backend/app/gateway/routers/threads.py:262-281`
- `backend/app/gateway/routers/threads.py:596-670`

### 6.3 隐式创建 Thread

`POST /api/threads/{thread_id}/runs/stream` 的 Run 请求默认
`if_not_exists=create`，因此可以不先调用 `POST /api/threads`。Run 启动时会补建
Thread metadata 记录。

生产集成仍建议显式创建：这样企业后端可以先提交映射事务、确认 owner 和
`assistant_id`，再发送首条消息，失败恢复更清楚。

代码依据：

- `backend/app/gateway/routers/thread_runs.py:71-91`
- `backend/app/gateway/services.py:972-992`

## 7. 发送消息与流式响应

### 7.1 创建并流式执行 Run

```http
POST /api/threads/{thread_id}/runs/stream
Content-Type: application/json
Accept: text/event-stream
Cookie: access_token=...; csrf_token=...
X-CSRF-Token: <csrf_token cookie value>
```

```json
{
  "assistant_id": "customer-service",
  "input": {
    "messages": [
      {
        "type": "human",
        "content": "我的订单什么时候发货？"
      }
    ]
  },
  "metadata": {
    "business_request_id": "opaque-request-id"
  },
  "stream_mode": ["values", "messages-tuple"],
  "stream_subgraphs": false,
  "stream_resumable": true,
  "on_disconnect": "continue",
  "multitask_strategy": "reject",
  "if_not_exists": "create"
}
```

最低业务输入是：

- URL 中的 `thread_id`；
- `assistant_id=customer-service`；
- `input.messages` 中至少一条 human 消息。

重要可选参数：

| 参数                 | 默认值       | 建议                                                                      |
| -------------------- | ------------ | ------------------------------------------------------------------------- |
| `stream_mode`        | 运行时规范化 | 显式传 `values` 和 `messages-tuple`                                       |
| `stream_subgraphs`   | `false`      | 客服 UI 通常无需子图事件                                                  |
| `stream_resumable`   | `null`       | LangGraph 兼容字段；现有前端传 `true`，但服务端重放能力不由该字段单独开启 |
| `on_disconnect`      | `cancel`     | BFF 代理建议传 `continue`；直接浏览器可按产品选择                         |
| `multitask_strategy` | `reject`     | 推荐保留，避免同一 Thread 并发回答                                        |
| `if_not_exists`      | `create`     | 已显式创建时仍可保留                                                      |
| `context`            | `null`       | 只传允许的模型/运行选项，不传身份和内部字段                               |
| `config`             | `null`       | 高级 RunnableConfig；不要在其中放业务密钥                                 |

响应媒体类型是 `text/event-stream`，并通过 `Content-Location` 返回规范 Run 资源：

```text
/api/threads/{thread_id}/runs/{run_id}
```

客户端至少处理：

- `messages-tuple`：AI 文本增量、工具调用和工具结果；
- `values`：节点完成后的完整状态快照；
- 流结束和错误事件；
- HTTP `401`、`403`、`404`、`409`、`422` 和 `5xx`。

代码依据：

- `backend/app/gateway/routers/thread_runs.py:71-130`
- `backend/app/gateway/routers/thread_runs.py:486-519`
- `backend/app/gateway/services.py:885-1045`
- `backend/packages/harness/deerflow/client.py:687-753`

### 7.2 继续已有会话

继续会话不需要单独的“续聊”接口。对相同 `thread_id` 再次调用
`POST /api/threads/{thread_id}/runs/stream` 并提交新 human 消息即可。Checkpoint
会为同一 Thread 保存多轮状态。

每次请求都应继续由 BFF 固定 `assistant_id`，不要允许浏览器任意切换 Agent。即使
Thread 创建时已经记录 `assistant_id`，显式传入也能让调用契约和审计更清晰。

### 7.3 同步等待与后台 Run

除 SSE 外还存在：

- `POST /api/threads/{thread_id}/runs`：立即返回 `RunResponse`，后台执行；
- `POST /api/threads/{thread_id}/runs/wait`：等待完成并返回最终状态；
- `POST /api/runs/stream` 和 `POST /api/runs/wait`：Thread ID 位于请求体或配置的
  无状态兼容入口。

客服实时聊天优先使用 Thread 下的 `/runs/stream`。后台批处理才使用创建后轮询，
简单的内部同步调用才考虑 `/wait`。

代码依据：

- `backend/app/gateway/routers/thread_runs.py:486-548`
- `backend/app/gateway/routers/runs.py:24-97`

## 8. Run 查询、取消与断线恢复

| 用途             | 方法与路径                                           | 说明                          |
| ---------------- | ---------------------------------------------------- | ----------------------------- |
| 查询 Run         | `GET /api/threads/{thread_id}/runs/{run_id}`         | 返回状态、时间和 Token 用量   |
| 列出 Thread Runs | `GET /api/threads/{thread_id}/runs`                  | 返回该 owner 下的 Runs        |
| 加入现有 SSE     | `GET /api/threads/{thread_id}/runs/{run_id}/join`    | 继续消费现有流                |
| SDK 兼容重连     | `GET /api/threads/{thread_id}/runs/{run_id}/stream`  | 加入现有流                    |
| 取消             | `POST /api/threads/{thread_id}/runs/{run_id}/cancel` | query 可传 `action` 和 `wait` |

取消参数：

- `action=interrupt`：停止执行，保留当前 checkpoint，默认值；
- `action=rollback`：停止并回退到 Run 前状态；
- `wait=true`：等到停止后返回 `204`；否则接受取消后返回 `202`。

恢复注意事项：

- BFF 必须在收到 `Content-Location` 后保存 `run_id`。
- 重连客户端应携带最后收到的 SSE `Last-Event-ID`；StreamBridge 据此重放仍在保留
  窗口内的事件。
- `stream_resumable=true` 当前是 LangGraph 兼容请求字段；Gateway 接受它，但真正
  的重放由 `Last-Event-ID` 和 StreamBridge 实现，不能把该布尔值当作恢复保证。
- `on_disconnect=continue` 只表示生产者不因当前 SSE 断开而取消，不等于任意拓扑都
  能跨进程恢复。
- 多 Gateway worker 部署若要跨实例重连，必须配置支持跨进程的共享
  StreamBridge；进程内 StreamBridge 不能保证在另一 worker 加入流。
- 连接结果不明确时先查询 Run 状态，不要直接重发消息。

代码依据：

- `backend/app/gateway/routers/thread_runs.py:551-690`
- `backend/packages/harness/deerflow/config/stream_bridge_config.py`

## 9. 历史、列表与页面定位

### 9.1 分页读取消息

```http
GET /api/threads/{thread_id}/messages/page?limit=50&before_seq=123
```

| 参数         | 必填 | 约束                                             |
| ------------ | ---- | ------------------------------------------------ |
| `limit`      | 否   | 默认 50，范围 1 到 200；客服 UI 应显式控制页大小 |
| `before_seq` | 否   | 读取指定序号之前的更早消息                       |

响应：

```json
{
  "data": [],
  "has_more": false,
  "next_before_seq": null
}
```

还可以使用 `GET /api/threads/{thread_id}/messages` 获取兼容格式，但新 UI 应优先
使用 `/messages/page`，避免长会话一次性加载。

代码依据：

- `backend/app/gateway/routers/thread_runs.py:706-904`

### 9.2 搜索会话

```http
POST /api/threads/search
Content-Type: application/json
```

```json
{
  "metadata": {
    "agent_name": "customer-service"
  },
  "limit": 50,
  "offset": 0,
  "status": "idle"
}
```

| 参数       | 必填 | 说明                                 |
| ---------- | ---- | ------------------------------------ |
| `metadata` | 否   | 精确匹配过滤；只能使用支持的 JSON 值 |
| `limit`    | 否   | 默认 100，范围 1 到 1000             |
| `offset`   | 否   | 默认 0                               |
| `status`   | 否   | Thread 状态过滤                      |

搜索结果由认证 owner 自动过滤。企业后端仍应先查自己的业务映射表，不应依赖跨
全量 Thread metadata 搜索来定位唯一业务会话。

代码依据：

- `backend/app/gateway/routers/threads.py:284-312`
- `backend/app/gateway/routers/threads.py:784-820`

### 9.3 页面 URL

现有前端页面路径为：

- 默认 Agent：`/workspace/chats/{thread_id}`；
- Custom Agent：`/workspace/agents/{agent_name}/chats/{thread_id}`；
- 新会话入口：把 `{thread_id}` 替换为 `new`。

`pathOfThread()` 会优先从 Thread context 或 metadata 的 `agent_name` 生成 Custom
Agent 页面 URL。因此创建客服 Thread 时建议同时保存非敏感的
`metadata.agent_name`，但实际 Run 仍应显式传 `assistant_id`。

代码依据：

- `frontend/src/core/threads/utils.ts:11-40`
- `frontend/src/components/workspace/chats/use-thread-chat.ts:28-70`
- `frontend/src/app/workspace/chats/[thread_id]/page.tsx:105-124`

## 10. 数据所有权与隔离

| 数据                          | 所有者   | 当前隔离键                               |
| ----------------------------- | -------- | ---------------------------------------- |
| 企业客户、租户、订单、工单    | 企业系统 | 企业自身 tenant/user ID                  |
| 业务会话映射、幂等、审计      | 企业后端 | tenant + external user + conversation    |
| DeerFlow 用户                 | DeerFlow | `user_id`                                |
| Thread metadata 与 checkpoint | DeerFlow | `user_id` + `thread_id`                  |
| Run 与 Run events             | DeerFlow | `user_id` + `thread_id` + `run_id`       |
| 上传、workspace、产物         | DeerFlow | `users/{user_id}/threads/{thread_id}`    |
| Custom Agent                  | DeerFlow | `users/{user_id}/agents/{agent_name}`    |
| Custom Skill                  | DeerFlow | `users/{user_id}/skills/custom`          |
| 用户长期记忆                  | DeerFlow | `users/{user_id}`，事实可再按 Agent 分桶 |
| Public Skill                  | 部署方   | 全局只读目录                             |

认证中间件把服务端解析出的用户写入 request state 和 runtime user context。Thread
和 Run owner 来自该服务端认证态；调用方在 `context`、`config` 或 metadata 中
伪造 `user_id`、`is_internal` 等字段不会取得 owner 权限。访问其他 owner 的
Thread 按 `404` 处理，避免枚举。

当前抽象权限会向已认证普通用户授予完整的已定义权限集合，资源 owner 是主要隔离
边界。因此把客服账户 Cookie 交给终端客户会同时暴露该账户下的其他 Thread 以及
Agent、Skill、Memory 等管理面，这是上线阻断风险。

代码依据：

- `backend/app/gateway/authz.py:116-150`
- `backend/app/gateway/authz.py:275-300`
- `backend/app/gateway/services.py:225-389`
- `backend/app/gateway/services.py:925-947`
- `backend/packages/harness/deerflow/persistence/thread_meta/sql.py:42-128`
- `backend/packages/harness/deerflow/persistence/run/sql.py:92-165`
- `backend/packages/harness/deerflow/config/paths.py:180-257`

## 11. 共享客服账户与终端客户身份

### 11.1 共享客服账户

所有客户请求都由一个 DeerFlow 普通用户发起，每个业务会话使用不同 Thread。

优点：

- 当前 Cookie 模型下改造最少；
- 只需维护一个 Custom Agent 和 Skill 集合；
- Thread 仍可隔离短期对话上下文和文件。

风险：

- DeerFlow 的用户级长期记忆会在这些客户之间共享，可能发生记忆串扰；
- 一个凭据泄漏会影响所有客户会话；
- 当前权限模型无法把同一 DeerFlow 用户下的 Thread 再按终端客户授权；
- 企业后端必须成为唯一访问代理，不能让客户直接调用 Gateway。

采用该模型时，至少应关闭该账户的长期记忆注入，或在上线前新增
`tenant_id/external_user_id` 记忆命名空间。

### 11.2 每客户或每租户映射 DeerFlow owner

为每个租户或终端客户创建独立 DeerFlow owner，再由正式的 S2S 代调用契约传递
owner。

优点是记忆、Custom Agent、Skill 和文件天然隔离；缺点是需要账户生命周期、批量
配置、代调用和规模治理能力，而这些目前没有完整的公共管理 API。

推荐决策：

- 一期验证可以使用共享客服账户，但关闭用户级长期记忆，并由 BFF 强制隔离；
- 正式多租户版本优先采用“每租户 owner + 每客户 Thread”；
- 只有确实需要每客户独立记忆或数据主权时，才扩大到每客户 owner。

## 12. 系统内嵌方案比较

| 方案                     | 认证与跨域                                   | 会话隔离                   | 改造成本 | 维护风险                        | 结论       |
| ------------------------ | -------------------------------------------- | -------------------------- | -------- | ------------------------------- | ---------- |
| 同源反向代理整页         | 与现有 Cookie/CSRF/SSE 最一致                | 复用 DeerFlow owner/Thread | 低       | UI 与企业系统风格、导航耦合     | 推荐一期   |
| 跨站 iframe              | 受第三方 Cookie、CSP、点击劫持保护影响       | 仍依赖 DeerFlow Cookie     | 低到中   | 浏览器兼容和 SSO 最脆弱         | 默认不推荐 |
| 复用 DeerFlow 前端组件   | 可统一到企业前端登录态，但仍需适配 Gateway   | 可由业务路由控制           | 中       | 依赖 LangGraph SDK 和内部 hooks | 适合二期   |
| 自建 UI + BFF 调 Gateway | BFF 隔离 DeerFlow 凭据，浏览器只认企业登录态 | 业务映射最清楚             | 高       | API 契约需自行维护              | 推荐目标态 |

### 12.1 同源反向代理整页

将 DeerFlow 的页面和 `/api/*` 代理到企业域名下，使浏览器看到同一 origin。优点是
现有 Cookie、CSRF 和 SSE 逻辑可以原样工作。需要额外处理：

- 企业 SSO 与 DeerFlow 登录/退出的衔接；
- 隐藏 Agent、Skill、Memory、配置等非客服导航；
- URL allowlist，只允许进入指定客服 Agent 页面；
- 不把该页面误认为最小权限 API 边界。

### 12.2 iframe

仓库当前没有面向产品集成声明稳定的 `frame-ancestors` 或 `X-Frame-Options`
契约。即使允许 frame，跨站 iframe 中 `access_token SameSite=Lax` 和
`csrf_token SameSite=Strict` 也会使认证与写请求不可靠。

只有 iframe 与 DeerFlow 部署为同站/同源，并补齐 CSP、防点击劫持、登录同步、
退出同步和浏览器 E2E 后才可采用。不要通过降低 Cookie 安全属性来换取兼容。

### 12.3 复用前端组件

可抽取 ChatBox、消息渲染和 Thread hooks，但当前实现深度依赖 LangGraph SDK、
Cookie/CSRF 和 DeerFlow Thread 缓存。需要建立稳定的组件输入契约，避免企业前端
直接依赖内部目录结构。

### 12.4 自建 UI 与 BFF

浏览器只调用企业 BFF：创建/查找业务会话、发送消息、读取历史、取消生成。BFF
将业务请求转换为 DeerFlow Thread/Run API，并将 SSE 逐事件代理给浏览器。

该方案改造成本最高，但能够集中处理租户、权限、审计、限流、敏感信息脱敏和未来
Gateway 认证升级，是推荐目标态。

## 13. 正式接入仍需新增的能力

### 13.1 服务到服务认证

当前没有面向企业后端的正式 client credential。若必须先接入，只能使用受限过渡
方案：BFF 在服务端保险库保管客服普通账户凭据，调用 `/login/local`，维护
`access_token + csrf_token` Cookie jar，并在写请求回送 `X-CSRF-Token`。

该 Cookie 不得下发浏览器，也不能作为长期架构。正式版本应新增：

- 可轮换、可撤销、有过期时间的 client credential 或签名 token；
- 固定 audience 和调用方身份；
- `threads:create/read`、`runs:create/read/cancel` 等最小 scope；
- 受审计的 owner 代调用契约；
- 速率、并发、Token 成本和模型 allowlist；
- 禁止服务凭据访问 Agent、Skill、Memory 管理接口。

### 13.2 服务账户管理

新增管理员创建、停用、删除、轮换客服服务账户的 API 或部署工具。自助
`/register` 不应承担生产服务账户生命周期。

### 13.3 Run 幂等

`POST /api/threads` 在固定 `thread_id` 下具有幂等语义，但
`POST .../runs/stream` 没有业务 `Idempotency-Key` 契约。网络超时后盲目重试可能
创建两次 Run 并产生双回复。

过渡期由 BFF：

1. 为每条客户消息生成唯一 `business_request_id`；
2. 在企业库中以唯一约束登记 pending；
3. 保存 DeerFlow `run_id`；
4. 结果不明确时先查询 Run，而不是重发；
5. 仅在确认没有创建 Run 后重试。

后续应在 Gateway 增加 `Idempotency-Key -> run_id` 的持久化去重契约。

### 13.4 最小权限与审计

当前已认证用户拥有统一权限集合。正式客服集成需要把服务主体、普通用户和管理员
权限拆分，并审计：调用方、owner、tenant、thread、run、assistant、模型、Token
用量、取消、错误和敏感工具使用。

## 14. 失败模型与恢复策略

| 情况               | 处理策略                                                      |
| ------------------ | ------------------------------------------------------------- |
| `401`              | BFF 重新建立服务会话；浏览器不得接触 DeerFlow 登录            |
| `403`              | 检查 CSRF 双提交、CORS 和 scope；不要自动降级安全策略         |
| `404`              | 当作资源不存在或不可见，不向终端客户区分 owner 越权           |
| `409`              | 同 Thread 并发、Run lease 或取消冲突；查询当前 Run 后决定     |
| `422`              | 参数错误，记录安全的结构化诊断，不重试                        |
| `429`              | 按服务端退避信息限流重试，保持相同业务幂等键                  |
| `5xx`/网络超时     | 先按 `business_request_id` 和已保存 `run_id` 对账，再决定重试 |
| SSE 断开           | 使用保存的 `thread_id/run_id` 重新加入；不要直接重复 POST     |
| Agent/Skill 不存在 | 配置错误，阻断流量并通知运维，不回退到无约束默认 Agent        |

企业 BFF 应设置独立的连接超时、首事件超时、空闲超时和单 Run 总时限；Nginx 当前
为长请求配置 600 秒，但这不是业务 SLA。

## 15. 演进路径

### 阶段 1：同源页面验证

1. 操作员初始化管理员。
2. 受控创建客服普通账户。
3. 使用客服账户创建固定 Custom Agent 和 Skill/工具白名单。
4. 关闭共享客服账户的长期记忆注入。
5. 企业域名同源反向代理指定客服页面和 API。
6. 企业系统保存业务会话到 `thread_id` 的映射。

### 阶段 2：企业 BFF 接管会话

1. 浏览器只调用企业 BFF。
2. BFF 暂时以服务端 Cookie jar 调用 Gateway。
3. BFF 代理 SSE，持久化 `run_id`，实现消息级去重和失败对账。
4. 企业前端复用或重建聊天组件，不再暴露 DeerFlow 完整工作区。

### 阶段 3：正式系统端接入

1. 上线正式 S2S 凭据、scope 和 owner 代调用契约。
2. 上线服务账户生命周期和 Run 幂等键。
3. 按租户规模选择共享 owner、每租户 owner 或每客户 owner。
4. 建立审计、限流、成本、告警和数据保留策略。
5. 删除 BFF 模拟网页登录的过渡实现。

## 16. 产品待确认项

在实施前必须确认：

1. 一个 Thread 对应客户、工单、咨询还是一次页面访问。
2. 是否需要跨设备续聊，以及由谁决定恢复哪个业务会话。
3. 是否需要人工客服接管、转派、关闭、重开、删除和导出。
4. 对话、Run event、上传文件和产物的保留期限及删除责任方。
5. 共享客服账户是否可接受；若接受，是否明确关闭长期记忆。
6. 正式环境按客户还是按租户建立 DeerFlow owner。
7. 客服 Agent 可用的 Skill、工具、模型、知识库和上传类型。
8. 是否允许执行 Sandbox/MCP 等高风险工具，如何审批和审计。
9. 一期能否使用同源整页代理，企业域名和 SSO 拓扑是什么。
10. 目标并发、SLA、单次回答时限、Token 预算和失败降级策略。

## 17. 上线检查清单

- [ ] 客服账户不是管理员账户。
- [ ] 浏览器无法读取客服账户凭据、Cookie 或内部 token。
- [ ] Custom Agent 显式配置 Skill 和工具白名单。
- [ ] BFF 固定并校验 `assistant_id`，终端客户不能任意选择 Agent。
- [ ] 企业数据库有业务会话映射唯一约束。
- [ ] 每条消息有 `business_request_id`，不盲目重试 Run 创建。
- [ ] 共享账户的长期记忆已关闭或完成客户级命名空间改造。
- [ ] Thread、Run、文件和历史访问都经过 owner 校验。
- [ ] SSE 代理不缓冲，并保存 `run_id` 以支持恢复。
- [ ] 多 worker 环境使用共享 StreamBridge，或明确限制到单 worker。
- [ ] 已验证 `401/403/404/409/422/429/5xx` 和断线恢复。
- [ ] 已完成同源代理或目标域名下的浏览器 E2E。
- [ ] 已配置限流、Token 预算、审计、告警和数据保留策略。

## 18. 验证范围

本文接口和边界已通过静态代码核验。相关聚焦测试覆盖认证中间件、内部认证、
Threads、Runs、无状态 Run owner 隔离和 Skill 路由鉴权。

本文没有声明已完成以下验证：

- 完整生产拓扑启动；
- 企业真实域名、SSO 或反向代理联调；
- iframe 浏览器兼容测试；
- 多 Gateway worker 的真实 Redis StreamBridge 故障切换；
- 尚未实现的 S2S、服务账户、Run 幂等和客户级记忆能力。

这些项目应在产品确认身份模型和部署拓扑后补充集成测试与安全评审。
