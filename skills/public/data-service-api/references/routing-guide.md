# 数据服务路由说明

## 1. 为什么不能只分为聚合和 Group By

`group` 是接口能力，不是稳定的接口类别：

- 32 个接口不接受 `group`，对应 `group_mode=forbidden`。
- 5 个接口强制要求 `group`，对应 `group_mode=required`。
- 16 个接口可选接受 `group`，对应 `group_mode=optional`。

当 `group` 可选时，同一个接口既可返回不分组汇总，也可返回分组结果。路由时必须同时考虑结果性质、请求主体、时间形态和版本。

## 2. 路由属性

| 属性 | 值 | 说明 |
| --- | --- | --- |
| `result_kind` | `summary/detail/snapshot` | 汇总、明细或当前/最终状态 |
| `group_mode` | `forbidden/optional/required` | 分组字段能力 |
| `entity_kind` | `stations/measures/special` | 场站、测点或特殊参数 |
| `time_mode` | `range/instant/none` | 时间范围、单时间点或无时间参数 |
| `namespace` | `indicators/indicatorsV2/hems` | 接口族或版本 |
| `stability` | `stable/planned` | 是否可默认暴露 |

## 3. 常见输入轮廓

- `stations_range`：25 个接口。通常需要 `stations`、`startTime`、`endTime`。
- `measures_range`：15 个接口。通常需要 `measures`、`startTime`、`endTime`。
- `stations_none`：8 个接口。场站快照、价格、基础信息或设备结构查询。
- `measures_none`：3 个接口。测点最终值查询。
- `special_none` / `special_instant`：2 个接口。使用独立标量字段，不套用场站或测点对象。

这些轮廓只用于缩小候选范围。最终必填字段必须以所选接口的 `request_fields` 为准。

## 4. 选择顺序

1. 用中文业务词、接口路径和返回字段搜索候选接口。
2. 涉及毛利时先确定 `profit_scope`，不要先按是否支持 `group` 选择接口。
3. 根据用户要总量、分组序列、明细还是最终状态确定 `result_kind`。
4. 根据输入对象确定 `entity_kind`。
5. 根据是否传 `group` 过滤 `group_mode`。
6. 根据时间参数过滤 `time_mode`。
7. 在 V1、V2、HEMS 之间按明确需求选择；没有明确版本时不要仅凭名称猜测。
8. 排除 `planned`，除非用户明确要求。
9. 候选仍多于一个时，比较返回字段和必传字段，只追问真正影响选择的差异。

## 5. 毛利接口口径

| `profit_scope` | 接口 | 业务口径 | 分组能力 | 使用规则 |
| --- | --- | --- | --- | --- |
| `order-inclusive` | `6 /indicators/stationProfitSummary` | 订单口径场站总毛利，包含电费收益与服务费；`totalProfit = totalElectricityProfit + totalService` | `optional` | 用户只说“毛利”“总毛利”或要求按日/月统计完整毛利时默认使用 |
| `base-equipment` | `14 /indicators/stationProfitMinuteSummay` | 基础设备/供给侧电费毛利；`profit = gridProfit + pvProfit + stProfit`，不含订单侧服务费 | `required` | 仅在用户明确要求网供、光伏、储能拆分或基础设备毛利时使用 |
| `profit-items` | `7 /indicators/stationProfitItemSummary` | 供给项汇总，包含电费收益、服务费和相关电量 | `forbidden` | 查询整个区间的供给项汇总时使用 |
| `profit-items` | `8 /indicators/stationProfitItemDetail` | 供给项与负荷维度明细，包含服务费 | `forbidden` | 查询毛利项明细时使用 |

接口 `14` 的源字段把 `profit` 描述为“总毛利”，但该名称不是完整业务口径。任何面向用户的报表都必须标注为“基础设备毛利”或“电费毛利”，不能标注为完整“场站总毛利”。

## 6. 示例

“查询场站电量电费，按天返回”应优先筛选：

```text
result_kind=summary
entity_kind=stations
group_mode=required 或 optional
time_mode=range
group=%Y-%m-%d
```

“查询测点最终值”应优先筛选：

```text
result_kind=snapshot
entity_kind=measures
time_mode=none
```

“查询场站收入总计”没有分组要求，应排除 `group_mode=required`，再比较候选接口的返回字段是否包含目标指标。

“查询场站 6 月每天的毛利”应选择接口 `6`，传 `group=%Y-%m-%d`，输出 `totalElectricityProfit`、`totalService` 和 `totalProfit`。不能因为接口 `14` 强制支持分组而选择它。

“查询场站 6 月每天的网供、光伏、储能基础设备毛利”才选择接口 `14`，并明确 `profit` 不含订单侧服务费。

## 7. 歧义处理

以下情况必须先澄清或展示候选：

- 相同业务名称同时存在 `indicators`、`indicatorsV2` 和 `hems`。
- 用户只说“汇总”，但没有说明是否按月、日或时分秒分组。
- 多个接口名称接近，但返回指标不同，例如收入、电量、电费、毛利或渠道。
- 场站标识可能来自 `tenantId`、`siteIdentify` 或 `stationId`，而所选接口要求不同。

不要根据拼写自动修改原始路径，也不要把一个接口的参数规则迁移到另一个接口。
