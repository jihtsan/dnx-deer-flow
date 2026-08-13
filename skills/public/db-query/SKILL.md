---
name: db-query
description: >
  Read-only database query workflow for MySQL and PostgreSQL. Use this skill whenever
  the user wants to query a database, inspect tables, browse data, or troubleshoot
  database issues. Triggers on: "查数据库", "查一下表", "show tables", "describe table",
  "query the DB", "check database", "数据库查询", or any request involving SQL or
  database exploration. Use it as a fallback when a domain skill explicitly determines
  that governed APIs cannot answer. For DNX business questions, use dnx-data first and
  do not let direct database access bypass its station scope or fallback gate.
---

# db-query skill

`SCRIPT_DIR` = the `scripts/` directory next to this file.

Database connection settings must come from a server-owned file outside this
public skill. Set `DB_ENV_FILE` to that file. When aliases are supplied only as
process environment variables, also set `DB_ALIAS_PREFIXES` to a comma-separated
list of their uppercase prefixes, such as `SUWEN_DNX_METRIC_PG`.

Never create or copy `.env` files under this public skill directory.

## 工作流

默认按以下顺序执行，但满足跳过条件时可以直接进入后续步骤，不要机械串行。

### 1. 确认数据源

当用户没有明确 alias，或无法从上下文唯一判断目标库时，先执行：

```bash
python3 $SCRIPT_DIR/db_query.py list_aliases
```

返回字段：`alias`、`type`、`host`、`database`。
若配置中提供了补充元数据，结果也可能包含：`project`、`desc`。

可跳过条件：
- 用户已经明确提供 alias
- 上下文已经唯一确定目标库

### 2. 确认表范围

当用户只知道数据库、不知道表名时，执行：

```bash
python3 $SCRIPT_DIR/db_query.py list_tables <alias>
```

如果只知道部分关键词，优先执行：

```bash
python3 $SCRIPT_DIR/db_query.py search_tables <alias> <keyword>
```

可跳过条件：
- 用户已经明确提供表名
- 当前任务已明确指向具体表

### 3. 确认表结构

写 SQL 前，如果列名、字段类型、关联键、过滤条件不明确，执行：

```bash
python3 $SCRIPT_DIR/db_query.py describe_table <alias> <table>
```

若需要确认跨库表、字段或 JOIN 键，也可以直接查询 `information_schema`。
支持传入 `schema.table`。
如果只知道字段名或模糊表名，优先执行：

```bash
python3 $SCRIPT_DIR/db_query.py search_columns <alias> <keyword>
```

可跳过条件：
- 相关字段和关联关系在上下文中已经明确
- 当前只需要简单预览样本数据

### 4. 预览数据（可选）

当需要理解枚举值、空值、数据格式、样本分布时，执行：

```bash
python3 $SCRIPT_DIR/db_query.py sample_data <alias> <table> [limit]
```

样本数据默认不超过 100 条。
支持传入 `schema.table`。

### 5. 执行查询

确认结构后执行只读查询：

```bash
python3 $SCRIPT_DIR/db_query.py query <alias> "<SELECT ...>"
```

只允许单条只读查询。支持 `SELECT` / `WITH ... SELECT`，写操作和多语句会被拒绝。

## 特殊规则

### 领域 Skill 回退

当 `dnx-data` 等领域 Skill 调用本 Skill 时：

- 继承上游已经确定的固定用户上下文、场站、时间范围和回退原因，不扩大查询范围；
- DNX 临时模式不校验授权，但场站列表或业务服务不可用时仍不执行数据库回退；
- 先用 `search_tables` / `search_columns` / `describe_table` 渐进发现，不加载全库 Schema；
- 返回 alias、表名、时间范围和 SQL，使上游能标注“原始数据库结果”与“治理后的 API 指标”的差异；
- 不自行复刻未知的业务指标口径，不把原始字段冒充成 API 指标。

### MySQL 同实例跨 schema

如果同一个 alias 对应的 MySQL 用户具备跨 schema 权限，可以直接写：

```sql
SELECT ...
FROM db_a.table_a a
JOIN db_b.table_b b ON ...
```

此时不要求必须分别切换 alias。

### information_schema 优先场景

遇到以下情况，优先允许直接查询 `information_schema.tables` / `information_schema.columns`：
- 确认跨库表是否存在
- 校验字段是否存在、类型是否匹配
- 校验 JOIN 键是否可直接关联
- 需要快速确认少量目标表，而不是先列全库表名

### PostgreSQL schema

PostgreSQL 的 `list_tables` 会返回非系统 schema 下的表。
对于非 `public` schema，结果可能显示为 `schema.table`。

### 已知 alias 直接执行

如果用户已经明确给出 alias、表名或 SQL 目标，可以直接从最相关的步骤开始，不要求回到 `list_aliases`。

## 异常处理

- 若 alias 缺失，但上下文已经明确提到目标库，先告知缺口，再尝试同实例可用 alias 或 `information_schema` 方案。
- 若 `list_tables` 结果过大，不要硬读全量，改用 `information_schema` 精确过滤目标表。
- 若只知道关键词，不要先全量列库，优先使用 `search_tables` 或 `search_columns`。
- 若 `describe_table` 不足以表达跨库关系，补充查询 `information_schema.columns`。
- 若用户已经给出明确 SQL 目标，不要为了“走流程”重复执行无价值步骤。

## 当前约束

- 只做只读查询，不做写入、DDL、DML。
- MySQL / PostgreSQL 语法要区分处理，不要混用。
- 单次样本预览控制在 100 条以内。
