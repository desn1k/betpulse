"""tiers + subscriptions (Phase 7)

Revision ID: 0007_tiers
Revises: 0006_fixture_last_polled
Create Date: 2026-07-15

Seeds the four default tiers (guest/free/pro/expert). The seed values are a
point-in-time snapshot; the running app treats the DB rows as authoritative and
lets an admin edit them (see app.services.tiers).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_tiers"
down_revision: str | None = "0006_fixture_last_polled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

subscription_source = postgresql.ENUM("manual", "promo", "payment", name="subscription_source")

_SEED = [
    {
        "name": "guest",
        "price": 0,
        "period": None,
        "feature_flags": {
            "methods": "blurred_consensus",
            "per_half_totals": False,
            "live_recompute": False,
        },
        "limits": {"matches_per_day": 3, "pushes_per_day": 0, "backtester_runs_per_day": 0},
        "is_public": False,
        "sort_order": 0,
    },
    {
        "name": "free",
        "price": 0,
        "period": None,
        "feature_flags": {
            "methods": "consensus",
            "per_half_totals": False,
            "live_recompute": False,
        },
        "limits": {"matches_per_day": 10, "pushes_per_day": 1, "backtester_runs_per_day": 3},
        "is_public": True,
        "sort_order": 1,
    },
    {
        "name": "pro",
        "price": 9.99,
        "period": "month",
        "feature_flags": {"methods": "all", "per_half_totals": True, "live_recompute": True},
        "limits": {"matches_per_day": -1, "pushes_per_day": 10, "backtester_runs_per_day": 50},
        "is_public": True,
        "sort_order": 2,
    },
    {
        "name": "expert",
        "price": 19.99,
        "period": "month",
        "feature_flags": {
            "methods": "all_weights",
            "per_half_totals": True,
            "live_recompute": True,
        },
        "limits": {"matches_per_day": -1, "pushes_per_day": -1, "backtester_runs_per_day": -1},
        "is_public": True,
        "sort_order": 3,
    },
]


def upgrade() -> None:
    subscription_source.create(op.get_bind(), checkfirst=True)

    tiers = op.create_table(
        "tiers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), server_default="0", nullable=False),
        sa.Column("period", sa.String(length=16), nullable=True),
        sa.Column("feature_flags", postgresql.JSONB(), nullable=False),
        sa.Column("limits", postgresql.JSONB(), nullable=False),
        sa.Column("is_public", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
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
        sa.UniqueConstraint("name", name="uq_tiers_name"),
    )
    op.create_index(op.f("ix_tiers_name"), "tiers", ["name"], unique=True)

    op.bulk_insert(
        tiers,
        [
            {
                "id": uuid.uuid4(),
                "name": t["name"],
                "price": t["price"],
                "period": t["period"],
                "feature_flags": t["feature_flags"],
                "limits": t["limits"],
                "is_public": t["is_public"],
                "sort_order": t["sort_order"],
            }
            for t in _SEED
        ],
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tier_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(
                "manual", "promo", "payment", name="subscription_source", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["tier_id"], ["tiers.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", "tier_id", name="uq_subscription_user_tier"),
    )
    op.create_index(op.f("ix_subscriptions_user_id"), "subscriptions", ["user_id"])
    op.create_index(op.f("ix_subscriptions_tier_id"), "subscriptions", ["tier_id"])


def downgrade() -> None:
    op.drop_table("subscriptions")
    op.drop_index(op.f("ix_tiers_name"), table_name="tiers")
    op.drop_table("tiers")
    subscription_source.drop(op.get_bind(), checkfirst=True)
