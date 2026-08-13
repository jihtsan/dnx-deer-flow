---
name: data-service-api
description: Route, validate, preview, and call standard data service endpoints, and resolve stations from the bundled fixed catalog. Use when users ask to list or search stations, obtain tenantId and siteIdentify values, query data service metrics, select aggregate, grouped, detail, or snapshot endpoints, build station or measure payloads, validate group formats, inspect endpoint schemas, or execute authenticated POST JSON requests through the skill-local .env file.
---

# Data Service API

Use the catalog to select one of 53 standard data service endpoints, validate its exact request schema, and preview or execute the request. Keep endpoint knowledge in references and deterministic protocol behavior in the Python script.

## Station Catalog

Resolve station input from the bundled fixed catalog before constructing a `stations` request. Treat `references/station-catalog.json` as the queryable source and `assets/iot-device-stations.sql` as the canonical SQL template. The template is deduplicated by `siteIdentify`: prefer the record with more populated fields, then the longer station name.

List every deduplicated station record:

```bash
python3 .codex/skills/data-service-api/scripts/station_catalog.py list
```

Search station code, name, or tenant ID:

```bash
python3 .codex/skills/data-service-api/scripts/station_catalog.py search "南京"
python3 .codex/skills/data-service-api/scripts/station_catalog.py show NTHMQZFGCZ --json
```

Generate one object for an endpoint's `stations` array:

```bash
python3 .codex/skills/data-service-api/scripts/station_catalog.py payload ZSHYZYFGCZ
```

If future template edits introduce duplicate identifiers, compare populated fields first and station-name length second before rebuilding. If the correct record remains unclear, display all candidates and require `--source-line`; never guess between equally qualified conflicting records. Keep `tenantId` as a JSON string. Regenerate the reference after intentionally editing the SQL template:

```bash
python3 .codex/skills/data-service-api/scripts/station_catalog.py build
```

## Profit Semantics

Choose the profit scope before choosing by grouping capability. Treat the following distinction as a business contract:

- Use endpoint `6` (`/indicators/stationProfitSummary`, `profit_scope=order-inclusive`) for an unqualified request for "毛利" or "总毛利". Its `totalProfit` is the order-inclusive station gross profit: `totalElectricityProfit + totalService`. It accepts an optional daily or monthly `group`.
- Use endpoint `14` (`/indicators/stationProfitMinuteSummay`, `profit_scope=base-equipment`) only for base-equipment or supply-side profit broken into `gridProfit`, `pvProfit`, and `stProfit`. Its `profit` is their sum and excludes order-side service fees; do not present it as the station's complete gross profit despite the original field description "总毛利".
- Use endpoints `7` and `8` (`profit_scope=profit-items`) for supply-item aggregate or detail analysis. They expose service-fee fields but do not support `group`.

Never select endpoint `14` merely because the user requests daily grouping. For daily complete gross profit, use endpoint `6` with `group=%Y-%m-%d`.

Search by the explicit business scope when profit endpoints are candidates:

```bash
python3 .codex/skills/data-service-api/scripts/data_service.py search \
  --query "订单 总毛利" \
  --profit-scope order-inclusive \
  --entity-kind stations \
  --json
```

## Environment

Read credentials only from `.env` in this skill directory or from process environment variables. The process environment takes precedence.

Required for execution:

```dotenv
DATA_SERVICE_BASE_URL=https://host/api/api_platform/openapi
DATA_SERVICE_APP_KEY=
DATA_SERVICE_APP_SECRET=
DATA_SERVICE_TIMEOUT_SECONDS=30
```

Run the environment check before an authenticated call:

```bash
python3 .codex/skills/data-service-api/scripts/check_env.py
```

Report only missing variable names. Never print App Key, App Secret, raw authentication headers, or credential values.

## Routing Workflow

1. Determine the requested metric and result shape: `summary`, `detail`, or `snapshot`.
2. Determine the entity kind: `stations`, `measures`, or `special`.
3. For station requests, resolve `tenantId` and `siteIdentify` from the fixed station catalog unless the user supplied both explicitly.
4. For profit requests, determine `profit_scope` before considering `group`; default unqualified station gross profit to `order-inclusive`.
5. Determine whether the user requested grouping.
   - Group requested: keep endpoints with `group_mode=optional|required`.
   - No group requested: exclude endpoints with `group_mode=required`.
   - Never treat an endpoint as permanently grouped merely because it accepts an optional `group` field.
6. Filter by `time_mode`, `namespace`, and `stability` when relevant.
7. Search the endpoint catalog and inspect the complete schema of the best candidate.
8. If multiple candidates remain, ask only for the missing distinction that changes endpoint selection.
9. Build the payload from the chosen endpoint's `request_fields`; do not reuse a payload from a similarly named endpoint.
10. Validate and preview the request.
11. Execute only when the user explicitly requests an actual call.

Read `references/routing-guide.md` when selecting between similar endpoints. Read `references/common-protocol.md` when working with authentication, request construction, pagination, or response handling. Use `references/endpoint-catalog.json` as the source of truth for exact paths, fields, required status, capabilities, and original spelling. Do not load the complete catalog into context; use `search` and `show` for targeted retrieval.

## Commands

Search by business text and structural capabilities:

```bash
python3 .codex/skills/data-service-api/scripts/data_service.py search \
  --query "场站 电量 电费" \
  --result-kind summary \
  --entity-kind stations \
  --group-mode required
```

Inspect one endpoint by ID, exact path, exact name, or unambiguous partial name:

```bash
python3 .codex/skills/data-service-api/scripts/data_service.py show 5
```

Validate a payload without using credentials or the network:

```bash
python3 .codex/skills/data-service-api/scripts/data_service.py validate 5 \
  --payload-file /absolute/path/request.json
```

Preview the final URL, generated headers, and JSON body. Secret values remain redacted:

```bash
python3 .codex/skills/data-service-api/scripts/data_service.py call 5 \
  --payload-file /absolute/path/request.json
```

Send the request only after explicit user intent:

```bash
python3 .codex/skills/data-service-api/scripts/data_service.py call 5 \
  --payload-file /absolute/path/request.json \
  --execute
```

## Request Rules

- Use `POST` with a JSON body for all catalog endpoints.
- Preserve endpoint paths and field spelling exactly, including apparent source typos.
- Treat `group` as one string, never a list or multiple fields.
- Allow only `%Y-%m`, `%Y-%m-%d`, `%Y-%m-%d %H`, `%Y-%m-%d %H:%M`, or `%Y-%m-%d %H:%M:%S`.
- Respect endpoint-specific `group_mode`: `forbidden`, `optional`, or `required`.
- Respect endpoint-specific conflicts such as `group` versus `groupDay`.
- Treat endpoint `6` as the default complete station gross-profit source and verify `totalProfit = totalElectricityProfit + totalService` when exporting results.
- Treat endpoint `14` as base-equipment profit only and verify `profit = gridProfit + pvProfit + stProfit`; never imply that it includes order-side service fees.
- Exclude `planned` endpoints unless the user explicitly asks to include them.
- Do not infer unresolved time-zone, interval-boundary, retry, or type-coercion rules.
- Do not force pagination values into numbers; the source examples return strings.

## Response Handling

Treat HTTP failures and `code != 0` as errors. Preserve `X-Request-Id` in error or result objects for diagnosis. Return endpoint identity, HTTP status, request ID, business code, message, and raw `data`; do not normalize domain-specific result fields across unrelated endpoints.
