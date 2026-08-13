# 统一知识库中间层 PRD

状态：Draft

日期：2026-07-15

适用范围：DeerFlow、n8n 及后续需要访问同一知识库的内部系统

## 1. Executive Summary

### Problem Statement

当前知识库的目录、文档元数据、入库任务、重试机制、LightRAG 版本校验、图谱聚合和结构化检索都实现于 DeerFlow。其他系统如果也需要知识库能力，要么直接依赖 LightRAG 的私有契约，要么重复实现同一套可靠性和数据治理逻辑，最终会产生状态不一致、密钥扩散和接口漂移。

### Proposed Solution

建设一个独立的统一知识库中间层。中间层对 DeerFlow、n8n 和其他内部系统提供稳定、版本化的业务 API，并独占 LightRAG 连接、目录与文档元数据、原始文件、入库任务和重试状态。第一阶段采用“单部署、单知识库”模型，不提供知识库选择器或多知识库路由。

DeerFlow 保留知识库工作台、Agent 检索策略、答案引用展示和用户交互；它不再直接访问 LightRAG，也不再持有知识库业务表。

### Success Criteria

- DeerFlow 中 100% 的知识库请求经由中间层，运行配置中不再包含 LightRAG endpoint 或 API key。
- 相同 `Idempotency-Key` 的重复上传和人工重试不会创建重复文档、重复任务或重复远端处理。
- 中间层进程在上传、跟踪或状态提交任一阶段重启后，所有非终态任务都能自动恢复或进入明确失败状态。
- LightRAG 离线或版本不兼容时，状态接口在 5 秒内反映为非 `ready`，读写接口返回稳定错误码且不泄露 endpoint、密钥或原始异常。
- 删除接口只有在确认 LightRAG 远端文档不存在后才删除本地记录；无法确认时保留本地状态并返回失败。
- 建议初始性能基线：除上传流量与 LightRAG 耗时外，中间层自身增加的 p95 延迟不超过 100 ms；元数据类接口在 100 并发连接下 p95 不超过 300 ms。最终 SLO 需在部署规格确定后压测确认。

## 2. User Experience & Functionality

### User Personas

- **知识库使用者**：在 DeerFlow 中上传、整理、检索文档并查看知识图谱。
- **Agent / 工作流调用方**：DeerFlow Agent、n8n 或其他系统，通过 API 获取可归因的知识证据。
- **知识库管理员**：配置 LightRAG、查看在线状态、处理失败任务并管理文档。
- **平台运维人员**：负责中间层、数据库、对象存储和 LightRAG 的部署、监控与升级。

### User Stories

1. As a 知识库使用者, I want 在统一目录树中管理文档, so that 不同客户端看到一致的目录和文档状态。
2. As a 知识库使用者, I want 看到真实的入库阶段、耗时、分块数和失败状态, so that 长时间索引不会表现为无响应。
3. As a 知识库使用者, I want 在 LightRAG 离线时看到统一提醒并停止数据面操作, so that 页面不会继续展示不可用或误导性内容。
4. As a 知识库管理员, I want 对可恢复的失败任务执行幂等重试, so that 网络重放或重复点击不会重复启动任务。
5. As a 知识库管理员, I want 永久删除文档时同时清理 LightRAG、本地元数据、任务记录和托管原文件, so that 不留下不可见残留。
6. As a DeerFlow Agent, I want 获取结构化实体、关系、分块和来源, so that DeerFlow 可以独立决定如何组织回答和展示引用。
7. As an n8n workflow, I want 通过同一套认证和版本化 API 使用知识库, so that 工作流不需要理解 LightRAG 私有接口。
8. As a 平台运维人员, I want LightRAG 升级或异常只影响适配层, so that DeerFlow 和 n8n 的业务协议保持稳定。

### Acceptance Criteria

#### 单知识库与状态

- 每个中间层部署只有一个逻辑知识库，不接受客户端传入 `knowledge_base_id`、workspace 或 LightRAG workspace header。
- 所有已授权客户端共享该知识库；调用主体只用于权限判断和审计，不参与知识库路由。
- 状态固定为 `unconfigured | disabled | offline | incompatible | ready`。
- 状态响应只提供业务可用性、稳定原因和安全版本信息，不返回 API key、完整 endpoint 或堆栈诊断。
- 当状态不是 `ready` 时，上传、重试、图谱、检索和需要远端确认的删除必须失败；目录和本地元数据的只读接口仍可供管理与审计使用。

#### 目录

- 目录树由中间层数据库维护，LightRAG 不感知目录层级。
- 支持列出目录、新建子目录、重命名目录、删除空目录和移动文档。
- 同一父目录下不允许规范化后重名；根目录同样适用唯一约束。
- 只能删除空目录；包含子目录或文档时返回 `409`。
- 移动文档只更新目录关联，不重新上传或重新索引 LightRAG。

#### 文档上传与入库

- 一次请求只上传一个文件，并要求 `Idempotency-Key`。
- 支持的文件类型由中间层白名单控制；文件名必须经过安全规范化。
- 保留当前限制：单文件最大 25 MiB，单知识库托管原文件总量最大 1 GiB。限制需支持服务端配置，但客户端不得覆盖。
- 接受上传前，原文件必须先原子写入服务端托管存储；文档和入库任务在同一数据库事务创建。
- 对外文档状态固定为 `pending | indexing | ready | failed`。
- 内部任务状态固定为 `pending | leased | retry_wait | succeeded | dead | cancelled`。
- 索引进度来自 LightRAG 真实状态，阶段固定为 `pending | parsing | analyzing | processing | preprocessed | processed | failed`；允许提供 `chunks_count` 和 `stage_updated_at`，不得伪造百分比。
- 只有 `ready` 文档可以成为 Agent 检索候选。

#### 任务可靠性

- SQL 是任务状态的唯一事实来源，进程内队列或通知只能降低拾取延迟。
- 任务领取必须使用带条件的原子更新并设置 lease；禁止先查询再无条件更新。
- 支持有界并发、lease 续租、lease 过期恢复、指数退避、最大尝试次数和启动恢复。
- 每次任务尝试前重新读取当前 LightRAG 配置，使 endpoint 或凭证轮换无需重新上传。
- 人工重试只允许预定义的可恢复错误；历史重试 key 必须永久去重，直到对应任务被删除。
- 错误信息必须使用稳定、安全的错误码和用户文案，不持久化文档内容、密钥、完整 endpoint 或原始异常。

#### 删除

- 活跃入库任务不可删除，返回 `409`。
- 存在 LightRAG 状态的文档必须先调用远端删除，再轮询确认远端文档不存在。
- 远端不可用、响应不兼容或无法确认删除时，中间层必须保留本地记录和托管原文件。
- 远端确认后，在一个本地事务中删除文档、任务和重试 ledger，再删除托管原文件。
- 删除接口成功返回 `204`；重复删除返回 `404`，不得伪装成功。

#### 图谱与检索

- 中间层返回归一化、安全化的图谱字段，客户端不直接消费 LightRAG 私有响应。
- 全局图谱包含不连通分量，最多返回 5,000 个逻辑节点，并明确 `is_truncated`、`total_labels` 和 `components`。
- 图谱节点提供 `id`、`label`、`entity_type`、`description`、安全文件名；关系提供方向、类型、描述、关键词、权重和安全文件名。
- 结构化检索支持 `local | global | hybrid | naive | mix` 模式。
- 检索响应只返回实体、关系、分块、来源和处理元数据，不在中间层合成最终答案。
- 文件路径必须收敛为安全文件名，不向调用方暴露 LightRAG 或服务器绝对路径。

### Non-Goals

- 第一阶段不支持多知识库、知识库选择器、客户端传入 scope ID 或 LightRAG workspace。
- 不在中间层实现 DeerFlow 的 Agent 自动/强制检索决策、提示词拼装或最终答案生成。
- 不在中间层实现知识库页面、Canvas 图谱渲染或引用 UI。
- 不管理聊天附件、对话历史、Memory 或 DeerFlow thread 文件。
- 不重新实现向量化、实体抽取、图谱构建或 LightRAG 检索算法。
- 不承诺跨知识引擎通用抽象；第一阶段只适配已固定版本的 LightRAG。

## 3. AI System Requirements (If Applicable)

### Tool Requirements

#### 当前 DeerFlow 已使用的业务 API

下表是迁移基线。目标中间层应在 `/api/v1` 下提供等价契约；迁移期间 DeerFlow Gateway 可以继续把现有 `/api/knowledge/*` 转发到新地址。

| Method | 当前路径 | 用途 | 关键输入/输出 |
| --- | --- | --- | --- |
| `GET` | `/api/features` | 读取知识库数据面状态 | `knowledge_base.status/reason` |
| `GET` | `/api/knowledge/scope` | 读取单例知识库、统计和在线状态 | `scope + data_plane` |
| `POST` | `/api/knowledge/scope` | 首次创建单例知识库 | `name, description, enabled` |
| `PATCH` | `/api/knowledge/scope` | 更新单例知识库元数据或启停 | 部分更新字段 |
| `GET` | `/api/knowledge/directories` | 列出目录树 | `directories[]` |
| `POST` | `/api/knowledge/directories` | 新建目录 | `name, parent_id` |
| `PATCH` | `/api/knowledge/directories/{directory_id}` | 重命名目录 | `name` |
| `DELETE` | `/api/knowledge/directories/{directory_id}` | 删除空目录 | 成功 `204` |
| `GET` | `/api/knowledge/documents` | 列出托管文档和远端镜像 | `documents[]`，含状态、任务和进度 |
| `POST` | `/api/knowledge/documents` | 上传一个文档 | multipart `file`, `directory_id`; header `Idempotency-Key` |
| `PATCH` | `/api/knowledge/documents/{document_id}` | 移动文档 | `directory_id | null` |
| `DELETE` | `/api/knowledge/documents/{document_id}` | 永久删除文档 | 成功 `204` |
| `POST` | `/api/knowledge/documents/{document_id}/retry` | 人工重试失败任务 | header `Idempotency-Key` |
| `GET` | `/api/knowledge/graph/labels?limit=` | 热门图谱标签 | `labels[]` |
| `GET` | `/api/knowledge/graph/search?q=&limit=` | 搜索图谱标签 | `labels[]` |
| `GET` | `/api/knowledge/graph/global?max_nodes=` | 读取全局图谱 | 节点、边、分量、截断状态 |
| `GET` | `/api/knowledge/graph?label=&max_depth=&max_nodes=` | 读取指定标签连通图 | 节点、边、截断状态 |
| `POST` | `/api/knowledge/retrieval` | 结构化知识检索 | `query, mode, top_k, chunk_top_k, max_total_tokens` |

#### 目标中间层公开 API

- Canonical base path：`/api/v1/knowledge`。
- 保留上述 scope、directory、document、graph 和 retrieval 资源语义。
- 将 `/api/features` 的知识库部分收敛为 `GET /api/v1/knowledge/status`；`GET /scope` 仍携带同一份 `data_plane` 状态，避免页面额外请求。
- 所有错误使用统一结构：

```json
{
  "detail": {
    "code": "knowledge_data_plane_unavailable",
    "message": "知识服务暂时不可用。",
    "status": "offline"
  }
}
```

- OpenAPI 是跨项目契约源；Java、Python、TypeScript 和 n8n 客户端应从版本化 schema 生成或验证模型。
- 破坏性字段变更必须发布新 API major version；新增可选字段允许在当前版本演进。

#### 当前中间层需要适配的 LightRAG API

这些接口只能由中间层访问，不对 DeerFlow 或 n8n 透传。

| Method | LightRAG 路径 | 中间层用途 |
| --- | --- | --- |
| `GET` | `/health` | 在线、核心版本、API 版本和 pipeline 状态检查 |
| `POST` | `/documents/upload` | 上传服务端生成稳定文件名的原文件 |
| `GET` | `/documents/track_status/{track_id}` | 跟踪处理状态、阶段和分块数 |
| `POST` | `/documents/paginated` | 全量分页对账、文件名冲突恢复和远端镜像同步 |
| `DELETE` | `/documents/delete_document` | 删除远端文档及文件 |
| `GET` | `/graph/label/popular` | 获取热门实体标签 |
| `GET` | `/graph/label/list` | 获取全部实体标签，用于全局图谱发现 |
| `GET` | `/graph/label/search` | 搜索实体标签 |
| `GET` | `/graphs` | 按标签读取连通图 |
| `POST` | `/query/data` | 获取结构化检索证据，不生成回答 |

### Evaluation Strategy

- **公开 API 契约测试**：以 OpenAPI schema 验证 Python/Java/TypeScript 客户端，覆盖成功、幂等重放和所有稳定错误码。
- **LightRAG 适配契约测试**：针对固定 LightRAG 版本运行 live integration，验证 health、upload、tracking、pagination、delete、graph 和 query/data。
- **可靠性故障注入**：分别在领取任务后、远端上传后、本地 tracking 提交前、远端完成后和本地终态提交前终止进程，验证最终收敛。
- **检索质量基线**：建立至少 50 个带预期来源文档的查询集；检索结果的来源文档命中率建议达到 90% 以上，最终阈值由业务语料确认。
- **引用准确性**：中间层返回的每个 `reference_id` 必须能映射到同一响应中的安全来源，契约测试通过率 100%。
- **图谱完整性**：小于 5,000 节点的数据集不得无提示丢失分量；超限必须返回 `is_truncated=true`。
- **负载测试**：分别测试元数据、结构化检索和并发上传；上游限流与中间层资源耗尽必须返回可识别的限流或暂时不可用错误。

## 4. Technical Specifications

### Architecture Overview

```text
DeerFlow UI / Agent ----\
                        \
n8n / Other Clients ----- Knowledge Middleware ---- PostgreSQL
                         |          |
                         |          +-------------- Object Storage
                         |
                         +------------------------- LightRAG
```

调用方向必须保持单向：客户端只能调用中间层；只有中间层可以访问 LightRAG、知识库数据库和托管原文件。

### Responsibility Boundary

#### 中间层负责

- 单例知识库配置、启停和安全状态。
- 目录树、文档目录关联和文档业务元数据。
- 托管原文件及容量限制。
- 入库任务、lease、重试、恢复、进度和失败分类。
- LightRAG 客户端、版本兼容校验、响应归一化和凭证管理。
- 远端文档对账、冲突恢复和删除确认。
- 图谱聚合、截断、安全字段和结构化检索 API。
- RBAC、服务认证、审计、指标、日志和 OpenAPI 契约。

#### DeerFlow 负责

- 知识库工作台和离线时的阻断式用户体验。
- 当前用户身份与权限到中间层调用身份的映射。
- Agent 的自动/强制检索策略、当前问题是否需要检索的判断。
- 将结构化证据注入当前 Agent run、生成最终答案和展示引用。
- 不保存中间层的目录、文档、任务或 LightRAG 镜像副本作为事实来源。

#### LightRAG 负责

- 文档解析、分块、embedding、实体与关系抽取。
- 向量索引、知识图谱和结构化检索。
- 不负责业务目录、用户权限、幂等键、跨系统审计或最终答案 UI。

### Data Ownership

中间层至少需要以下持久化实体：

- `knowledge_scope`：固定单例记录，可用数据库唯一约束保证。
- `knowledge_directories`：父子目录、自身名称和规范化名称。
- `knowledge_documents`：原文件、安全存储名、目录、状态、LightRAG tracking ID 和进度。
- `knowledge_ingestion_jobs`：lease、尝试次数、下次重试、错误和终态。
- `knowledge_ingestion_retry_requests`：人工重试幂等 ledger。
- `knowledge_remote_documents`：LightRAG 中非中间层创建文档的安全镜像；如果生产环境禁止旁路写入，可在后续移除该兼容实体。
- `audit_events`：建议新增，用于记录配置、上传、移动、重试和删除操作，不保存文档正文。

生产环境应使用 PostgreSQL；SQLite 仅用于本地开发。原始文件应使用 S3/MinIO 等对象存储，本地磁盘仅用于单机开发。

### Integration Points

- DeerFlow Gateway 第一阶段充当兼容代理，把 `/api/knowledge/*` 转发到中间层 `/api/v1/knowledge/*`，并转发调用身份与 trace ID。
- DeerFlow 前端在兼容期无需立即修改 API path；稳定后可直接使用中间层 SDK 或继续经 Gateway BFF。
- DeerFlow Agent 的知识检索客户端改为调用中间层 `/retrieval`，不得绕过中间层访问 LightRAG。
- n8n 使用服务账号调用同一公开 API；上传、重试等可重放操作必须自行生成稳定 `Idempotency-Key`。
- 中间层向 LightRAG 发送服务端生成的稳定文件名，不发送客户端 scope、workspace 或目录信息。

### Security & Privacy

- 客户端到中间层必须认证。推荐内部 JWT/OIDC 服务身份；是否叠加 mTLS 由部署环境决定。
- 权限至少分为 `knowledge:read`、`knowledge:write`、`knowledge:admin`。
- 单知识库不等于匿名共享；所有操作仍需鉴权和审计。
- LightRAG API key 只保存在中间层密钥管理系统，不返回客户端、不写入业务日志。
- 上传必须校验扩展名、MIME、大小和安全文件名；禁止路径穿越和客户端指定服务端存储路径。
- 检索与图谱响应不得暴露服务器绝对路径、对象存储签名、API endpoint 或底层异常。
- 日志只记录业务 ID、trace ID、稳定错误码和耗时；不得记录文档正文、检索完整内容或密钥。

### Technology Choice

#### Option A: Java 21 + Spring Boot 3

建议组件：Spring Boot、Spring Security、WebClient 或 Java 21 virtual threads、PostgreSQL、Flyway、jOOQ/MyBatis、S3 SDK、Micrometer/OpenTelemetry。

优势：

- 企业级认证、配置管理、审计、监控和运维体系成熟。
- Java 21 virtual threads 或 WebFlux 能稳定承载大量 I/O 并发。
- 强类型 DTO、OpenAPI 和数据库迁移适合长期维护的大型平台服务。
- 如果组织已有 Spring Cloud、统一网关和 Java 运维平台，接入成本较低。

代价与风险：

- 当前 DeerFlow 中已经验证的 LightRAG 适配、状态机、错误归一化和故障恢复需要重新实现。
- LightRAG JSON 契约变化较快，Java DTO 的适配代码量会高于 Pydantic 模型。
- 任务 lease 需要原子 SQL；不建议只依赖 JPA 实体状态和悲观锁，应使用 jOOQ/MyBatis 或显式 SQL。
- 初期迁移周期和双实现行为漂移风险更高。

#### Option B: Python 3.12 + FastAPI

建议组件：FastAPI、Pydantic v2、SQLAlchemy async、Alembic、httpx、PostgreSQL、S3/MinIO SDK、OpenTelemetry。

优势：

- 可以直接提取并复用 DeerFlow 当前已经通过测试的 LightRAG client、Pydantic 契约、SQL repository 和入库 worker。
- 与 DeerFlow、LightRAG 及多数 AI 工具链语言一致，调试 JSON/模型契约和跟进上游版本更快。
- 当前工作负载主要是数据库、对象存储、HTTP 和 LightRAG 等 I/O，async Python 与多进程横向扩展足以承载；瓶颈通常在模型和 LightRAG，不在 Python GIL。
- 从现有实现迁出时，最容易保持错误码、状态语义和可靠性行为一致。

代价与风险：

- 需要严格约束同步 I/O，避免阻塞 event loop。
- 多进程 worker 必须完全依赖数据库 lease 或外部队列协调，不能依赖进程内状态。
- 企业级配置中心、审批和管理后台生态通常不如既有 Spring 平台统一，需要额外集成。
- CPU 密集型解析不适合留在 API 进程；本方案中该职责仍由 LightRAG 承担。

#### Recommendation

第一阶段推荐 **Python 3.12 + FastAPI + PostgreSQL**。

原因不是“Python 更适合高并发”，而是当前核心风险是迁移后的行为一致性、LightRAG 兼容和任务恢复，不是 API 进程的 CPU 吞吐。现有实现已经覆盖这些复杂边界，Python 可以最大程度复用并缩短验证周期。高并发应通过 async I/O、PostgreSQL、连接池、有界 worker 和水平扩展解决，而不是仅凭语言选择解决。

满足以下任一条件时，Java/Spring Boot 更合适：

- 公司已有强制的 Spring Cloud、统一配置、审计和发布平台，中间层必须成为该平台的一等服务。
- 负责该服务的长期团队主要维护 Java，愿意承担一次完整的契约重实现和双栈对照测试。
- 中间层未来明显扩展为以复杂管理、审批、计费和多租户治理为主的平台，而不是 LightRAG 适配与 AI 数据面服务。

不建议第一阶段同时维护 Java 管理面和 Python 数据面两个服务。先用一个服务形成清晰 OpenAPI、数据模型和运行指标；后续若治理需求成立，再基于稳定契约决定是否拆分或迁移。

## 5. Risks & Roadmap

### Phased Rollout

#### MVP

- 从 DeerFlow 提取 LightRAG client、status、document、directory、ingestion、graph 和 retrieval 模块。
- 使用 PostgreSQL、对象存储和版本化 OpenAPI。
- DeerFlow Gateway 保留兼容代理，前端行为不变。
- 迁移现有目录、文档、任务和远端映射数据，并完成一次 LightRAG 全量对账。
- DeerFlow 切断直接 LightRAG 配置和访问。

#### v1.1

- 增加管理配置、审计查询、任务指标、告警和限流。
- 提供正式 TypeScript、Python 客户端和 n8n 使用示例。
- 评估任务状态 webhook/SSE，减少客户端轮询；不是 MVP 阻塞项。
- 建立固定语料的检索质量和来源命中评测。

#### v2.0

- 根据真实需求评估多知识库、多租户、知识库绑定和 provider abstraction。
- 若管理治理成为主要复杂度，再评估 Java 管理面或整体迁移。
- 在不破坏 v1 客户端的前提下扩展 API major version。

### Technical Risks

- **双写风险**：迁移期间 DeerFlow 与中间层同时写数据库会导致状态分叉。切换必须采用单写者原则，迁移窗口内禁止旁路写入。
- **LightRAG 非 exactly-once**：上传 API 没有业务幂等键。中间层必须继续使用稳定文件名和分页对账，不得宣称远端严格 exactly-once。
- **删除部分成功**：远端删除成功而本地事务失败时，重试必须识别远端已不存在并继续本地清理。
- **长任务与重启**：任何只存在内存中的任务状态都会丢失；SQL lease 和启动恢复属于强制要求。
- **版本漂移**：LightRAG 升级可能改变字段或状态。必须固定支持版本，并通过 health 版本门禁和 live contract test 升级。
- **全局图谱成本**：全标签发现可能产生大量上游请求。必须保留请求数、分量数和 5,000 节点上限，并缓存稳定结果。
- **共享单库权限**：虽然不做多知识库，多个客户端仍可能具有不同读写权限。必须使用 RBAC 和审计，不能把单库理解为无权限边界。
- **技术栈过早拆分**：Java 管理面加 Python 数据面会增加部署、追踪和契约成本。没有独立扩缩容或组织边界证据前不拆分。

### Open Decisions

- 部署环境最终采用的 JWT/OIDC 发行方以及是否要求 mTLS。
- PostgreSQL、对象存储和密钥管理的具体产品。
- 建议性能基线对应的目标并发、文件吞吐和可用性 SLO。
- 是否允许管理员绕过中间层直接向 LightRAG 上传文档；推荐生产环境禁止。
