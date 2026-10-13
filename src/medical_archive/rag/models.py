from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from medical_archive.db import Base

EMBEDDING_DIMENSIONS = 768


class ChunkMetadata(Base):
    __tablename__ = "rag_chunk_metadata"
    __table_args__ = (
        CheckConstraint("page_number > 0", name="ck_rag_chunk_metadata_page_positive"),
        Index(
            "ix_rag_chunk_metadata_document_partition",
            "document_id",
            "owner_partition",
        ),
    )

    chunk_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_partition: Mapped[str] = mapped_column(String(100), nullable=False)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
