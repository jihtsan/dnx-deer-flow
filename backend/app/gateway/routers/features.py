"""Read-only feature-flag endpoint for the frontend bootstrap.

Reports which optional, config-gated features are exposed over HTTP so the
frontend can gate UI and avoid firing requests that the backend would reject
with 403. Reads through ``get_config`` so edits to ``config.yaml`` take effect
on the next request without a restart (config hot-reload boundary).
"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.gateway.browser_capability import browser_capability
from app.gateway.deps import get_config
from app.knowledge.lightrag import (
    LightRAGAuthenticationError,
    LightRAGClient,
    LightRAGContractError,
    LightRAGOfflineError,
)
from deerflow.config.app_config import AppConfig
from deerflow.config.knowledge_base_config import (
    LIGHTRAG_EXPECTED_API_VERSION,
    LIGHTRAG_EXPECTED_COMMIT,
    LIGHTRAG_EXPECTED_CORE_VERSION,
    LIGHTRAG_EXPECTED_TAG,
)

router = APIRouter(prefix="/api", tags=["features"])


class AgentsApiFeature(BaseModel):
    """Availability of the custom-agent management API."""

    enabled: bool = Field(..., description="Whether the agents_api routes are exposed over HTTP")


class KnowledgeBaseDiagnostics(BaseModel):
    """Redacted compatibility details safe to expose to workspace users."""

    workspace_mode: Literal["single"] = "single"
    expected_tag: str = LIGHTRAG_EXPECTED_TAG
    expected_commit: str = LIGHTRAG_EXPECTED_COMMIT
    expected_core_version: str = LIGHTRAG_EXPECTED_CORE_VERSION
    expected_api_version: str = LIGHTRAG_EXPECTED_API_VERSION
    observed_core_version: str | None = None
    observed_api_version: str | None = None
    service_status: str | None = None


class KnowledgeBaseFeature(BaseModel):
    """Availability of the unified Knowledge Base and its LightRAG data plane."""

    enabled: bool
    status: Literal["unconfigured", "disabled", "offline", "incompatible", "ready"]
    reason: str
    diagnostics: KnowledgeBaseDiagnostics = Field(default_factory=KnowledgeBaseDiagnostics)


class BrowserControlFeature(BaseModel):
    """Availability of live agentic browser control."""

    enabled: bool = Field(..., description="Whether the live browser routes and UI are available")


class FeaturesResponse(BaseModel):
    """Frontend-facing feature availability flags."""

    agents_api: AgentsApiFeature
    knowledge_base: KnowledgeBaseFeature
    browser_control: BrowserControlFeature


def get_lightrag_client(config: AppConfig = Depends(get_config)) -> LightRAGClient | None:
    """Build a fresh client from hot-reloaded operator configuration."""
    if not config.knowledge_base.lightrag.base_url:
        return None
    return LightRAGClient(config.knowledge_base.lightrag)


def _knowledge_base_without_health_check(config: AppConfig) -> KnowledgeBaseFeature | None:
    knowledge_config = config.knowledge_base
    if not knowledge_config.enabled:
        return KnowledgeBaseFeature(
            enabled=False,
            status="disabled",
            reason="Knowledge Base is disabled by configuration.",
        )
    if not knowledge_config.lightrag.base_url:
        return KnowledgeBaseFeature(
            enabled=False,
            status="unconfigured",
            reason="Knowledge Base is enabled but no LightRAG service is configured.",
        )
    return None


@router.get(
    "/features",
    response_model=FeaturesResponse,
    summary="List Feature Flags",
    description="Report which optional config-gated features are enabled, so the frontend can gate UI before issuing requests.",
)
async def list_features(
    config: AppConfig = Depends(get_config),
    lightrag_client: LightRAGClient | None = Depends(get_lightrag_client),
) -> FeaturesResponse:
    """Return availability of optional, config-gated frontend features."""
    knowledge_base = await resolve_knowledge_base_feature(config, lightrag_client)
    browser = browser_capability(config)
    return FeaturesResponse(
        agents_api=AgentsApiFeature(enabled=config.agents_api.enabled),
        knowledge_base=knowledge_base,
        browser_control=BrowserControlFeature(enabled=browser.available),
    )


async def resolve_knowledge_base_feature(
    config: AppConfig,
    lightrag_client: LightRAGClient | None,
) -> KnowledgeBaseFeature:
    """Resolve the shared, redacted LightRAG availability contract."""
    knowledge_base = _knowledge_base_without_health_check(config)
    if knowledge_base is None:
        if lightrag_client is None:
            knowledge_base = KnowledgeBaseFeature(
                enabled=False,
                status="unconfigured",
                reason="Knowledge Base is enabled but no LightRAG service is configured.",
            )
        else:
            try:
                health = await lightrag_client.health()
                diagnostics = KnowledgeBaseDiagnostics(
                    observed_core_version=health.core_version,
                    observed_api_version=health.api_version,
                    service_status=health.status,
                )
                compatible = health.status == "healthy" and health.core_version == LIGHTRAG_EXPECTED_CORE_VERSION and health.api_version == LIGHTRAG_EXPECTED_API_VERSION
                knowledge_base = KnowledgeBaseFeature(
                    enabled=compatible,
                    status="ready" if compatible else "incompatible",
                    reason="LightRAG is ready." if compatible else "LightRAG version is incompatible with this DeerFlow build.",
                    diagnostics=diagnostics,
                )
            except LightRAGAuthenticationError:
                knowledge_base = KnowledgeBaseFeature(
                    enabled=False,
                    status="incompatible",
                    reason="LightRAG authentication failed.",
                )
            except LightRAGOfflineError:
                knowledge_base = KnowledgeBaseFeature(
                    enabled=False,
                    status="offline",
                    reason="LightRAG is unreachable.",
                )
            except LightRAGContractError:
                knowledge_base = KnowledgeBaseFeature(
                    enabled=False,
                    status="incompatible",
                    reason="LightRAG returned an incompatible contract.",
                )
    return knowledge_base
