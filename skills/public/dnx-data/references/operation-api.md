# Operation API

All endpoints return `{ "code": 0, "data": ..., "message": "ok" }` on success. Treat `code != 0` as a business failure.

Read `station-resolution.md` first for every station-specific route. Resolve the exact `siteId` from `/station/list`. Current global metric routes are not station-scoped.

## Metric Endpoints

| Method and path | Scope | Body |
|---|---|---|
| `POST /api/v1/metric/availability/station` | station | metric body with `siteId` |
| `POST /api/v1/metric/availability/global` | global | metric body without `siteId` |
| `POST /api/v1/metric/charge/station` | station | metric body with `siteId` |
| `POST /api/v1/metric/charge/global` | global | metric body without `siteId` |

Metric body:

```json
{
  "siteId": "JTTYGGCCZ",
  "startDate": "2026-07-01",
  "endDate": "2026-07-14",
  "sortBy": "onlineRate",
  "sortOrder": "asc",
  "limit": 5
}
```

`siteId` is station-only. `sortBy`, `sortOrder`, and `limit` apply only to the global availability station list. Supported `sortBy` values are `onlineRate`, `faultCount`, `mttr`, and `pileCount`. Sorting and limiting do not change the global summary aggregation.

## Daily Average and Comparison

| Method and path | Purpose |
|---|---|
| `POST /api/v1/daily-avg` | One period's daily-average operations, energy, income, cost, and intraday profile |
| `POST /api/v1/comparison/daily-avg` | Compare two periods |
| `POST /api/v1/comparison/competitor` | Compare station and competitor prices across two periods |

Daily-average body:

```json
{
  "siteId": "JTTYGGCCZ",
  "startDate": "2026-07-01",
  "endDate": "2026-07-14",
  "excludedDates": []
}
```

Comparison body:

```json
{
  "siteId": "JTTYGGCCZ",
  "periodAStart": "2026-06-01",
  "periodAEnd": "2026-06-14",
  "periodBStart": "2026-07-01",
  "periodBEnd": "2026-07-14",
  "excludedDatesA": [],
  "excludedDatesB": [],
  "pricePolicySource": "A"
}
```

`pricePolicySource` accepts `A` or `B` and selects the reference period for station pricing.

## Trend

| Method and path | Grain |
|---|---|
| `POST /api/v1/trend/daily` | One point per day |
| `POST /api/v1/trend/timeseries` | 30-minute intraday points |

Both use:

```json
{
  "siteId": "JTTYGGCCZ",
  "startDate": "2026-07-01",
  "endDate": "2026-07-14"
}
```

Keep the inclusive range within 14 days. Do not automatically stitch larger ranges unless the user accepts that separately executed windows may have different freshness.

## SOC Snapshot

`POST /api/v1/soc/snapshot`

```json
{
  "siteId": "JTTYGGCCZ",
  "startDate": "2026-07-01",
  "endDate": "2026-07-14",
  "hour": 14,
  "minute": 0
}
```

`hour` accepts `0..23`; `minute` accepts `0..59` and the backend rounds it down to a five-minute boundary.

## Request Example

```bash
curl -sS -X POST "$BASE_URL/api/v1/metric/availability/station" \
  -H "Content-Type: application/json" \
  -d "$REQUEST_JSON"
```
