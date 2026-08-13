from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from data_service import (  # noqa: E402
    DataServiceClient,
    EndpointCatalog,
    RemoteCallError,
    Settings,
    ValidationError,
)


class EndpointCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = EndpointCatalog()

    def test_catalog_capability_counts(self):
        counts = {"forbidden": 0, "optional": 0, "required": 0}
        for endpoint in self.catalog.endpoints:
            counts[endpoint["capabilities"]["group_mode"]] += 1
        self.assertEqual(counts, {"forbidden": 32, "optional": 16, "required": 5})

    def test_search_uses_structural_filters(self):
        matches = self.catalog.search(
            query="电量 电费",
            result_kind="summary",
            group_mode="required",
            entity_kind="stations",
        )
        self.assertEqual({endpoint["id"] for endpoint in matches}, {5, 32, 33})

    def test_order_inclusive_profit_search_prefers_complete_station_profit(self):
        matches = self.catalog.search(
            query="订单 总毛利",
            entity_kind="stations",
            profit_scope="order-inclusive",
        )
        self.assertEqual([endpoint["id"] for endpoint in matches], [6])
        self.assertIn("服务费", matches[0]["capabilities"]["profit_note"])

    def test_base_equipment_profit_search_is_explicit(self):
        matches = self.catalog.search(
            query="基础设备 毛利",
            entity_kind="stations",
            profit_scope="base-equipment",
        )
        self.assertEqual([endpoint["id"] for endpoint in matches], [14])
        self.assertIn("不含订单侧服务费", matches[0]["capabilities"]["profit_note"])

    def test_planned_endpoints_require_explicit_inclusion(self):
        hidden = self.catalog.search(query="供给 消费")
        visible = self.catalog.search(query="供给 消费", include_planned=True)
        self.assertNotIn(10, {endpoint["id"] for endpoint in hidden})
        self.assertIn(10, {endpoint["id"] for endpoint in visible})

    def test_required_group_and_nested_fields_are_validated(self):
        endpoint = self.catalog.resolve(5)
        with self.assertRaises(ValidationError) as caught:
            self.catalog.validate(endpoint, {"stations": [{}]})
        self.assertIn("Missing required field: group", caught.exception.errors)
        self.assertIn(
            "Missing required field: stations.tenantId at stations[0]",
            caught.exception.errors,
        )

    def test_optional_group_can_be_omitted(self):
        endpoint = self.catalog.resolve(41)
        payload = {
            "measures": [{"deviceIdentify": "d1", "propertyIdentify": "p1"}],
            "startTime": "2026-07-01 00:00:00",
            "endTime": "2026-07-02 00:00:00",
        }
        self.catalog.validate(endpoint, payload)

    def test_group_and_group_day_conflict(self):
        endpoint = self.catalog.resolve(23)
        payload = {
            "stations": [{}],
            "endTime": "2026-07-02 00:00:00",
            "group": "%Y-%m-%d",
            "groupDay": 1,
        }
        with self.assertRaises(ValidationError) as caught:
            self.catalog.validate(endpoint, payload)
        self.assertIn("Fields group and groupDay cannot be used together", caught.exception.errors)


class DataServiceClientTests(unittest.TestCase):
    def setUp(self):
        self.catalog = EndpointCatalog()
        self.settings = Settings(
            base_url="https://example.test/api/api_platform/openapi",
            app_key="test-key",
            app_secret="test-secret",
            timeout_seconds=5,
        )
        self.endpoint = self.catalog.resolve(5)
        self.payload = {
            "stations": [{"tenantId": "t1", "siteIdentify": "s1"}],
            "startTime": "2026-07-01 00:00:00",
            "endTime": "2026-07-31 23:59:59",
            "group": "%Y-%m-%d",
        }

    def test_preview_redacts_credentials(self):
        client = DataServiceClient(self.catalog, self.settings)
        preview = client.preview(self.endpoint, self.payload)
        self.assertEqual(preview["headers"]["X-App-Key"], "<configured>")
        self.assertEqual(preview["headers"]["X-App-Secret"], "***")
        self.assertEqual(len(preview["headers"]["X-App-Nonce"]), 12)
        self.assertEqual(len(preview["headers"]["X-App-Timestamp"]), 13)

    def test_execute_uses_injected_transport(self):
        captured = {}

        def fake_transport(url, headers, body, timeout):
            captured.update(
                {"url": url, "headers": headers, "body": json.loads(body), "timeout": timeout}
            )
            response = json.dumps({"code": 0, "msg": "success", "data": {"value": 7}})
            return 200, {"X-Request-Id": "req-1"}, response.encode()

        client = DataServiceClient(self.catalog, self.settings, transport=fake_transport)
        result = client.execute(self.endpoint, self.payload)
        self.assertTrue(captured["url"].endswith("/indicators/stationEnergySummary"))
        self.assertEqual(captured["headers"]["X-App-Secret"], "test-secret")
        self.assertEqual(captured["body"], self.payload)
        self.assertEqual(result["request_id"], "req-1")
        self.assertEqual(result["data"], {"value": 7})

    def test_remote_message_redacts_secret(self):
        def fake_transport(url, headers, body, timeout):
            response = json.dumps(
                {"code": 42202, "msg": "invalid test-secret", "data": None}
            )
            return 200, {}, response.encode()

        client = DataServiceClient(self.catalog, self.settings, transport=fake_transport)
        with self.assertRaisesRegex(RemoteCallError, r"invalid \*\*\*"):
            client.execute(self.endpoint, self.payload)


if __name__ == "__main__":
    unittest.main()
