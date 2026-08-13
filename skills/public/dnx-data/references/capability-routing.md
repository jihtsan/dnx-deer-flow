# Capability Routing

Use this file to map a business question to the smallest sufficient DNX capability. Read the linked API reference only after selecting a route.

## Routing Table

| User intent | Preferred capability | Reference |
|---|---|---|
| List stations or resolve a station name | `station/list` | `station-resolution.md` |
| Station capacity, power, pile count, address, device tree | `station/info` | `station-api.md` |
| Device definitions or available measures | `station/device-measures` | `station-api.md` |
| Device measure history or latest value | `station/device-measure-data`, batch, or latest | `station-api.md` |
| Station electricity price | `station/price-policy` | `station-api.md` |
| Nearby competitors and their prices | `station/competitors` | `station-api.md` |
| Availability, online rate, fault rate/count/duration, MTTR, MTBF | `metric/availability/station` or `metric/availability/global` | `operation-api.md` |
| Charge amount, sessions, duration, users, pile utilization | `metric/charge/station` or `metric/charge/global` | `operation-api.md` |
| Daily average, energy structure, income, cost, profit | `daily-avg` | `operation-api.md` |
| Compare two periods | `comparison/daily-avg` | `operation-api.md` |
| Compare station and competitor prices across periods | `comparison/competitor` | `operation-api.md` |
| Daily or 30-minute operating trend | `trend/daily` or `trend/timeseries` | `operation-api.md` |
| SOC at a fixed time across dates | `soc/snapshot` | `operation-api.md` |
| Unsupported field, lower grain, freshness, source discrepancy | Database fallback gate | `database-fallback.md` |

## Intent Normalization

Map these phrases to availability instead of treating them as separate metrics:

| Phrase | Field or derivation |
|---|---|
| 可用率、在线率 | `onlineRate` or `avgOnlineRate` |
| 故障率、不可用率、离线率 | `1 - onlineRate` |
| 故障次数、告警次数 | `faultCount`, `totalFaultCount`, or `warnCount` |
| 故障时长、离线时长 | `offlineDurationSec` |
| 平均恢复时间 | `mttrSec` or `avgMttrSec` |
| 平均无故障时间 | `mtbfSec` or `avgMtbfSec` |
| 最差、最不稳定、故障最多 | Global availability ranking |

For fault-rate answers, calculate `(1 - onlineRate) * 100%` and state the formula. Do not claim that the backend stores a separate fault-rate metric.

## Route Selection Rules

- Prefer station routes when the user identifies one station.
- Prefer global routes only for cross-station summaries or rankings permitted by product policy.
- Prefer `daily-avg` for one period and `comparison/daily-avg` for two periods.
- Prefer `trend/daily` for day-over-day change and `trend/timeseries` for intraday shape.
- Use the batch measure endpoint when multiple device/property pairs share one time range.
- Do not combine raw database facts with governed API metrics without labeling the semantic difference.

## Data Quality

If a global availability result has a null `stationName`, display its `siteId` as the fallback identifier and flag a station-ledger mapping issue. Do not present an unnamed row as an unqualified business conclusion.
