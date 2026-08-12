"""Migration coverage for durable Nexus receiver operations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import bootstrap_schema


@pytest.mark.asyncio
async def test_migration_0013_creates_receiver_operation_constraints(tmp_path: Path) -> None:
    database = tmp_path / "receiver-migration.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        connection.execute("INSERT INTO alembic_version (version_num) VALUES ('0012_merge_knowledge_and_mcp_tasks')")

    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    try:
        await bootstrap_schema(engine, backend="sqlite")
        async with engine.connect() as connection:
            table_names = await connection.run_sync(lambda sync: sa.inspect(sync).get_table_names())
            indexes = await connection.run_sync(lambda sync: sa.inspect(sync).get_indexes("nexus_receiver_operations"))
            constraints = await connection.run_sync(lambda sync: sa.inspect(sync).get_unique_constraints("nexus_receiver_operations"))
            columns = await connection.run_sync(lambda sync: sa.inspect(sync).get_columns("nexus_receiver_operations"))
        assert "nexus_receiver_operations" in table_names
        assert {item["name"] for item in indexes} >= {"ix_nexus_receiver_operations_phase"}
        assert {item["name"] for item in constraints} >= {"uq_nexus_receiver_operations_idempotency_key"}
        assert {item["name"] for item in columns} >= {"execution_owner", "execution_expires_at"}
    finally:
        await engine.dispose()
