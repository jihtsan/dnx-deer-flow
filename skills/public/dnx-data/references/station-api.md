# Station API

Base path: `{BASE_URL}/api/v1/station`

All endpoints return `{ "code": 0, "data": ..., "message": "ok" }` on success. Treat `code != 0` as a business failure.

Read `station-resolution.md` first for every station-specific call. Resolve the station from `/station/list` and retain its exact `siteId` even when a measure endpoint does not accept `siteId` itself.

## Endpoints

| Method and path | Parameters | Purpose |
|---|---|---|
| `GET /info` | `siteId` | Station details and device tree |
| `GET /list` | none | System station list and temporary station-resolution entry point |
| `GET /price-policy` | `siteId`, optional `date=YYYY-MM-DD` | Effective station time-of-use price slots |
| `GET /competitors` | `siteId`, optional `date=YYYY-MM-DD` | Effective competitor stations and price slots |
| `GET /device-measures` | `siteId`, `identify` | Measures defined on one device in the station device tree |
| `GET /device-measure-data` | `deviceIdentify`, `propertyIdentify`, `startTime`, `endTime`, optional `window` | One device/property time series |
| `POST /device-measure-data/batch` | JSON body below | Multiple device/property time series over one range |
| `GET /device-measure-latest` | `deviceIdentify`, `propertyIdentify` | Latest measure value |

`startTime` and `endTime` query parameters use `YYYY-MM-DD HH:mm:ss`. URL-encode spaces. `window` accepts only `5`, `10`, or `30` minutes and defaults to `5`.

Batch request:

```json
{
  "queries": [
    {"deviceIdentify": "PCS001", "propertyIdentify": "SOC"}
  ],
  "startTime": "2026-07-01T00:00:00",
  "endTime": "2026-07-02T00:00:00",
  "window": 5
}
```

## Device Workflow

1. Call `/info` to obtain the selected station's device tree.
2. Resolve a device `identify` from that tree.
3. Call `/device-measures` to discover valid property codes.
4. Query history, batch history, or latest data.

Do not accept an arbitrary device identifier as proof of station ownership. Retain the selected station context in the answer and report an ownership mismatch rather than trying other stations.

## Request Examples

```bash
curl -sG "$BASE_URL/api/v1/station/info" --data-urlencode "siteId=$SITE_ID"
```

```bash
curl -sG "$BASE_URL/api/v1/station/device-measure-data" \
  --data-urlencode "deviceIdentify=$DEVICE_ID" \
  --data-urlencode "propertyIdentify=$PROPERTY_ID" \
  --data-urlencode "startTime=$START_TIME" \
  --data-urlencode "endTime=$END_TIME" \
  --data-urlencode "window=5"
```
