"""Configuration and compatibility baseline for the Knowledge Base data plane."""

from pydantic import BaseModel, Field, SecretStr

LIGHTRAG_EXPECTED_TAG = "v1.5.2-4-gab86f430"
LIGHTRAG_EXPECTED_COMMIT = "ab86f4303aafb2e66543ce3e0c735e8b698141ab"
LIGHTRAG_EXPECTED_CORE_VERSION = "1.5.2"
LIGHTRAG_EXPECTED_API_VERSION = "0308"


class LightRAGConfig(BaseModel):
    """Operator-owned connection settings for the single LightRAG service."""

    base_url: str | None = Field(default=None, description="Base URL for the operator-managed LightRAG service.")
    api_key: SecretStr | None = Field(default=None, description="Optional LightRAG X-API-Key credential.")
    timeout_seconds: float = Field(default=5.0, gt=0, le=60, description="Timeout for LightRAG data-plane requests.")
    query_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=300,
        description="Timeout for structured retrieval requests, which may include LLM keyword extraction.",
    )


class KnowledgeBaseConfig(BaseModel):
    """Knowledge Base feature gate and its LightRAG data-plane settings."""

    enabled: bool = Field(default=False, description="Expose the unified Knowledge Base feature.")
    lightrag: LightRAGConfig = Field(default_factory=LightRAGConfig)
