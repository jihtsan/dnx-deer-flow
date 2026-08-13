from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import bootstrap_schema


@pytest.mark.asyncio
async def test_migration_0014_adds_catalog_coordination_and_execution_token(tmp_path: Path) -> None:
    database = tmp_path / "receiver-coordination.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
        connection.execute("INSERT INTO alembic_version (version_num) VALUES ('0013_nexus_receiver_operations')")
        connection.execute(
            "CREATE TABLE nexus_receiver_operations ("
            "operation_id VARCHAR(36) PRIMARY KEY, idempotency_key VARCHAR(160) NOT NULL UNIQUE, "
            "request_sha256 VARCHAR(71) NOT NULL, phase VARCHAR(16) NOT NULL, command_json JSON NOT NULL, "
            "operation_json JSON NOT NULL, execution_owner VARCHAR(100), execution_expires_at DATETIME, "
            "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        )

    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    try:
        await bootstrap_schema(engine, backend="sqlite")
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names()))
            operation_columns = await connection.run_sync(lambda sync: {column["name"] for column in sa.inspect(sync).get_columns("nexus_receiver_operations")})
            catalog_constraints = await connection.run_sync(lambda sync: sa.inspect(sync).get_check_constraints("nexus_receiver_catalog_state"))
        assert {"nexus_receiver_catalog_state", "nexus_receiver_global_skills", "nexus_receiver_recovery_lease"} <= tables
        assert "execution_token" in operation_columns
        assert {item["name"] for item in catalog_constraints} >= {"ck_nexus_receiver_catalog_singleton"}
    finally:
        await engine.dispose()
