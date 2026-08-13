#!/usr/bin/env python3
"""Search, validate, preview, and call the standard data service catalog."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


SKILL_DIR = Path(__file__).resolve().parents[1]
CATALOG_PATH = SKILL_DIR / "references" / "endpoint-catalog.json"
ENV_PATH = SKILL_DIR / ".env"
CANONICAL_BASE_PATH = "/api/api_platform/openapi"


class DataServiceError(Exception):
    """Base error for local validation and remote calls."""


class CatalogError(DataServiceError):
    pass


class ValidationError(DataServiceError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


class ConfigurationError(DataServiceError):
    pass


class RemoteCallError(DataServiceError):
    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        business_code: Any = None,
        request_id: str | None = None,
    ):
        self.http_status = http_status
        self.business_code = business_code
        self.request_id = request_id
        super().__init__(message)


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


@dataclass(frozen=True)
class Settings:
    base_url: str
    app_key: str
    app_secret: str
    timeout_seconds: float

    @classmethod
    def load(cls, *, require_credentials: bool) -> "Settings":
        file_values = parse_env(ENV_PATH)
        values = {**file_values, **os.environ}
        base_url = values.get("DATA_SERVICE_BASE_URL", "").rstrip("/")
        app_key = values.get("DATA_SERVICE_APP_KEY", "")
        app_secret = values.get("DATA_SERVICE_APP_SECRET", "")
        timeout_raw = values.get("DATA_SERVICE_TIMEOUT_SECONDS", "30")

        try:
            timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise ConfigurationError("DATA_SERVICE_TIMEOUT_SECONDS must be numeric") from exc
        if timeout_seconds <= 0:
            raise ConfigurationError("DATA_SERVICE_TIMEOUT_SECONDS must be greater than zero")

        missing = []
        if require_credentials:
            for name, value in (
                ("DATA_SERVICE_BASE_URL", base_url),
                ("DATA_SERVICE_APP_KEY", app_key),
                ("DATA_SERVICE_APP_SECRET", app_secret),
            ):
                if not value:
                    missing.append(name)
        if missing:
            raise ConfigurationError(
                "Missing required environment variables: " + ", ".join(missing)
            )

        if base_url:
            parsed = urlparse(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ConfigurationError("DATA_SERVICE_BASE_URL must be an absolute HTTP(S) URL")
            if not parsed.path.rstrip("/").endswith(CANONICAL_BASE_PATH):
                raise ConfigurationError(
                    f"DATA_SERVICE_BASE_URL must end with {CANONICAL_BASE_PATH}"
                )

        return cls(
            base_url=base_url,
            app_key=app_key,
            app_secret=app_secret,
            timeout_seconds=timeout_seconds,
        )


def is_missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


class EndpointCatalog:
    def __init__(self, path: Path = CATALOG_PATH):
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.protocol = payload["protocol"]
        self.endpoints = payload["endpoints"]

    def resolve(self, selector: str | int) -> dict[str, Any]:
        text = str(selector).strip()
        if text.isdigit():
            endpoint_id = int(text)
            for endpoint in self.endpoints:
                if endpoint["id"] == endpoint_id:
                    return endpoint
            raise CatalogError(f"Endpoint id {endpoint_id} was not found")

        exact = [
            endpoint
            for endpoint in self.endpoints
            if text in {endpoint["name"], endpoint["path"]}
        ]
        if len(exact) == 1:
            return exact[0]

        lowered = text.lower()
        partial = [
            endpoint
            for endpoint in self.endpoints
            if lowered in endpoint["name"].lower() or lowered in endpoint["path"].lower()
        ]
        if len(partial) == 1:
            return partial[0]
        if partial:
            candidates = ", ".join(f"{item['id']}:{item['name']}" for item in partial[:8])
            raise CatalogError(f"Endpoint selector is ambiguous: {candidates}")
        raise CatalogError(f"Endpoint selector was not found: {text}")

    def search(
        self,
        *,
        query: str | None = None,
        result_kind: str | None = None,
        group_mode: str | None = None,
        entity_kind: str | None = None,
        time_mode: str | None = None,
        namespace: str | None = None,
        profit_scope: str | None = None,
        include_planned: bool = False,
    ) -> list[dict[str, Any]]:
        tokens = [token.lower() for token in (query or "").split() if token]
        matches = []
        for endpoint in self.endpoints:
            capabilities = endpoint["capabilities"]
            if not include_planned and capabilities["stability"] == "planned":
                continue
            filters = {
                "result_kind": result_kind,
                "group_mode": group_mode,
                "entity_kind": entity_kind,
                "time_mode": time_mode,
                "namespace": namespace,
                "profit_scope": profit_scope,
            }
            if any(
                value and capabilities.get(key) != value
                for key, value in filters.items()
            ):
                continue
            searchable = " ".join(
                [
                    endpoint["name"],
                    endpoint["path"],
                    capabilities.get("profit_note", ""),
                    *(
                        f"{field['name']} {field.get('description') or ''}"
                        for field in endpoint["request_fields"] + endpoint["response_fields"]
                    ),
                ]
            ).lower()
            if tokens and not all(token in searchable for token in tokens):
                continue
            matches.append(endpoint)
        return matches

    def validation_errors(
        self,
        endpoint: dict[str, Any],
        params: dict[str, Any],
        *,
        allow_extra: bool = False,
    ) -> list[str]:
        if not isinstance(params, dict):
            return ["Payload must be a JSON object"]

        errors: list[str] = []
        fields = endpoint["request_fields"]
        top_fields = {field["name"]: field for field in fields if "." not in field["name"]}
        allowed = set(top_fields) | {"_pageNum", "_pageSize"}
        if not allow_extra:
            unknown = sorted(set(params) - allowed)
            if unknown:
                errors.append("Unknown fields: " + ", ".join(unknown))

        for name, field in top_fields.items():
            value = params.get(name)
            if field.get("required") == "是" and is_missing(value):
                errors.append(f"Missing required field: {name}")
                continue
            if not is_missing(value):
                expected = field.get("type")
                if expected == "List" and not isinstance(value, list):
                    errors.append(f"Field {name} must be a list")
                elif expected == "String" and not isinstance(value, str):
                    errors.append(f"Field {name} must be a string")
                elif expected == "Int" and (not isinstance(value, int) or isinstance(value, bool)):
                    errors.append(f"Field {name} must be an integer")
                elif expected == "Boolean" and not isinstance(value, bool):
                    errors.append(f"Field {name} must be a boolean")
                elif expected == "Decimal" and (
                    not isinstance(value, (int, float, str)) or isinstance(value, bool)
                ):
                    errors.append(f"Field {name} must be numeric or a decimal string")

        for field in fields:
            name = field["name"]
            if "." not in name or field.get("required") != "是":
                continue
            parent_name, child_name = name.split(".", 1)
            parent = params.get(parent_name)
            if is_missing(parent):
                continue
            items = parent if isinstance(parent, list) else [parent]
            for index, item in enumerate(items):
                if not isinstance(item, dict) or is_missing(item.get(child_name)):
                    errors.append(f"Missing required field: {name} at {parent_name}[{index}]")

        capabilities = endpoint["capabilities"]
        group_mode = capabilities["group_mode"]
        group = params.get("group")
        if group_mode == "required" and is_missing(group):
            errors.append("Missing required field: group")
        elif group_mode == "forbidden" and "group" in params:
            errors.append("Field group is not supported by this endpoint")
        elif isinstance(group, str):
            allowed_formats = self.protocol["group"]["allowed_formats"]
            if group not in allowed_formats:
                errors.append(
                    "Field group must be one of: " + ", ".join(allowed_formats)
                )

        for left, right in capabilities.get("parameter_conflicts", []):
            if not is_missing(params.get(left)) and not is_missing(params.get(right)):
                errors.append(f"Fields {left} and {right} cannot be used together")
        return errors

    def validate(
        self,
        endpoint: dict[str, Any],
        params: dict[str, Any],
        *,
        allow_extra: bool = False,
    ) -> None:
        errors = self.validation_errors(endpoint, params, allow_extra=allow_extra)
        if errors:
            raise ValidationError(errors)


Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, dict[str, str], bytes]]


def urllib_transport(
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()
    except urllib.error.URLError as exc:
        raise RemoteCallError(f"Data service request failed: {exc.reason}") from exc


class DataServiceClient:
    def __init__(
        self,
        catalog: EndpointCatalog,
        settings: Settings,
        *,
        transport: Transport = urllib_transport,
    ):
        self.catalog = catalog
        self.settings = settings
        self.transport = transport

    def _url(self, endpoint: dict[str, Any]) -> str:
        base = self.settings.base_url or f"https://{{host}}{CANONICAL_BASE_PATH}"
        return base.rstrip("/") + "/" + endpoint["path"].lstrip("/")

    def _safe_message(self, message: str) -> str:
        safe = str(message)
        if self.settings.app_secret:
            safe = safe.replace(self.settings.app_secret, "***")
        return safe

    @staticmethod
    def _generated_headers(app_key: str, app_secret: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-App-Key": app_key,
            "X-App-Secret": app_secret,
            "X-App-Timestamp": str(int(time.time() * 1000)),
            "X-App-Nonce": secrets.token_hex(6),
        }

    def preview(
        self,
        endpoint: dict[str, Any],
        params: dict[str, Any],
        *,
        allow_extra: bool = False,
    ) -> dict[str, Any]:
        self.catalog.validate(endpoint, params, allow_extra=allow_extra)
        headers = self._generated_headers("<configured>" if self.settings.app_key else "<missing>", "***")
        return {
            "dry_run": True,
            "endpoint_id": endpoint["id"],
            "endpoint_name": endpoint["name"],
            "method": "POST",
            "url": self._url(endpoint),
            "headers": headers,
            "json": params,
        }

    def execute(
        self,
        endpoint: dict[str, Any],
        params: dict[str, Any],
        *,
        allow_extra: bool = False,
    ) -> dict[str, Any]:
        self.catalog.validate(endpoint, params, allow_extra=allow_extra)
        if not self.settings.base_url or not self.settings.app_key or not self.settings.app_secret:
            raise ConfigurationError("Execution requires configured base URL, app key, and app secret")
        headers = self._generated_headers(self.settings.app_key, self.settings.app_secret)
        body = json.dumps(params, ensure_ascii=False).encode("utf-8")
        status, response_headers, raw_body = self.transport(
            self._url(endpoint), headers, body, self.settings.timeout_seconds
        )
        request_id = next(
            (
                value
                for key, value in response_headers.items()
                if key.lower() == "x-request-id"
            ),
            None,
        )
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RemoteCallError(
                "Data service returned a non-JSON response",
                http_status=status,
                request_id=request_id,
            ) from exc
        if not isinstance(payload, dict):
            raise RemoteCallError(
                "Data service returned a non-object JSON response",
                http_status=status,
                request_id=request_id,
            )

        if status < 200 or status >= 300:
            raise RemoteCallError(
                self._safe_message(payload.get("msg") or f"Data service returned HTTP {status}"),
                http_status=status,
                business_code=payload.get("code"),
                request_id=request_id,
            )
        if payload.get("code") != 0:
            raise RemoteCallError(
                self._safe_message(payload.get("msg") or "Data service returned a business error"),
                http_status=status,
                business_code=payload.get("code"),
                request_id=request_id,
            )
        return {
            "endpoint_id": endpoint["id"],
            "endpoint_name": endpoint["name"],
            "http_status": status,
            "request_id": request_id,
            "code": payload.get("code"),
            "message": self._safe_message(payload.get("msg") or ""),
            "data": payload.get("data"),
        }


def load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "payload_file", None):
        payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    else:
        payload = json.loads(getattr(args, "payload_json", None) or "{}")
    if not isinstance(payload, dict):
        raise ValidationError(["Payload must be a JSON object"])
    return payload


def endpoint_summary(endpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": endpoint["id"],
        "name": endpoint["name"],
        "path": endpoint["path"],
        **endpoint["capabilities"],
    }


def print_search_results(endpoints: list[dict[str, Any]], *, as_json: bool) -> None:
    summaries = [endpoint_summary(endpoint) for endpoint in endpoints]
    if as_json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        return
    if not summaries:
        print("No endpoints matched.")
        return
    print("ID  RESULT    GROUP      ENTITY    TIME     NAMESPACE     NAME")
    for item in summaries:
        print(
            f"{item['id']:>2}  {item['result_kind']:<9} {item['group_mode']:<10} "
            f"{item['entity_kind']:<9} {item['time_mode']:<8} "
            f"{item['namespace']:<13} {item['name']}"
        )


def add_payload_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--payload-json", help="Inline JSON object")
    group.add_argument("--payload-file", help="Path to a JSON payload file")
    parser.add_argument("--allow-extra", action="store_true", help="Allow undocumented top-level fields")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="Search endpoint capabilities")
    search.add_argument("--query")
    search.add_argument("--result-kind", choices=["summary", "detail", "snapshot"])
    search.add_argument("--group-mode", choices=["forbidden", "optional", "required"])
    search.add_argument("--entity-kind", choices=["stations", "measures", "special"])
    search.add_argument("--time-mode", choices=["range", "instant", "none"])
    search.add_argument("--namespace", choices=["indicators", "indicatorsV2", "hems"])
    search.add_argument(
        "--profit-scope",
        choices=["order-inclusive", "base-equipment", "profit-items"],
    )
    search.add_argument("--include-planned", action="store_true")
    search.add_argument("--json", action="store_true", dest="as_json")

    show = subparsers.add_parser("show", help="Show one complete endpoint definition")
    show.add_argument("selector")

    validate = subparsers.add_parser("validate", help="Validate an endpoint payload")
    validate.add_argument("selector")
    add_payload_arguments(validate)

    call = subparsers.add_parser("call", help="Preview a request or execute it explicitly")
    call.add_argument("selector")
    add_payload_arguments(call)
    call.add_argument("--execute", action="store_true", help="Send the request over the network")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        catalog = EndpointCatalog()
        if args.command == "search":
            endpoints = catalog.search(
                query=args.query,
                result_kind=args.result_kind,
                group_mode=args.group_mode,
                entity_kind=args.entity_kind,
                time_mode=args.time_mode,
                namespace=args.namespace,
                profit_scope=args.profit_scope,
                include_planned=args.include_planned,
            )
            print_search_results(endpoints, as_json=args.as_json)
            return 0

        endpoint = catalog.resolve(args.selector)
        if args.command == "show":
            print(json.dumps(endpoint, ensure_ascii=False, indent=2))
            return 0

        params = load_payload(args)
        if args.command == "validate":
            catalog.validate(endpoint, params, allow_extra=args.allow_extra)
            print(
                json.dumps(
                    {"valid": True, "endpoint": endpoint_summary(endpoint)},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        settings = Settings.load(require_credentials=args.execute)
        client = DataServiceClient(catalog, settings)
        if args.execute:
            result = client.execute(endpoint, params, allow_extra=args.allow_extra)
        else:
            result = client.preview(endpoint, params, allow_extra=args.allow_extra)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ValidationError as exc:
        print(json.dumps({"valid": False, "errors": exc.errors}, ensure_ascii=False, indent=2))
        return 2
    except DataServiceError as exc:
        error = {"error": str(exc)}
        if isinstance(exc, RemoteCallError):
            error.update(
                {
                    "http_status": exc.http_status,
                    "business_code": exc.business_code,
                    "request_id": exc.request_id,
                }
            )
        print(json.dumps(error, ensure_ascii=False, indent=2))
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
