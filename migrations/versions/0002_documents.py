"""Add private uploaded documents."""

import sqlalchemy as sa
from alembic import op

revision = "0002_documents"
down_revision = "0002_authentication_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(length=36), nullable=False, unique=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=127), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("document_type", sa.String(length=100)),
        sa.Column("document_date", sa.Date()),
        sa.Column(
            "processing_status",
            sa.String(length=32),
            server_default="needs_review",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("byte_size > 0", name="ck_documents_byte_size_positive"),
        sa.CheckConstraint(
            "processing_status IN ('needs_review', 'ready', 'ocr_failed', "
            "'index_failed', 'indexing')",
            name="ck_documents_processing_status",
        ),
    )
    op.create_index("ix_documents_owner_user_id", "documents", ["owner_user_id"])
    op.create_index("ix_documents_processing_status", "documents", ["processing_status"])


def downgrade() -> None:
    op.drop_index("ix_documents_processing_status", table_name="documents")
    op.drop_index("ix_documents_owner_user_id", table_name="documents")
    op.drop_table("documents")
