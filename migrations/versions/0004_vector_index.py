"""Create private user-partitioned document vectors."""

import sqlalchemy as sa
from alembic import op

from medical_archive.rag.index import load_sqlite_vec
from medical_archive.rag.models import EMBEDDING_DIMENSIONS

revision = "0004_vector_index"
down_revision = "0003_document_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    load_sqlite_vec(op.get_bind())
    op.create_table(
        "rag_chunk_metadata",
        sa.Column("chunk_id", sa.String(length=36), primary_key=True),
        sa.Column("owner_partition", sa.String(length=100), nullable=False),
        sa.Column(
            "document_id",
            sa.String(length=36),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.CheckConstraint("page_number > 0", name="ck_rag_chunk_metadata_page_positive"),
    )
    op.create_index(
        "ix_rag_chunk_metadata_document_partition",
        "rag_chunk_metadata",
        ["document_id", "owner_partition"],
    )
    op.get_bind().exec_driver_sql(
        "CREATE VIRTUAL TABLE rag_chunk_vectors USING vec0("
        f"chunk_id TEXT PRIMARY KEY, owner_partition TEXT partition key, "
        f"embedding float[{EMBEDDING_DIMENSIONS}] distance_metric=cosine)"
    )


def downgrade() -> None:
    load_sqlite_vec(op.get_bind())
    op.get_bind().exec_driver_sql("DROP TABLE rag_chunk_vectors")
    op.drop_index(
        "ix_rag_chunk_metadata_document_partition",
        table_name="rag_chunk_metadata",
    )
    op.drop_table("rag_chunk_metadata")
