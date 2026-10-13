"""Add CSRF state and short-lived pre-authentication sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0002_authentication_sessions"
down_revision = "0001_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Old sessions have no synchronizer token, so force them to authenticate again.
    op.execute(sa.text("DELETE FROM sessions"))
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(length=64), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("csrf_token_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "(user_id IS NULL AND purpose IN ('login', 'registration')) "
            "OR (user_id IS NOT NULL AND purpose = 'authenticated')",
            name="ck_sessions_identity_purpose",
        ),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM sessions WHERE user_id IS NULL"))
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(length=64), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
