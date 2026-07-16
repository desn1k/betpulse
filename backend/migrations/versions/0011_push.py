"""push follows + telegram link tokens + push tier limit (Phase 11)

Revision ID: 0011_push
Revises: 0010_llm
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_push"
down_revision: str | None = "0010_llm"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A user follows a specific fixture to receive its swing pushes (no spam to
    # everyone). One row per (user, fixture).
    op.create_table(
        "push_follows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("fixture_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["fixture_id"], ["fixtures.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "fixture_id", name="uq_push_follow"),
    )
    op.create_index(op.f("ix_push_follows_user_id"), "push_follows", ["user_id"])
    op.create_index(op.f("ix_push_follows_fixture_id"), "push_follows", ["fixture_id"])

    # One-time deep-link tokens for connecting a Telegram chat. Only the SHA-256
    # hash is stored; the plaintext is shown once in the t.me/<bot>?start= link.
    op.create_table(
        "telegram_link_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_telegram_link_token_hash"),
    )
    op.create_index(op.f("ix_telegram_link_tokens_user_id"), "telegram_link_tokens", ["user_id"])

    # Push is a Pro/Expert feature: free users cannot receive pushes. Patch the
    # limit on the tier rows seeded by 0007 (guest 0 / pro 10 / expert -1 already
    # correct; only free changes 1 -> 0).
    op.execute(
        sa.text(
            "UPDATE tiers SET limits = limits || jsonb_build_object('pushes_per_day', 0) "
            "WHERE name = 'free'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE tiers SET limits = limits || jsonb_build_object('pushes_per_day', 1) "
            "WHERE name = 'free'"
        )
    )
    op.drop_index(op.f("ix_telegram_link_tokens_user_id"), table_name="telegram_link_tokens")
    op.drop_table("telegram_link_tokens")
    op.drop_index(op.f("ix_push_follows_fixture_id"), table_name="push_follows")
    op.drop_index(op.f("ix_push_follows_user_id"), table_name="push_follows")
    op.drop_table("push_follows")
