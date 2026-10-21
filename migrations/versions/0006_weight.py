"""Create private weight entries and user heights."""

import sqlalchemy as sa
from alembic import op

revision = "0006_weight"
down_revision = "0005_measurements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weight_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("weight_kg", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("measured_on", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint("weight_kg > 0", name="ck_weight_entries_weight_positive"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_weight_entries_owner_measured_on",
        "weight_entries",
        ["owner_user_id", "measured_on"],
    )
    op.create_table(
        "user_heights",
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("height_cm", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.CheckConstraint("height_cm > 0", name="ck_user_heights_height_positive"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("owner_user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_heights")
    op.drop_index("ix_weight_entries_owner_measured_on", table_name="weight_entries")
    op.drop_table("weight_entries")
