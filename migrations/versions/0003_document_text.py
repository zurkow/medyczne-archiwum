"""Store extracted page text and OCR status."""

import sqlalchemy as sa
from alembic import op

revision = "0003_document_text"
down_revision = "0002_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "ocr_status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
    )
    op.create_table(
        "document_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(length=36),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("extraction_method", sa.String(length=16), nullable=False),
        sa.UniqueConstraint("document_id", "page_number", name="uq_document_pages_document_page"),
        sa.CheckConstraint("page_number > 0", name="ck_document_pages_page_number_positive"),
        sa.CheckConstraint(
            "extraction_method IN ('text', 'ocr')",
            name="ck_document_pages_extraction_method",
        ),
    )
    op.create_index("ix_document_pages_document_id", "document_pages", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_document_pages_document_id", table_name="document_pages")
    op.drop_table("document_pages")
    op.drop_column("documents", "ocr_status")
