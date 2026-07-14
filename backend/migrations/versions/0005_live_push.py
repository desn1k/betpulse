"""live streaming + push notifications (Phase 5)

Revision ID: 0005_live_push
Revises: 0004_model_registry
Create Date: 2026-07-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_live_push"
down_revision: str | None = "0004_model_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

user_tier = postgresql.ENUM("free", "pro", "expert", name="user_tier")
push_channel = postgresql.ENUM("telegram", "webpush", name="push_channel")


def upgrade() -> None:
    user_tier.create(op.get_bind(), checkfirst=True)
    push_channel.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "users",
        sa.Column(
            "tier",
            postgresql.ENUM("free", "pro", "expert", name="user_tier", create_type=False),
            server_default="free",
            nullable=False,
        ),
    )

    op.create_table(
        "live_updates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column("minute", sa.Integer(), nullable=False),
        sa.Column("home_score", sa.Integer(), nullable=False),
        sa.Column("away_score", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_live_updates_fixture_id"), "live_updates", ["fixture_id"])
    op.create_index(op.f("ix_live_updates_created_at"), "live_updates", ["created_at"])

    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "channel",
            postgresql.ENUM("telegram", "webpush", name="push_channel", create_type=False),
            nullable=False,
        ),
        sa.Column("endpoint", sa.String(length=512), nullable=False),
        sa.Column("keys", postgresql.JSONB(), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "channel", "endpoint", name="uq_push_subscription"),
    )
    op.create_index(op.f("ix_push_subscriptions_user_id"), "push_subscriptions", ["user_id"])


def downgrade() -> None:
    op.drop_table("push_subscriptions")
    op.drop_table("live_updates")
    op.drop_column("users", "tier")
    push_channel.drop(op.get_bind(), checkfirst=True)
    user_tier.drop(op.get_bind(), checkfirst=True)
