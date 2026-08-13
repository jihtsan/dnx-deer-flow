# DeerFlow `config.yaml` 完整字段参考

本文列出当前 DeerFlow 主配置文件的全部已知配置项，作为配置中台字段目录、表单设计和
发布策略的基础。字段来源以代码中的 `AppConfig` Pydantic schema 为准，并补充
`config.example.yaml` 中由兼容层或 Gateway 直接读取的字段。

## 1. 口径与发布通道

- 当前 `AppConfig` 有 **41 个正式顶层字段**。
- `config_version`、`uploads`、`channels` 不在正式 schema 内，但当前运行时明确读取，
  因此也纳入字段目录。合计 **44 个已知顶层入口**。
- `AppConfig`、`models[]`、`tools[]`、`tool_groups[]` 和 `sandbox` 允许额外字段；这类字段
  由 `use` 指向的 provider/tool 实现解释，无法形成一个永久封闭的字段全集。
- 表中默认值是代码默认值；`config.example.yaml` 可能显式覆盖，例如模板把
  `database.backend` 从代码默认的 `memory` 改为 `sqlite`。
- `$ENV_NAME` 形式的完整字符串会在加载 YAML 时从当前进程环境变量解析。

发布通道：

| 通道 | 含义 |
| --- | --- |
| `NEXT_RUN` | 无需重启；下一次请求、消息或 Agent run 使用新值 |
| `CACHE_INVALIDATE` | 下发后还要通知每个 Gateway 实例清理对应缓存；新 run 生效 |
| `ROLLING_RESTART` | 下发后滚动重启目标 Gateway；所有实例必须收敛到同一 revision |
| `MANUAL_MIGRATION` | 可能改变持久化位置/格式，需要人工迁移、校验和回滚预案 |
| `METADATA` | 只用于配置管理，不直接改变运行行为 |

除表中标出的特例外，某个嵌套字段继承所属顶层字段的发布通道。完整发布流程见
[配置中台发布通道](config-center-release-channels.zh-CN.md)，代码级热加载依据见
[配置文件与热加载边界](configuration-reload-matrix.zh-CN.md)。
哪些字段应该开放给配置中台、需要什么权限以及一期范围，见
[配置中台准入与分级清单](config-center-manageability.zh-CN.md)。

## 2. 顶层目录

| 顶层字段 | 类型 | 发布通道 | 用途 |
| --- | --- | --- | --- |
| `config_version` | integer | `METADATA` | 模板版本与升级提示 |
| `log_level` | string | `ROLLING_RESTART` | DeerFlow/Gateway 日志级别 |
| `logging` | object | `ROLLING_RESTART` | 结构化日志和 Trace ID |
| `token_usage` | object | `NEXT_RUN` | Token 用量采集 |
| `token_budget` | object | `NEXT_RUN` | 单次 run 的 Token 预算 |
| `max_recursion_limit` | integer | `NEXT_RUN` | 客户端 recursion limit 的服务端上限 |
| `models` | array | `NEXT_RUN` | 可用模型及 provider 参数 |
| `sandbox` | object | `ROLLING_RESTART` | Sandbox provider 和进程级实例参数 |
| `tools` | array | `NEXT_RUN` | 工具注册表 |
| `tool_groups` | array | `NEXT_RUN` | 工具分组 |
| `skills` | object | `NEXT_RUN`；存储/路径字段按 `ROLLING_RESTART` | Skill 存储与发现 |
| `skill_scan` | object | `NEXT_RUN` | Skill 安全扫描 |
| `skill_evolution` | object | `NEXT_RUN` | Agent 自主演进 Skill |
| `extensions` | object | `CACHE_INVALIDATE` | MCP、Skill 状态和扩展 middleware |
| `tool_output` | object | `NEXT_RUN` | 大型工具输出外置与截断 |
| `tool_search` | object | `NEXT_RUN` | 工具延迟加载和自动提升 |
| `title` | object | `NEXT_RUN` | 会话标题生成 |
| `summarization` | object | `NEXT_RUN` | 对话压缩/总结 |
| `memory` | object | `NEXT_RUN`；后端字段按 `ROLLING_RESTART` | 长期记忆 |
| `knowledge_base` | object | `NEXT_RUN` | LightRAG 知识库接入 |
| `agents_api` | object | `NEXT_RUN` | 自定义 Agent API |
| `acp_agents` | map | `NEXT_RUN` | ACP 外部 Agent |
| `subagents` | object | `NEXT_RUN` | 子 Agent 运行策略和定义 |
| `guardrails` | object | `NEXT_RUN` | 工具调用前置安全决策 |
| `authorization` | object | `NEXT_RUN` | 资源级授权 provider |
| `input_polish` | object | `NEXT_RUN` | 输入润色 |
| `suggestions` | object | `NEXT_RUN` | 后续问题建议 |
| `circuit_breaker` | object | `NEXT_RUN` | LLM 熔断器 |
| `llm_call` | object | `NEXT_RUN`；并发上限按 `ROLLING_RESTART` | LLM 并发和重试 |
| `channel_connections` | object | `ROLLING_RESTART` | 用户自助绑定 IM 通道 |
| `loop_detection` | object | `NEXT_RUN` | Agent/工具循环检测 |
| `tool_progress` | object | `NEXT_RUN` | 工具进展状态检测 |
| `read_before_write` | object | `NEXT_RUN` | 写文件前强制读取 |
| `safety_finish_reason` | object | `NEXT_RUN` | Provider 安全终止拦截 |
| `auth` | object | `NEXT_RUN` | 本地认证和 OIDC |
| `database` | object | `ROLLING_RESTART` / `MANUAL_MIGRATION` | 主数据库和连接池 |
| `run_events` | object | `ROLLING_RESTART` / `MANUAL_MIGRATION` | Run event 存储 |
| `agent_storage` | object | `ROLLING_RESTART` / `MANUAL_MIGRATION` | 自定义 Agent 定义存储 |
| `scheduler` | object | `ROLLING_RESTART` | 定时任务 worker |
| `checkpointer` | object/null | `ROLLING_RESTART` / `MANUAL_MIGRATION` | LangGraph checkpoint 存储 |
| `stream_bridge` | object/null | `ROLLING_RESTART` | SSE 事件桥接 |
| `run_ownership` | object | `ROLLING_RESTART` | 多 worker run lease |
| `uploads` | object | `NEXT_RUN` | 会话附件限制和转换策略 |
| `channels` | object | `ROLLING_RESTART` | Feishu/Slack/Telegram 等 IM 客户端 |

## 3. 基础、日志与 Token

| 字段路径 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `config_version` | integer | 模板当前为 `29` | 低于模板版本时提示执行 `make config-upgrade` |
| `log_level` | string | `info` | `debug`、`info`、`warning` 或 `error` |
| `logging.enhance.enabled` | boolean | `false` | 启用请求 Trace ID、响应头和增强日志 |
| `logging.enhance.format` | string | `text` | `text` 或 `json` |
| `token_usage.enabled` | boolean | `true` | 采集并展示模型 Token 用量 |
| `token_budget.enabled` | boolean | `false` | 启用单次 run 预算控制 |
| `token_budget.max_tokens` | integer | `200000` | 输入与输出 Token 总上限 |
| `token_budget.max_input_tokens` | integer/null | `null` | 可选输入 Token 独立上限 |
| `token_budget.max_output_tokens` | integer/null | `null` | 可选输出 Token 独立上限 |
| `token_budget.warn_threshold` | number | `0.8` | 达到预算比例后注入警告 |
| `token_budget.hard_stop_threshold` | number | `1.0` | 达到预算比例后强制结束 |
| `max_recursion_limit` | integer | `1000` | 客户端传入 recursion limit 的硬上限，最小为 1 |

## 4. 模型、工具与 Sandbox

### 4.1 `models[]`

| 字段路径 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `models[].name` | string | 必填 | DeerFlow 内部唯一模型名 |
| `models[].display_name` | string/null | `null` | UI 展示名 |
| `models[].description` | string/null | `null` | 模型说明 |
| `models[].use` | string | 必填 | 模型类路径，如 `langchain_openai:ChatOpenAI` |
| `models[].model` | string | 必填 | Provider 模型 ID |
| `models[].use_responses_api` | boolean/null | `null` | OpenAI 模型是否使用 Responses API |
| `models[].output_version` | string/null | `null` | 响应结构版本，如 `responses/v1` |
| `models[].supports_thinking` | boolean | `false` | 是否支持 thinking 模式 |
| `models[].supports_reasoning_effort` | boolean | `false` | 是否支持 reasoning effort |
| `models[].when_thinking_enabled` | object/null | `null` | thinking 打开时合并给 provider 的参数 |
| `models[].when_thinking_disabled` | object/null | `null` | thinking 关闭时合并给 provider 的参数 |
| `models[].thinking` | object/null | `null` | `when_thinking_enabled` 的快捷配置 |
| `models[].supports_vision` | boolean | `false` | 是否支持图片输入 |
| `models[].stream_chunk_timeout` | number/null | `null` | OpenAI 兼容流式响应的相邻 chunk 超时 |

`ModelConfig` 允许 provider 私有参数。模板中出现的常用参数包括：
`api_key`、`api_base`/`base_url`、`timeout`/`request_timeout`/
`default_request_timeout`、`max_retries`、`max_tokens`、`temperature`、
`num_predict`、`reasoning`、`reasoning_effort`、`extra_body` 和 `pricing`。
其中 `pricing` 常用字段为 `currency`、`input_per_million`、
`output_per_million`、`input_cache_hit_per_million`。最终可用字段由 `use` 指向的类决定，
中台应保留一个经过白名单/JSON Schema 校验的 provider 扩展区。

### 4.2 `tools[]` 与 `tool_groups[]`

| 字段路径 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `tool_groups[].name` | string | 必填 | 工具组唯一名 |
| `tools[].name` | string | 必填 | 工具唯一名 |
| `tools[].group` | string | 必填 | 所属工具组名 |
| `tools[].use` | string | 必填 | 工具对象/工厂的类路径 |

两种对象都允许扩展字段。模板中 `web_search`、`image_search`、`glob`、`grep` 使用
`max_results`，`web_fetch` 使用 `timeout`；其他工具可定义自己的构造参数。

### 4.3 `sandbox`

| 字段路径 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `sandbox.use` | string | 必填 | SandboxProvider 类路径 |
| `sandbox.allow_host_bash` | boolean | `false` | Local provider 是否允许在宿主机执行 bash |
| `sandbox.image` | string/null | `null` | AIO/BoxLite 容器或 OCI 镜像 |
| `sandbox.port` | integer/null | `null` | AIO sandbox 基础端口 |
| `sandbox.replicas` | integer/null | `null` | 每进程最大活动+预热实例数，provider 缺省通常为 3 |
| `sandbox.container_prefix` | string/null | `null` | 容器名称前缀 |
| `sandbox.idle_timeout` | integer/null | `null` | 空闲回收秒数；provider 缺省通常为 600，0 表示关闭 |
| `sandbox.health_check_skip_seconds` | number/null | `null` | BoxLite 热复用健康检查跳过窗口 |
| `sandbox.ownership.type` | string | `memory` | `memory` 或 `redis` |
| `sandbox.ownership.redis_url` | string/null | `null` | 跨实例 ownership Redis URL |
| `sandbox.ownership.renewal_interval_seconds` | number | `30.0` | ownership lease 续约间隔 |
| `sandbox.ownership.ttl_multiplier` | number | `4.0` | lease TTL 相对续约间隔的倍数，最小 2 |
| `sandbox.ownership.key_prefix` | string | `deerflow:sandbox:owner` | Redis ownership key 前缀 |
| `sandbox.mounts[].host_path` | string | 必填 | 宿主端挂载源路径 |
| `sandbox.mounts[].container_path` | string | 必填 | 容器内目标路径 |
| `sandbox.mounts[].read_only` | boolean | `false` | 是否只读挂载 |
| `sandbox.environment.<name>` | string | 无 | 注入 sandbox 的环境变量 |
| `sandbox.bash_output_max_chars` | integer | `20000` | bash 输出保留字符数，0 关闭截断 |
| `sandbox.read_file_output_max_chars` | integer | `50000` | read_file 输出保留字符数 |
| `sandbox.ls_output_max_chars` | integer | `20000` | ls 输出保留字符数 |
| `sandbox.bash_command_timeout` | integer | `600` | Local host bash 最大执行秒数 |
| `sandbox.provisioner_api_key` | string/null | `null` | Provisioner `X-API-Key` |

`SandboxConfig` 允许 provider 私有字段。模板还展示了 `provisioner_url`、BoxLite 的
`memory_mib`/`cpus`、以及 E2B 等 provider 自定义参数；发布前应按 `sandbox.use` 选择对应
provider schema 校验。整个 `sandbox` 顶层属于 `ROLLING_RESTART`。

## 5. Skill、Extension 与工具输出

| 字段路径 | 类型 | 默认值 | 发布通道/说明 |
| --- | --- | --- | --- |
| `skills.use` | string | `deerflow.skills.storage.local_skill_storage:LocalSkillStorage` | `ROLLING_RESTART`；SkillStorage 实现 |
| `skills.path` | string/null | `null` | `ROLLING_RESTART`；宿主 Skill 根目录 |
| `skills.container_path` | string | `/mnt/skills` | `ROLLING_RESTART`；Sandbox 内挂载路径 |
| `skills.deferred_discovery` | boolean | `false` | `NEXT_RUN`；是否延迟发现 Skill |
| `skill_scan.enabled` | boolean | `true` | 原生确定性 Skill 安全扫描总开关 |
| `skill_evolution.enabled` | boolean | `false` | 允许 Agent 写入 `skills/custom` |
| `skill_evolution.moderation_model_name` | string/null | `null` | 安全审核模型；null 使用默认模型 |
| `skill_evolution.security_fail_closed` | boolean | `true` | 审核模型不可用时是否拒绝写入 |
| `extensions.middlewares[]` | string | `[]` | `CACHE_INVALIDATE`；零参数 AgentMiddleware 类路径 |
| `extensions.mcpServers.<server>.enabled` | boolean | `true` | MCP server 开关 |
| `extensions.mcpServers.<server>.type` | string | `stdio` | `stdio`、`sse` 或 `http`；也接受别名 `transport` |
| `extensions.mcpServers.<server>.command` | string/null | `null` | stdio 启动命令 |
| `extensions.mcpServers.<server>.args[]` | string[] | `[]` | stdio 命令参数 |
| `extensions.mcpServers.<server>.env.<name>` | string | 无 | stdio 子进程环境变量 |
| `extensions.mcpServers.<server>.url` | string/null | `null` | HTTP/SSE MCP URL |
| `extensions.mcpServers.<server>.headers.<name>` | string | 无 | MCP 请求头 |
| `extensions.mcpServers.<server>.description` | string | 空字符串 | Server 说明 |
| `extensions.mcpServers.<server>.tool_call_timeout` | number/null | `null` | 单次 MCP 工具调用超时 |
| `extensions.mcpServers.<server>.routing.mode` | string | `off` | Server 路由模式 |
| `extensions.mcpServers.<server>.routing.priority` | integer | `0` | 路由优先级 |
| `extensions.mcpServers.<server>.routing.keywords[]` | string[] | `[]` | 路由关键词 |
| `extensions.mcpServers.<server>.tools.<tool>.routing.mode` | string | `off` | 单工具路由覆盖 |
| `extensions.mcpServers.<server>.tools.<tool>.routing.priority` | integer | `0` | 单工具优先级 |
| `extensions.mcpServers.<server>.tools.<tool>.routing.keywords[]` | string[] | `[]` | 单工具关键词 |
| `extensions.skills.<skill>.enabled` | boolean | `true` | Skill 启用状态 |

MCP OAuth 字段：

| 字段路径 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `extensions.mcpServers.<server>.oauth.enabled` | boolean | `true` | 是否启用 OAuth token 获取 |
| `extensions.mcpServers.<server>.oauth.token_url` | string | 必填 | Token endpoint |
| `extensions.mcpServers.<server>.oauth.grant_type` | string | `client_credentials` | `client_credentials` 或 `refresh_token` |
| `extensions.mcpServers.<server>.oauth.client_id` | string/null | `null` | Client ID |
| `extensions.mcpServers.<server>.oauth.client_secret` | string/null | `null` | Client secret |
| `extensions.mcpServers.<server>.oauth.refresh_token` | string/null | `null` | Refresh token |
| `extensions.mcpServers.<server>.oauth.scope` | string/null | `null` | Scope 字符串 |
| `extensions.mcpServers.<server>.oauth.audience` | string/null | `null` | Audience |
| `extensions.mcpServers.<server>.oauth.token_field` | string | `access_token` | 响应中的 token 字段名 |
| `extensions.mcpServers.<server>.oauth.token_type_field` | string | `token_type` | 响应中的 token type 字段名 |
| `extensions.mcpServers.<server>.oauth.expires_in_field` | string | `expires_in` | 响应中的过期秒数字段名 |
| `extensions.mcpServers.<server>.oauth.default_token_type` | string | `Bearer` | 缺失 token type 时的默认值 |
| `extensions.mcpServers.<server>.oauth.refresh_skew_seconds` | integer | `60` | 提前刷新窗口（秒） |
| `extensions.mcpServers.<server>.oauth.extra_token_params.<name>` | string | 无 | 额外 token 表单参数 |

`extensions_config.json` 还支持 `mcpInterceptors`；它不是 `config.yaml` 当前
`ExtensionsConfig` 的正式字段，配置中台应作为 Extension 制品独立管理。

工具输出字段：

| 字段路径 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `tool_output.enabled` | boolean | `true` | 启用大输出保护 |
| `tool_output.externalize_min_chars` | integer | `12000` | 超过此长度时外置到文件 |
| `tool_output.preview_head_chars` | integer | `2000` | 外置摘要头部字符数 |
| `tool_output.preview_tail_chars` | integer | `1000` | 外置摘要尾部字符数 |
| `tool_output.fallback_max_chars` | integer | `30000` | 外置失败时最大保留字符数 |
| `tool_output.fallback_head_chars` | integer | `8000` | 回退摘要头部字符数 |
| `tool_output.fallback_tail_chars` | integer | `3000` | 回退摘要尾部字符数 |
| `tool_output.storage_subdir` | string | `.tool-results` | 工具结果子目录 |
| `tool_output.exempt_tools[]` | string[] | `[]` | 不做外置的工具名；模板含 `read_file`、`read_file_tool` |
| `tool_output.tool_overrides.<tool>` | integer | 无 | 单工具外置阈值覆盖 |
| `tool_search.enabled` | boolean | `false` | 启用工具搜索/延迟加载 |
| `tool_search.auto_promote_top_k` | integer | `3` | 自动提升的匹配工具数量 |

## 6. Agent 行为

| 字段路径 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `title.enabled` | boolean | `true` | 自动生成标题 |
| `title.max_words` | integer | `6` | 标题最大词数 |
| `title.max_chars` | integer | `60` | 标题最大字符数 |
| `title.model_name` | string/null | `null` | 指定模型；null 使用快速本地回退 |
| `title.prompt_template` | string | 内置模板 | 标题 Prompt，支持 `{max_words}`、`{user_msg}`、`{assistant_msg}` |
| `summarization.enabled` | boolean | `false`；模板为 `true` | 启用上下文总结 |
| `summarization.model_name` | string/null | `null` | 总结模型；null 使用默认模型 |
| `summarization.trigger` | object/object[]/null | `null` | 一个或多个总结触发条件 |
| `summarization.trigger[].type` | string | 必填 | 触发类型，如 `tokens` |
| `summarization.trigger[].value` | integer/number | 必填 | 触发阈值 |
| `summarization.keep.type` | string | 必填 | 总结后保留策略，如 `messages` |
| `summarization.keep.value` | integer/number | 必填 | 保留数量/比例 |
| `summarization.trim_tokens_to_summarize` | integer/null | `4000`；模板为 `15564` | 送入总结器前裁剪的 Token 目标 |
| `summarization.summary_prompt` | string/null | `null` | 自定义总结 Prompt |
| `summarization.skill_file_read_tool_names[]` | string[] | `[]` | 识别 Skill 文件读取的工具名 |
| `input_polish.enabled` | boolean | `true` | 输入润色开关 |
| `input_polish.max_chars` | integer | `4000` | 最大输入字符数 |
| `input_polish.model_name` | string/null | `null` | 润色模型 |
| `suggestions.enabled` | boolean | `true` | 后续问题建议开关 |
| `circuit_breaker.failure_threshold` | integer | `5` | 连续失败多少次后熔断 |
| `circuit_breaker.recovery_timeout_sec` | integer | `60` | 半开恢复等待秒数 |
| `llm_call.max_concurrent_calls` | integer | `0` | `ROLLING_RESTART`；进程内 LLM 并发上限，0 关闭 |
| `llm_call.retry_max_attempts` | integer | `3` | 最大尝试次数，1 表示不重试 |
| `llm_call.retry_base_delay_ms` | integer | `1000` | 抖动退避初始毫秒数 |
| `llm_call.retry_cap_delay_ms` | integer | `8000` | 单次退避最大毫秒数 |
| `llm_call.burst_retry_base_delay_ms` | integer | `5000` | burst-rate 429 专用退避基数 |
| `loop_detection.enabled` | boolean | `true` | 循环检测总开关 |
| `loop_detection.warn_threshold` | integer | `3` | 相似动作告警阈值 |
| `loop_detection.hard_limit` | integer | `5` | 相似动作硬限制 |
| `loop_detection.window_size` | integer | `20` | 检测窗口大小 |
| `loop_detection.max_tracked_threads` | integer | `100` | 进程内最多跟踪 thread 数 |
| `loop_detection.tool_freq_warn` | integer | `30` | 单工具调用频次告警阈值 |
| `loop_detection.tool_freq_hard_limit` | integer | `50` | 单工具调用频次硬限制 |
| `loop_detection.tool_freq_overrides.<tool>.warn` | integer | 必填 | 单工具告警覆盖 |
| `loop_detection.tool_freq_overrides.<tool>.hard_limit` | integer | 必填 | 单工具硬限制覆盖 |
| `tool_progress.enabled` | boolean | `false` | 工具进展检测开关 |
| `tool_progress.stagnation_threshold` | integer | `3` | 停滞判定阈值 |
| `tool_progress.warn_escalation_count` | integer | `2` | 警告升级次数 |
| `tool_progress.inject_assessment` | boolean | `true` | 是否向上下文注入进展评估 |
| `tool_progress.jaccard_similarity_threshold` | number | `0.8` | 文本相似度阈值 |
| `tool_progress.min_word_count_for_similarity` | integer | `10` | 参与相似度判断的最小词数 |
| `tool_progress.exempt_tools[]` | string[] | `[]` | 免检工具 |
| `tool_progress.max_tracked_threads` | integer | `100` | 最多跟踪 thread 数 |
| `read_before_write.enabled` | boolean | `true` | 写文件前要求先读 |
| `safety_finish_reason.enabled` | boolean | `true` | 拦截安全终止但仍携带工具调用的响应 |
| `safety_finish_reason.detectors[].use` | string | 内置 detector 集合 | 自定义 detector 类路径；设置数组会完全覆盖内置集合 |
| `safety_finish_reason.detectors[].config` | object | `{}` | Detector 构造参数 |

## 7. Memory、知识库与上传

### 7.1 `memory`

| 字段路径 | 类型 | 默认值 | 发布通道/说明 |
| --- | --- | --- | --- |
| `memory.enabled` | boolean | `true` | `NEXT_RUN`；Memory 总开关 |
| `memory.mode` | string | `middleware` | `NEXT_RUN`；集成模式 |
| `memory.injection_enabled` | boolean | `true` | `NEXT_RUN`；是否注入系统 Prompt |
| `memory.shutdown_flush_timeout_seconds` | number | `30.0` | 进程关闭时 flush 等待秒数 |
| `memory.manager_class` | string | `deermem` | `ROLLING_RESTART`；MemoryManager 实现 |
| `memory.backend_config` | object | `{}` | `ROLLING_RESTART`；后端私有参数 |

当前内置 DeerMem 的 `backend_config`：

| 字段路径 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `memory.backend_config.storage_path` | string | 空字符串 | 数据根目录；空值由宿主注入运行目录 |
| `memory.backend_config.storage_class` | string | 空字符串；模板写 `file` | 自定义 storage 类路径；空值使用文件存储 |
| `memory.backend_config.strict_user_scope` | boolean | `false` | 是否强制所有存储访问携带 user_id |
| `memory.backend_config.manifest_filename` | string | `memory.json` | 用户级摘要文件名 |
| `memory.backend_config.file_lock_timeout_seconds` | integer | `10` | 文件锁等待秒数 |
| `memory.backend_config.retrieval_adapter` | string | 空字符串 | 自定义 RetrievalPort 工厂路径 |
| `memory.backend_config.debounce_seconds` | integer | `30` | 队列更新去抖秒数 |
| `memory.backend_config.max_facts` | integer | `100` | 最大事实数 |
| `memory.backend_config.fact_confidence_threshold` | number | `0.7` | 事实入库最低置信度 |
| `memory.backend_config.max_injection_tokens` | integer | `2000` | Memory 注入 Token 上限 |
| `memory.backend_config.token_counting` | string | `tiktoken` | `tiktoken` 或 `char` |
| `memory.backend_config.guaranteed_categories[]` | string[] | `[correction]` | 不受普通预算淘汰的类别 |
| `memory.backend_config.guaranteed_token_budget` | integer | `500` | 保证类别的 Token 上限 |
| `memory.backend_config.staleness_review_enabled` | boolean | `true` | 启用陈旧事实复查 |
| `memory.backend_config.staleness_age_days` | integer | `90` | 进入复查的事实年龄 |
| `memory.backend_config.staleness_min_candidates` | integer | `3` | 启动一次复查的最少候选数 |
| `memory.backend_config.staleness_max_removals_per_cycle` | integer | `10` | 每轮最大删除数 |
| `memory.backend_config.staleness_protected_categories[]` | string[] | `[correction]` | 不参加陈旧复查的类别 |
| `memory.backend_config.staleness_max_lifetime_multiplier` | number | `20.0` | 新事实有效期相对 age 的上限倍数 |
| `memory.backend_config.staleness_max_extension_days` | integer | `3650` | 延长后有效期绝对上限 |
| `memory.backend_config.consolidation_enabled` | boolean | `false` | 启用事实合并 |
| `memory.backend_config.consolidation_min_facts` | integer | `8` | 触发合并的同类事实最少数量 |
| `memory.backend_config.consolidation_max_groups_per_cycle` | integer | `3` | 每轮最多合并组数 |
| `memory.backend_config.consolidation_max_sources` | integer | `8` | 单组合并的最大来源事实数 |
| `memory.backend_config.patterns_dir` | string/null | `null` | 自定义 correction/reinforcement pattern 目录 |
| `memory.backend_config.prompts_dir` | string/null | `null` | 自定义 Memory Prompt 目录 |
| `memory.backend_config.model.provider` | string/null | `null` | Memory LLM provider |
| `memory.backend_config.model.model` | string/null | `null` | Memory LLM 模型名 |
| `memory.backend_config.model.api_key` | string/null | `null` | Memory LLM API key |
| `memory.backend_config.model.base_url` | string/null | `null` | OpenAI 兼容网关地址 |
| `memory.backend_config.model.temperature` | number/null | `null` | 采样温度 |

`should_keep_hidden_message`、`host_llm`、`trace_context_manager` 只能由宿主程序注入，
不是可通过 YAML 下发的字段。

### 7.2 `knowledge_base`、`uploads`

| 字段路径 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `knowledge_base.enabled` | boolean | `false` | 知识库总开关 |
| `knowledge_base.lightrag.base_url` | string/null | `null` | LightRAG 服务地址 |
| `knowledge_base.lightrag.api_key` | string/null | `null` | LightRAG API key |
| `knowledge_base.lightrag.timeout_seconds` | number | `5.0` | 健康/普通请求超时 |
| `knowledge_base.lightrag.query_timeout_seconds` | number | `60.0` | 查询请求超时 |
| `uploads.max_files` | integer | `10` | 单次上传最大文件数 |
| `uploads.max_file_size` | integer | `52428800` | 单文件最大字节数，模板为 50 MiB |
| `uploads.max_total_size` | integer | `104857600` | 单次总字节数，模板为 100 MiB |
| `uploads.auto_convert_documents` | boolean | `false` | 是否自动把文档转换为 Markdown |
| `uploads.pdf_converter` | string | `auto` | `auto`、`pymupdf4llm` 或 `markitdown` |

`uploads` 由兼容层作为开放 object 读取，上表是当前代码明确消费的完整字段。

## 8. 子 Agent 与 ACP

| 字段路径 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `agents_api.enabled` | boolean | `false` | 自定义 Agent 管理 API 开关 |
| `acp_agents.<name>.command` | string | 必填 | ACP Agent 启动命令 |
| `acp_agents.<name>.args[]` | string[] | `[]` | 命令参数 |
| `acp_agents.<name>.env.<name>` | string | 无 | 子进程环境变量 |
| `acp_agents.<name>.description` | string | 必填 | Agent 描述 |
| `acp_agents.<name>.model` | string/null | `null` | 模型覆盖 |
| `acp_agents.<name>.auto_approve_permissions` | boolean | `false` | 是否自动批准 ACP 权限请求 |
| `acp_agents.<name>.timeout_seconds` | integer | `1800` | 总超时 |
| `subagents.timeout_seconds` | integer | `1800` | 全局子 Agent 超时 |
| `subagents.max_turns` | integer/null | `null` | 全局最大 turn 数 |
| `subagents.max_total_per_run` | integer | `6` | 单次 lead run 最多启动子 Agent 数 |
| `subagents.token_budget.enabled` | boolean | `true` | 全局子 Agent Token 预算开关 |
| `subagents.token_budget.max_tokens` | integer | 未启用总结时 `2000000`；启用时 `1000000` | 默认总预算会与总结开关联动 |
| `subagents.token_budget.max_input_tokens` | integer/null | `null` | 输入 Token 独立上限 |
| `subagents.token_budget.max_output_tokens` | integer/null | `null` | 输出 Token 独立上限 |
| `subagents.token_budget.warn_threshold` | number | `0.7` | 预算告警比例 |
| `subagents.token_budget.hard_stop_threshold` | number | `1.0` | 强制结束比例 |
| `subagents.agents.<name>.timeout_seconds` | integer/null | `null` | 内置 Agent 超时覆盖 |
| `subagents.agents.<name>.max_turns` | integer/null | `null` | 内置 Agent turn 覆盖 |
| `subagents.agents.<name>.model` | string/null | `null` | 内置 Agent 模型覆盖 |
| `subagents.agents.<name>.skills[]` | string[]/null | `null` | Skill allowlist 覆盖 |
| `subagents.agents.<name>.token_budget.enabled` | boolean | 对象缺省为 `false` | 单 Agent 预算开关；整个对象缺失时继承全局预算 |
| `subagents.agents.<name>.token_budget.max_tokens` | integer | 对象缺省为 `200000` | 单 Agent 总预算覆盖 |
| `subagents.agents.<name>.token_budget.max_input_tokens` | integer/null | `null` | 单 Agent 输入预算覆盖 |
| `subagents.agents.<name>.token_budget.max_output_tokens` | integer/null | `null` | 单 Agent 输出预算覆盖 |
| `subagents.agents.<name>.token_budget.warn_threshold` | number | `0.8` | 单 Agent 告警比例覆盖 |
| `subagents.agents.<name>.token_budget.hard_stop_threshold` | number | `1.0` | 单 Agent 硬停止比例覆盖 |
| `subagents.custom_agents.<name>.description` | string | 必填 | 自定义 Agent 描述 |
| `subagents.custom_agents.<name>.system_prompt` | string | 必填 | System Prompt |
| `subagents.custom_agents.<name>.tools[]` | string[]/null | `null` | Tool allowlist；null 使用默认 |
| `subagents.custom_agents.<name>.disallowed_tools[]` | string[]/null | `[task, ask_clarification, present_files]` | Tool denylist |
| `subagents.custom_agents.<name>.skills[]` | string[]/null | `null` | Skill allowlist |
| `subagents.custom_agents.<name>.model` | string | `inherit` | 模型名或继承 lead model |
| `subagents.custom_agents.<name>.max_turns` | integer | `50` | 最大 turn 数 |
| `subagents.custom_agents.<name>.timeout_seconds` | integer | `900` | 超时秒数 |

## 9. 安全、授权与认证

| 字段路径 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `guardrails.enabled` | boolean | `false` | 工具调用 guardrail 开关 |
| `guardrails.fail_closed` | boolean | `true` | Provider 异常时是否拒绝工具调用 |
| `guardrails.passport` | string/null | `null` | OAP passport 路径或 hosted agent ID |
| `guardrails.provider.use` | string | provider 启用时必填 | GuardrailProvider 类路径 |
| `guardrails.provider.config` | object | `{}` | Provider 私有参数 |
| `authorization.enabled` | boolean | `false` | 细粒度授权开关 |
| `authorization.fail_closed` | boolean | `true` | Provider/身份异常时是否拒绝访问 |
| `authorization.default_role` | string | `user` | 无显式角色时使用的角色 |
| `authorization.provider.use` | string | provider 启用时必填 | AuthorizationProvider 类路径 |
| `authorization.provider.config` | object | `{}` | Provider 私有参数 |
| `auth.local.allow_registration` | boolean | `true` | 是否允许邮箱/密码自助注册 |
| `auth.oidc.enabled` | boolean | `false` | OIDC SSO 开关 |
| `auth.oidc.frontend_base_url` | string/null | `null` | SSO 完成后的前端跳转基地址 |
| `auth.oidc.providers.<name>.display_name` | string | 必填 | 登录页显示名 |
| `auth.oidc.providers.<name>.issuer` | string | 必填 | OIDC issuer |
| `auth.oidc.providers.<name>.client_id` | string | 必填 | OAuth client ID |
| `auth.oidc.providers.<name>.client_secret` | string/null | `null` | OAuth client secret |
| `auth.oidc.providers.<name>.redirect_uri` | string/null | `null` | 回调地址；null 时开发环境自动推导 |
| `auth.oidc.providers.<name>.scopes[]` | string[] | `[]` | OIDC scopes |
| `auth.oidc.providers.<name>.token_endpoint_auth_method` | string | `client_secret_post` | Token endpoint 认证方式 |
| `auth.oidc.providers.<name>.auto_create_users` | boolean | `true` | 首次 SSO 是否自动建用户 |
| `auth.oidc.providers.<name>.require_verified_email` | boolean | `true` | 是否要求已验证邮箱 |
| `auth.oidc.providers.<name>.allowed_email_domains[]` | string[] | `[]` | 允许的邮箱域；空数组不限制 |
| `auth.oidc.providers.<name>.admin_emails[]` | string[] | `[]` | 自动授予管理员的邮箱 |
| `auth.oidc.providers.<name>.pkce_enabled` | boolean | `true` | 启用 PKCE S256 |
| `auth.oidc.providers.<name>.nonce_enabled` | boolean | `true` | 校验 ID token nonce |
| `auth.oidc.providers.<name>.authorization_endpoint` | string/null | `null` | 覆盖 discovery 的授权端点 |
| `auth.oidc.providers.<name>.token_endpoint` | string/null | `null` | 覆盖 token 端点 |
| `auth.oidc.providers.<name>.userinfo_endpoint` | string/null | `null` | 覆盖 userinfo 端点 |
| `auth.oidc.providers.<name>.jwks_uri` | string/null | `null` | 覆盖 JWKS 地址 |

## 10. 数据、调度与流式基础设施

这些顶层字段均为启动期配置；切换 backend 或存储位置时同时进入
`MANUAL_MIGRATION` 审批。

| 字段路径 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `database.backend` | string | 代码 `memory`；模板 `sqlite` | `memory`、`sqlite` 或 `postgres` |
| `database.checkpoint_channel_mode` | string | `full` | Checkpoint channel 存储模式；共享库的所有实例必须一致 |
| `database.sqlite_dir` | string | `.deer-flow/data` | SQLite 文件目录 |
| `database.postgres_url` | string | 空字符串 | PostgreSQL DSN |
| `database.echo_sql` | boolean | `false` | 输出 SQL 日志 |
| `database.pool_size` | integer | `5` | 连接池大小 |
| `database.pool_recycle` | integer | `300` | 连接回收秒数 |
| `database.command_timeout` | number/null | `30` | 数据库命令超时秒数 |
| `run_events.backend` | string | `memory` | `memory`、`db` 或 `jsonl`，以实现支持为准 |
| `run_events.max_trace_content` | integer | `10240` | 单事件 Trace 内容最大字符数 |
| `run_events.track_token_usage` | boolean | `true` | 是否记录 Token 用量事件 |
| `agent_storage.backend` | string | `file` | `file` 或 `db` |
| `scheduler.enabled` | boolean | `false` | 定时任务后台 poller 开关 |
| `scheduler.poll_interval_seconds` | integer | `5` | 扫描到期任务间隔 |
| `scheduler.lease_seconds` | integer | `120` | 任务 claim lease |
| `scheduler.max_concurrent_runs` | integer | `3` | 定时任务最大并发 run 数 |
| `scheduler.min_once_delay_seconds` | integer | `60` | 一次性任务最小未来偏移 |
| `checkpointer` | object/null | `null` | null 表示不配置独立 checkpointer |
| `checkpointer.type` | string | 对象存在时必填 | `memory`、`sqlite` 或 `postgres` |
| `checkpointer.connection_string` | string/null | `null` | SQLite 文件或 PostgreSQL DSN |
| `stream_bridge` | object/null | `null` | null 时回退内存 bridge |
| `stream_bridge.type` | string | `memory` | `memory` 或 `redis` |
| `stream_bridge.redis_url` | string/null | `null` | Redis URL；可回退环境变量 |
| `stream_bridge.queue_maxsize` | integer | `256` | 每个 run 保留的最大事件数 |
| `stream_bridge.max_connections` | integer/null | `null` | Redis 连接池上限 |
| `stream_bridge.stream_ttl_seconds` | integer | `86400` | Redis stream 滚动 TTL；0 关闭 |
| `stream_bridge.recovered_stream_cleanup_delay_seconds` | number | `60.0` | orphan run END 后延迟清理秒数 |
| `run_ownership.lease_seconds` | integer | `30` | Run ownership lease 时长 |
| `run_ownership.grace_seconds` | integer | `10` | 回收宽限和时钟偏差预算 |
| `run_ownership.heartbeat_enabled` | boolean | `false` | 多 worker 应启用 heartbeat |

## 11. IM Channel

### 11.1 `channel_connections`

| 字段路径 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `channel_connections.enabled` | boolean | `false` | 用户自助绑定总开关 |
| `channel_connections.require_bound_identity` | boolean | `true` | 是否拒绝未绑定外部身份 |
| `channel_connections.slack.enabled` | boolean | `false` | Slack 绑定入口 |
| `channel_connections.telegram.enabled` | boolean | `false` | Telegram 绑定入口 |
| `channel_connections.telegram.bot_username` | string | 空字符串 | Telegram deep link 的 bot username |
| `channel_connections.discord.enabled` | boolean | `false` | Discord 绑定入口 |
| `channel_connections.feishu.enabled` | boolean | `false` | Feishu 绑定入口 |
| `channel_connections.dingtalk.enabled` | boolean | `false` | DingTalk 绑定入口 |
| `channel_connections.wechat.enabled` | boolean | `false` | WeChat 绑定入口 |
| `channel_connections.wecom.enabled` | boolean | `false` | WeCom 绑定入口 |

### 11.2 `channels`

`channels` 由 Gateway ChannelManager 直接读取，不属于 `AppConfig` Pydantic schema；以下是
当前 `config.example.yaml` 公布的完整字段。整个 section 默认 `ROLLING_RESTART`。

| 字段路径 | 类型 | 模板值/默认 | 说明 |
| --- | --- | --- | --- |
| `channels.langgraph_url` | string | `http://localhost:8001/api` | LangGraph 兼容 API 地址 |
| `channels.gateway_url` | string | `http://localhost:8001` | Gateway 辅助 API 地址 |
| `channels.session.assistant_id` | string | `lead_agent` | 默认 Agent |
| `channels.session.config.recursion_limit` | integer | `100` | 默认 Channel run recursion limit |
| `channels.session.context.thinking_enabled` | boolean | `true` | 默认 thinking 开关 |
| `channels.session.context.is_plan_mode` | boolean | `false` | 默认 plan mode |
| `channels.session.context.subagent_enabled` | boolean | `false` | 默认子 Agent 开关 |
| `channels.<provider>.session.*` | object | 继承全局 | Provider 级 session 覆盖 |
| `channels.<provider>.session.users.<user_id>.*` | object | 继承 provider | 用户级 session 覆盖 |
| `channels.feishu.enabled` | boolean | `false` | Feishu 客户端开关 |
| `channels.feishu.app_id` | string | 无 | Feishu App ID |
| `channels.feishu.app_secret` | string | 无 | Feishu App Secret |
| `channels.feishu.domain` | string | `https://open.feishu.cn` | Feishu/Lark API 域名 |
| `channels.slack.enabled` | boolean | `false` | Slack Socket Mode 开关 |
| `channels.slack.bot_token` | string | 无 | `xoxb-...` token |
| `channels.slack.app_token` | string | 无 | `xapp-...` token |
| `channels.slack.allowed_users` | string/string[] | `[]` | 允许用户；空表示全部 |
| `channels.telegram.enabled` | boolean | `false` | Telegram polling 开关 |
| `channels.telegram.bot_token` | string | 无 | Bot token |
| `channels.telegram.allowed_users` | string/string[] | `[]` | 允许用户 |
| `channels.wechat.enabled` | boolean | `false` | WeChat iLink 开关 |
| `channels.wechat.bot_token` | string | 无 | Bot token |
| `channels.wechat.ilink_bot_id` | string | 无 | iLink bot ID |
| `channels.wechat.qrcode_login_enabled` | boolean | `true` | 缺少 token 时是否允许二维码启动 |
| `channels.wechat.ilink_app_id` | string | 空字符串 | `iLink-App-Id` 请求头 |
| `channels.wechat.route_tag` | string | 空字符串 | `SKRouteTag` 请求头 |
| `channels.wechat.allowed_users` | string/string[] | `[]` | 允许用户 |
| `channels.wechat.polling_timeout` | number | `35` | 长轮询超时秒数 |
| `channels.wechat.polling_retry_delay` | number | `5` | 轮询失败重试间隔 |
| `channels.wechat.qrcode_poll_interval` | number | `2` | 二维码状态轮询间隔 |
| `channels.wechat.qrcode_poll_timeout` | number | `180` | 二维码启动总超时 |
| `channels.wechat.state_dir` | string | `./.deer-flow/wechat/state` | Cursor 状态目录 |
| `channels.wechat.max_inbound_image_bytes` | integer | `20971520` | 入站图片上限 |
| `channels.wechat.max_outbound_image_bytes` | integer | `20971520` | 出站图片上限 |
| `channels.wechat.max_inbound_file_bytes` | integer | `52428800` | 入站文件上限 |
| `channels.wechat.max_outbound_file_bytes` | integer | `52428800` | 出站文件上限 |
| `channels.wechat.allowed_file_extensions[]` | string[] | 模板白名单 | 收发普通文件扩展名白名单 |
| `channels.wecom.enabled` | boolean | `false` | WeCom 开关 |
| `channels.wecom.bot_id` | string | 无 | WeCom Bot ID |
| `channels.wecom.bot_secret` | string | 无 | WeCom Bot Secret |
| `channels.dingtalk.enabled` | boolean | `false` | DingTalk Stream 开关 |
| `channels.dingtalk.client_id` | string | 无 | Client ID |
| `channels.dingtalk.client_secret` | string | 无 | Client Secret |
| `channels.dingtalk.allowed_users` | string/string[] | `[]` | 允许用户 |
| `channels.dingtalk.card_template_id` | string | 空字符串 | AI Card 模板 ID |
| `channels.discord.enabled` | boolean | `false` | Discord 开关 |
| `channels.discord.bot_token` | string | 无 | Bot token |
| `channels.discord.allowed_guilds` | string/string[] | `[]` | 允许 guild |
| `channels.discord.mention_only` | boolean | `false` | 是否只响应 mention |
| `channels.discord.allowed_channels` | string/string[] | `[]` | 可免 mention 的 channel |
| `channels.discord.thread_mode` | boolean | `false` | 是否把频道会话聚合为 thread |

## 12. 配置中台落库建议

配置中台不应只存一份无类型 YAML。每个字段至少记录：

| 元数据 | 建议内容 |
| --- | --- |
| `path` | 本文中的规范路径，例如 `llm_call.max_concurrent_calls` |
| `value_type` | scalar/object/array/map 及可空性 |
| `schema_owner` | `deerflow`、`provider:<class-path>` 或 `channel:<provider>` |
| `apply_channel` | `NEXT_RUN`、`CACHE_INVALIDATE`、`ROLLING_RESTART` 等 |
| `target_service` | 通常为 `gateway`，也可细分 channel/provider |
| `secret` | API key、token、client secret 等必须只引用 Secret，不明文回显 |
| `validation` | 枚举、范围、必填条件和跨字段约束 |
| `desired_revision` / `active_revision` | 下发版本与实例实际生效版本 |

对于开放字段，不要允许任意键直接进入生产：先根据 `use` 选择 provider schema；没有
schema 的 provider 至少采用审批白名单，并保留原始 JSON object 以避免中台吞掉未知参数。
