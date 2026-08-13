# DeerFlow 配置中台准入与分级清单

本文回答的不是“哪些字段技术上可以写入 `config.yaml`”，而是“哪些字段应该进入配置
中台、由谁修改、多久修改一次，以及通过什么方式生效”。完整字段定义见
[config.yaml 完整字段参考](config-yaml-reference.zh-CN.md)，运行时边界见
[配置文件与热加载边界](configuration-reload-matrix.zh-CN.md)，发布执行方式见
[配置中台发布通道](config-center-release-channels.zh-CN.md)。

## 1. 核心结论

DeerFlow 的所有已知字段都可以进入配置目录，但不能全部成为普通用户可编辑项。配置定义
必须同时保存“管理方式”和“生效方式”，不能只保存一个 `hot_reload` 布尔值。

推荐的管理方式：

| `manage_mode` | 含义 | 默认权限 |
| --- | --- | --- |
| `DAILY_MANAGED` | 高频、低风险的运行策略，可直接编辑和发布 | 业务运维/应用管理员 |
| `CONTROLLED_MANAGED` | 中低频或涉及安全、外部集成，需要审批 | 平台管理员/安全管理员 |
| `DEPLOYMENT_MANAGED` | 进程、存储或部署拓扑配置，只能通过部署流程修改 | SRE/平台管理员 |
| `READ_ONLY` | 只在目录中展示；不允许配置中台直接修改 | 无写权限 |

生效方式仍使用：

- `NEXT_RUN`
- `CACHE_INVALIDATE`
- `ROLLING_RESTART`
- `MANUAL_MIGRATION`
- `METADATA`

例如 `llm_call.max_concurrent_calls` 的管理方式是 `DEPLOYMENT_MANAGED`，生效方式是
`ROLLING_RESTART`；`extensions.skills.<name>.enabled` 的管理方式是
`CONTROLLED_MANAGED`，生效方式是 `CACHE_INVALIDATE`。两者不是同一个维度。

## 2. 一期直接开放的配置

以下字段进入一期普通配置页面，默认 `DAILY_MANAGED`。除特别说明外均通过
`NEXT_RUN` 生效。

### 2.1 成本与运行限制

| 字段范围 | 说明 |
| --- | --- |
| `token_usage.enabled` | Token 用量采集开关 |
| `token_budget.*` | Lead Agent 单次 run 的 Token 预算 |
| `max_recursion_limit` | LangGraph 最大递归上限 |
| `subagents.timeout_seconds` | 子 Agent 默认超时 |
| `subagents.max_turns` | 子 Agent 默认最大 turn 数 |
| `subagents.max_total_per_run` | 单次 lead run 的子 Agent 总数限制 |
| `subagents.token_budget.*` | 子 Agent 全局 Token 预算 |
| `subagents.agents.<name>.timeout_seconds` | 内置子 Agent 超时覆盖 |
| `subagents.agents.<name>.max_turns` | 内置子 Agent turn 覆盖 |
| `subagents.agents.<name>.model` | 内置子 Agent 模型选择 |
| `subagents.agents.<name>.skills` | 内置子 Agent Skill allowlist |
| `subagents.agents.<name>.token_budget.*` | 单个子 Agent Token 预算覆盖 |

### 2.2 Agent 体验与上下文控制

| 字段范围 | 说明 |
| --- | --- |
| `title.*` | 标题生成开关、长度、模型和 Prompt |
| `summarization.*` | 总结开关、触发条件、保留策略和 Prompt |
| `input_polish.*` | 输入润色开关、长度和模型 |
| `suggestions.enabled` | 后续问题建议 |
| `memory.enabled` | Memory 总开关 |
| `memory.injection_enabled` | 是否向上下文注入 Memory |
| `knowledge_base.enabled` | 知识库检索总开关 |
| `knowledge_base.lightrag.timeout_seconds` | LightRAG 普通请求超时 |
| `knowledge_base.lightrag.query_timeout_seconds` | LightRAG 查询超时 |

### 2.3 保护与容错策略

| 字段范围 | 说明 |
| --- | --- |
| `circuit_breaker.*` | LLM 熔断阈值和恢复时间 |
| `llm_call.retry_max_attempts` | LLM 最大尝试次数 |
| `llm_call.retry_base_delay_ms` | 普通退避基数 |
| `llm_call.retry_cap_delay_ms` | 单次退避上限 |
| `llm_call.burst_retry_base_delay_ms` | burst-rate 429 退避基数 |
| `loop_detection.*` | Agent/工具循环检测；包含单工具阈值覆盖 |
| `tool_progress.*` | 工具执行停滞检测 |
| `read_before_write.enabled` | 写文件前读取保护 |
| `safety_finish_reason.enabled` | Provider 安全终止拦截总开关 |

### 2.4 工具输出与上传限制

| 字段范围 | 说明 |
| --- | --- |
| `tool_output.enabled` | 大型工具输出保护开关 |
| `tool_output.externalize_min_chars` | 外置阈值 |
| `tool_output.preview_head_chars` | 外置摘要头部长度 |
| `tool_output.preview_tail_chars` | 外置摘要尾部长度 |
| `tool_output.fallback_max_chars` | 外置失败时最大保留长度 |
| `tool_output.fallback_head_chars` | 回退摘要头部长度 |
| `tool_output.fallback_tail_chars` | 回退摘要尾部长度 |
| `tool_output.exempt_tools` | 免外置工具名单 |
| `tool_output.tool_overrides` | 单工具阈值覆盖 |
| `tool_search.*` | 工具搜索和自动提升数量 |
| `uploads.*` | 文件数量、大小和转换策略 |

`tool_output.storage_subdir` 虽然可以热加载，但涉及文件落盘位置，一期按
`CONTROLLED_MANAGED` 处理。

## 3. 受控开放的配置

以下字段进入配置中台，但不提供无审批的直接发布。默认管理方式为
`CONTROLLED_MANAGED`。

### 3.1 模型目录

允许修改：

- `models[].name`
- `models[].display_name`
- `models[].description`
- `models[].model`
- `models[].supports_thinking`
- `models[].supports_reasoning_effort`
- `models[].supports_vision`
- `models[].use_responses_api`
- `models[].output_version`
- `models[].stream_chunk_timeout`
- `models[].when_thinking_enabled`
- `models[].when_thinking_disabled`
- `models[].thinking`
- 经过 provider schema 校验的 endpoint、timeout、重试、采样、Token 和 pricing 参数

限制：

- `models[].use` 不允许在普通表单修改；它决定要实例化的 Python 类。
- `api_key` 等敏感值只能保存 Secret 引用。
- 新增或删除模型必须校验所有引用该模型的 Agent、标题、总结和 Memory 配置。

模型目录通过 `NEXT_RUN` 生效，但生产环境默认需要审批。

### 3.2 MCP、Skill 与 Extension

| 字段范围 | 生效方式 | 约束 |
| --- | --- | --- |
| `extensions.mcpServers.*` | `CACHE_INVALIDATE` | URL、headers、OAuth、routing 和工具覆盖需要 schema 校验 |
| `extensions.skills.<name>.enabled` | `CACHE_INVALIDATE` | 允许启停 Skill |
| `skills.deferred_discovery` | `NEXT_RUN` | 修改 Skill 发现方式 |
| `skill_scan.enabled` | `NEXT_RUN` | 关闭安全扫描需要安全审批 |
| `skill_evolution.*` | `NEXT_RUN` | 开启写 Skill 能力需要安全审批 |

以下字段不进入普通编辑器：

- `extensions.middlewares[]`
- MCP `command` 和任意未审核本地进程参数
- `skills.use`
- `skills.path`
- `skills.container_path`

### 3.3 自定义 Agent

以下字段受控开放：

- `subagents.custom_agents.<name>.description`
- `subagents.custom_agents.<name>.system_prompt`
- `subagents.custom_agents.<name>.tools`
- `subagents.custom_agents.<name>.disallowed_tools`
- `subagents.custom_agents.<name>.skills`
- `subagents.custom_agents.<name>.model`
- `subagents.custom_agents.<name>.max_turns`
- `subagents.custom_agents.<name>.timeout_seconds`
- `acp_agents` 中已有 Agent 的模型、超时和权限审批策略

新增 ACP `command`、`args` 或进程环境变量属于代码/进程执行边界，需要平台管理员审批。

### 3.4 安全、认证和外部连接

| 字段范围 | 管理要求 |
| --- | --- |
| `guardrails.*` | 安全管理员审批；`provider.use` 不允许普通修改 |
| `authorization.*` | 安全管理员审批；必须保持 fail-closed 策略可审计 |
| `auth.local.allow_registration` | 安全管理员审批 |
| `auth.oidc.*` | Secret 引用、回调地址和域名白名单校验 |
| `knowledge_base.lightrag.base_url` | 连通性检查后发布 |
| `knowledge_base.lightrag.api_key` | 只保存 Secret 引用 |
| `channels.*` 的会话策略和 allowlist | 受控修改；随 Channel 重启生效 |
| `channels.*` 的凭据 | 只保存 Secret 引用；随 Channel 重启生效 |
| `channel_connections.*` | 受控修改；Gateway 滚动重启 |

## 4. 只通过部署流程管理的配置

以下配置默认 `DEPLOYMENT_MANAGED`，不出现在普通配置编辑页，只出现在“部署配置”视图。

| 字段范围 | 生效方式 | 主要风险 |
| --- | --- | --- |
| `log_level`、`logging.*` | `ROLLING_RESTART` | 启动时安装日志 handler/middleware |
| `database.*` | `ROLLING_RESTART` + `MANUAL_MIGRATION` | 数据库、连接池和 schema 一致性 |
| `checkpointer.*` | `ROLLING_RESTART` + `MANUAL_MIGRATION` | checkpoint 兼容和历史状态 |
| `run_events.*` | `ROLLING_RESTART` + `MANUAL_MIGRATION` | event store 切换和历史查询 |
| `agent_storage.*` | `ROLLING_RESTART` + `MANUAL_MIGRATION` | Agent 定义迁移 |
| `stream_bridge.*` | `ROLLING_RESTART` | Redis/SSE 拓扑 |
| `sandbox.*` | `ROLLING_RESTART` | Provider、镜像、挂载和进程级实例 |
| `scheduler.*` | `ROLLING_RESTART` | 后台 poller 和并发控制 |
| `run_ownership.*` | `ROLLING_RESTART` | 多 worker lease 和时钟一致性 |
| `llm_call.max_concurrent_calls` | `ROLLING_RESTART` | 进程级 limiter |
| `memory.manager_class` | `ROLLING_RESTART` | MemoryManager 单例 |
| `memory.mode` | `ROLLING_RESTART` | 中台按保守边界处理 Memory 集成模式变化 |
| `memory.backend_config.*` | `ROLLING_RESTART`；存储路径变化加 `MANUAL_MIGRATION` | Memory 存储、Prompt、模型和后台实例 |
| `skills.use/path/container_path` | `ROLLING_RESTART` | SkillStorage 和挂载拓扑 |

配置中台可以保存这些字段的期望状态和 revision，但发布按钮必须进入部署编排器，而不是
直接修改运行中实例的文件。

## 5. 默认只读的配置

以下配置默认 `READ_ONLY`：

| 字段/模式 | 原因 |
| --- | --- |
| `config_version` | 模板升级元数据，不是运行策略 |
| 所有未审核的 `use` 类路径 | 可以加载并执行任意 Python 代码 |
| `extensions.middlewares[]` | 可以向 Agent 链注入任意代码 |
| 未审核的 MCP `command` | 可以启动本地进程 |
| `sandbox.allow_host_bash` | 允许工具直接执行宿主机命令 |
| `sandbox.mounts.*` | 涉及宿主文件系统暴露 |
| `memory.backend_config.storage_class` | 可以加载自定义存储实现 |
| 未注册 provider 的开放字段 | 缺少类型和安全校验 |
| 明文 API key、token、password、client secret | 必须使用 Secret 引用 |

平台管理员可以通过代码发布或受控部署模板改变这些值，但普通配置 API 必须拒绝修改。

## 6. 配置定义必须保存的元数据

每个 `ConfigurationDefinition` 至少包含：

| 字段 | 说明 |
| --- | --- |
| `system_code` | 目标系统，例如 `deerflow` |
| `path` | 规范字段路径 |
| `value_type` | 类型、数组/对象结构和可空性 |
| `default_value` | 代码默认值和模板覆盖值 |
| `description` | 面向操作者的说明 |
| `schema_owner` | `deerflow`、`provider:<class>` 或 `channel:<provider>` |
| `manage_mode` | 四种管理方式之一 |
| `change_frequency` | `HIGH`、`MEDIUM`、`LOW` |
| `risk_level` | `LOW`、`MEDIUM`、`HIGH`、`CRITICAL` |
| `apply_channel` | 实际生效方式 |
| `target_service` | Gateway、Channel、部署控制器等 |
| `approval_policy` | 是否审批以及需要的角色 |
| `secret` | 是否只允许 Secret 引用 |
| `validation_schema` | 枚举、范围、条件必填和跨字段约束 |
| `dependencies` | 其他字段或被引用资源 |

## 7. 一期范围

一期建议：

1. 全量导入 44 个已知顶层入口和正式 schema 字段，形成只读配置目录。
2. 只开放第 2 节的 `DAILY_MANAGED` 字段直接编辑和发布。
3. 开放模型、MCP、Skill、子 Agent 和知识库的受控发布，但生产环境必须审批。
4. `DEPLOYMENT_MANAGED` 先提供展示、差异和发布计划，不自动执行迁移。
5. `READ_ONLY` 字段在后端 API 层拒绝修改，不能只靠前端隐藏。
6. 所有敏感值只保存 Secret 引用，任何 revision、日志和 diff 都不得出现明文。

一期完成的判断标准不是“能保存一段 YAML”，而是能够证明：字段被正确分级、发布通道
正确、权限不可绕过、Secret 不泄漏、revision 可追踪，并且目标实例报告一致的
`active_revision`。
