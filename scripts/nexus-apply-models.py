#!/usr/bin/env python3
"""Apply a versioned Nexus model catalog to Deer Flow config.yaml."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "backend" / "packages" / "harness"))

from deerflow.config.app_config import AppConfig  # noqa: E402

SCHEMA_VERSION = 1
MODEL_FIELDS = {
    "name",
    "display_name",
    "description",
    "use",
    "model",
    "base_url",
    "api_key",
    "request_timeout",
    "max_retries",
    "use_responses_api",
    "output_version",
    "supports_thinking",
    "supports_reasoning_effort",
    "supports_vision",
}
REQUIRED_MODEL_FIELDS = {"name", "display_name", "use", "model", "base_url", "api_key"}


class ConfigError(Exception):
    """A safe-to-report payload or configuration error."""


def load_payload(stdin_text: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError as exc:
        raise ConfigError("invalid JSON payload") from exc
    if not isinstance(payload, dict) or set(payload) != {"schemaVersion", "models"}:
        raise ConfigError("invalid payload schema")
    if payload["schemaVersion"] != SCHEMA_VERSION:
        raise ConfigError(f"schemaVersion must be {SCHEMA_VERSION}")
    models = payload["models"]
    if not isinstance(models, list) or not models:
        raise ConfigError("models must be a non-empty array")

    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    for model in models:
        if not isinstance(model, dict):
            raise ConfigError("each model must be an object")
        if not REQUIRED_MODEL_FIELDS.issubset(model) or not set(model).issubset(MODEL_FIELDS):
            raise ConfigError("model contains missing or unsupported fields")
        name = model.get("name")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ConfigError("model names must be non-empty and unique")
        names.add(name)
        normalized.append(dict(model))
    return normalized


def validate_candidate(candidate: dict[str, Any]) -> None:
    try:
        resolved = AppConfig.resolve_env_variables(candidate)
        AppConfig.model_validate(resolved)
    except Exception as exc:
        raise ConfigError("candidate AppConfig validation failed") from exc


def atomic_write(path: Path, content: str, mode: int) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def apply_models(config_path: Path, models: list[dict[str, Any]]) -> dict[str, Any]:
    config_path = config_path.resolve()
    if not config_path.is_file():
        raise ConfigError("config.yaml does not exist")
    lock_path = config_path.with_name(f".{config_path.name}.nexus-models.lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                current = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError) as exc:
                raise ConfigError("existing config.yaml cannot be read") from exc
            if not isinstance(current, dict):
                raise ConfigError("existing config.yaml root must be an object")

            candidate = dict(current)
            candidate["models"] = models
            validate_candidate(candidate)
            rendered = yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True)
            mode = stat.S_IMODE(config_path.stat().st_mode)
            atomic_write(config_path, rendered, mode)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return {
        "modelCount": len(models),
        "modelNames": [model["name"] for model in models],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config.yaml")
    args = parser.parse_args()
    try:
        models = load_payload(sys.stdin.read())
        result = apply_models(args.config, models)
    except ConfigError as exc:
        print(json.dumps({"success": False, "error": str(exc)}), file=sys.stderr)
        return 2
    except Exception:
        print(
            json.dumps({"success": False, "error": "internal apply failure"}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"success": True, **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
