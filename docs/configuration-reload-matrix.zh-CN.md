# DeerFlow 配置文件与热加载边界

本文面向开发和运维人员，回答两个问题：

1. DeerFlow 的配置分别放在哪里；
2. 修改后是自动生效、需要重启服务，还是需要重新构建/部署。

本文中的“热加载”不等于修改正在执行的任务。除特别说明外，热加载配置在
**下一次 HTTP 请求、下一条消息或下一次 Agent run** 生效；已经开始的 run 继续使用它
启动时拿到的配置快照。

配置中台如何把这些边界组织成 `NEXT_RUN`、`CACHE_INVALIDATE` 和
`ROLLING_RESTART` 三条发布通道，见
[DeerFlow 配置中台发布通道](config-center-release-channels.zh-CN.md)。
`config.yaml` 的完整字段、类型、默认值和字段级发布通道见
[DeerFlow config.yaml 完整字段参考](config-yaml-reference.zh-CN.md)。
配置项进入中台后的编辑权限和准入分级见
[配置中台准入与分级清单](config-center-manageability.zh-CN.md)。

## 生效级别

| 级别 | 含义 |
| --- | --- |
| `H0` | 当前进程无需重启；最早在下一次模型调用生效 |
| `H1` | 当前进程无需重启；在下一次请求、消息或 run 生效 |
| `R-GW` | 必须重启 Gateway；多 worker/多 Pod 时必须滚动全部实例 |
| `R-SVC` | 只需重启对应服务，例如 Frontend、Nginx、Provisioner |
| `BUILD` | 需要重新构建前端或容器镜像，然后重新部署 |
| `DEPLOY` | 需要 Compose recreate 或 `helm upgrade`；是否重建镜像取决于变更内容 |

## 快速结论

| 配置入口 | 默认生效方式 | 主要例外 |
| --- | --- | --- |
| 根目录 `config.yaml` | 大部分字段 `H1` | 基础设施字段 `R-GW`；`memory`、`llm_call` 是混合边界 |
| 根目录 `extensions_config.json` | MCP 为 `H1`；API 修改的技能状态为 `H0/H1` | 直接改技能配置后建议调用技能 reload；每个 worker/Pod 都要失效缓存 |
| 根目录 `.env` | `R-GW`/`R-SVC` | 环境变量是进程启动时注入的；`$VAR` 引用也不会重新读取 `.env` |
| `frontend/.env` | 开发环境 `R-SVC` | 生产环境的 `NEXT_PUBLIC_*` 为 `BUILD` |
| 自定义 Agent 的 `config.yaml`、`SOUL.md` | `H1` | 已经运行中的 Agent 不变；`agent_storage` 后端切换是 `R-GW` |
| Skill 包的 `SKILL.md` 和资源文件 | API 安装/编辑后 `H1` | 外部直接写盘后调用 `POST /api/skills/reload`，每个 worker/Pod 各调用一次 |
| Docker Compose / Nginx 配置 | `DEPLOY` / `R-SVC` | Dockerfile、依赖锁文件变更需要 `BUILD` |
| Helm `values.yaml` / templates | `DEPLOY` | Chart 对 Gateway、Extensions、Nginx ConfigMap 使用 checksum，会滚动 Pod |

## 1. 主配置 `config.yaml`

推荐路径是仓库根目录的 `config.yaml`，模板是 `config.example.yaml`。查找顺序为：

1. 显式传入的 `config_path`；
2. `DEER_FLOW_CONFIG_PATH`；
3. 当前目录下的 `config.yaml`；
4. 上一级目录下的 `config.yaml`。

`get_app_config()` 使用“解析后的路径 + mtime/size/SHA-256 内容签名”检测变化，
因此正常文件修改不依赖单纯的 mtime。实现见
[`app_config.py`](../backend/packages/harness/deerflow/config/app_config.py)；重启边界的权威清单见
[`reload_boundary.py`](../backend/packages/harness/deerflow/config/reload_boundary.py)。

### 1.1 必须重启 Gateway 的字段

以下是当前代码 `STARTUP_ONLY_FIELDS` 的完整清单。修改这些顶层字段的任何子项，
都按 `R-GW` 处理。

| 顶层字段 | 为什么需要重启 |
| --- | --- |
| `database` | SQLAlchemy engine、连接池和持久化实现启动时创建 |
| `checkpointer` | checkpointer、SQLite WAL/busy timeout 等启动时绑定 |
| `run_events` | run event store 实现启动时创建并挂到 `app.state` |
| `agent_storage` | 文件/数据库 Agent repository 后端启动时选择 |
| `stream_bridge` | 内存或 Redis StreamBridge 单例启动时创建 |
| `sandbox` | SandboxProvider 是进程级缓存单例 |
| `log_level` | 日志级别只在 Gateway 启动时应用 |
| `logging` | formatter、trace filter 和 `TraceMiddleware` 启动时安装 |
| `channels` | IM channel 客户端在 lifespan 启动时创建 |
| `channel_connections` | repository、worker 和 provider 合并配置启动时绑定 |
| `scheduler` | 调度服务和后台 poller 启动时创建 |
| `run_ownership` | RunManager lease/heartbeat 在启动时创建 |

补充说明：

- `database.checkpoint_channel_mode` 也属于 `database`，必须重启，并且共享同一数据库的
  所有进程必须使用同一个值。
- 修改 `channels.*` 后，可以通过 channel restart API 只重启对应 Channel；如果没有走
  该 API，则重启 Gateway。`channel_connections.*` 仍按 Gateway 重启处理。
- 开发模式的 Uvicorn 文件监听可能因为 YAML 变化而碰巧重启进程，但这属于开发服务器
  自动重启，不代表这些字段支持运行时热替换。

### 1.2 下一次请求或 run 生效的字段

除上一节和下一节的混合字段外，以下顶层字段按 `H1` 处理：

`token_usage`、`token_budget`、`max_recursion_limit`、`models`、`tools`、
`tool_groups`、`skills`、`skill_scan`、`skill_evolution`、`extensions`、
`tool_output`、`tool_search`、`title`、`summarization`、`knowledge_base`、
`agents_api`、`acp_agents`、`subagents`、`guardrails`、`authorization`、
`input_polish`、`suggestions`、`circuit_breaker`、`loop_detection`、
`tool_progress`、`read_before_write`、`safety_finish_reason`、`auth`、`uploads`。

典型行为：

- 修改 `models[*]`、模型参数、价格或默认模型：下一次请求/run 生效；
- 修改 prompt、工具、子 Agent、总结、标题、Token 预算：下一次 run 生效；
- 修改 `knowledge_base`：新的健康检查/查询请求和 ingestion worker 的下一次尝试生效；
- 修改 `uploads`：下一次上传请求生效；
- 修改 `auth.local` / `auth.oidc`：下一次认证请求读取新配置，但根 `.env` 中的密钥仍需重启。

### 1.3 混合边界

| 字段 | 可热加载部分 | 必须重启部分 |
| --- | --- | --- |
| `memory` | `enabled`、`mode`、`injection_enabled` 等调用点开关在下一次 run 读取；shutdown timeout 在关闭时重新读取 | `manager_class`、`backend_config` 和由它们创建的 MemoryManager 是进程单例，切换后端、存储路径或后端私有参数时重启 Gateway |
| `llm_call` | `retry_max_attempts`、`retry_base_delay_ms`、`retry_cap_delay_ms`、`burst_retry_base_delay_ms` 在新 run 使用 | `max_concurrent_calls` 首次创建进程级 limiter 后冻结，必须重启 Gateway |
| `config_version` | 仅用于版本检查和升级提示 | 不控制运行时行为；修改它不能代替 `make config-upgrade` |

## 2. 扩展配置 `extensions_config.json`

推荐路径是仓库根目录的 `extensions_config.json`，模板是
`extensions_config.example.json`。查找顺序与主配置相同，但环境变量名是
`DEER_FLOW_EXTENSIONS_CONFIG_PATH`。

| 内容 | 生效级别 | 说明 |
| --- | --- | --- |
| `mcpServers` | `H1` | MCP cache 检查路径和内容签名；新增、删除、启停、headers、OAuth、routing、工具覆盖在下一次工具加载/run 生效 |
| `mcpInterceptors` | `H1` | MCP 工具重新初始化时生效；已经建立的当前 run/session 不变 |
| `skills.<name>.enabled` | `H0/H1` | 通过 Gateway API 修改会写文件并失效当前 worker 缓存；新的模型调用/下一次 run 读取新状态 |
| `middlewares` | `H1` | 新建 Agent graph 时加载；正在运行的 graph 不变。该字段可实例化任意代码，只允许受信任运维人员修改 |

优先级规则：如果 `config.yaml -> extensions` 显式声明了同名字段，它覆盖
`extensions_config.json` 的对应字段，而不是做列表拼接。

推荐通过 `PUT /api/mcp/config` 和技能管理 API 修改。直接编辑文件时：

- MCP 会通过内容签名在下一次使用时检测；
- Skill 文件或启用状态的外部写入，调用 `POST /api/skills/reload`；
- 多 worker/多 Pod 部署中，reload 是进程本地操作，必须让每个实例都收到失效请求，
  或执行 Gateway 滚动重启；
- 当前活跃 run 保留已经构建好的工具和 prompt 快照。

## 3. Skill 与自定义 Agent 文件

### 3.1 Skill 包

运行时 Skill 根目录由 `config.yaml -> skills.path/container_path` 决定，通常为：

- `skills/public/<skill>/SKILL.md`：仓库内公共 Skill；
- `skills/custom/<skill>/SKILL.md`：本地自定义 Skill；
- 用户级 Skill：运行目录下对应用户的 Skill 存储。

通过安装/编辑 API 修改时，缓存会被失效，新 run 使用新内容。外部系统直接修改
NFS/MinIO/CSI/本地挂载内容后，调用 `POST /api/skills/reload`。Skill policy 会在每次
模型调用重新解析当前 registry，因此禁用或修改 `allowed-tools` 最早可在下一次模型调用
生效；已经注入本轮上下文的 `SKILL.md` 正文不会追溯修改。

### 3.2 自定义 Agent

文件后端的定义位于运行目录的：

```text
users/<user_id>/agents/<agent_name>/config.yaml
users/<user_id>/agents/<agent_name>/SOUL.md
```

它们在创建 Agent graph 时读取，修改后按 `H1` 处理。推荐通过 `/api/agents` 或 Agent
自身的 `update_agent` 工具修改，以获得校验和原子写入。已经运行中的 run 不会换模型、
SOUL、工具组或 Skill allowlist。

`config.yaml -> agent_storage.backend` 决定这些定义使用文件还是数据库；这是 `R-GW`，
不能通过修改单个 Agent 的 `config.yaml` 完成切换。

### 3.3 Memory 数据不是配置

`.deer-flow` 下的 `memory.json`、Markdown fact、run/event、SQLite/JSONL 等是运行状态，
不属于配置文件。通过 Memory API 修改会按正常数据路径生效。外部直接改 Markdown fact
时，使用 Memory reload API；不要通过重启来替代数据一致性操作。

## 4. 环境变量文件

### 4.1 根目录 `.env`

根 `.env` 包含模型/Search/MCP/Channel 密钥、数据库 URL、Gateway 开关、追踪、代理、
内部认证和部署参数。Shell、Compose 和 Python 进程只在启动时装载环境变量，因此统一按
`R-GW` 或对应 `R-SVC` 处理。

重要规则：`config.yaml` / `extensions_config.json` 中的 `$OPENAI_API_KEY` 之类引用，
只是从当前进程环境解析。只改 `.env` 不会改变已经运行进程的 `os.environ`，即使 YAML
配置本身支持热加载也一样。

典型的启动期环境变量包括：

- `DEER_FLOW_CONFIG_PATH`、`DEER_FLOW_EXTENSIONS_CONFIG_PATH`、`DEER_FLOW_HOME`；
- `DATABASE_URL`、Redis/stream bridge、sandbox/provisioner 参数；
- `GATEWAY_WORKERS`、`GATEWAY_CORS_ORIGINS`、`GATEWAY_ENABLE_DOCS`；
- `AUTH_JWT_SECRET`、内部认证 token、OIDC/provider secret；
- LangSmith、Langfuse、Monocle 和代理环境变量。

### 4.2 `frontend/.env`

| 变量类型 | 本地 `pnpm dev` | 生产构建 |
| --- | --- | --- |
| `NEXT_PUBLIC_*` | 重启 Frontend dev server | 值会进入浏览器 bundle，重新 build 镜像/前端并部署 |
| Server-only，例如 `DEER_FLOW_INTERNAL_GATEWAY_BASE_URL` | 重启 Frontend dev server | 当前 Next 配置/rewrites 在构建或启动阶段解析；为避免 manifest 仍是旧值，重新 build 并部署 |

`frontend/src/env.js` 是校验 schema，`frontend/next.config.js` 决定 rewrites 和 build
输出；它们本身属于构建配置，不是在线热加载配置。

## 5. Docker、Nginx 与 Helm

| 文件/目录 | 修改后操作 |
| --- | --- |
| `docker/docker-compose.yaml`、`docker/docker-compose-dev.yaml` | recreate 受影响的 service；环境、volume、command、port 变化不会自动进入现有容器 |
| `docker/docker-compose.dood.yaml`、`docker/docker-compose.cli-auth.yaml` | 使用对应 overlay 重新创建 Gateway/Provisioner |
| `docker/nginx/nginx.conf`、`docker/nginx/nginx.local.conf` | 重启或 reload Nginx；当前容器启动命令会先复制模板，单纯改宿主文件不会改已加载配置 |
| `backend/Dockerfile`、`frontend/Dockerfile`、`docker/provisioner/Dockerfile` | 重新 build 对应镜像并 recreate |
| `backend/pyproject.toml`、`backend/uv.lock` | 重新同步依赖；生产环境重新 build Gateway 镜像 |
| `frontend/package.json`、`frontend/pnpm-lock.yaml` | `pnpm install`；生产环境重新 build Frontend 镜像 |
| `deploy/helm/deer-flow/values.yaml` 或 Chart templates | 执行 `helm upgrade`；不要只编辑集群内生成的 ConfigMap |

Helm Chart 对 Gateway 的主配置、Extensions 配置以及 Nginx 配置使用 Pod template checksum。
因此 `helm upgrade` 修改这些 ConfigMap 时会滚动对应 Pod。也就是说，即便某个
`config.yaml` 字段在单进程文件挂载场景支持 `H1`，通过当前 Chart 发布它仍会触发 Pod
滚动，这是部署层行为，不是后端热加载失效。

Docker 生产 Compose 使用单文件 bind mount 挂载 `config.yaml` 和
`extensions_config.json`。某些编辑器会用原子替换保存文件，单文件 bind mount 可能仍指向
旧 inode。出现宿主机已修改但容器看不到时，recreate Gateway；Docker 开发配置使用目录
bind mount，专门规避了这个问题。

## 6. 开发、构建和测试配置

这些文件会影响下一次命令、测试或构建，不属于在线运行时热加载：

| 类别 | 文件 | 生效方式 |
| --- | --- | --- |
| Python 工程 | `backend/pyproject.toml`、`backend/packages/harness/pyproject.toml`、`backend/uv.lock` | 下一次 `uv sync`；生产镜像需重建 |
| Python 格式/迁移 | `backend/ruff.toml`、`backend/packages/harness/deerflow/persistence/migrations/alembic.ini` | 下一次 Ruff/Alembic 命令 |
| LangGraph Studio | `backend/langgraph.json` | 重启 LangGraph Studio/对应开发进程 |
| Frontend 编译 | `frontend/next.config.js`、`frontend/tsconfig.json`、`frontend/postcss.config.js` | dev server 可能重启；生产重新 build |
| Frontend 质量工具 | `frontend/eslint.config.js`、`frontend/prettier.config.js`、`frontend/rstest.config.ts`、`frontend/playwright*.config.ts` | 下一次 lint/format/test |
| UI registry | `frontend/components.json` | 下一次组件生成命令 |
| 仓库自动化 | 根目录/模块 `Makefile`、`scripts/`、`.pre-commit-config.yaml`、`.github/workflows/` | 下一次命令、hook 或 CI run |
| API/Skill contract | `contracts/**/*.json` | 被读取该 contract 的下一次测试/工具调用；如代码将其打包进镜像则需重建 |

模板文件 `config.example.yaml`、`extensions_config.example.json`、`.env.example`、
`frontend/.env.example` 只用于生成或升级本地配置，修改模板不会自动修改正在使用的文件。
主配置新增模板字段后，运行 `make config-upgrade` 合并缺失字段。

## 7. 推荐操作

### 只修改 `config.yaml` 的热加载字段

保存后发起一条新的请求或新 run。先用以下命令检查配置：

```bash
make doctor
```

### 修改 Gateway 启动字段或根 `.env`

本地开发：

```bash
make stop
make dev
```

Docker 开发：

```bash
make docker-restart
```

Docker 生产：

```bash
make down
make up
```

### 修改 Frontend 环境变量

本地开发重启 `pnpm dev`；生产环境重新构建 Frontend 镜像并部署。

### 修改 Helm 配置

```bash
helm upgrade deer-flow deploy/helm/deer-flow \
  -n deer-flow \
  -f my-values.yaml
```

升级后确认 Gateway/Nginx rollout 完成。多 Pod 环境不要只验证一个 Pod。

## 8. 维护规则

新增或修改配置时遵守以下规则：

1. 新的 Gateway 启动期字段必须加入
   `backend/packages/harness/deerflow/config/reload_boundary.py::STARTUP_ONLY_FIELDS`；
2. 对应 Pydantic 字段使用 `startup-only:` 描述；
3. 混合字段必须在字段自身描述和本文“混合边界”同时注明；
4. 更新 `config.example.yaml` 的 `config_version`，并保持示例、实现和本文一致；
5. 运行配置边界测试：

```bash
cd backend
PYTHONPATH=. uv run pytest \
  tests/test_reload_boundary.py \
  tests/test_app_config_reload.py \
  tests/test_gateway_config_freshness.py
```
