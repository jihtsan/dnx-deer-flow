"""Merge knowledge ingestion and agent storage migration branches.

Revision ID: 0007_merge_knowledge_and_agents
Revises: 0006_reliable_knowledge_ingestion, 0006_agents
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0007_merge_knowledge_and_agents"
down_revision: str | Sequence[str] | None = (
    "0006_reliable_knowledge_ingestion",
    "0006_agents",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
