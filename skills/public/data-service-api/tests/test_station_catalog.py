from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from station_catalog import (  # noqa: E402
    CATALOG_PATH,
    SQL_TEMPLATE_PATH,
    StationCatalog,
    build_catalog,
    parse_sql_template,
)


class StationTemplateTests(unittest.TestCase):
    def test_checked_in_catalog_matches_sql_template(self):
        generated = build_catalog(parse_sql_template(SQL_TEMPLATE_PATH))
        checked_in = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(checked_in, generated)

    def test_catalog_contains_only_unique_site_identifiers(self):
        catalog = build_catalog(parse_sql_template(SQL_TEMPLATE_PATH))
        self.assertEqual(catalog["recordCount"], 72)
        self.assertEqual(catalog["uniqueSiteIdentifyCount"], 72)
        self.assertEqual(catalog["duplicateSiteIdentifyCount"], 0)
        self.assertEqual(catalog["conflictingSiteIdentifyCount"], 0)
        self.assertEqual(catalog["duplicateSiteIdentifies"], [])
        self.assertEqual(catalog["conflictingSiteIdentifies"], [])


class StationCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = StationCatalog()

    def test_list_returns_every_source_record(self):
        self.assertEqual(len(self.catalog.list()), 72)

    def test_search_matches_station_name(self):
        matches = self.catalog.search("南京 江北")
        self.assertEqual([record["siteIdentify"] for record in matches], ["NJQLHGCZ"])

    def test_payload_uses_string_tenant_id(self):
        payload = self.catalog.payload("ZSHYZYFGCZ")
        self.assertEqual(
            payload,
            {
                "tenantId": "1937774603663912962",
                "siteIdentify": "ZSHYZYFGCZ",
            },
        )

    def test_deduplication_keeps_more_complete_record(self):
        payload = self.catalog.payload("NTHMQZFGCZ")
        self.assertEqual(
            payload,
            {"tenantId": "3662819686115328", "siteIdentify": "NTHMQZFGCZ"},
        )
        record = self.catalog.find("NTHMQZFGCZ")[0]
        self.assertEqual(record["longitude"], "121.191433554")
        self.assertEqual(record["latitude"], "31.877250190")

    def test_deduplication_keeps_longer_more_complete_record(self):
        records = self.catalog.find("HYXCGCZ")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["stationName"], "新运微电网-常州钟楼花园新村站")
        self.assertEqual(records[0]["tenantId"], "1800435403244253186")
        self.assertEqual(records[0]["longitude"], "119.923436026")


if __name__ == "__main__":
    unittest.main()
