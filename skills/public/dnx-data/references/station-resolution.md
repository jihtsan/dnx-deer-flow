# Temporary Station Resolution

Use this flow before every station-specific business API or database fallback while authorization mode is disabled.

## Fixed Context

- `DEFAULT_USER_ID=181`
- `DEFAULT_EMAIL=liguoqing@swdnkj.com`
- `AUTHORIZATION_MODE=disabled`

Keep these values as request context for integrations that explicitly support them. The current station-list and metric endpoints do not accept these fields, so do not append them as undocumented parameters.

## Resolve a Station

Call:

```text
GET {BASE_URL}/api/v1/station/list
```

Then resolve the user's station input against the returned records:

1. Prefer an exact `siteId` match.
2. Otherwise prefer an exact station-name match.
3. Otherwise use a case-insensitive keyword match against station name or address.
4. Never infer a station from a `siteId` prefix, city abbreviation, geographic guess, or outside knowledge.
5. If one record matches, continue with its exact `siteId` and state whether the match came from name or address.
6. If multiple records match, show concise choices and ask the user to select.
7. If no record matches, report that the station was not found.

When the user supplies no station, present a concise subset or searchable summary instead of dumping a large full response.

## Temporary Rules

- Do not call `/api/v1/permission/user-station`.
- Do not filter the station list by `user_id` or email.
- Do not state or imply that a listed station is authorized for the fixed user.
- Reuse the selected `siteId` only within the same environment and conversation.
- If `/station/list` fails, stop the station-specific flow; do not use database fallback to reconstruct the station list.
