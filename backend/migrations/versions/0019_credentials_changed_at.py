"""users.credentials_changed_at: access tokens issued before it are dead (ER2-01)

An access JWT used to stay valid for its whole lifetime (15 minutes) after a
password change, a TOTP reset or an admin disabling the account. The new column
records when the account's credentials last changed; every access token carries
the value it was issued under, and a mismatch is refused (app.core.deps).

Upgrade adds the column as NULL for every existing row: "nothing to revoke yet".
Tokens issued before this migration carry no claim and keep working while the
column is NULL, so the deploy signs nobody out. Setting it to now() here would
end every live session at once.

Downgrade drops the column. Tokens that carry the claim are accepted by the
older code, which ignores unknown claims. Expand-only, so an image from before
0019 runs on this schema unchanged (ER-H-08); it just does not check the column.

Revision ID: 0019_credentials_changed_at
Revises: 0018_llm_generations
Create Date: 2026-10-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_credentials_changed_at"
down_revision: str | None = "0018_llm_generations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("credentials_changed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "credentials_changed_at")
