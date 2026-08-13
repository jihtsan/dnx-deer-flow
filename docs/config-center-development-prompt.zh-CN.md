# 数据中台配置中心开发提示词

下面的提示词用于在 `dnx-nexus-ai-platform` 开发模板中启动 DeerFlow 配置中台一期实现。

```text
请在数据中台仓库中实现 DeerFlow 配置中心一期功能。

目标仓库：
/Users/sw-jooder/Documents/dnx-nexus-ai-platform

DeerFlow 参考仓库：
/Users/sw-jooder/Documents/dnx-deer-flow

开始前必须阅读两个仓库适用范围内的 AGENTS.md，并完整阅读以下 DeerFlow 文档：

1. /Users/sw-jooder/Documents/dnx-deer-flow/docs/config-yaml-reference.zh-CN.md
2. /Users/sw-jooder/Documents/dnx-deer-flow/docs/config-center-manageability.zh-CN.md
3. /Users/sw-jooder/Documents/dnx-deer-flow/docs/config-center-release-channels.zh-CN.md
4. /Users/sw-jooder/Documents/dnx-deer-flow/docs/configuration-reload-matrix.zh-CN.md

代码事实来源：

- /Users/sw-jooder/Documents/dnx-deer-flow/config.example.yaml
- /Users/sw-jooder/Documents/dnx-deer-flow/backend/packages/harness/deerflow/config/app_config.py
- /Users/sw-jooder/Documents/dnx-deer-flow/backend/packages/harness/deerflow/config/reload_boundary.py

目标：

在 dnx-nexus-ai-platform 中建设一个通用配置中心，第一期接入 DeerFlow config.yaml。
系统必须把“配置是否允许由中台管理”和“配置如何生效”作为两个独立维度，不能使用一个
hot_reload 布尔字段代替。

一、先做仓库调查

1. 定位现有后端框架、数据库、迁移工具、认证授权、审计日志和任务调度机制。
2. 定位现有前端路由、表格、表单、代码编辑器、审批页面和 API client 模式。
3. 定位现有系统/环境/应用/实例/部署目标模型，优先复用，避免建立平行概念。
4. 定位现有 Secret 管理、发布任务、滚动部署或 Agent 下发机制。
5. 检查 DeerFlow 已有的配置、MCP、Skill reload、Channel restart 和健康检查接口。
6. 在开始修改前给出简短的现状映射、复用点、文件改动范围和风险，然后继续实现；
   不要停在方案阶段等待确认。

二、配置定义模型

每个配置定义至少包含：

- system_code
- path
- value_type
- nullable
- required
- default_value
- description
- schema_owner
- manage_mode
- change_frequency
- risk_level
- apply_channel
- target_service
- approval_policy
- secret
- validation_schema
- dependencies
- enabled

manage_mode 枚举：

- DAILY_MANAGED
- CONTROLLED_MANAGED
- DEPLOYMENT_MANAGED
- READ_ONLY

apply_channel 枚举：

- NEXT_RUN
- CACHE_INVALIDATE
- ROLLING_RESTART
- MANUAL_MIGRATION
- METADATA

以 config-center-manageability.zh-CN.md 为准初始化字段分级。正式 Pydantic schema 字段必须
全部进入配置目录，但只有 DAILY_MANAGED 和满足审批条件的 CONTROLLED_MANAGED 可以通过
普通配置发布流程修改。

三、一期编辑范围

普通配置页面直接开放：

- token_usage.enabled
- token_budget.*
- max_recursion_limit
- title.*
- summarization.*
- input_polish.*
- suggestions.enabled
- circuit_breaker.*
- llm_call 的 retry/backoff 字段，不包含 max_concurrent_calls
- loop_detection.*
- tool_progress.*
- read_before_write.enabled
- safety_finish_reason.enabled
- tool_output 的阈值、预览、回退、exempt_tools 和 tool_overrides
- tool_search.*
- uploads.*
- memory.enabled
- memory.injection_enabled
- knowledge_base.enabled 和两个 timeout 字段
- subagents 的全局限制、预算以及已有 Agent 的运行覆盖

受控开放：

- 模型目录中除 use 类路径以外的经过 schema 校验字段
- MCP server、OAuth、routing 和工具覆盖
- Skill 启停、skill_scan 和 skill_evolution
- 自定义子 Agent 定义
- Guardrail、Authorization、OIDC 和 Channel 策略
- Knowledge Base endpoint

默认不允许普通配置 API 修改：

- config_version
- 任意未审核 use 类路径
- extensions.middlewares
- MCP 本地 command
- sandbox.allow_host_bash
- sandbox.mounts
- 数据库、checkpointer、run event、agent storage、stream bridge、sandbox、scheduler、
  run ownership 等部署级字段
- memory manager、storage 和 backend_config
- skills storage/path 字段
- 任何明文 Secret

READ_ONLY 和 DEPLOYMENT_MANAGED 的限制必须在后端校验，不能只在前端隐藏。

四、版本和发布模型

至少实现：

- ConfigurationDefinition
- ConfigurationValue
- ConfigurationRevision
- ConfigurationRelease
- InstanceApplyStatus

revision 保存完整 snapshot、字段 diff、创建人、审批人、checksum 和状态。

发布状态至少支持：

DRAFT -> VALIDATED -> PENDING_APPROVAL -> APPROVED -> PUBLISHING
      -> PARTIALLY_ACTIVE -> ACTIVE
      -> FAILED
      -> ROLLED_BACK

所有发布必须携带 revision、checksum 和 idempotency_key。同一个 idempotency_key 重试时
不得创建多个发布任务。

目标实例 ACK 至少包含：

- instance_id
- desired_revision
- observed_revision
- active_revision
- checksum
- apply_status
- error
- acknowledged_at

只有全部目标实例 active_revision 和 checksum 收敛时，发布才能进入 ACTIVE。部分实例失败
或没有 ACK 时必须显示 PARTIALLY_ACTIVE，不能显示发布成功。

五、下发 Adapter

定义通用 TargetConfigAdapter，至少包含：

- validate(snapshot)
- publish(revision, snapshot, target)
- invalidate_cache(revision, target)
- rolling_restart(revision, target)
- query_status(target)
- verify(revision, target)
- rollback(revision, target)

实现 DeerFlow adapter 时先复用 DeerFlow 已有 API 和数据中台现有部署/Agent 能力。不要通过
SSH 任意执行命令，不要直接修改运行容器里的临时文件，也不要把“文件写入成功”等同于
“配置已经生效”。

如果 DeerFlow 当前缺少统一的配置接收接口：

1. 先完成 adapter 接口、持久化、发布状态机和可测试的 transport 边界。
2. 使用仓库现有安全传输/部署机制实现实际下发。
3. 不得用无认证 HTTP 接口或共享目录写入作为临时捷径。
4. 明确记录 DeerFlow receiver 的缺口、所需请求/ACK contract 和后续接入点。

不要在没有明确必要时修改 DeerFlow 仓库；若必须增加接收端，保持改动最小、经过认证、
幂等，并分别验证两个仓库。

六、Secret 和安全

- API key、token、password、client secret 只保存 Secret reference。
- Secret 不得出现在数据库 snapshot、revision diff、日志、审计详情或 API 响应中。
- 生产环境 CONTROLLED_MANAGED 配置必须审批。
- DEPLOYMENT_MANAGED 和 MANUAL_MIGRATION 必须使用平台管理员权限。
- provider 开放字段必须根据 schema_owner 选择对应 JSON Schema；没有 schema 时默认拒绝。
- 所有修改、审批、发布、失败和回滚必须写审计日志。

七、前端页面

实现面向平台运维的配置工作台，至少包含：

1. 系统、环境和部署目标选择。
2. 按 DeerFlow 配置分类展示配置项。
3. 按 manage_mode、apply_channel、风险和关键词筛选。
4. 字段类型、默认值、当前值、说明和生效方式。
5. 修改前后 diff 和发布影响分析。
6. Secret reference 选择器，不提供明文输入和回显。
7. READ_ONLY 字段只读展示。
8. DEPLOYMENT_MANAGED 字段展示部署影响，不进入普通发布按钮。
9. revision 历史、审批、发布进度、逐实例 ACK、错误和回滚。
10. YAML/JSON 高级视图必须复用同一份 schema 和权限校验，不能绕过字段级限制。

界面遵循目标仓库现有设计系统，优先清晰、紧凑和可扫描，不创建营销式页面。

八、接口能力

按照目标仓库现有 API 风格实现以下能力，路径可以调整：

- 查询系统、环境和配置目录
- 查询环境当前 snapshot
- 创建草稿 revision
- 校验 revision
- 提交和审批 revision
- 发布 revision
- 查询 release 和逐实例状态
- 比较两个 revision
- 回滚到历史 revision

后端必须重新执行类型、权限、manage_mode、Secret 和跨字段校验，不能信任前端提交结果。

九、初始化与同步

提供可重复执行的 DeerFlow 配置目录初始化机制：

1. 从 DeerFlow schema/受版本控制的种子数据初始化定义。
2. 重复执行不能创建重复字段。
3. 新增字段时补充定义；已有人工管理元数据不得被无条件覆盖。
4. schema 已删除字段应标记 deprecated，不立即删除历史 revision。
5. 添加自动化校验，确保 DeerFlow 正式 schema 顶层和叶子字段没有遗漏。

十、测试与验收

至少覆盖：

- 四种 manage_mode 的后端权限边界
- 五种 apply_channel 的路由
- 字段类型、枚举、范围和条件必填校验
- READ_ONLY/DEPLOYMENT_MANAGED 无法绕过 API 修改
- Secret 不进入持久化 snapshot、diff、日志和响应
- revision 状态机
- 发布幂等
- 多实例 ACK 和 PARTIALLY_ACTIVE
- rollback
- 配置目录重复初始化和 schema 演进
- 前端表单、筛选、diff、审批、发布状态和错误展示

完成后运行目标仓库要求的格式化、lint、类型检查、单元测试和相关集成测试。若修改了
DeerFlow，同时运行 DeerFlow 的针对性配置和 reload-boundary 测试。

最终交付：

1. 数据模型和迁移。
2. 配置目录初始化数据/生成器。
3. 后端 API、权限、审计和发布状态机。
4. TargetConfigAdapter 与 DeerFlow adapter。
5. 配置工作台前端。
6. 测试。
7. 架构、发布协议、运维和回滚文档。
8. 已完成能力、验证证据、尚未接通的外部依赖和剩余风险说明。

不要只输出设计方案；完成能够安全落地的一期实现并验证。
```
