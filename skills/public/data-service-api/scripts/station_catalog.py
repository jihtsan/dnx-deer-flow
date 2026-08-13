#!/usr/bin/env python3
"""Build and query the fixed station catalog bundled with this skill."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
SQL_TEMPLATE_PATH = SKILL_DIR / "assets" / "iot-device-stations.sql"
CATALOG_PATH = SKILL_DIR / "references" / "station-catalog.json"
DECIMAL_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
INSERT_PATTERN = re.compile(
    rf"^\s*INSERT\s+INTO\s+public\.iot_device\s*"
    rf"\(\s*siteidentify\s*,\s*station_name\s*,\s*tenant_id\s*,\s*longitude\s*,\s*latitude\s*\)\s*"
    rf"VALUES\s*\(\s*'(?P<site>(?:''|[^'])*)'\s*,\s*'(?P<name>(?:''|[^'])*)'\s*,\s*"
    rf"(?P<tenant>\d+)\s*,\s*(?P<longitude>{DECIMAL_PATTERN}|null)\s*,\s*"
    rf"(?P<latitude>{DECIMAL_PATTERN}|null)\s*\)\s*;\s*$",
    re.IGNORECASE,
)


class StationCatalogError(Exception):
    """Raised when the station template or catalog cannot be used safely."""


@dataclass(frozen=True)
class StationRecord:
    source_line: int
    site_identify: str
    station_name: str
    tenant_id: str
    longitude: str | None
    latitude: str | None

    def signature(self) -> tuple[str, str, str | None, str | None]:
        return self.station_name, self.tenant_id, self.longitude, self.latitude


def _decode_sql_string(value: str) -> str:
    return value.replace("''", "'")


def _nullable_decimal(value: str) -> str | None:
    return None if value.lower() == "null" else value


def parse_sql_template(path: Path = SQL_TEMPLATE_PATH) -> list[StationRecord]:
    records: list[StationRecord] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        match = INSERT_PATTERN.fullmatch(raw_line)
        if not match:
            raise StationCatalogError(
                f"Unsupported SQL template syntax at {path}:{line_number}"
            )
        records.append(
            StationRecord(
                source_line=line_number,
                site_identify=_decode_sql_string(match.group("site")),
                station_name=_decode_sql_string(match.group("name")),
                tenant_id=match.group("tenant"),
                longitude=_nullable_decimal(match.group("longitude")),
                latitude=_nullable_decimal(match.group("latitude")),
            )
        )
    if not records:
        raise StationCatalogError(f"No station records were found in {path}")
    return records


def build_catalog(records: list[StationRecord]) -> dict[str, Any]:
    by_site: dict[str, list[StationRecord]] = defaultdict(list)
    for record in records:
        by_site[record.site_identify].append(record)

    duplicate_sites = {site: items for site, items in by_site.items() if len(items) > 1}
    conflicting_sites = {
        site: items
        for site, items in duplicate_sites.items()
        if len({item.signature() for item in items}) > 1
    }
    output_records = []
    for record in records:
        siblings = by_site[record.site_identify]
        output_records.append(
            {
                "sourceLine": record.source_line,
                "siteIdentify": record.site_identify,
                "stationName": record.station_name,
                "tenantId": record.tenant_id,
                "longitude": record.longitude,
                "latitude": record.latitude,
                "duplicateSiteIdentify": len(siblings) > 1,
                "conflictingRecord": record.site_identify in conflicting_sites,
            }
        )

    return {
        "schemaVersion": 1,
        "sourceAsset": "assets/iot-device-stations.sql",
        "recordCount": len(records),
        "uniqueSiteIdentifyCount": len(by_site),
        "tenantCount": len({record.tenant_id for record in records}),
        "duplicateSiteIdentifyCount": len(duplicate_sites),
        "conflictingSiteIdentifyCount": len(conflicting_sites),
        "duplicateSiteIdentifies": sorted(duplicate_sites),
        "conflictingSiteIdentifies": sorted(conflicting_sites),
        "records": output_records,
    }


def write_catalog(source: Path = SQL_TEMPLATE_PATH, output: Path = CATALOG_PATH) -> dict[str, Any]:
    catalog = build_catalog(parse_sql_template(source))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return catalog


class StationCatalog:
    def __init__(self, path: Path = CATALOG_PATH):
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload.get("records")
        if not isinstance(records, list):
            raise StationCatalogError(f"Catalog records are missing from {path}")
        self.metadata = {key: value for key, value in payload.items() if key != "records"}
        self.records: list[dict[str, Any]] = records

    def list(self, *, tenant_id: str | None = None) -> list[dict[str, Any]]:
        if tenant_id is None:
            return list(self.records)
        return [record for record in self.records if record["tenantId"] == tenant_id]

    def search(self, query: str) -> list[dict[str, Any]]:
        tokens = [token.casefold() for token in query.split() if token]
        if not tokens:
            return list(self.records)
        matches = []
        for record in self.records:
            searchable = " ".join(
                [record["siteIdentify"], record["stationName"], record["tenantId"]]
            ).casefold()
            if all(token in searchable for token in tokens):
                matches.append(record)
        return matches

    def find(self, selector: str) -> list[dict[str, Any]]:
        exact_site = [
            record for record in self.records if record["siteIdentify"] == selector
        ]
        if exact_site:
            return exact_site
        folded = selector.casefold()
        exact_folded_site = [
            record
            for record in self.records
            if record["siteIdentify"].casefold() == folded
        ]
        if exact_folded_site:
            return exact_folded_site
        exact_name = [
            record for record in self.records if record["stationName"] == selector
        ]
        if exact_name:
            return exact_name
        return self.search(selector)

    def payload(self, selector: str, *, source_line: int | None = None) -> dict[str, str]:
        matches = self.find(selector)
        if source_line is not None:
            matches = [record for record in matches if record["sourceLine"] == source_line]
        if not matches:
            suffix = f" at source line {source_line}" if source_line is not None else ""
            raise StationCatalogError(f"No station matched {selector!r}{suffix}")
        if len(matches) > 1:
            lines = ", ".join(str(record["sourceLine"]) for record in matches)
            raise StationCatalogError(
                f"Station selector {selector!r} is ambiguous; choose --source-line from: {lines}"
            )
        record = matches[0]
        return {
            "tenantId": record["tenantId"],
            "siteIdentify": record["siteIdentify"],
        }


def _print_records(records: list[dict[str, Any]], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"count": len(records), "records": records}, ensure_ascii=False, indent=2))
        return
    if not records:
        print("No stations matched.")
        return
    print("LINE\tSITE_IDENTIFY\tTENANT_ID\tLONGITUDE\tLATITUDE\tSTATION_NAME")
    for record in records:
        longitude = record["longitude"] if record["longitude"] is not None else "null"
        latitude = record["latitude"] if record["latitude"] is not None else "null"
        print(
            f"{record['sourceLine']}\t{record['siteIdentify']}\t{record['tenantId']}\t"
            f"{longitude}\t{latitude}\t{record['stationName']}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Regenerate JSON from the fixed SQL template")
    build.add_argument("--source", type=Path, default=SQL_TEMPLATE_PATH)
    build.add_argument("--output", type=Path, default=CATALOG_PATH)

    list_parser = subparsers.add_parser("list", help="List all station records")
    list_parser.add_argument("--tenant-id")
    list_parser.add_argument("--json", action="store_true", dest="as_json")

    search = subparsers.add_parser("search", help="Search station code, name, or tenant")
    search.add_argument("query")
    search.add_argument("--json", action="store_true", dest="as_json")

    show = subparsers.add_parser("show", help="Show exact or partial station matches")
    show.add_argument("selector")
    show.add_argument("--json", action="store_true", dest="as_json")

    payload = subparsers.add_parser("payload", help="Create one API station object")
    payload.add_argument("selector")
    payload.add_argument("--source-line", type=int)

    subparsers.add_parser("stats", help="Show station catalog metadata")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build":
            catalog = write_catalog(args.source, args.output)
            print(json.dumps({key: value for key, value in catalog.items() if key != "records"}, ensure_ascii=False, indent=2))
            return 0

        catalog = StationCatalog()
        if args.command == "list":
            _print_records(catalog.list(tenant_id=args.tenant_id), as_json=args.as_json)
        elif args.command == "search":
            _print_records(catalog.search(args.query), as_json=args.as_json)
        elif args.command == "show":
            _print_records(catalog.find(args.selector), as_json=args.as_json)
        elif args.command == "payload":
            print(json.dumps(catalog.payload(args.selector, source_line=args.source_line), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(catalog.metadata, ensure_ascii=False, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, StationCatalogError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
