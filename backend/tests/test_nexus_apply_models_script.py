from __future__ import annotations

import importlib.util
import io
import json
import multiprocessing
import stat
import sys
from multiprocessing.connection import Connection
from pathlib import Path

import pytest
import yaml

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts/nexus-apply-models.py"
SPEC = importlib.util.spec_from_file_location("nexus_apply_models", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
nexus_apply_models = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nexus_apply_models)


def _apply_models_in_process(
    config_path: Path,
    models: list[dict],
    connection: Connection,
    pause_during_validation: bool,
) -> None:
    validate_candidate = nexus_apply_models.validate_candidate

    def signal_validation(candidate: dict) -> None:
        connection.send("validating")
        if pause_during_validation:
            if connection.recv() != "continue":
                raise RuntimeError("unexpected test synchronization message")
        validate_candidate(candidate)

    nexus_apply_models.validate_candidate = signal_validation
    try:
        result = nexus_apply_models.apply_models(config_path, models)
    except Exception as exc:
        connection.send(("error", type(exc).__name__, str(exc)))
    else:
        connection.send(("done", result))
    finally:
        connection.close()


def base_config() -> dict:
    return {
        "config_version": 30,
        "log_level": "info",
        "models": [
            {
                "name": "legacy",
                "display_name": "Legacy",
                "description": None,
                "use": "langchain_openai:ChatOpenAI",
                "model": "legacy-model",
                "api_key": "legacy-secret",
            }
        ],
        "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
    }


def desired_models() -> list[dict]:
    return [
        {
            "name": "gpt-5.6",
            "display_name": "GPT-5.6 (Sol)",
            "description": "Managed by Nexus",
            "use": "langchain_openai:ChatOpenAI",
            "model": "gpt-5.6",
            "base_url": "https://models.example.test/v1",
            "api_key": "new-secret",
            "supports_thinking": True,
            "supports_reasoning_effort": True,
            "supports_vision": True,
        }
    ]


def write_config(path: Path, value: dict) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_applies_only_models_and_preserves_file_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    original = base_config()
    write_config(config_path, original)
    config_path.chmod(0o640)

    result = nexus_apply_models.apply_models(config_path, desired_models())

    written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert written["models"] == desired_models()
    assert written["log_level"] == original["log_level"]
    assert written["sandbox"] == original["sandbox"]
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o640
    assert result == {"modelCount": 1, "modelNames": ["gpt-5.6"]}
    assert list(tmp_path.glob(".config.yaml.*.tmp")) == []


def test_replaces_config_atomically_from_the_same_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path, base_config())
    original_bytes = config_path.read_bytes()
    original_replace = nexus_apply_models.os.replace
    replacements: list[tuple[Path, Path]] = []

    def observe_replace(source: Path, destination: Path) -> None:
        assert config_path.read_bytes() == original_bytes
        assert source.parent == config_path.parent
        replacements.append((source, destination))
        original_replace(source, destination)

    monkeypatch.setattr(nexus_apply_models.os, "replace", observe_replace)

    nexus_apply_models.apply_models(config_path, desired_models())

    assert len(replacements) == 1
    assert replacements[0][1] == config_path
    assert yaml.safe_load(config_path.read_text(encoding="utf-8"))["models"] == desired_models()


def test_serializes_concurrent_catalog_updates(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path, base_config())
    first_models = desired_models()
    second_models = desired_models()
    second_models[0] = {
        **second_models[0],
        "name": "gpt-5.6-second",
        "display_name": "GPT-5.6 Second",
        "model": "gpt-5.6-second",
    }
    context = multiprocessing.get_context("fork")
    first_parent, first_child = context.Pipe()
    second_parent, second_child = context.Pipe()
    first_process = context.Process(
        target=_apply_models_in_process,
        args=(config_path, first_models, first_child, True),
    )
    second_process = context.Process(
        target=_apply_models_in_process,
        args=(config_path, second_models, second_child, False),
    )

    try:
        first_process.start()
        assert first_parent.poll(5)
        assert first_parent.recv() == "validating"

        second_process.start()
        assert not second_parent.poll(0.5)

        first_parent.send("continue")
        assert first_parent.poll(5)
        assert first_parent.recv() == (
            "done",
            {"modelCount": 1, "modelNames": ["gpt-5.6"]},
        )
        assert second_parent.poll(5)
        assert second_parent.recv() == "validating"
        assert second_parent.poll(5)
        assert second_parent.recv() == (
            "done",
            {"modelCount": 1, "modelNames": ["gpt-5.6-second"]},
        )
    finally:
        first_parent.close()
        second_parent.close()
        for process in (first_process, second_process):
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert first_process.exitcode == 0
    assert second_process.exitcode == 0
    written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert written["models"] == second_models


def test_invalid_candidate_does_not_modify_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path, base_config())
    original_bytes = config_path.read_bytes()
    invalid = desired_models()
    del invalid[0]["use"]

    with pytest.raises(nexus_apply_models.ConfigError, match="validation"):
        nexus_apply_models.apply_models(config_path, invalid)

    assert config_path.read_bytes() == original_bytes


def test_payload_validation_rejects_unknown_fields_without_echoing_secrets() -> None:
    payload = {
        "schemaVersion": 1,
        "models": [{**desired_models()[0], "unexpected": "new-secret"}],
    }

    with pytest.raises(nexus_apply_models.ConfigError) as error:
        nexus_apply_models.load_payload(json.dumps(payload))

    assert "new-secret" not in str(error.value)


def test_cli_validation_error_does_not_echo_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path, base_config())
    original_bytes = config_path.read_bytes()
    secret = "nexus-private-credential"
    models = desired_models()
    models[0]["api_key"] = secret
    models[0]["supports_vision"] = secret
    payload = {"schemaVersion": 1, "models": models}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT_PATH), "--config", str(config_path)],
    )

    exit_code = nexus_apply_models.main()

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "success": False,
        "error": "candidate AppConfig validation failed",
    }
    assert secret not in captured.err
    assert config_path.read_bytes() == original_bytes


def test_payload_requires_a_non_empty_unique_model_catalog() -> None:
    with pytest.raises(nexus_apply_models.ConfigError):
        nexus_apply_models.load_payload(json.dumps({"schemaVersion": 1, "models": []}))

    duplicate = desired_models() * 2
    with pytest.raises(nexus_apply_models.ConfigError):
        nexus_apply_models.load_payload(json.dumps({"schemaVersion": 1, "models": duplicate}))
