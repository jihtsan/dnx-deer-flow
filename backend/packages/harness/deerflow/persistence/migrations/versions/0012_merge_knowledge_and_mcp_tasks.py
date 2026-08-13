"""Merge knowledge catalog and MCP task migration branches.

Revision ID: 0012_merge_knowledge_and_mcp_tasks
Revises: 0009_repair_knowledge_retry_requests, 0011_mcp_tasks
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0012_merge_knowledge_and_mcp_tasks"
down_revision: str | Sequence[str] | None = (
    "0009_repair_knowledge_retry_requests",
    "0011_mcp_tasks",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
