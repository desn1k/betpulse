"""promo batches, codes, redemptions (Phase 8)

Revision ID: 0008_promos
Revises: 0007_tiers
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_promos"
down_revision: str | None = "0007_tiers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

code_type = postgresql.ENUM("percent", "fixed", "trial", "upgrade", name="promo_code_type")
batch_status = postgresql.ENUM("active", "disabled", name="promo_batch_status")
code_status = postgresql.ENUM("active", "redeemed", "disabled", name="promo_code_status")
redemption_status = postgresql.ENUM("applied", "pending", "expired", name="promo_redemption_status")


def upgrade() -> None:
    code_type.create(op.get_bind(), checkfirst=True)
    batch_status.create(op.get_bind(), checkfirst=True)
    code_status.create(op.get_bind(), checkfirst=True)
    redemption_status.create(op.get_bind(), checkfirst=True)

    def ct(name: str) -> postgresql.ENUM:
        return postgresql.ENUM("percent", "fixed", "trial", "upgrade", name=name, create_type=False)

    op.create_table(
        "promo_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("code_type", ct("promo_code_type"), nullable=False),
        sa.Column("value", sa.Numeric(10, 2), nullable=True),
        sa.Column("tier_id", sa.Uuid(), nullable=True),
        sa.Column("bound_user_id", sa.Uuid(), nullable=True),
        sa.Column("max_activations", sa.Integer(), server_default="1", nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("stackable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM("active", "disabled", name="promo_batch_status", create_type=False),
            server_default="active",
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tier_id"], ["tiers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bound_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
    )

    op.create_table(
        "promo_codes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("activations_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_activations", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "active", "redeemed", "disabled", name="promo_code_status", create_type=False
            ),
            server_default="active",
            nullable=False,
        ),
        sa.Column("bound_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["promo_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bound_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("code_hash", name="uq_promo_code_hash"),
    )
    op.create_index(op.f("ix_promo_codes_batch_id"), "promo_codes", ["batch_id"])
    op.create_index(op.f("ix_promo_codes_code_hash"), "promo_codes", ["code_hash"], unique=True)

    op.create_table(
        "promo_redemptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("code_type", ct("promo_code_type"), nullable=False),
        sa.Column("value", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "applied", "pending", "expired", name="promo_redemption_status", create_type=False
            ),
            nullable=False,
        ),
        sa.Column(
            "redeemed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["batch_id"], ["promo_batches.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "code_hash", name="uq_redemption_user_code"),
    )
    op.create_index(op.f("ix_promo_redemptions_user_id"), "promo_redemptions", ["user_id"])
    op.create_index(op.f("ix_promo_redemptions_batch_id"), "promo_redemptions", ["batch_id"])


def downgrade() -> None:
    op.drop_table("promo_redemptions")
    op.drop_index(op.f("ix_promo_codes_code_hash"), table_name="promo_codes")
    op.drop_index(op.f("ix_promo_codes_batch_id"), table_name="promo_codes")
    op.drop_table("promo_codes")
    op.drop_table("promo_batches")
    redemption_status.drop(op.get_bind(), checkfirst=True)
    code_status.drop(op.get_bind(), checkfirst=True)
    batch_status.drop(op.get_bind(), checkfirst=True)
    code_type.drop(op.get_bind(), checkfirst=True)
