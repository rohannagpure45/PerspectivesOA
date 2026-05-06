"""initial schema: extractions, asam_audits, tjc_audits

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-05 19:32:00

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "extractions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("hashed_id", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="fixture"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_extractions_hashed_id", "extractions", ["hashed_id"])
    op.create_index("ix_extractions_hashed_created", "extractions", ["hashed_id", "created_at"])

    op.create_table(
        "asam_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("hashed_id", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_asam_audits_hashed_id", "asam_audits", ["hashed_id"])
    op.create_index("ix_asam_audits_hashed_created", "asam_audits", ["hashed_id", "created_at"])

    op.create_table(
        "tjc_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("hashed_id", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_tjc_audits_hashed_id", "tjc_audits", ["hashed_id"])
    op.create_index("ix_tjc_audits_hashed_created", "tjc_audits", ["hashed_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_tjc_audits_hashed_created", table_name="tjc_audits")
    op.drop_index("ix_tjc_audits_hashed_id", table_name="tjc_audits")
    op.drop_table("tjc_audits")

    op.drop_index("ix_asam_audits_hashed_created", table_name="asam_audits")
    op.drop_index("ix_asam_audits_hashed_id", table_name="asam_audits")
    op.drop_table("asam_audits")

    op.drop_index("ix_extractions_hashed_created", table_name="extractions")
    op.drop_index("ix_extractions_hashed_id", table_name="extractions")
    op.drop_table("extractions")
