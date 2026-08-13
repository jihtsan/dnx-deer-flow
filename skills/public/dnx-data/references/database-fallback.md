# Read-Only Database Fallback

Read this file only after the fallback gate in `SKILL.md` passes.

## Boundary

Database fallback is a diagnostic and capability-gap path, not a substitute for business APIs. It may expose raw fields whose grain, freshness, and semantics differ from governed API metrics.

Authorization checks are temporarily disabled. Before fallback, resolve a station through `/station/list` and constrain the database investigation to that station and requested time range. Never use fallback to bypass station-list or business-service unavailability.

Use `DEFAULT_USER_ID=181` and `DEFAULT_EMAIL=liguoqing@swdnkj.com` only when a confirmed table column and query purpose require them. Do not assume that every DNX table is user-scoped.

## Reuse db-query

Read the sibling `../db-query/SKILL.md` completely and use its existing script. Do not duplicate credentials, aliases, SQL validation, or connection logic inside `dnx-data`.

Resolve paths from the Skill directory:

```text
DB_QUERY_SKILL = SKILL_DIR/../db-query
DB_QUERY_SCRIPT = DB_QUERY_SKILL/scripts/db_query.py
```

Follow this progressive sequence and skip a step only when its answer is already proven:

```bash
python3 "$DB_QUERY_SCRIPT" list_aliases
python3 "$DB_QUERY_SCRIPT" search_tables <alias> <keyword>
python3 "$DB_QUERY_SCRIPT" search_columns <alias> <keyword>
python3 "$DB_QUERY_SCRIPT" describe_table <alias> <schema.table>
python3 "$DB_QUERY_SCRIPT" sample_data <alias> <schema.table> 20
python3 "$DB_QUERY_SCRIPT" query <alias> "<single bounded SELECT>"
```

## Known Search Anchors

Treat these as search hints, not permission grants or guaranteed aliases:

| Domain | Likely alias | Search anchors |
|---|---|---|
| DNX application tables | `suwen_dnx_metric_pg` | `dnx_station_info`, `dnx_users`, `dnx_user_station`, `metric_pile_availability_daily`, `metric_pile_charge_daily`, `price_policy`, `dnx_dws_es_5min_base` |
| Doris operational facts | discover from `list_aliases` | `dws_cal_factor_full_result_d`, `dws_top_level_device_measure_final_h` |
| CTMS source investigation | discover from `list_aliases` | station, pile, user, and permission source tables |

Always confirm a table with `search_tables` or `information_schema` before writing SQL. The backend's Hutool alias names are not guaranteed to match the `db-query` aliases.

## Query Rules

- Use only one `SELECT` or `WITH ... SELECT` statement.
- Select explicit columns; avoid `SELECT *` except a bounded schema/sample inspection.
- Add a time predicate for fact tables and a row limit for detail queries.
- Keep samples at or below 100 rows.
- Do not join PostgreSQL, Doris, and MySQL in one query.
- For small cross-source investigations, query each source separately and compare results in memory while preserving source labels.
- Do not recreate a governed metric from raw rows when its business formula is unknown. Return the raw evidence and the semantic gap.

## Fallback Result

Report:

- why the API was insufficient;
- alias and tables queried;
- SQL or a concise query description;
- row limit and time range;
- whether the result is raw, reconciled, or directly comparable to a governed API metric.
