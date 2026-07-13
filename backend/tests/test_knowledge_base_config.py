from pathlib import Path

import yaml

from deerflow.config.knowledge_base_config import KnowledgeBaseConfig


def test_knowledge_base_defaults_to_disabled_and_unconfigured() -> None:
    config = KnowledgeBaseConfig()
    assert config.enabled is False
    assert config.lightrag.base_url is None
    assert config.lightrag.api_key is None


def test_lightrag_api_key_is_masked_in_config_representation() -> None:
    config = KnowledgeBaseConfig.model_validate(
        {
            "enabled": True,
            "lightrag": {
                "base_url": "http://127.0.0.1:9621",
                "api_key": "sensitive-value",
            },
        }
    )
    assert "sensitive-value" not in repr(config)
    assert config.lightrag.api_key is not None
    assert config.lightrag.api_key.get_secret_value() == "sensitive-value"


def test_config_example_documents_the_lightrag_connection() -> None:
    root = Path(__file__).resolve().parents[2]
    config = yaml.safe_load((root / "config.example.yaml").read_text(encoding="utf-8"))
    assert config["config_version"] == 22
    assert config["knowledge_base"] == {
        "enabled": False,
        "lightrag": {
            "base_url": None,
            "api_key": None,
            "timeout_seconds": 5.0,
        },
    }
