---
name: dnx-data
description: Query DNX station, device, pricing, competitor, availability, charging, daily-average, comparison, trend, and SOC data. Use for DNX business questions involving stations, siteId, devices, measures, electricity prices, competitors, online or fault rates, charging, operations metrics, time comparisons, trends, or database-backed investigation. Prefer governed DNX HTTP APIs and use progressive read-only database discovery only when the APIs cannot answer the requested field or grain.
---

# DNX Data

Answer DNX data questions through business APIs first. Escalate to `db-query` only after proving that no existing API supplies the required data.

Set `BASE_URL` to the target `suwen-dnx-metric` service; default to `http://localhost:8081`. Treat the directory containing this file as `SKILL_DIR`, and the sibling `../db-query` directory as `DB_QUERY_SKILL`.

## HTTP Transport

- Use the `bash` tool and `curl` for every DNX API request, including both GET and POST endpoints.
- Never use `web_fetch` for DNX API requests. It sends the target to an external reader provider such as Jina, so its rejection of `localhost` says nothing about reachability from the local `bash` tool.
- Always attempt the required `curl` request before deciding that `BASE_URL` is unreachable.
- Do not infer current reachability from memory or from an earlier `web_fetch` failure. Only a failure from the current local `curl` attempt proves that the DNX service is unavailable for this run.
- Do not ask the user for another URL or copied JSON before this local `curl` attempt. If it fails, report the exact curl error without substituting a presumed platform restriction.
- Run `curl` from the DeerFlow execution environment. The address must be reachable from that environment; in a container deployment, use an operator-configured service name or host address instead of assuming that container-local `localhost` points to the DNX service.
- Use `curl -sS --fail-with-body` so connection failures and non-2xx responses remain distinguishable from a successful DNX response envelope.

Initialize the base URL before using it in the same shell invocation:

```bash
BASE_URL="${BASE_URL:-http://localhost:8081}"
curl -sS --fail-with-body "$BASE_URL/api/v1/station/list"
```

## Temporary Access Context

- Set `DEFAULT_USER_ID=181`.
- Set `DEFAULT_EMAIL=liguoqing@swdnkj.com`.
- Set authorization mode to disabled: do not call `/api/v1/permission/user-station`.
- Resolve stations from `/api/v1/station/list` before station-specific queries.
- Treat the fixed user values as context only. Do not add them to an endpoint or SQL unless that endpoint/table explicitly supports the field and the query requires it.

## Progressive Disclosure

Load only the references needed for the current question:

1. Read [capability-routing.md](references/capability-routing.md) to select the smallest sufficient business capability.
2. For any station-specific request, read [station-resolution.md](references/station-resolution.md) before accessing business data.
3. Read [station-api.md](references/station-api.md) only for station, device, price, competitor, or measure questions.
4. Read [operation-api.md](references/operation-api.md) only for metrics, comparisons, trends, or SOC questions.
5. Read [database-fallback.md](references/database-fallback.md) only after the fallback gate below passes.

Do not load all references preemptively.

## Workflow

1. Normalize the question into:
   - scope: global or a specific station;
   - subject: station, device, price, competitor, availability, charge, comparison, trend, SOC, or unsupported/raw field;
   - time range and grain;
   - requested filters, ranking, or comparison.
2. Call `/api/v1/station/list` and resolve the station when the scope is station-specific. Use the exact returned `siteId`.
3. Select one primary business API. Call additional APIs only when the answer genuinely combines separate capabilities.
4. Check the unified response envelope. Read `data` only when `code == 0`; otherwise report `message` and stop or recover as specified below.
5. Answer from the API when it provides the requested metric or source fields. Do not query the database merely to confirm a successful API result.
6. Apply the database fallback gate if the business API lacks the requested field, historical grain, or diagnostic evidence.
7. When fallback is allowed, follow `DB_QUERY_SKILL/SKILL.md`: discover the alias, search the relevant tables, describe them, and execute one bounded read-only query.
8. Return the result with its time range, scope, metric definition, source layer (`DNX API` or `database fallback`), and important data-quality caveats.

## Database Fallback Gate

Use database fallback only when at least one condition holds:

- no DNX API exposes the requested metric or dimension;
- the API exposes an aggregate but the user explicitly needs a lower-level diagnostic breakdown;
- the user asks to validate data quality, freshness, or a discrepancy against source tables;
- the user explicitly asks for raw-table or SQL investigation.

Do not fall back when:

- the station cannot be resolved from `/api/v1/station/list`;
- the station-list or business API is unavailable;
- an API request is invalid and can be corrected;
- the desired result can be derived safely from returned API fields;
- fallback would require a write, DDL/DML, an unbounded export, or a cross-data-source join.

State why fallback is needed before querying. Keep database fallback constrained to the station and time range resolved through the API flow.

## Guardrails

- Treat the backend as the authority for metric semantics and station-device ownership.
- Never place credentials, connection strings, full DDL, or unrestricted sample rows in prompts or answers.
- Do not call the permission API while the temporary authorization mode is disabled.
- Do not describe the returned station list as an authorized-station list.
- Keep the selected station scoped to the current environment and conversation.
- Distinguish `no data` from service failure, invalid input, and permission denial.
- Do not silently invent joins, units, time zones, metric definitions, site IDs, or device ownership.
- Limit trend requests to the API's supported range. Split only when semantics remain equivalent.
- Keep database samples at or below 100 rows and final queries bounded.

## Recovery

- Missing station: call `/api/v1/station/list` and present relevant matches.
- Ambiguous station name: present the matching stations and wait for a selection.
- Missing date range: infer only when the user's wording is conventional and disclose the range; otherwise ask one concise question.
- API validation error: correct the request once from the relevant API reference.
- Station-list or API connection/5xx error: report service unavailability; do not bypass it with direct database access.
- Unknown database table or column: return to `search_tables`, `search_columns`, or `describe_table`; do not guess.

## Response Contract

Give the business answer first. Then include only the evidence useful to audit it:

- station/global scope and exact `siteId` when applicable;
- inclusive date range and time grain;
- metric formula when derived, such as `faultRate = 1 - onlineRate`;
- source endpoint, or database alias plus table names when fallback was used;
- warnings for null station names, empty results, truncation, or raw-versus-governed semantic differences.
