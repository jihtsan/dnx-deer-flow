"""Knowledge directory catalog and LightRAG metadata mirror.

Revision ID: 0007_knowledge_catalog
Revises: 0007_merge_knowledge_and_agents
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "0007_knowledge_catalog"
down_revision: str | Sequence[str] | None = "0007_merge_knowledge_and_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIRECTORY_TABLE = "knowledge_directories"
_DOCUMENT_TABLE = "knowledge_documents"
_REMOTE_TABLE = "knowledge_remote_documents"


def _table_exists(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def _index_names(table: str) -> set[str]:
    if not _table_exists(table):
        return set()
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def _foreign_key_names(table: str) -> set[str | None]:
    if not _table_exists(table):
        return set()
    return {foreign_key.get("name") for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table)}


def _create_directories() -> None:
    if not _table_exists(_DIRECTORY_TABLE):
        op.create_table(
            _DIRECTORY_TABLE,
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("scope_id", sa.String(length=64), nullable=False),
            sa.Column("owner_user_id", sa.String(length=64), nullable=False),
            sa.Column("parent_id", sa.String(length=64), nullable=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("normalized_name", sa.String(length=255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["parent_id"], ["knowledge_directories.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["scope_id"], ["knowledge_scopes.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    indexes = _index_names(_DIRECTORY_TABLE)
    index_specs = (
        ("ix_knowledge_directories_scope_id", ["scope_id"], False, None),
        ("ix_knowledge_directories_owner_user_id", ["owner_user_id"], False, None),
        ("ix_knowledge_directories_parent_id", ["parent_id"], False, None),
        (
            "uq_knowledge_directories_root_name",
            ["scope_id", "normalized_name"],
            True,
            sa.text("parent_id IS NULL"),
        ),
        (
            "uq_knowledge_directories_child_name",
            ["scope_id", "parent_id", "normalized_name"],
            True,
            sa.text("parent_id IS NOT NULL"),
        ),
    )
    for name, columns, unique, where in index_specs:
        if name in indexes:
            continue
        kwargs = {}
        if where is not None:
            kwargs = {"sqlite_where": where, "postgresql_where": where}
        op.create_index(name, _DIRECTORY_TABLE, columns, unique=unique, **kwargs)


def _add_document_directory() -> None:
    if not _table_exists(_DOCUMENT_TABLE):
        return
    safe_add_column(
        _DOCUMENT_TABLE,
        sa.Column("directory_id", sa.String(length=64), nullable=True),
    )
    indexes = _index_names(_DOCUMENT_TABLE)
    foreign_keys = _foreign_key_names(_DOCUMENT_TABLE)
    with op.batch_alter_table(_DOCUMENT_TABLE, schema=None) as batch_op:
        if "ix_knowledge_documents_directory_id" not in indexes:
            batch_op.create_index("ix_knowledge_documents_directory_id", ["directory_id"], unique=False)
        if "fk_knowledge_documents_directory_id" not in foreign_keys:
            batch_op.create_foreign_key(
                "fk_knowledge_documents_directory_id",
                _DIRECTORY_TABLE,
                ["directory_id"],
                ["id"],
                ondelete="RESTRICT",
            )


def _create_remote_documents() -> None:
    if not _table_exists(_REMOTE_TABLE):
        op.create_table(
            _REMOTE_TABLE,
            sa.Column("id", sa.String(length=64), nullable=False),
            sa.Column("scope_id", sa.String(length=64), nullable=False),
            sa.Column("owner_user_id", sa.String(length=64), nullable=False),
            sa.Column("directory_id", sa.String(length=64), nullable=True),
            sa.Column("remote_document_id", sa.String(length=255), nullable=False),
            sa.Column("original_filename", sa.String(length=255), nullable=False),
            sa.Column("content_type", sa.String(length=255), nullable=False),
            sa.Column("content_length", sa.BigInteger(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("lightrag_tracking_id", sa.String(length=255), nullable=False),
            sa.Column("failure_code", sa.String(length=64), nullable=True),
            sa.Column("failure_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "content_length >= 0",
                name="ck_knowledge_remote_documents_content_length",
            ),
            sa.CheckConstraint(
                "status IN ('pending', 'indexing', 'ready', 'failed')",
                name="ck_knowledge_remote_documents_status",
            ),
            sa.ForeignKeyConstraint(["directory_id"], ["knowledge_directories.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["scope_id"], ["knowledge_scopes.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "scope_id",
                "remote_document_id",
                name="uq_knowledge_remote_documents_scope_remote",
            ),
        )

    indexes = _index_names(_REMOTE_TABLE)
    for name, columns in (
        ("ix_knowledge_remote_documents_scope_id", ["scope_id"]),
        ("ix_knowledge_remote_documents_owner_user_id", ["owner_user_id"]),
        ("ix_knowledge_remote_documents_directory_id", ["directory_id"]),
        ("ix_knowledge_remote_documents_status", ["status"]),
        ("ix_knowledge_remote_documents_lightrag_tracking_id", ["lightrag_tracking_id"]),
    ):
        if name not in indexes:
            op.create_index(name, _REMOTE_TABLE, columns, unique=False)


def upgrade() -> None:
    _create_directories()
    _add_document_directory()
    _create_remote_documents()


def downgrade() -> None:
    if _table_exists(_REMOTE_TABLE):
        op.drop_table(_REMOTE_TABLE)

    if _table_exists(_DOCUMENT_TABLE):
        indexes = _index_names(_DOCUMENT_TABLE)
        foreign_keys = _foreign_key_names(_DOCUMENT_TABLE)
        with op.batch_alter_table(_DOCUMENT_TABLE, schema=None) as batch_op:
            if "ix_knowledge_documents_directory_id" in indexes:
                batch_op.drop_index("ix_knowledge_documents_directory_id")
            if "fk_knowledge_documents_directory_id" in foreign_keys:
                batch_op.drop_constraint("fk_knowledge_documents_directory_id", type_="foreignkey")
        safe_drop_column(_DOCUMENT_TABLE, "directory_id")

    if _table_exists(_DIRECTORY_TABLE):
        op.drop_table(_DIRECTORY_TABLE)
